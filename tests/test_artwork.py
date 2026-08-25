from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil

import pytest
from PIL import Image

from mug_previewer.cli import main
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.rendering.artwork import (
    PixelBox,
    TEMPLATE_V2_WRAP_LAYOUT,
    WrapComposer,
    render_wrap,
    render_wrap_result,
)
from mug_previewer.rendering.context_map import (
    ATTRIBUTION_LINE_HEIGHT,
    ContextRenderError,
    REAR_PANEL_SCALE,
    REAR_PANEL_PX,
    _rear_panel_layout,
)
from mug_previewer.rendering.face import FRONT_PANEL_PX, FaceRenderError

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def dataset_copy(tmp_path: Path) -> Path:
    path = tmp_path / "workflow_v6_valid"
    shutil.copytree(FIXTURE, path)
    return path


def test_template_v2_wrap_uses_measured_canvas_and_print_zones() -> None:
    layout = TEMPLATE_V2_WRAP_LAYOUT
    assert (layout.canvas_width_px, layout.canvas_height_px) == (2362, 1063)
    assert layout.dpi == 300
    assert layout.front_box == PixelBox(0, 0, 945, 1063)
    assert layout.rear_box == PixelBox(1417, 0, 945, 1063)
    assert layout.seam_zone == PixelBox(945, 0, 472, 1063)
    assert layout.bleed_px == 0


def test_composer_contains_synthetic_panels_at_deterministic_boxes() -> None:
    front = Image.new("RGBA", FRONT_PANEL_PX, (210, 40, 40, 255))
    rear = Image.new("RGBA", REAR_PANEL_PX, (40, 80, 210, 255))
    image, front_box, rear_box = WrapComposer().compose(front, rear)

    assert image.size == (2362, 1063)
    assert image.mode == "RGBA"
    assert front_box == PixelBox(0, 90, 945, 882)
    assert rear_box == PixelBox(1417, 90, 945, 882)
    assert image.getpixel((0, 90)) == (210, 40, 40, 255)
    assert image.getpixel((944, 971)) == (210, 40, 40, 255)
    assert image.getpixel((945, 90)) == (0, 0, 0, 0)
    assert image.getpixel((1417, 90)) == (40, 80, 210, 255)
    assert image.getpixel((2361, 971)) == (40, 80, 210, 255)


def test_composer_preserves_panel_aspect_ratio() -> None:
    front = Image.new("RGBA", FRONT_PANEL_PX, (255, 255, 255, 255))
    rear = Image.new("RGBA", REAR_PANEL_PX, (255, 255, 255, 255))
    _, front_box, rear_box = WrapComposer().compose(front, rear)

    assert front_box.width * FRONT_PANEL_PX[1] == front_box.height * FRONT_PANEL_PX[0]
    assert rear_box.width * REAR_PANEL_PX[1] == rear_box.height * REAR_PANEL_PX[0]


def test_enlarged_rear_content_stays_centred_inside_rear_zone() -> None:
    _, rear_box = WrapComposer().compose(
        Image.new("RGBA", FRONT_PANEL_PX), Image.new("RGBA", REAR_PANEL_PX),
    )[1:]
    map_x, map_y, map_width, map_height, attribution_y = _rear_panel_layout(*REAR_PANEL_PX)
    scale = rear_box.width / REAR_PANEL_PX[0]
    map_left = rear_box.x + map_x * scale
    map_right = map_left + map_width * scale
    map_bottom = rear_box.y + (map_y + map_height) * scale
    attribution_bottom = rear_box.y + (attribution_y + ATTRIBUTION_LINE_HEIGHT) * scale
    assert REAR_PANEL_SCALE == pytest.approx(1.20)
    assert rear_box.x >= TEMPLATE_V2_WRAP_LAYOUT.rear_box.x

    assert rear_box.x >= TEMPLATE_V2_WRAP_LAYOUT.seam_zone.right
    assert map_left >= rear_box.x and map_right <= rear_box.right
    assert map_bottom <= rear_box.bottom and attribution_bottom <= rear_box.bottom
    assert rear_box.right <= TEMPLATE_V2_WRAP_LAYOUT.canvas_width_px
    assert map_left - rear_box.x == pytest.approx(rear_box.right - map_right)
    assert rear_box.x + rear_box.width / 2 == TEMPLATE_V2_WRAP_LAYOUT.rear_box.x + TEMPLATE_V2_WRAP_LAYOUT.rear_box.width / 2

def test_real_front_and_rear_renderers_compose(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    result = render_wrap_result(data, data.get_street("0001"))

    assert result.image.size == (2362, 1063)
    assert result.image.mode == "RGBA"
    assert result.front_panel.size == FRONT_PANEL_PX
    assert result.rear_panel.size == REAR_PANEL_PX
    assert result.front_placed_box == PixelBox(0, 90, 945, 882)
    assert result.rear_placed_box == PixelBox(1417, 90, 945, 882)
    assert result.context.framing_mode == "metric"


def test_wrap_propagates_missing_front_renderer_input(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = replace(data.get_street("0001"), glyph_path=tmp_path / "missing.svg")
    with pytest.raises(FaceRenderError, match="glyph file does not exist"):
        render_wrap(data, street)


def test_wrap_propagates_missing_rear_renderer_input(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = replace(data.get_street("0001"), context_path=None)
    with pytest.raises(ContextRenderError, match="no context SVG"):
        render_wrap(data, street)


def test_cli_renders_fixture_wrap(tmp_path: Path) -> None:
    path = dataset_copy(tmp_path)
    output = tmp_path / "wrap.png"
    assert main(["render", "wrap", "--dataset", str(path), "--street-id", "0001", "--output", str(output)]) == 0
    with Image.open(output) as image:
        assert image.size == (2362, 1063)
        assert image.mode == "RGBA"