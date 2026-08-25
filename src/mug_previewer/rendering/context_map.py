"""Dataset-aware rendering of the V28 rear context-map panel."""

from __future__ import annotations

import io
import json
import logging
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import cairosvg
from PIL import Image

from ..datasets.models import Dataset, MetricBounds, StreetRecord
from .native import face_policy as native

LOGGER = logging.getLogger(__name__)

# The rear half of V28's 990 x 462 fast preview.
REAR_PANEL_PX = (495, 462)
REAR_MAP_PHYSICAL_ASPECT = 2 / 3  # width / height
# Physical composition only: metric framing is calculated before this panel is
# rasterised. Keep the selected production presentation scale centralised here:
# it enlarges the map-and-attribution group without changing map geography.
REAR_PANEL_BASE_SCALE = 1.00
REAR_PANEL_SCALE = 1.20
REAR_MAP_BASE_HEIGHT_RATIO = 0.70
REAR_MAP_HEIGHT_RATIO = REAR_MAP_BASE_HEIGHT_RATIO * (REAR_PANEL_SCALE / REAR_PANEL_BASE_SCALE)
# A small type-only reduction leaves the required credit plainly legible while
# keeping it visually tertiary to the highlighted street and map context.
ATTRIBUTION_FONT_SIZE = 12.0
ATTRIBUTION_LINE_HEIGHT = 16.5
ATTRIBUTION_MAP_GAP = 11.0
ATTRIBUTION_LINES = ("Map data: OpenStreetMap", "openstreetmap.org/copyright")


class ContextRenderError(ValueError):
    """Raised when a rear context panel cannot be rendered."""


class RearMapMetadataError(ValueError):
    """Raised by the strict metadata parser for unusable metric metadata."""


@dataclass(frozen=True)
class ContextScalePolicy:
    """V28 rear-map policy. These values must remain in lockstep with upstream."""

    percentile: float = 90.0
    dataset_scale_multiplier: float = 1.75
    minimum_width_m: float = 1400.0
    maximum_width_m: float = 2400.0
    street_padding_multiplier: float = 1.25
    maximum_street_expansion: float = 1.35


DEFAULT_CONTEXT_SCALE_POLICY = ContextScalePolicy()


@dataclass(frozen=True)
class RearMapMetadata:
    street_bounds_m: MetricBounds | None
    source_bounds_m: MetricBounds


@dataclass(frozen=True)
class ContextRenderOptions:
    panel_size: tuple[int, int] = REAR_PANEL_PX
    policy: ContextScalePolicy = DEFAULT_CONTEXT_SCALE_POLICY


@dataclass(frozen=True)
class ContextRenderResult:
    image: Image.Image
    framing_mode: str
    dataset_context_width_m: float | None
    street_context_width_m: float | None
    final_context_width_m: float | None
    centre_x_m: float | None
    centre_y_m: float | None
    metric_metadata_available: bool


def calculate_context_width_m(
    dataset_p90_span_m: float,
    street_span_m: float,
    policy: ContextScalePolicy = DEFAULT_CONTEXT_SCALE_POLICY,
) -> float:
    """Return the policy-capped geographic context width for one street."""
    p90 = _positive_finite(dataset_p90_span_m, "Dataset P90 bbox span")
    span = _positive_finite(street_span_m, "Street bbox span")
    _validate_policy(policy)
    dataset_width = min(max(p90 * policy.dataset_scale_multiplier, policy.minimum_width_m), policy.maximum_width_m)
    street_required_width = span * policy.street_padding_multiplier
    return min(max(dataset_width, street_required_width), dataset_width * policy.maximum_street_expansion)


