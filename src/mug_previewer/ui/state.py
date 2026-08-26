"""Testable UI state and rendering orchestration, independent of Tk widgets."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image

from ..config import load_settings
from ..design import DesignOptions, build_render_options
from ..datasets.discovery import DatasetCandidate, discover_datasets
from ..datasets.models import Dataset, StreetRecord
from ..exporting import save_provider_export
from ..preview.mockup import MugPreviewOptions, PreviewOrientation, render_mug_preview
from ..providers import ProviderProfile, get_provider_profile
from ..rendering.artwork import WrapRenderOptions, WrapRenderResult, render_wrap, render_wrap_result

DEVELOPMENT_DATASET_ROOT = Path(r"E:\Python_Stuff\OS_Mail_Addresses\workflow_outputs_v6")
PREVIEW_SIZE = (1024, 1536)
INKTHREADABLE_PROFILE_ID = 'inkthreadable_11oz_white'
PRINTIFY_PROFILE_ID = 'printify_generic_11oz_ceramic'
# Export helpers use the production provider profile.


class UIDataError(ValueError):
    """A concise, user-presentable failure while preparing UI data."""


@dataclass(frozen=True)
class DatasetOption:
    """A selector entry which retains the loaded domain dataset."""
    label: str
    dataset: Dataset


@dataclass(frozen=True)
class PreviewPair:
    """One canonical wrap and both production mockup viewpoints."""
    wrap: Image.Image
    front: Image.Image
    rear: Image.Image
    framing_mode: str


@dataclass
class AppState:
    """State owned by the UI controller rather than individual widgets."""
    dataset_root: Path | None = None
    datasets: list[DatasetOption] = field(default_factory=list)
    selected_dataset: Dataset | None = None
    street_filter: str = ""
    filtered_streets: list[StreetRecord] = field(default_factory=list)
    selected_street: StreetRecord | None = None
    current_wrap: Image.Image | None = None
    current_front_preview: Image.Image | None = None
    current_rear_preview: Image.Image | None = None
    render_status: str = "Ready"
    error: str | None = None
    framing_mode: str | None = None
    design_options: DesignOptions = field(default_factory=DesignOptions)

    def set_design_options(self, front_feature_weight: float, rear_highlight_weight: float) -> None:
        """Validate and retain the current style selections for this app session."""
        self.design_options = DesignOptions(front_feature_weight, rear_highlight_weight)

    def reset_design_options(self) -> None:
        """Restore only user-adjustable design values, preserving selection state."""
        self.design_options = DesignOptions()


class WrapRenderer(Protocol):
    def __call__(
        self, dataset: Dataset, street: StreetRecord, options: WrapRenderOptions | None = None,
    ) -> Image.Image | WrapRenderResult: ...


class PreviewRenderer(Protocol):
    def __call__(self, wrap: Image.Image, options: MugPreviewOptions) -> Image.Image: ...


class ProviderExporter(Protocol):
    def __call__(self, wrap: Image.Image, profile: ProviderProfile, destination: Path) -> Path: ...


def resolve_dataset_root(dataset_root: Path | str | None = None) -> Path:
    """Resolve UI root with CLI/environment/config precedence then dev fallback."""
    settings = load_settings(dataset_root=dataset_root)
    return settings.dataset_root or DEVELOPMENT_DATASET_ROOT


def dataset_options(
    root: Path | str,
    *,
    discover: Callable[[Path | str], list[DatasetCandidate]] = discover_datasets,
) -> list[DatasetOption]:
    """Discover datasets as stable, readable choices without exposing paths."""
    root_path = Path(root)
    if not root_path.is_dir():
        raise UIDataError(f"Dataset root does not exist: {root_path}")
    candidates = discover(root_path)
    if not candidates:
        raise UIDataError(f"No usable workflow-v6 datasets were found in: {root_path}")
    labels = [candidate.dataset.display_name for candidate in candidates]
    duplicates = {
        label.casefold()
        for label in labels
        if sum(item.casefold() == label.casefold() for item in labels) > 1
    }
    return [
        DatasetOption(
            label=(
                f"{candidate.dataset.display_name} ({candidate.dataset.id})"
                if candidate.dataset.display_name.casefold() in duplicates
                else candidate.dataset.display_name
            ),
            dataset=candidate.dataset,
        )
        for candidate in candidates
    ]


def filter_streets(streets: Iterable[StreetRecord], query: str) -> list[StreetRecord]:
    """Case-insensitive substring filter over user-visible street fields."""
    needle = query.casefold().strip()
    items = list(streets)
    if not needle:
        return items
    return [
        street
        for street in items
        if needle in street.id.casefold()
        or needle in street.display_name.casefold()
        or needle in street.street_name.casefold()
    ]


def display_image(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Crop the presentation copy around its subject, then fit without distortion."""
    target_width, target_height = target_size
    if target_width <= 0 or target_height <= 0:
        raise ValueError("Preview display dimensions must be positive.")
    source = image.convert("RGBA")
    left, top, right, bottom = _content_bounds(source)
    cropped = source.crop((left, top, right, bottom))
    scale = min(target_width / cropped.width, target_height / cropped.height)
    return cropped.resize(
        (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale))),
        Image.Resampling.LANCZOS,
    )


