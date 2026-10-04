from __future__ import annotations

import json

import pytest

from mug_previewer.rendering import context_map
from mug_previewer.rendering.rear_source_frame import (
    SOURCE_MAP_MAX_WIDTH_FRACTION,
    is_authoritative_local_osm_context,
    source_map_layout,
)


def authoritative_svg() -> str:
    metadata = {
        "street_name": "Stoke Newington Church Street",
        "svg_type": "context",
        "context_extent": "individual_street_bounds_with_padding_independent_export_size",
        "background_source": "local_osm_pbf",
        "native_size_px": {"width": 1024, "height": 444},
        "render_canvas_size_px": {"width": 4096, "height": 1776},
        "highlight_color": "#E83E8C",
    }
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="444" '
        'viewBox="0 0 800 347" preserveAspectRatio="xMidYMid meet">'
        f'<metadata id="street-svg-metadata">{json.dumps(metadata)}</metadata>'
        '<rect width="800" height="347" fill="#f5f2eb"/>'
        '<polyline points="10,170 790,170" fill="none" stroke="#ffffff" stroke-width="8"/>'
        '<polyline points="10,170 790,170" fill="none" stroke="#E83E8C" stroke-width="4"/>'
        '</svg>'
    )


def test_verified_local_osm_context_is_recognised() -> None:
    assert is_authoritative_local_osm_context(authoritative_svg())
    assert not is_authoritative_local_osm_context(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 347"/>'
    )


def test_authoritative_frame_is_not_recropped_by_metric_or_margin_paths() -> None:
    markup = authoritative_svg()
    cropped, diagnostics = context_map._metric_crop_markup(None, None, markup, None, None)
    assert cropped == markup
    assert diagnostics["framing_mode"] == "source-authoritative"

    # The highlighted line deliberately reaches near both horizontal edges.
    # Verified upstream framing is authoritative, so the legacy 2:3 safety
    # recrop must not replace the source viewBox.
    adjusted = context_map._ensure_highlight_margin(
        markup,
        context_map._svg_view_box(markup),
        0.10,
    )
    assert context_map._svg_view_box(adjusted) == pytest.approx((0.0, 0.0, 800.0, 347.0))


def test_source_aspect_uses_much_more_rear_panel_width_than_legacy_crop() -> None:
    markup = authoritative_svg()
    panel_width, panel_height = 945, 882
    _x, _y, width, height, attribution_y = source_map_layout(
        context_map, markup, panel_width, panel_height
    )
    _lx, _ly, legacy_width, _legacy_height, _la = context_map._rear_panel_layout(
        panel_width, panel_height
    )

    assert width == pytest.approx(panel_width * SOURCE_MAP_MAX_WIDTH_FRACTION)
    assert width / height == pytest.approx(1024 / 444, rel=1e-3)
    assert width > legacy_width * 1.5
    assert attribution_y > height


def test_authoritative_context_rasterises_at_panel_size_with_attribution() -> None:
    image = context_map._rasterise_rear_panel(
        authoritative_svg(),
        945,
        882,
        attribution_line1_font_scale=1.2,
        attribution_line2_font_scale=1.2,
    )
    assert image.size == (945, 882)
    assert image.mode == "RGBA"
    assert image.getchannel("A").getbbox() is not None
