"""Preserve verified local-OSM context SVG framing through mug rendering.

New context SVGs already contain an intentionally fitted street frame, a high
resolution embedded raster and vector highlight geometry.  Re-cropping those
assets to the historical 2:3 rear-map window throws away useful context and
reduces the physical pixel area available to labels.  This adapter recognises
those verified SVGs and keeps their source viewBox/aspect authoritative while
leaving legacy/workflow-v6 contexts on the existing framing path.
"""
from __future__ import annotations

import io
import json
import math
import xml.etree.ElementTree as ET
from functools import wraps
from typing import Any

import cairosvg
from PIL import Image

AUTHORITATIVE_CONTEXT_EXTENT = "individual_street_bounds_with_padding_independent_export_size"
AUTHORITATIVE_BACKGROUND_SOURCE = "local_osm_pbf"
SOURCE_MAP_MAX_WIDTH_FRACTION = 0.90
SOURCE_MAP_SUPERSAMPLE = 4.0


def _metadata(markup: str) -> dict[str, object] | None:
    try:
        root = ET.fromstring(markup)
    except ET.ParseError:
        return None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "metadata":
            continue
        text = (element.text or "").strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("svg_type") == "context"
            and payload.get("context_extent") == AUTHORITATIVE_CONTEXT_EXTENT
            and payload.get("background_source") == AUTHORITATIVE_BACKGROUND_SOURCE
            and isinstance(payload.get("native_size_px"), dict)
            and isinstance(payload.get("render_canvas_size_px"), dict)
        ):
            return payload
    return None


def is_authoritative_local_osm_context(markup: str) -> bool:
    """Return true for the verified individually-fitted local OSM context format."""
    return _metadata(markup) is not None


def _source_aspect(markup: str) -> float:
    metadata = _metadata(markup)
    if metadata is not None:
        native = metadata.get("native_size_px")
        if isinstance(native, dict):
            try:
                width = float(native["width"])
                height = float(native["height"])
            except (KeyError, TypeError, ValueError):
                pass
            else:
                if math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0:
                    return width / height
    try:
        root = ET.fromstring(markup)
        values = [float(value) for value in root.get("viewBox", "").replace(",", " ").split()]
    except (ET.ParseError, ValueError):
        values = []
    if len(values) == 4 and values[2] > 0 and values[3] > 0:
        return values[2] / values[3]
    return 1.0


def source_map_layout(
    context: Any,
    markup: str,
    panel_width: int,
    panel_height: int,
) -> tuple[float, float, float, float, float]:
    """Fit the authoritative source aspect into the rear panel without cropping."""
    scale = context._panel_reference_scale(panel_width)
    aspect = _source_aspect(markup)
    maximum_height = panel_height * context.REAR_MAP_HEIGHT_RATIO
    maximum_width = panel_width * SOURCE_MAP_MAX_WIDTH_FRACTION
    map_width = min(maximum_width, maximum_height * aspect)
    map_height = map_width / aspect
    attribution_gap = context.ATTRIBUTION_MAP_GAP * scale
    attribution_line_height = context.ATTRIBUTION_LINE_HEIGHT * scale
    composition_offset_y = (
        context.REAR_COMPOSITION_CANONICAL_OFFSET_Y_PX
        * panel_width
        / context.CANONICAL_REAR_PANEL_WIDTH_PX
    )
    content_height = (
        map_height
        + attribution_gap
        + attribution_line_height * len(context.ATTRIBUTION_LINES)
    )
    if content_height > panel_height:
        # This is only expected for very tall source frames.  Reduce uniformly;
        # never distort the authoritative source aspect.
        available_map_height = max(
            1.0,
            panel_height
            - attribution_gap
            - attribution_line_height * len(context.ATTRIBUTION_LINES),
        )
        map_height = min(map_height, available_map_height)
        map_width = map_height * aspect
        content_height = (
            map_height
            + attribution_gap
            + attribution_line_height * len(context.ATTRIBUTION_LINES)
        )
    map_y = (panel_height - content_height) / 2 + composition_offset_y
    map_x = (panel_width - map_width) / 2
    return map_x, map_y, map_width, map_height, map_y + map_height + attribution_gap