def parse_rear_map_metadata(markup: str) -> RearMapMetadata | None:
    """Read optional, strictly validated upstream metric-framing metadata."""
    try:
        root = ET.fromstring(markup)
    except ET.ParseError as error:
        raise RearMapMetadataError("Context SVG is not valid XML.") from error
    payload: object | None = None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "metadata" or not (element.text or "").strip():
            continue
        if element.get("id") == "rear-map-framing":
            try:
                payload = json.loads(element.text or "")
            except json.JSONDecodeError as error:
                raise RearMapMetadataError("rear-map-framing metadata is not valid JSON.") from error
            break
        try:
            candidate = json.loads(element.text or "")
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("rear_map_framing"), dict):
            payload = candidate["rear_map_framing"]
            break
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise RearMapMetadataError("rear-map-framing metadata must be a JSON object.")
    if "source_bounds_m" not in payload:
        raise RearMapMetadataError("rear-map-framing metadata is missing source_bounds_m.")
    source = _bounds_from_metadata(payload["source_bounds_m"], "source_bounds_m")
    if source.width_m <= 0 or source.height_m <= 0:
        raise RearMapMetadataError("rear-map-framing source_bounds_m must have positive width and height.")
    street = _bounds_from_metadata(payload["street_bounds_m"], "street_bounds_m") if "street_bounds_m" in payload else None
    return RearMapMetadata(street_bounds_m=street, source_bounds_m=source)


def render_context_map(dataset: Dataset, street: StreetRecord, options: ContextRenderOptions | None = None) -> Image.Image:
    """Return the V28-compatible RGBA rear panel for ``street``."""
    return render_context_map_result(dataset, street, options).image


def render_context_map_result(
    dataset: Dataset,
    street: StreetRecord,
    options: ContextRenderOptions | None = None,
) -> ContextRenderResult:
    """Render a rear panel and expose framing diagnostics for callers/tests."""
    options = options or ContextRenderOptions()
    panel_width, panel_height = options.panel_size
    if panel_width <= 0 or panel_height <= 0:
        raise ContextRenderError("Rear context panel dimensions must be positive.")
    markup = _read_context_svg(street)
    try:
        _svg_view_box(markup)
    except RearMapMetadataError as error:
        raise ContextRenderError(f"Context SVG has no valid viewBox: {street.context_path}") from error

    metadata: RearMapMetadata | None
    try:
        metadata = parse_rear_map_metadata(markup)
    except RearMapMetadataError as error:
        LOGGER.warning("Rear-map metric metadata for %s is invalid; using legacy SVG-space framing: %s", street.id, error)
        metadata = None
    diagnostics: dict[str, float | str | bool | None] = {
        "framing_mode": "legacy", "dataset_context_width_m": None, "street_context_width_m": None,
        "final_context_width_m": None, "centre_x_m": None, "centre_y_m": None,
        "metric_metadata_available": metadata is not None or street.context_source_bounds is not None,
    }
    typed_source_bounds = street.context_source_bounds
    if typed_source_bounds is not None:
        try:
            markup, diagnostics = _metric_crop_markup(dataset, street, markup, typed_source_bounds, options.policy)
        except ContextRenderError as error:
            LOGGER.warning("Typed rear-map metric framing for %s is unavailable; using legacy SVG-space framing: %s", street.id, error)
            markup = _legacy_crop_markup(markup)
    elif metadata is not None:
        try:
            markup, diagnostics = _metric_crop_markup(dataset, street, markup, metadata.source_bounds_m, options.policy)
        except ContextRenderError as error:
            LOGGER.warning("Rear-map metric framing for %s is unavailable; using legacy SVG-space framing: %s", street.id, error)
            markup = _legacy_crop_markup(markup)
    else:
        markup = _legacy_crop_markup(markup)
    try:
        image = _rasterise_rear_panel(markup, panel_width, panel_height)
    except (cairosvg.CairoSVGError, ET.ParseError, ValueError, OSError) as error:
        raise ContextRenderError(f"Could not rasterise context SVG for street {street.id}: {error}") from error
    LOGGER.info(
        "Rear context render: street=%s %s framing=%s dataset_width=%s street_span=%s final_width=%s",
        street.id, street.display_name, diagnostics["framing_mode"], diagnostics["dataset_context_width_m"],
        street.bbox_span_m, diagnostics["final_context_width_m"],
    )
    return ContextRenderResult(image=image, **diagnostics)  # type: ignore[arg-type]


