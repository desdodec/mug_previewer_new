"""Production composition from indexed prepared artwork, without live face generation."""
from dataclasses import dataclass
from pathlib import Path
import os
import tempfile

from PIL import Image

from .datasets.models import Dataset, StreetRecord
from .design import DesignOptions, build_render_options
from .diagnostics.front_candidates import ProductionTriageStatus
from .exporting import save_provider_export
from .prepared_asset import PreparedFaceSource, load_prepared_face_image, resolve_prepared_face_source
from .preprocess import FaceSvgResolution, INDEX_FILENAME, _load_index, resolve_authoritative_face_svg
from .providers import get_provider_profile
from .review_index import current_review_state
from .rendering.artwork import WrapComposer
from .rendering.context_map import render_context_map_result


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


def render_authoritative_face_panel(
    preprocessed: Path | str,
    dataset: Dataset,
    street: StreetRecord,
    *,
    require_production_approved: bool = False,
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
        panel = load_prepared_face_image(source)
    except Exception as error:
        raise AuthoritativeArtworkError(
            f"{label}: cannot rasterise prepared face source {source.path}: {error}"
        ) from error
    return AuthoritativeFacePanel(panel, resolution, source)


def render_preprocessed_wrap(
    preprocessed: Path | str,
    dataset: Dataset,
    street: StreetRecord,
    *,
    design_options: DesignOptions | None = None,
    require_production_approved: bool = True,
) -> Image.Image:
    """Compose prepared front artwork with the existing rear renderer."""
    front = render_authoritative_face_panel(
        preprocessed,
        dataset,
        street,
        require_production_approved=require_production_approved,
    )
    options = build_render_options(design_options or DesignOptions(), area=dataset.display_name)
    rear = render_context_map_result(dataset, street, options.context_options)
    image, _, _ = WrapComposer().compose(front.image, rear.image)
    return image


def export_preprocessed_provider_png(
    preprocessed: Path | str,
    dataset: Dataset,
    street: StreetRecord,
    destination: Path | str,
    *,
    profile_id: str,
    design_options: DesignOptions | None = None,
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
    wrap = render_preprocessed_wrap(root, dataset, street, design_options=design_options)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".single-export-") as staging:
        temporary = Path(staging) / destination.name
        save_provider_export(wrap, profile, temporary)
        if checkpoint() != before:
            raise AuthoritativeArtworkError(
                "Artwork or QA changed during export; rebuild the batch plan or retry after review."
            )
        os.replace(temporary, destination)
    return destination