def _content_bounds(image: Image.Image) -> tuple[int, int, int, int]:
    """Find content differing from corner background, retaining a display margin."""
    rgba = image.load()
    points = ((0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1))
    background = tuple(
        sorted(rgba[x, y][channel] for x, y in points)[len(points) // 2]
        for channel in range(3)
    )
    mask = Image.new("L", image.size, 0)
    mask.putdata([
        255 if pixel[3] < 245 or max(abs(pixel[channel] - background[channel]) for channel in range(3)) > 12 else 0
        for pixel in image.getdata()
    ])
    bounds = mask.getbbox()
    if bounds is None:
        return (0, 0, image.width, image.height)
    left, top, right, bottom = bounds
    padding = max(12, round(max(right - left, bottom - top) * 0.06))
    return (
        max(0, left - padding), max(0, top - padding),
        min(image.width, right + padding), min(image.height, bottom + padding),
    )


def render_preview_pair(
    dataset: Dataset,
    street: StreetRecord,
    *,
    design_options: DesignOptions | None = None,
    wrap_renderer: WrapRenderer = render_wrap_result,
    preview_renderer: PreviewRenderer = render_mug_preview,
) -> PreviewPair:
    """Render one canonical wrap, then derive both fixed production mockups."""
    design = design_options or DesignOptions()
    rendered = wrap_renderer(
        dataset,
        street,
        build_render_options(design, area=dataset.display_name),
    )
    if isinstance(rendered, WrapRenderResult):
        wrap, framing_mode = rendered.image, rendered.context.framing_mode
    else:
        wrap = rendered
        framing_mode = "metric" if dataset.capabilities.metric_context_framing else "legacy"
    front = preview_renderer(wrap, MugPreviewOptions(orientation=PreviewOrientation.FRONT_HANDLE_RIGHT))
    rear = preview_renderer(wrap, MugPreviewOptions(orientation=PreviewOrientation.REAR_HANDLE_LEFT))
    for name, preview in (("Front", front), ("Rear", rear)):
        if preview.size != PREVIEW_SIZE or preview.mode != "RGBA":
            raise UIDataError(
                f"{name} preview renderer returned {preview.mode} {preview.size}; expected RGBA {PREVIEW_SIZE}."
            )
    return PreviewPair(wrap=wrap, front=front, rear=rear, framing_mode=framing_mode)


def export_provider_png(
    dataset: Dataset,
    street: StreetRecord,
    destination: Path | str,
    *,
    profile_id: str,
    design_options: DesignOptions | None = None,
    wrap_renderer: WrapRenderer = render_wrap,
    profile_lookup: Callable[[str], ProviderProfile] = get_provider_profile,
    exporter: ProviderExporter = save_provider_export,
) -> Path:
    """Freshly render current UI state and save it through one provider profile."""
    design = design_options or DesignOptions()
    wrap = wrap_renderer(dataset, street, build_render_options(design, area=dataset.display_name))
    if isinstance(wrap, WrapRenderResult):
        wrap = wrap.image
    return exporter(wrap, profile_lookup(profile_id), Path(destination))


def export_inkthreadable_png(
    dataset: Dataset,
    street: StreetRecord,
    destination: Path | str,
    *,
    design_options: DesignOptions | None = None,
    wrap_renderer: WrapRenderer = render_wrap,
    profile_lookup: Callable[[str], ProviderProfile] = get_provider_profile,
    exporter: ProviderExporter = save_provider_export,
) -> Path:
    """Freshly render current UI state and save it through Inkthreadable."""
    return export_provider_png(
        dataset,
        street,
        destination,
        profile_id=INKTHREADABLE_PROFILE_ID,
        design_options=design_options,
        wrap_renderer=wrap_renderer,
        profile_lookup=profile_lookup,
        exporter=exporter,
    )


def export_printify_png(
    dataset: Dataset,
    street: StreetRecord,
    destination: Path | str,
    *,
    design_options: DesignOptions | None = None,
    wrap_renderer: WrapRenderer = render_wrap,
    profile_lookup: Callable[[str], ProviderProfile] = get_provider_profile,
    exporter: ProviderExporter = save_provider_export,
) -> Path:
    """Freshly render current UI state and save it through generic Printify."""
    return export_provider_png(
        dataset,
        street,
        destination,
        profile_id=PRINTIFY_PROFILE_ID,
        design_options=design_options,
        wrap_renderer=wrap_renderer,
        profile_lookup=profile_lookup,
        exporter=exporter,
    )