def _read_context_svg(street: StreetRecord) -> str:
    path = street.context_path
    if path is None:
        raise ContextRenderError(f'Cannot render street {street.id} "{street.display_name}": no context SVG is resolved.')
    if not path.is_file():
        raise ContextRenderError(f'Cannot render street {street.id} "{street.display_name}": context SVG does not exist: {path}')
    if path.suffix.casefold() != ".svg":
        raise ContextRenderError(f'Cannot render street {street.id} "{street.display_name}": context asset is not an SVG file: {path}')
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ContextRenderError(f"Could not read context SVG: {path}") from error


def _refine_context_markup(markup: str, street: StreetRecord) -> str:
    """Apply the retained V28 highlighted-road treatment before rasterising."""
    colour = re.search(r'"highlight_color"\s*:\s*"(#[0-9A-Fa-f]{6})"', markup)
    legacy_colour = colour.group(1) if colour else None
    try:
        palette = native.get_face_palette(native.DEFAULT_PALETTE_KEY)
        specs = native.build_specs([street.glyph_path], palette=palette)
        native.apply_gallery_context_to_single_spec(specs, [street.glyph_path], palette, None)
        accent = specs[0].group_color
    except Exception as error:
        LOGGER.debug("Could not derive V28 context accent for %s: %s", street.id, error)
        return markup

    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        classes = tag.split('class="', 1)[1].split('"', 1)[0].split() if 'class="' in tag else []
        stroke = re.search(r'\bstroke="(#[0-9A-Fa-f]{6})"', tag)
        marked = "highlighted-street" in classes or (legacy_colour is not None and stroke is not None and stroke.group(1).casefold() == legacy_colour.casefold())
        if not marked:
            return tag
        if "highlighted-street" not in classes:
            tag = tag[:-2] + ' class="highlighted-street"/>' if tag.endswith('/>') else tag[:-1] + ' class="highlighted-street">'
        if stroke is not None:
            tag = re.sub(r'\bstroke="#[0-9A-Fa-f]{6}"', f'stroke="{accent}"', tag, count=1)
        width = re.search(r'\bstroke-width="([0-9.]+)"', tag)
        if width is not None:
            tag = re.sub(r'\bstroke-width="[0-9.]+"', f'stroke-width="{float(width.group(1)) * 1.20:.2f}"', tag, count=1)
        return tag

    refined = re.sub(r'<(?:[A-Za-z0-9_]+:)?(?:polyline|path)\b[^>]*>', replace, markup)
    return re.sub(r'(<image\b[^>]*\bopacity=")[0-9.]+', r'\g<1>0.58', refined, count=1)

def _metric_crop_markup(
    dataset: Dataset, street: StreetRecord, markup: str, source_bounds: MetricBounds, policy: ContextScalePolicy,
) -> tuple[str, dict[str, float | str | bool | None]]:
    street_bounds = _street_metric_bounds(street)
    p90 = _dataset_p90(dataset)
    dataset_width = min(max(p90 * policy.dataset_scale_multiplier, policy.minimum_width_m), policy.maximum_width_m)
    final_width = calculate_context_width_m(p90, street_bounds.span_m, policy)
    final_height = final_width / REAR_MAP_PHYSICAL_ASPECT
    centre_x, centre_y = street_bounds.centre
    desired = MetricBounds(centre_x - final_width / 2, centre_y - final_height / 2, centre_x + final_width / 2, centre_y + final_height / 2)
    if not source_bounds.contains(desired):
        raise ContextRenderError("Context SVG source bounds cannot contain the requested metric crop.")
    source_x, source_y, source_width, source_height = _svg_view_box(markup)
    view_width = desired.width_m * source_width / source_bounds.width_m
    view_height = desired.height_m * source_height / source_bounds.height_m
    view_x = source_x + (desired.min_x - source_bounds.min_x) * source_width / source_bounds.width_m
    # Projected metric Y grows upward while SVG Y grows downward.
    view_y = source_y + (source_bounds.max_y - desired.max_y) * source_height / source_bounds.height_m
    return _replace_view_box(markup, (view_x, view_y, view_width, view_height)), {
        "framing_mode": "metric", "dataset_context_width_m": dataset_width,
        "street_context_width_m": street_bounds.span_m * policy.street_padding_multiplier,
        "final_context_width_m": final_width, "centre_x_m": centre_x, "centre_y_m": centre_y,
        "metric_metadata_available": True,
    }


