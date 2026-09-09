from __future__ import annotations

import json
import base64
from dataclasses import replace
from io import BytesIO
import shutil
from pathlib import Path

import pytest
from PIL import Image

from mug_previewer.cli import main
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.datasets.models import MetricBounds
from mug_previewer.datasets.workflow_v6 import metric_bounds_from_raster_crop
from mug_previewer.rendering.context_map import (
    ATTRIBUTION_FONT_SIZE,
    ATTRIBUTION_LINE_HEIGHT,
    ATTRIBUTION_LINES,
    ATTRIBUTION_MAP_GAP,
    ContextRenderError,
    ContextRenderOptions,
    REAR_MAP_BASE_HEIGHT_RATIO,
    REAR_MAP_HEIGHT_RATIO,
    REAR_PANEL_SCALE,
    REAR_PANEL_PX,
    REAR_COMPOSITION_OFFSET_Y_PX,
    REAR_STREET_HIGHLIGHT_SCALE,
    LEGACY_MAX_RASTER_MAGNIFICATION,
    _legacy_crop_markup,
    _scale_highlight_stroke,
    _rear_panel_layout,
    calculate_context_width_m,
    parse_rear_map_metadata,
    render_context_map_result,
)

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def dataset_copy(tmp_path: Path) -> Path:
    path = tmp_path / "workflow_v6_valid"
    shutil.copytree(FIXTURE, path)
    return path


@pytest.mark.parametrize(
    ("p90", "span", "expected"),
    [
        (1012, 500, 1771),
        (400, 500, 1400),
        (2000, 500, 2400),
        (1800 / 1.75, 1600, 2000),
        (1800 / 1.75, 5000, 2430),
    ],
)
def test_context_width_preserves_metric_policy(p90: float, span: float, expected: float) -> None:
    assert calculate_context_width_m(p90, span) == pytest.approx(expected)


def test_parse_rear_map_metadata() -> None:
    markup = '<svg viewBox="0 0 1 1"><metadata id="rear-map-framing">{"street_bounds_m":[1,2,3,4],"source_bounds_m":[0,0,10,20]}</metadata></svg>'
    metadata = parse_rear_map_metadata(markup)
    assert metadata is not None
    assert metadata.street_bounds_m == MetricBounds(1, 2, 3, 4)
    assert metadata.source_bounds_m == MetricBounds(0, 0, 10, 20)
    assert parse_rear_map_metadata('<svg viewBox="0 0 1 1"/>') is None


@pytest.mark.parametrize(
    "markup",
    [
        '<svg><metadata id="rear-map-framing">not json</metadata></svg>',
        '<svg><metadata id="rear-map-framing">{"source_bounds_m":[0,0,0,1]}</metadata></svg>',
        '<svg><metadata id="rear-map-framing">{"source_bounds_m":[0,0,1]}</metadata></svg>',
    ],
)
def test_invalid_rear_map_metadata_is_clear(markup: str) -> None:
    with pytest.raises(ValueError, match="rear-map-framing"):
        parse_rear_map_metadata(markup)


