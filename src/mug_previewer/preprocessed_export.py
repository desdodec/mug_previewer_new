"""Production composition from indexed prepared artwork, without live face generation."""
from dataclasses import dataclass, replace
from pathlib import Path
import os
import re
import tempfile

from PIL import Image

from .datasets.models import Dataset, StreetRecord
from .design import DesignOptions, build_render_options, design_profile_fingerprint
from .diagnostics.front_candidates import ProductionTriageStatus
from .exporting import DEFAULT_PROFILE_IDS, save_profile_set, save_provider_artwork
from .prepared_asset import PreparedFaceSource, load_prepared_face_image, resolve_prepared_face_source
from .preprocess import FaceSvgResolution, INDEX_FILENAME, _load_index, resolve_authoritative_face_svg
from .providers import get_provider_profile
from .review_index import current_review_state
from .rendering.artwork import WrapComposer
from .rendering.context_map import render_context_map_result
from .rendering.face import (
    AREA_Y_RATIO,
    LOCALITY_FONT_SIZE,
    SUPPORTING_STROKE_WIDTH,
    TITLE_Y_RATIO,
    _scale_face_linework,
    _spread_face_component_rows,
    _spread_vertical_position,
    extract_street_feature_colour,
    street_feature_colour,
)
from .rendering.svg_raster import rasterize_face_svg


PRODUCTION_FRONT_WEIGHT_KEY = "mug_previewer_front_feature_weight"
PRODUCTION_REAR_WEIGHT_KEY = "mug_previewer_rear_highlight_weight"
PRODUCTION_STYLE_FINGERPRINT_KEY = "mug_previewer_style_fingerprint"


def production_png_matches_design(path: Path | str, design_options: DesignOptions | None) -> bool:
    """Return true only when an existing production PNG records these design settings."""
    design = design_options or DesignOptions()
    try:
        with Image.open(path) as image:
            recorded_fingerprint = image.info.get(PRODUCTION_STYLE_FINGERPRINT_KEY)
            if recorded_fingerprint is not None:
                return recorded_fingerprint == design_profile_fingerprint(design)
            return (
                image.info.get(PRODUCTION_FRONT_WEIGHT_KEY) == f"{design.front_feature_weight:.2f}"
                and image.info.get(PRODUCTION_REAR_WEIGHT_KEY) == f"{design.rear_highlight_weight:.2f}"
                and design == DesignOptions(
                    front_feature_weight=design.front_feature_weight,
                    rear_highlight_weight=design.rear_highlight_weight,
                )
            )
    except (OSError, ValueError):
        return False


class AuthoritativeArtworkError(ValueError):
    """Indexed artwork is unavailable, invalid, or not approved for production."""


@dataclass(frozen=True)
class AuthoritativeFacePanel:
    image: Image.Image
    resolution: FaceSvgResolution
    source: PreparedFaceSource

    @property
    def production_approved(self) -> bool:
        return self.resolution.production_approved


def _indexed_record(root: Path, dataset: Dataset, street: StreetRecord):
    return next(
        (
            item
            for item in _load_index(root / INDEX_FILENAME)
            if (item.get("dataset_id"), item.get("street_id")) == (dataset.id, street.id)
        ),
        None,
    )


def _resolve_front_source(preprocessed: Path | str, dataset: Dataset, street: StreetRecord):
    root = Path(preprocessed).resolve()
    resolution = resolve_authoritative_face_svg(root, dataset, street)
    if resolution.state is ProductionTriageStatus.UNRENDERABLE_INPUT:
        return resolution, None
    record = _indexed_record(root, dataset, street)
    if record is None or not record.get("success"):
        raise AuthoritativeArtworkError("Prepared artwork index record is unavailable.")
    try:
        source = resolve_prepared_face_source(root, record)
    except (OSError, ValueError) as error:
        raise AuthoritativeArtworkError(f"Prepared front artwork asset integrity problem: {error}") from error
    return resolution, source