def _legacy_crop_markup(markup: str) -> str:
    """Legacy highlighted-polyline SVG-space crop for older datasets."""
    source_x, source_y, source_width, source_height = _svg_view_box(markup)
    bounds = _highlight_bounds(markup)
    if bounds is None:
        return markup
    left, top, right, bottom = bounds
    crop_width = max(right - left, source_width * 0.03) * 1.60
    crop_height = max(bottom - top, source_height * 0.03) * 1.15
    target_ratio = REAR_MAP_PHYSICAL_ASPECT
    if crop_width / crop_height < target_ratio:
        crop_width = crop_height * target_ratio
    else:
        crop_height = crop_width / target_ratio
    crop_width, crop_height = min(crop_width, source_width), min(crop_height, source_height)
    centre_x, centre_y = (left + right) / 2, (top + bottom) / 2
    crop_x = min(max(centre_x - crop_width / 2, source_x), source_x + source_width - crop_width)
    crop_y = min(max(centre_y - crop_height / 2, source_y), source_y + source_height - crop_height)
    return _replace_view_box(markup, (crop_x, crop_y, crop_width, crop_height))


def _rasterise_rear_panel(markup: str, panel_width: int, panel_height: int) -> Image.Image:
    map_x, map_y, map_width, map_height, attribution_y = _rear_panel_layout(panel_width, panel_height)
    # CairoSVG can omit vector overlays (including the highlighted street) when
    # an SVG containing a raster map is itself used as an SVG ``<image>``.
    # Rasterise the completed source SVG first, then place that bitmap in the
    # panel SVG. This keeps the measured panel geometry while preserving the
    # upstream map and its overlay stack.
    map_png = cairosvg.svg2png(
        bytestring=markup.encode("utf-8"),
        output_width=round(map_width * 3),
        output_height=round(map_height * 3),
    )
    with Image.open(io.BytesIO(map_png)) as rendered:
        map_image = rendered.convert("RGBA").copy()
    target_size = (round(map_width), round(map_height))
    if map_image.size != target_size:
        map_image = map_image.resize(target_size, Image.Resampling.LANCZOS)
    attribution_svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{panel_width}" height="{panel_height}" viewBox="0 0 {panel_width} {panel_height}">\n'
        f'  <style>.attribution {{ font:400 {ATTRIBUTION_FONT_SIZE:.1f}px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill:#5c5750; text-anchor:middle; }}</style>\n'
        f'  <text class="attribution" x="{panel_width / 2:.1f}" y="{attribution_y:.1f}"><tspan x="{panel_width / 2:.1f}">{ATTRIBUTION_LINES[0]}</tspan><tspan x="{panel_width / 2:.1f}" dy="{ATTRIBUTION_LINE_HEIGHT:.1f}">{ATTRIBUTION_LINES[1]}</tspan></text>\n'
        '</svg>'
    )
    attribution_png = cairosvg.svg2png(bytestring=attribution_svg.encode("utf-8"), output_width=panel_width, output_height=panel_height)
    with Image.open(io.BytesIO(attribution_png)) as rendered:
        attribution = rendered.convert("RGBA").copy()
    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    panel.alpha_composite(map_image, (round(map_x), round(map_y)))
    panel.alpha_composite(attribution)
    return panel


def _rear_panel_layout(panel_width: int, panel_height: int) -> tuple[float, float, float, float, float]:
    """Return centred map bounds and first attribution baseline in panel pixels."""
    map_height = panel_height * REAR_MAP_HEIGHT_RATIO
    map_width = map_height * REAR_MAP_PHYSICAL_ASPECT
    content_height = map_height + ATTRIBUTION_MAP_GAP + ATTRIBUTION_LINE_HEIGHT * len(ATTRIBUTION_LINES)
    if content_height > panel_height:
        raise ContextRenderError("Rear map and attribution do not fit inside the context panel.")
    map_y = (panel_height - content_height) / 2
    map_x = (panel_width - map_width) / 2
    return map_x, map_y, map_width, map_height, map_y + map_height + ATTRIBUTION_MAP_GAP

