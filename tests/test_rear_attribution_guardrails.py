from __future__ import annotations

import shutil
from pathlib import Path

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.rendering.context_map import (
    ContextRenderOptions,
    _rear_panel_layout,
    render_context_map_result,
)


FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def dataset_copy(tmp_path: Path) -> Path:
    path = tmp_path / "workflow_v6_valid"
    shutil.copytree(FIXTURE, path)
    return path


def test_large_positive_rear_offsets_cannot_hide_required_attribution(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")
    image = render_context_map_result(
        data,
        street,
        ContextRenderOptions(
            attribution_line1_y_offset=30.0,
            attribution_line2_y_offset=30.0,
        ),
    ).image

    _, map_y, _, map_height, _ = _rear_panel_layout(*image.size)
    map_bottom = round(map_y + map_height)
    attribution_pixels = [
        (x, y)
        for y in range(map_bottom, image.height)
        for x in range(image.width)
        if image.getpixel((x, y))[3] > 0
    ]

    assert attribution_pixels
    assert max(y for _, y in attribution_pixels) < image.height


def test_default_rear_attribution_render_is_unchanged_by_guardrail(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")
    default = render_context_map_result(data, street).image
    explicit = render_context_map_result(
        data,
        street,
        ContextRenderOptions(
            attribution_line1_y_offset=0.0,
            attribution_line2_y_offset=0.0,
        ),
    ).image

    assert default.tobytes() == explicit.tobytes()