def _rasterise_authoritative(
    context: Any,
    markup: str,
    panel_width: int,
    panel_height: int,
    *,
    attribution_font_scale: float,
    attribution_line1_font_scale: float,
    attribution_line2_font_scale: float,
    attribution_line_spacing_scale: float,
    attribution_line1_y_offset: float,
    attribution_line2_y_offset: float,
) -> Image.Image:
    values = (
        attribution_font_scale,
        attribution_line1_font_scale,
        attribution_line2_font_scale,
        attribution_line_spacing_scale,
    )
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise context.ContextRenderError("Rear attribution scales must be positive and finite.")
    if not all(math.isfinite(value) for value in (attribution_line1_y_offset, attribution_line2_y_offset)):
        raise context.ContextRenderError("Rear attribution Y offsets must be finite.")

    map_x, map_y, map_width, map_height, attribution_y = source_map_layout(
        context, markup, panel_width, panel_height
    )
    scale = context._panel_reference_scale(panel_width)
    line1_font_size = (
        context.ATTRIBUTION_FONT_SIZE
        * scale
        * attribution_font_scale
        * attribution_line1_font_scale
    )
    line2_font_size = (
        context.ATTRIBUTION_FONT_SIZE
        * scale
        * attribution_font_scale
        * attribution_line2_font_scale
    )
    line_height = (
        context.ATTRIBUTION_LINE_HEIGHT
        * scale
        * attribution_line_spacing_scale
    )
    line1_y = attribution_y + attribution_line1_y_offset * scale
    line2_y = attribution_y + line_height + attribution_line2_y_offset * scale

    # Render close to the embedded 4x source density, then perform one controlled
    # Lanczos reduction to the exact print-panel map size.  This keeps labels and
    # road edges substantially cleaner than the old crop -> small panel -> enlarge
    # chain while retaining the vector street highlight during CairoSVG rendering.
    map_png = cairosvg.svg2png(
        bytestring=markup.encode("utf-8"),
        output_width=max(1, round(map_width * SOURCE_MAP_SUPERSAMPLE)),
        output_height=max(1, round(map_height * SOURCE_MAP_SUPERSAMPLE)),
    )
    with Image.open(io.BytesIO(map_png)) as rendered:
        map_image = rendered.convert("RGBA").copy()
    target_size = (max(1, round(map_width)), max(1, round(map_height)))
    if map_image.size != target_size:
        map_image = map_image.resize(target_size, Image.Resampling.LANCZOS)

    attribution_svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{panel_width}" height="{panel_height}" '
        f'viewBox="0 0 {panel_width} {panel_height}">\n'
        f'<style>'
        f'.attribution-line-1 {{ font:400 {line1_font_size:.2f}px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill:#5c5750; text-anchor:middle; }}'
        f'.attribution-line-2 {{ font:400 {line2_font_size:.2f}px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill:#5c5750; text-anchor:middle; }}'
        f'</style>\n'
        f'<text class="attribution-line-1" x="{panel_width / 2:.1f}" y="{line1_y:.2f}">{context.ATTRIBUTION_LINES[0]}</text>\n'
        f'<text class="attribution-line-2" x="{panel_width / 2:.1f}" y="{line2_y:.2f}">{context.ATTRIBUTION_LINES[1]}</text>\n'
        '</svg>'
    )
    attribution_png = cairosvg.svg2png(
        bytestring=attribution_svg.encode("utf-8"),
        output_width=panel_width,
        output_height=panel_height,
    )
    with Image.open(io.BytesIO(attribution_png)) as rendered:
        attribution = rendered.convert("RGBA").copy()

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    panel.alpha_composite(map_image, (round(map_x), round(map_y)))
    panel.alpha_composite(attribution)
    return panel


def install(context: Any) -> None:
    """Install source-frame preservation once on :mod:`context_map`."""
    if getattr(context, "_rear_source_frame_installed", False):
        return

    original_metric_crop = context._metric_crop_markup
    original_legacy_crop = context._legacy_crop_markup
    original_margin = context._ensure_highlight_margin
    original_rasterise = context._rasterise_rear_panel

    @wraps(original_metric_crop)
    def metric_crop(dataset, street, markup, source_bounds, policy):
        if is_authoritative_local_osm_context(markup):
            return markup, {
                "framing_mode": "source-authoritative",
                "dataset_context_width_m": None,
                "street_context_width_m": None,
                "final_context_width_m": None,
                "centre_x_m": None,
                "centre_y_m": None,
                "metric_metadata_available": True,
            }
        return original_metric_crop(dataset, street, markup, source_bounds, policy)

    @wraps(original_legacy_crop)
    def legacy_crop(dataset, street, markup, options):
        if is_authoritative_local_osm_context(markup):
            source_view_box = context._svg_view_box(markup)
            raster_size, image_size = context._legacy_source_raster(
                markup, (source_view_box[2], source_view_box[3])
            )
            return markup, {
                "framing_mode": "source-authoritative",
                "dataset_context_width_m": None,
                "street_context_width_m": None,
                "final_context_width_m": None,
                "centre_x_m": None,
                "centre_y_m": None,
                "metric_metadata_available": True,
                "source_context_raster_size_px": raster_size,
                "source_crop_size_px": tuple(float(value) for value in raster_size),
                "effective_raster_magnification": None,
                "projected_highlight_width_px": None,
            }
        return original_legacy_crop(dataset, street, markup, options)

    @wraps(original_margin)
    def ensure_margin(markup, source_view_box, margin_fraction):
        if is_authoritative_local_osm_context(markup):
            return markup
        return original_margin(markup, source_view_box, margin_fraction)

    @wraps(original_rasterise)
    def rasterise(
        markup: str,
        panel_width: int,
        panel_height: int,
        *,
        attribution_font_scale: float = 1.0,
        attribution_line1_font_scale: float = 1.0,
        attribution_line2_font_scale: float = 1.0,
        attribution_line_spacing_scale: float = 1.0,
        attribution_line1_y_offset: float = 0.0,
        attribution_line2_y_offset: float = 0.0,
    ) -> Image.Image:
        if not is_authoritative_local_osm_context(markup):
            return original_rasterise(
                markup,
                panel_width,
                panel_height,
                attribution_font_scale=attribution_font_scale,
                attribution_line1_font_scale=attribution_line1_font_scale,
                attribution_line2_font_scale=attribution_line2_font_scale,
                attribution_line_spacing_scale=attribution_line_spacing_scale,
                attribution_line1_y_offset=attribution_line1_y_offset,
                attribution_line2_y_offset=attribution_line2_y_offset,
            )
        return _rasterise_authoritative(
            context,
            markup,
            panel_width,
            panel_height,
            attribution_font_scale=attribution_font_scale,
            attribution_line1_font_scale=attribution_line1_font_scale,
            attribution_line2_font_scale=attribution_line2_font_scale,
            attribution_line_spacing_scale=attribution_line_spacing_scale,
            attribution_line1_y_offset=attribution_line1_y_offset,
            attribution_line2_y_offset=attribution_line2_y_offset,
        )

    context._metric_crop_markup = metric_crop
    context._legacy_crop_markup = legacy_crop
    context._ensure_highlight_margin = ensure_margin
    context._rasterise_rear_panel = rasterise
    context._rear_source_frame_installed = True