def _dataset_p90(dataset: Dataset) -> float:
    value = dataset.statistics.context_scale.get("bbox_span_p90_m")
    if value is None:
        raise ContextRenderError("Dataset does not provide bbox_span_p90_m required for metric context framing.")
    try:
        return _positive_finite(value, "Dataset P90 bbox span")
    except ValueError as error:
        raise ContextRenderError(str(error)) from error


def _street_metric_bounds(street: StreetRecord) -> MetricBounds:
    values = (street.bbox_min_x, street.bbox_min_y, street.bbox_max_x, street.bbox_max_y)
    if any(value is None for value in values):
        raise ContextRenderError("Street does not provide authoritative metric bounding coordinates.")
    try:
        bounds = MetricBounds(*(float(value) for value in values))
    except (TypeError, ValueError, RearMapMetadataError) as error:
        raise ContextRenderError("Street has invalid authoritative metric bounding coordinates.") from error
    if bounds.span_m <= 0:
        raise ContextRenderError("Street has a zero-size metric bounding box.")
    return bounds


def _bounds_from_metadata(value: object, name: str) -> MetricBounds:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise RearMapMetadataError(f"rear-map-framing {name} must contain four coordinates.")
    try:
        return MetricBounds(*(float(item) for item in value))
    except (TypeError, ValueError, RearMapMetadataError) as error:
        raise RearMapMetadataError(f"rear-map-framing {name} is invalid.") from error


def _svg_view_box(markup: str) -> tuple[float, float, float, float]:
    try:
        root = ET.fromstring(markup)
        values = tuple(float(value) for value in root.get("viewBox", "").replace(",", " ").split())
    except (ET.ParseError, ValueError) as error:
        raise RearMapMetadataError("Context SVG needs a valid positive viewBox.") from error
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0 or not all(math.isfinite(value) for value in values):
        raise RearMapMetadataError("Context SVG needs a valid positive viewBox.")
    return values  # type: ignore[return-value]


def _replace_view_box(markup: str, values: tuple[float, float, float, float]) -> str:
    value = " ".join(f"{item:.2f}" for item in values)
    return re.sub(r'(\bviewBox=")[^"]*(")', rf'\g<1>{value}\g<2>', markup, count=1)


def _highlight_bounds(markup: str) -> tuple[float, float, float, float] | None:
    try:
        root = ET.fromstring(markup)
    except ET.ParseError:
        return None
    legacy = re.search(r'"highlight_color"\s*:\s*"(#[0-9A-Fa-f]{6})"', markup)
    colour = legacy.group(1).casefold() if legacy else None
    points: list[tuple[float, float]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "polyline":
            continue
        if "highlighted-street" not in element.get("class", "").split() and element.get("stroke", "").casefold() != colour:
            continue
        numbers = [float(value) for value in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", element.get("points", ""))]
        points.extend(zip(numbers[0::2], numbers[1::2]))
    if not points:
        return None
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _positive_finite(value: object, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a positive finite metre value.") from error
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{label} must be a positive finite metre value.")
    return numeric


def _validate_policy(policy: ContextScalePolicy) -> None:
    values = (policy.percentile, policy.dataset_scale_multiplier, policy.minimum_width_m, policy.maximum_width_m, policy.street_padding_multiplier, policy.maximum_street_expansion)
    if not all(math.isfinite(value) for value in values) or policy.dataset_scale_multiplier <= 0 or policy.minimum_width_m <= 0 or policy.maximum_width_m < policy.minimum_width_m or policy.street_padding_multiplier <= 0 or policy.maximum_street_expansion < 1:
        raise ValueError("Context scale policy has invalid limits.")