def _scale_prepared_street_stroke(markup: str, scale: float) -> str:
    """Scale only the coloured .street feature in a prepared face SVG."""
    if scale == 1.0:
        return markup

    def adjust(match: re.Match[str]) -> str:
        tag = match.group(0)
        classes = re.search(r'\bclass="([^"]*)"', tag)
        if classes is None or "street" not in classes.group(1).split():
            return tag
        width = re.search(r'\bstroke-width="([0-9.]+)"', tag)
        if width is not None:
            value = float(width.group(1)) * scale
            return tag[:width.start(1)] + f"{value:.3f}" + tag[width.end(1):]
        style = re.search(r'\bstyle="([^"]*)"', tag)
        if style is not None:
            width = re.search(r'(?P<prefix>(?:^|;)\s*stroke-width\s*:\s*)(?P<value>[0-9.]+)', style.group(1))
            if width is not None:
                value = float(width.group("value")) * scale
                revised = style.group(1)[:width.start("value")] + f"{value:.3f}" + style.group(1)[width.end("value"):]
                return tag[:style.start(1)] + revised + tag[style.end(1):]
        return tag

    return re.sub(r'<(?:[A-Za-z0-9_]+:)?(?:polyline|path)\b[^>]*>', adjust, markup)

def _replace_css_numeric(markup: str, selector: str, property_name: str, multiplier: float) -> str:
    pattern = re.compile(
        rf"({re.escape(selector)}\s*\{{[^}}]*?{re.escape(property_name)}\s*:\s*)([0-9.]+)",
        re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub(
        lambda match: match.group(1) + f"{float(match.group(2)) * multiplier:.3f}",
        markup,
        count=1,
    )


def _apply_prepared_design_profile(markup: str, design: DesignOptions) -> str:
    """Apply one production style to authoritative canonical SVG markup.

    This changes presentation values only. Source path coordinates, reviewed
    street geometry and the authoritative SVG on disk are never modified.
    """
    adjusted = _scale_prepared_street_stroke(markup, design.front_feature_weight)

    if design.front_title_font_scale != 1.0:
        adjusted = _replace_css_numeric(
            adjusted, ".mug-title", "font-size", design.front_title_font_scale,
        )
    if design.front_locality_font_scale != 1.0:
        adjusted = re.sub(
            r"(\.mug-area\s*\{\s*font\s*:\s*500\s+)([0-9.]+)(px)",
            lambda match: (
                match.group(1)
                + f"{float(match.group(2)) * design.front_locality_font_scale:.3f}"
                + match.group(3)
            ),
            adjusted,
            count=1,
            flags=re.IGNORECASE,
        )

    if design.front_facial_linework_multiplier != 1.0:
        adjusted = _scale_face_linework(
            adjusted, design.front_facial_linework_multiplier,
        )

    support_width = (
        SUPPORTING_STROKE_WIDTH
        * design.front_facial_linework_multiplier
        * design.front_supporting_stroke_multiplier
    )
    adjusted = re.sub(
        r"(\.v28-face-linework\s+\.v28-support\s*\{[^}]*?stroke-width\s*:)[^;!}]+",
        rf"\g<1>{support_width:.3f}px ",
        adjusted,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    view_box = re.search(r'\bviewBox="[^"]*\s([0-9.]+)\s([0-9.]+)"', adjusted)
    panel_height = float(view_box.group(2)) if view_box is not None else 462.0
    title_y = panel_height * TITLE_Y_RATIO + design.front_typography_block_y_offset
    area_y = (
        panel_height * AREA_Y_RATIO
        + design.front_title_locality_gap_delta
        + design.front_typography_block_y_offset
    )
    title_y = _spread_vertical_position(
        title_y, panel_height, design.front_vertical_spread,
    )
    area_y = _spread_vertical_position(
        area_y, panel_height, design.front_vertical_spread,
    )
    adjusted = re.sub(
        r'(<g id="title"><text\b[^>]*\by=")[^"]+(")',
        rf"\g<1>{title_y:.3f}\g<2>",
        adjusted,
        count=1,
    )
    adjusted = re.sub(
        r'(<g id="locality"><text\b[^>]*\by=")[^"]+(")',
        rf"\g<1>{area_y:.3f}\g<2>",
        adjusted,
        count=1,
    )

    if design.front_vertical_spread != 1.0:
        adjusted = _spread_face_component_rows(
            adjusted, design.front_vertical_spread,
        )

    adjusted = re.sub(
        r'(class="front-composition"\s+transform=")translate\(0\s+[-+0-9.]+\)(\s+translate\([^)]*\)\s+scale\()[-+0-9.]+(\))',
        lambda match: (
            match.group(1)
            + f"translate(0 {design.front_group_y_offset:.2f})"
            + match.group(2)
            + f"{design.front_group_scale:.4f}"
            + match.group(3)
        ),
        adjusted,
        count=1,
    )
    return adjusted


def render_authoritative_face_panel(
    preprocessed: Path | str,
    dataset: Dataset,
    street: StreetRecord,
    *,
    require_production_approved: bool = False,
    street_stroke_scale: float = 1.0,
    design_options: DesignOptions | None = None,
) -> AuthoritativeFacePanel:
    """Resolve and rasterise the prepared face without regenerating it.

    Canonical SVG is preferred.  If an older prepared set has genuinely lost
    that SVG, its existing 495x462 cached preview is accepted as a read-only
    recovery source so reviewed visual work can still be previewed/exported.
    """
    try:
        resolution, source = _resolve_front_source(preprocessed, dataset, street)
    except (OSError, ValueError) as error:
        if isinstance(error, AuthoritativeArtworkError):
            raise
        raise AuthoritativeArtworkError(f"Cannot read prepared artwork index: {error}") from error
    label = f"{dataset.display_name} / {street.id} ({resolution.state.value})"
    if resolution.state is ProductionTriageStatus.UNRENDERABLE_INPUT:
        raise AuthoritativeArtworkError(f"{label}: no front artwork available; export forbidden.")
    if require_production_approved and not resolution.production_approved:
        raise AuthoritativeArtworkError(f"{label}: artwork is not production approved.")
    if source is None:
        raise AuthoritativeArtworkError(f"{label}: prepared front artwork is missing; asset integrity problem.")
    try:
        if source.is_svg and design_options is not None:
            markup = source.path.read_text(encoding="utf-8")
            panel = rasterize_face_svg(_apply_prepared_design_profile(markup, design_options))
        elif source.is_svg and street_stroke_scale != 1.0:
            markup = source.path.read_text(encoding="utf-8")
            panel = rasterize_face_svg(_scale_prepared_street_stroke(markup, street_stroke_scale))
        else:
            panel = load_prepared_face_image(source)
    except Exception as error:
        raise AuthoritativeArtworkError(
            f"{label}: cannot rasterise prepared face source {source.path}: {error}"
        ) from error
    return AuthoritativeFacePanel(panel, resolution, source)


def render_preprocessed_artwork_groups(
    preprocessed: Path | str,
    dataset: Dataset,
    street: StreetRecord,
    *,
    design_options: DesignOptions | None = None,
    require_production_approved: bool = True,
) -> tuple[Image.Image, Image.Image]:
    """Render supplier-independent front and rear artwork groups exactly once."""
    design = design_options or DesignOptions()
    front = render_authoritative_face_panel(
        preprocessed,
        dataset,
        street,
        require_production_approved=require_production_approved,
        street_stroke_scale=design.front_feature_weight,
        design_options=design,
    )
    options = build_render_options(design, area=dataset.display_name)
    feature_colour = None
    if front.source.is_svg:
        try:
            feature_colour = extract_street_feature_colour(
                front.source.path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError):
            feature_colour = None
    if feature_colour is None and not front.source.is_svg:
        try:
            feature_colour = street_feature_colour(street)
        except Exception:
            feature_colour = None
    context_options = options.context_options
    if context_options is not None and feature_colour is not None:
        context_options = replace(
            context_options,
            highlight_stroke_colour=feature_colour,
        )
    rear = render_context_map_result(dataset, street, context_options)
    return front.image, rear.image


def render_preprocessed_wrap(
    preprocessed: Path | str,
    dataset: Dataset,
    street: StreetRecord,
    *,
    design_options: DesignOptions | None = None,
    require_production_approved: bool = True,
) -> Image.Image:
    """Compose the legacy canonical preview without affecting V3 supplier export."""
    front, rear = render_preprocessed_artwork_groups(
        preprocessed,
        dataset,
        street,
        design_options=design_options,
        require_production_approved=require_production_approved,
    )
    image, _, _ = WrapComposer().compose(front, rear)
    return image


def export_preprocessed_provider_png(
    preprocessed: Path | str,
    dataset: Dataset,
    street: StreetRecord,
    destination: Path | str,
    *,
    profile_id: str,
    design_options: DesignOptions | None = None,
    debug_destination: Path | str | None = None,
) -> Path:
    """Export one prepared face through the existing provider system."""
    root = Path(preprocessed).resolve()

    def checkpoint():
        try:
            resolution, source = _resolve_front_source(root, dataset, street)
        except (OSError, ValueError) as error:
            if isinstance(error, AuthoritativeArtworkError):
                raise
            raise AuthoritativeArtworkError(f"Prepared face asset integrity problem: {error}") from error
        if resolution.state is ProductionTriageStatus.UNRENDERABLE_INPUT:
            raise AuthoritativeArtworkError("no front artwork available; export forbidden.")
        if not resolution.production_approved:
            raise AuthoritativeArtworkError("Artwork is not production approved.")
        if source is None:
            raise AuthoritativeArtworkError("Prepared front artwork missing; asset integrity problem.")
        review = current_review_state(root, dataset.id, street.id, source.review_path)
        if review.export_blocked:
            raise AuthoritativeArtworkError(f"Production export blocked: {review.label}")
        return (
            resolution.state.value,
            resolution.production_approved,
            source.kind,
            str(source.path),
            source.digest,
            review,
        )

    before = checkpoint()
    profile = get_provider_profile(profile_id)
    front_artwork, rear_artwork = render_preprocessed_artwork_groups(
        root, dataset, street, design_options=design_options,
    )
    destination = Path(destination)
    debug_path = Path(debug_destination) if debug_destination is not None else None
    if debug_path is not None and debug_path.resolve() == destination.resolve():
        raise AuthoritativeArtworkError("Debug output must use a different path from the production PNG.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".single-export-") as staging:
        temporary = Path(staging) / destination.name
        temporary_debug = Path(staging) / ("debug_" + destination.name) if debug_path is not None else None
        save_provider_artwork(
            front_artwork,
            rear_artwork,
            profile,
            temporary,
            debug_destination=temporary_debug,
            png_metadata={
                PRODUCTION_FRONT_WEIGHT_KEY: f"{(design_options or DesignOptions()).front_feature_weight:.2f}",
                PRODUCTION_REAR_WEIGHT_KEY: f"{(design_options or DesignOptions()).rear_highlight_weight:.2f}",
                PRODUCTION_STYLE_FINGERPRINT_KEY: design_profile_fingerprint(design_options),
            },
        )
        if checkpoint() != before:
            raise AuthoritativeArtworkError(
                "Artwork or QA changed during export; rebuild the batch plan or retry after review."
            )
        os.replace(temporary, destination)
        if debug_path is not None:
            assert temporary_debug is not None
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary_debug, debug_path)
    return destination



def export_preprocessed_provider_set(
    preprocessed: Path | str,
    dataset: Dataset,
    street: StreetRecord,
    destination_directory: Path | str,
    *,
    design_name: str | None = None,
    profile_ids: tuple[str, ...] = DEFAULT_PROFILE_IDS,
    design_options: DesignOptions | None = None,
    debug: bool = False,
) -> dict[str, tuple[Path, Path | None]]:
    """Render once and write multiple supplier-specific V3 PNGs atomically."""
    root = Path(preprocessed).resolve()

    def checkpoint():
        resolution, source = _resolve_front_source(root, dataset, street)
        if resolution.state is ProductionTriageStatus.UNRENDERABLE_INPUT:
            raise AuthoritativeArtworkError("no front artwork available; export forbidden.")
        if not resolution.production_approved or source is None:
            raise AuthoritativeArtworkError("Artwork is not production approved.")
        review = current_review_state(root, dataset.id, street.id, source.review_path)
        if review.export_blocked:
            raise AuthoritativeArtworkError(f"Production export blocked: {review.label}")
        return (
            resolution.state.value,
            resolution.production_approved,
            source.kind,
            str(source.path),
            source.digest,
            review,
        )

    before = checkpoint()
    front_artwork, rear_artwork = render_preprocessed_artwork_groups(
        root, dataset, street, design_options=design_options,
    )
    destination = Path(destination_directory)
    destination.mkdir(parents=True, exist_ok=True)
    label = design_name or street.display_name or street.id

    published: dict[str, tuple[Path, Path | None]] = {}
    with tempfile.TemporaryDirectory(dir=destination, prefix=".profile-set-") as staging:
        staged = save_profile_set(
            front_artwork,
            rear_artwork,
            Path(staging),
            label,
            profile_ids=profile_ids,
            debug=debug,
        )
        if checkpoint() != before:
            raise AuthoritativeArtworkError(
                "Artwork or QA changed during export; rebuild the batch plan or retry after review."
            )
        for profile_id, (production, diagnostic) in staged.items():
            final_production = destination / production.name
            os.replace(production, final_production)
            final_debug = None
            if diagnostic is not None:
                final_debug = destination / diagnostic.name
                os.replace(diagnostic, final_debug)
            published[profile_id] = (final_production, final_debug)
    return published
