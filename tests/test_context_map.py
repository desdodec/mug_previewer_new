from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image

from mug_previewer.cli import main
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.rendering.context_map import (
    ContextRenderError,
    MetricBounds,
    REAR_PANEL_PX,
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