def test_metric_context_render_returns_rear_panel(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    result = render_context_map_result(data, data.get_street("0001"))
    assert result.image.size == REAR_PANEL_PX
    assert result.image.mode == "RGBA"
    assert result.framing_mode == "metric"
    assert result.metric_metadata_available
    assert result.final_context_width_m == pytest.approx(1400)


def test_rear_stroke_option_preserves_metric_framing_and_panel_dimensions(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")
    baseline = render_context_map_result(data, street, ContextRenderOptions(highlight_stroke_scale=1.00))
    refined = render_context_map_result(data, street)
    assert baseline.image.size == refined.image.size == REAR_PANEL_PX
    assert baseline.framing_mode == refined.framing_mode == "metric"
    assert baseline.dataset_context_width_m == refined.dataset_context_width_m
    assert baseline.street_context_width_m == refined.street_context_width_m
    assert baseline.final_context_width_m == refined.final_context_width_m
    assert (baseline.centre_x_m, baseline.centre_y_m) == (refined.centre_x_m, refined.centre_y_m)


def test_enlarged_rear_map_and_attribution_remain_inside_panel() -> None:
    panel_width, panel_height = REAR_PANEL_PX
    map_x, map_y, map_width, map_height, attribution_y = _rear_panel_layout(panel_width, panel_height)

    assert REAR_PANEL_SCALE == pytest.approx(1.20)
    assert REAR_MAP_HEIGHT_RATIO == pytest.approx(REAR_MAP_BASE_HEIGHT_RATIO * REAR_PANEL_SCALE)
    assert map_x == pytest.approx((panel_width - map_width) / 2)
    assert map_x >= 0 and map_y >= 0
    assert map_x + map_width <= panel_width
    assert attribution_y - (map_y + map_height) == pytest.approx(17.0)
    # attribution_y is the first baseline, not the top of the text block.
    assert attribution_y + ATTRIBUTION_LINE_HEIGHT * (len(ATTRIBUTION_LINES) - 1) + ATTRIBUTION_FONT_SIZE <= panel_height
    assert ATTRIBUTION_FONT_SIZE == pytest.approx(9.6)
    assert ATTRIBUTION_FONT_SIZE == pytest.approx(12.0 * 0.80)
    assert ATTRIBUTION_MAP_GAP > 11.0
    assert ATTRIBUTION_LINES == ("Map data: OpenStreetMap", "openstreetmap.org/copyright")


def test_attribution_renders_below_the_map_without_clipping(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    image = render_context_map_result(data, data.get_street("0001")).image
    _, map_y, _, map_height, _ = _rear_panel_layout(*image.size)
    map_bottom = round(map_y + map_height)
    attribution_pixels = [
        (x, y)
        for y in range(map_bottom, image.height)
        for x in range(image.width)
        if image.getpixel((x, y))[3] > 0
    ]

    assert attribution_pixels
    assert min(y for _, y in attribution_pixels) > map_bottom
    assert max(y for _, y in attribution_pixels) < image.height - 1


def test_final_rear_highlight_scales_width_without_changing_geometry_or_colour() -> None:
    markup = (
        '<svg viewBox="0 0 100 100"><polyline class="highlighted-street" '
        'points="10,20 30,40" fill="none" stroke="#e83e8c" stroke-width="18"/>'
        '<path d="M1 1 L2 2" stroke="#112233" stroke-width="4"/></svg>'
    )
    adjusted = _scale_highlight_stroke(markup, REAR_STREET_HIGHLIGHT_SCALE)
    assert REAR_STREET_HIGHLIGHT_SCALE == pytest.approx(0.85)
    assert 'points="10,20 30,40"' in adjusted
    assert 'stroke="#e83e8c"' in adjusted
    assert 'stroke-width="15.30"' in adjusted
    assert '<path d="M1 1 L2 2" stroke="#112233" stroke-width="4"/>' in adjusted


def test_legacy_context_render_fallback(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")
    assert street.context_path is not None
    street.context_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" fill="#eee"/><polyline class="highlighted-street" points="45,10 55,90" stroke="#a43232" stroke-width="4"/></svg>',
        encoding="utf-8",
    )
    result = render_context_map_result(data, street)
    assert result.framing_mode == "legacy"
    assert not result.metric_metadata_available
    assert result.image.size == REAR_PANEL_PX


def test_context_panel_preserves_highlight_over_raster_map(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")
    assert street.context_path is not None
    base = Image.new("RGB", (16, 16), "#eeeeee")
    payload = BytesIO()
    base.save(payload, format="PNG")
    uri = base64.b64encode(payload.getvalue()).decode("ascii")
    street.context_path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<image href="data:image/png;base64,{uri}" width="100" height="100"/>'
        '<polyline points="50,10 50,90" fill="none" stroke="#e83e8c" stroke-width="8"/>'
        '</svg>',
        encoding="utf-8",
    )
    result = render_context_map_result(data, replace(street, context_source_bounds=None))
    assert any(
        red > 200 and green < 120 and blue > 80
        for y in range(result.image.height)
        for x in range(result.image.width)
        for red, green, blue, _ in (result.image.getpixel((x, y)),)
    )


def test_missing_context_svg_is_clear(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0002")
    with pytest.raises(ContextRenderError, match="no context SVG"):
        render_context_map_result(data, street)


def test_cli_renders_fixture_context(tmp_path: Path) -> None:
    path = dataset_copy(tmp_path)
    output = tmp_path / "context.png"
    assert main(["render", "context", "--dataset", str(path), "--street-id", "0001", "--output", str(output)]) == 0
    with Image.open(output) as image:
        assert image.size == REAR_PANEL_PX
        assert image.mode == "RGBA"

def test_workflow_v6_context_source_geometry_is_loaded(tmp_path: Path) -> None:
    path = dataset_copy(tmp_path)
    summary_path = path / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.update({
        "working_crs": "EPSG:3857",
        "frame_bbox": {"frame_bounds_3857": {"minx": -1000, "miny": -1000, "maxx": 1000, "maxy": 1000}},
        "context_glyphs": {
            "tile_zoom": 0,
            "shared_reference_window_px": {"width": 100, "height": 100},
            "padding_fraction_per_side": 0.1,
        },
    })
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    data = load_dataset(path)
    assert data.context_source_geometry is not None
    assert data.context_source_geometry.crs == "EPSG:3857"
    assert data.context_source_geometry.frame_bounds_m == MetricBounds(-1000, -1000, 1000, 1000)
    assert data.get_street("0001").context_source_bounds is not None


def test_raster_crop_metric_conversion_preserves_y_inversion() -> None:
    source = MetricBounds(0, 0, 2000, 1000)
    bounds = metric_bounds_from_raster_crop(source, (1000, 500), (100, 50, 200, 100))
    assert bounds == MetricBounds(200, 700, 600, 900)


def test_typed_context_bounds_take_precedence_over_embedded_metadata(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")
    assert street.context_path is not None
    street.context_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><metadata id="rear-map-framing">{"source_bounds_m":[0,0,1,1]}</metadata><rect width="100" height="100" fill="#eee"/></svg>',
        encoding="utf-8",
    )
    typed = replace(street, context_source_bounds=MetricBounds(-1000, -1000, 2000, 2000))
    result = render_context_map_result(data, typed)
    assert result.framing_mode == "metric"
    assert result.metric_metadata_available


def _legacy_raster_svg(bounds: tuple[float, float, float, float]) -> str:
    image = Image.new("RGB", (300, 800), "#eeeeee")
    payload = BytesIO()
    image.save(payload, format="PNG")
    uri = base64.b64encode(payload.getvalue()).decode("ascii")
    left, top, right, bottom = bounds
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 800">'
        '<metadata>{"highlight_color":"#E83E8C"}</metadata>'
        f'<image href="data:image/png;base64,{uri}" width="300" height="800"/>'
        f'<polyline points="{left},{top} {right},{bottom}" fill="none" stroke="#E83E8C" stroke-width="8"/>'
        '</svg>'
    )


def _legacy_dataset_with_spans(tmp_path: Path, data, spans: list[float]):
    records = []
    for index, span in enumerate(spans):
        context_path = tmp_path / f"legacy_{index}.svg"
        context_path.write_text(_legacy_raster_svg((150 - span / 2, 400 - span / 2, 150 + span / 2, 400 + span / 2)), encoding="utf-8")
        records.append(replace(data.get_street("0001"), id=f"L{index:03}", context_path=context_path, context_source_bounds=None))
    return replace(data, streets=tuple(records)), records


def test_legacy_tiny_street_uses_dataset_and_resolution_floors(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    legacy, records = _legacy_dataset_with_spans(tmp_path, data, [2.0] + [12.0] * 10)
    markup = records[0].context_path.read_text(encoding="utf-8")
    _, diagnostics = _legacy_crop_markup(legacy, records[0], markup, ContextRenderOptions())

    assert diagnostics["legacy_final_context_span"] >= diagnostics["legacy_base_context_span"]
    assert diagnostics["legacy_final_context_span"] >= diagnostics["legacy_resolution_floor_span"]
    assert diagnostics["legacy_final_context_span"] > diagnostics["legacy_street_required_span"]


def test_legacy_large_street_expands_beyond_dataset_and_resolution_defaults(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    legacy, records = _legacy_dataset_with_spans(tmp_path, data, [12.0] * 10 + [150.0])
    markup = records[-1].context_path.read_text(encoding="utf-8")
    _, diagnostics = _legacy_crop_markup(legacy, records[-1], markup, ContextRenderOptions())

    assert diagnostics["legacy_street_required_span"] > diagnostics["legacy_base_context_span"]
    assert diagnostics["legacy_street_required_span"] > diagnostics["legacy_resolution_floor_span"]
    assert diagnostics["legacy_final_context_span"] == pytest.approx(diagnostics["legacy_street_required_span"])


def test_legacy_resolution_floor_caps_canonical_raster_enlargement(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    legacy, records = _legacy_dataset_with_spans(tmp_path, data, [2.0] + [12.0] * 10)
    markup = records[0].context_path.read_text(encoding="utf-8")
    _, diagnostics = _legacy_crop_markup(legacy, records[0], markup, ContextRenderOptions())

    assert diagnostics["source_context_raster_size_px"] == (300, 800)
    assert diagnostics["effective_raster_magnification"] <= LEGACY_MAX_RASTER_MAGNIFICATION + 0.01


def test_metric_rendering_does_not_populate_legacy_framing_diagnostics(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    result = render_context_map_result(data, data.get_street("0001"))

    assert result.framing_mode == "metric"
    assert result.legacy_final_context_span is None
    assert result.effective_raster_magnification is None


def test_rear_highlight_and_supplied_halo_scale_together() -> None:
    markup = (
        '<svg viewBox="0 0 100 100">'
        '<polyline class="highlighted-street-halo" points="10,20 30,40" fill="none" stroke="#ffffff" stroke-width="20"/>'
        '<polyline class="highlighted-street" points="10,20 30,40" fill="none" stroke="#e83e8c" stroke-width="10"/>'
        '</svg>'
    )

    adjusted = _scale_highlight_stroke(markup, 1.25)

    assert 'stroke="#ffffff" stroke-width="25.00"' in adjusted
    assert 'stroke="#e83e8c" stroke-width="12.50"' in adjusted

def test_rear_composition_moves_down_fifteen_pixels_as_one_group(tmp_path: Path, monkeypatch) -> None:
    from mug_previewer.rendering import context_map

    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")
    shifted_layout = _rear_panel_layout(*REAR_PANEL_PX)
    shifted = render_context_map_result(data, street).image
    assert REAR_COMPOSITION_OFFSET_Y_PX == 15
    monkeypatch.setattr(context_map, "REAR_COMPOSITION_OFFSET_Y_PX", 0)
    baseline_layout = _rear_panel_layout(*REAR_PANEL_PX)
    baseline = render_context_map_result(data, street).image

    assert shifted_layout[1] - baseline_layout[1] == pytest.approx(15)
    assert shifted_layout[4] - baseline_layout[4] == pytest.approx(15)
    for index in (0, 2, 3):
        assert shifted_layout[index] == baseline_layout[index]
    assert shifted.size == baseline.size == REAR_PANEL_PX
    bounds = baseline.getbbox()
    assert bounds is not None and bounds[3] + 15 < shifted.height
    expected = Image.new("RGBA", REAR_PANEL_PX)
    expected.alpha_composite(baseline, (0, 15))
    assert shifted.tobytes() == expected.tobytes()
