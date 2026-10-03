from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.rendering.face import (
    FRONT_GROUP_SCALE,
    TITLE_FONT_SIZE_TIERS,
    _render_face_standard,
    layout_title_text,
    render_face_svg,
    title_safe_width_for_group,
)
from mug_previewer.rendering.native import face_policy as native


FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def dataset_copy(tmp_path: Path) -> Path:
    path = tmp_path / "workflow_v6_valid"
    shutil.copytree(FIXTURE, path)
    return path


def test_short_title_keeps_established_single_line_size() -> None:
    font_stack = native.get_text_font_stack(native.DEFAULT_TEXT_FONT_KEY)
    layout = layout_title_text("Park Road", font_stack, group_scale=FRONT_GROUP_SCALE)

    assert layout.lines == ("Park Road",)
    assert layout.status == "safe"
    assert layout.size_px == pytest.approx(TITLE_FONT_SIZE_TIERS[0])
    assert layout.rendered_width_px <= layout.safe_width_px


def test_stoke_newington_church_street_wraps_at_natural_boundary() -> None:
    font_stack = native.get_text_font_stack(native.DEFAULT_TEXT_FONT_KEY)
    layout = layout_title_text(
        "Stoke Newington Church Street",
        font_stack,
        group_scale=FRONT_GROUP_SCALE,
    )

    assert layout.lines == ("Stoke Newington", "Church Street")
    assert layout.status == "wrapped"
    assert layout.wrapped
    assert layout.size_px == pytest.approx(TITLE_FONT_SIZE_TIERS[0])
    assert all(width <= layout.safe_width_px for width in layout.rendered_widths_px)


def test_guardrail_safe_width_accounts_for_final_composition_scale() -> None:
    normal = title_safe_width_for_group(1.0)
    enlarged = title_safe_width_for_group(FRONT_GROUP_SCALE)

    assert enlarged < normal
    assert enlarged * FRONT_GROUP_SCALE == pytest.approx(440.0)


def test_wrapped_svg_uses_two_title_lines_and_moves_locality_down(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    source = data.get_street("0001")
    long_street = replace(
        source,
        street_name="Stoke Newington Church Street",
        display_name="Stoke Newington Church Street",
    )
    short_svg = render_face_svg(data, source)
    wrapped_svg = render_face_svg(data, long_street)

    def positions(markup: str):
        root = ET.fromstring(markup)
        title = next(node for node in root.iter() if node.get("id") == "title")
        locality = next(node for node in root.iter() if node.get("id") == "locality")
        title_nodes = [node for node in title.iter() if node.tag.rsplit("}", 1)[-1] == "text"]
        locality_node = next(node for node in locality.iter() if node.tag.rsplit("}", 1)[-1] == "text")
        return title_nodes, float(locality_node.get("y"))

    short_titles, short_locality_y = positions(short_svg)
    wrapped_titles, wrapped_locality_y = positions(wrapped_svg)

    assert len(short_titles) == 1
    assert len(wrapped_titles) == 2
    assert [node.text for node in wrapped_titles] == ["Stoke Newington", "Church Street"]
    assert float(wrapped_titles[1].get("y")) > float(wrapped_titles[0].get("y"))
    assert wrapped_locality_y > short_locality_y


def test_png_renderer_reports_wrapped_title_layout(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = replace(
        data.get_street("0001"),
        street_name="Stoke Newington Church Street",
        display_name="Stoke Newington Church Street",
    )

    image = _render_face_standard(street)

    assert image.info["title_layout_status"] == "wrapped"
    assert image.info["title_layout_lines"] == ("Stoke Newington", "Church Street")
    assert image.info["title_layout_size_px"] == pytest.approx(TITLE_FONT_SIZE_TIERS[0])
