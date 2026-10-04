"""Safety rails for calibrated rear attribution positioning.

The calibration lab intentionally exposes independent vertical offsets for the
two attribution lines.  Older saved profiles can therefore contain offsets
that place one or both baselines outside the rear panel.  Keep those controls
useful while guaranteeing that required OpenStreetMap attribution remains
visible in previews and exports.
"""
from __future__ import annotations

from functools import wraps
import math
from typing import Any

from .rear_source_frame import is_authoritative_local_osm_context, source_map_layout


ATTRIBUTION_EDGE_MARGIN_PX = 3.0


def _clamped_offset(
    context: Any,
    *,
    panel_width: int,
    panel_height: int,
    base_y: float,
    requested_offset: float,
    font_size: float,
) -> float:
    """Return a reference-pixel offset whose text baseline remains visible."""
    scale = context._panel_reference_scale(panel_width)
    requested_y = base_y + requested_offset * scale
    margin = ATTRIBUTION_EDGE_MARGIN_PX * scale

    # SVG text uses a baseline.  Leave approximately one font height above it
    # and a small descender margin below it so the glyphs cannot disappear when
    # a calibration profile pushes the line towards either panel edge.
    minimum_y = max(font_size + margin, margin)
    maximum_y = panel_height - margin
    safe_y = min(max(requested_y, minimum_y), maximum_y)
    return (safe_y - base_y) / scale


def install(context: Any) -> None:
    """Install rear-attribution clipping protection once."""
    if getattr(context, "_rear_attribution_guardrails_installed", False):
        return

    original = context._rasterise_rear_panel

    @wraps(original)
    def guarded(
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
    ):
        values = (
            attribution_font_scale,
            attribution_line1_font_scale,
            attribution_line2_font_scale,
            attribution_line_spacing_scale,
            attribution_line1_y_offset,
            attribution_line2_y_offset,
        )
        if all(math.isfinite(float(value)) for value in values):
            scale = context._panel_reference_scale(panel_width)
            if is_authoritative_local_osm_context(markup):
                _map_x, _map_y, _map_width, _map_height, attribution_y = source_map_layout(
                    context, markup, panel_width, panel_height
                )
            else:
                _map_x, _map_y, _map_width, _map_height, attribution_y = context._rear_panel_layout(
                    panel_width, panel_height
                )
            line_height = (
                context.ATTRIBUTION_LINE_HEIGHT
                * scale
                * attribution_line_spacing_scale
            )
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
            attribution_line1_y_offset = _clamped_offset(
                context,
                panel_width=panel_width,
                panel_height=panel_height,
                base_y=attribution_y,
                requested_offset=attribution_line1_y_offset,
                font_size=line1_font_size,
            )
            attribution_line2_y_offset = _clamped_offset(
                context,
                panel_width=panel_width,
                panel_height=panel_height,
                base_y=attribution_y + line_height,
                requested_offset=attribution_line2_y_offset,
                font_size=line2_font_size,
            )

        return original(
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

    context._rasterise_rear_panel = guarded
    context._rear_attribution_guardrails_installed = True
