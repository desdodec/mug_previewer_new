"""Production composition from indexed artwork, without live face generation."""
from dataclasses import dataclass
from pathlib import Path
import os
import tempfile

from PIL import Image

from .datasets.models import Dataset, StreetRecord
from .design import DesignOptions, build_render_options
from .diagnostics.front_candidates import ProductionTriageStatus
from .exporting import save_provider_export
from .preprocess import FaceSvgResolution, resolve_authoritative_face_svg, validate_manual_svg
from .providers import get_provider_profile
from .review_index import current_review_state, svg_sha256
from .rendering.artwork import WrapComposer
from .rendering.context_map import render_context_map_result
from .rendering.svg_raster import rasterize_face_svg


class AuthoritativeArtworkError(ValueError):
    """Indexed artwork is unavailable, invalid, or not approved for production."""


@dataclass(frozen=True)
class AuthoritativeFacePanel:
    image: Image.Image
    resolution: FaceSvgResolution

    @property
    def production_approved(self) -> bool:
        return self.resolution.production_approved


def render_authoritative_face_panel(
    preprocessed: Path | str, dataset: Dataset, street: StreetRecord,
    *, require_production_approved: bool = False,
) -> AuthoritativeFacePanel:
    """Resolve once and rasterise that exact asset; review previews are allowed."""
    try:
        resolution = resolve_authoritative_face_svg(preprocessed, dataset, street)
    except (OSError, ValueError) as error:
        raise AuthoritativeArtworkError(f"Cannot read authoritative artwork index: {error}") from error
    label = f"{dataset.display_name} / {street.id} ({resolution.state.value})"
    if resolution.state is ProductionTriageStatus.UNRENDERABLE_INPUT:
        raise AuthoritativeArtworkError(f"{label}: no front artwork available; export forbidden.")
    if require_production_approved and not resolution.production_approved:
        raise AuthoritativeArtworkError(f"{label}: artwork is not production approved.")
    if resolution.path is None:
        raise AuthoritativeArtworkError(f"{label}: authoritative SVG is missing; asset integrity problem.")
    try:
        panel = rasterize_face_svg(resolution.path)
    except Exception as error:
        raise AuthoritativeArtworkError(
            f"{label}: cannot rasterise authoritative SVG {resolution.path}: {error}"
        ) from error
    return AuthoritativeFacePanel(panel, resolution)


def render_preprocessed_wrap(
    preprocessed: Path | str, dataset: Dataset, street: StreetRecord,
    *, design_options: DesignOptions | None = None,
    require_production_approved: bool = True,
) -> Image.Image:
    """Compose production-approved SVG artwork with the existing rear renderer."""
    front = render_authoritative_face_panel(
        preprocessed, dataset, street, require_production_approved=require_production_approved,
    )
    options = build_render_options(design_options or DesignOptions(), area=dataset.display_name)
    rear = render_context_map_result(dataset, street, options.context_options)
    image, _, _ = WrapComposer().compose(front.image, rear.image)
    return image


def export_preprocessed_provider_png(
    preprocessed: Path | str, dataset: Dataset, street: StreetRecord,
    destination: Path | str, *, profile_id: str,
    design_options: DesignOptions | None = None,
) -> Path:
    """Export one indexed approved artwork through the existing provider system."""
    def checkpoint():
        resolution = resolve_authoritative_face_svg(preprocessed, dataset, street)
        if resolution.state is ProductionTriageStatus.UNRENDERABLE_INPUT:
            raise AuthoritativeArtworkError('no front artwork available; export forbidden.')
        if not resolution.production_approved:
            raise AuthoritativeArtworkError('Artwork is not production approved.')
        if resolution.path is None:
            raise AuthoritativeArtworkError('Authoritative SVG missing; asset integrity problem.')
        try:
            resolution.path.resolve().relative_to(Path(preprocessed).resolve())
            validate_manual_svg(resolution.path.read_bytes())
            digest = svg_sha256(resolution.path)
        except Exception as error:
            raise AuthoritativeArtworkError(f'Authoritative SVG asset integrity problem: {error}') from error
        review = current_review_state(Path(preprocessed), dataset.id, street.id, resolution.path)
        if review.export_blocked:
            raise AuthoritativeArtworkError(f'Production export blocked: {review.label}')
        return resolution, digest, review

    before = checkpoint()
    profile = get_provider_profile(profile_id)
    wrap = render_preprocessed_wrap(preprocessed, dataset, street, design_options=design_options)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.single-export-') as staging:
        temporary = Path(staging) / destination.name
        save_provider_export(wrap, profile, temporary)
        if checkpoint() != before:
            raise AuthoritativeArtworkError('Artwork or QA changed during export; rebuild the batch plan or retry after review.')
        os.replace(temporary, destination)
    return destination
