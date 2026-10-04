from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.design import (
    DesignOptions,
    PRODUCTION_REAR_PANEL_SIZE,
    build_render_options,
)
from mug_previewer.exporting import compose_provider_artwork
from mug_previewer.providers import get_provider_profile
from mug_previewer.rendering.context_map import render_context_map_result


FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def _artwork(size: tuple[int, int], box: tuple[int, int, int, int]) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(box, fill=(0, 0, 0, 255))
    return image


def test_profile_driven_rear_renders_at_canonical_resolution() -> None:
    data = load_dataset(FIXTURE)
    options = build_render_options(DesignOptions(), area=data.display_name)

    assert PRODUCTION_REAR_PANEL_SIZE == (945, 882)
    assert options.context_options is not None
    assert options.context_options.panel_size == PRODUCTION_REAR_PANEL_SIZE

    rendered = render_context_map_result(
        data,
        data.streets[0],
        options.context_options,
    )
    assert rendered.image.size == PRODUCTION_REAR_PANEL_SIZE


def test_supplier_compositor_preserves_physical_size_for_high_resolution_rear() -> None:
    profile = get_provider_profile("prodigi_h_mug_w")
    front = _artwork((495, 462), (100, 80, 300, 300))
    low = _artwork((495, 462), (100, 80, 300, 300))

    sx = 945 / 495
    sy = 882 / 462
    high_box = tuple(
        round(value * scale)
        for value, scale in zip((100, 80, 300, 300), (sx, sy, sx, sy))
    )
    high = _artwork((945, 882), high_box)

    low_result = compose_provider_artwork(front, low, profile)
    high_result = compose_provider_artwork(front, high, profile)

    # Extra source pixels improve sampling only; they must not make the rear
    # artwork physically larger on the supplier canvas.
    assert high_result.rear_center_xy == low_result.rear_center_xy
    assert high_result.rear_placement.width == pytest.approx(
        low_result.rear_placement.width, abs=2
    )
    assert high_result.rear_placement.height == pytest.approx(
        low_result.rear_placement.height, abs=2
    )
