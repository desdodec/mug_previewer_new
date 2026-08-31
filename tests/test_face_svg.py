from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import cairosvg
from PIL import Image, ImageChops

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.rendering.face import FRONT_PANEL_PX, _render_face_standard, render_face_svg, write_face_svg


FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"
NS = {"svg": "http://www.w3.org/2000/svg"}


def _fixture_data():
    return load_dataset(FIXTURE)


def test_face_svg_is_deterministic_and_has_editable_vector_groups() -> None:
    data = _fixture_data()
    street = data.get_street("0001")
    assert street is not None

    first = render_face_svg(data, street)
    second = render_face_svg(data, street)
    assert first == second
    assert "<image" not in first
    root = ET.fromstring(first)
    assert {"eyes", "nose-street", "mouth", "title", "locality"} <= {
        node.get("id") for node in root.iter() if node.get("id")
    }
    nose_street = root.find('.//svg:g[@id="nose-street"]', NS)
    assert nose_street is not None
    assert nose_street.findall(".//svg:polyline", NS)
    assert root.find(".//svg:metadata", NS) is not None


def test_face_svg_rasterises_to_the_current_front_layout(tmp_path: Path) -> None:
    data = _fixture_data()
    street = data.get_street("0001")
    assert street is not None
    svg = render_face_svg(data, street)
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=990, output_height=462)
    output = tmp_path / "face.svg"
    assert write_face_svg(data, street, output) == output
    assert output.read_text(encoding="utf-8") == svg

    import io
    with Image.open(io.BytesIO(png)) as image:
        vector_panel = image.convert("RGBA").crop((0, 0, FRONT_PANEL_PX[0], FRONT_PANEL_PX[1]))
    raster_panel = _render_face_standard(street, replace_face_area(data))
    difference = ImageChops.difference(vector_panel, raster_panel)
    assert difference.getbbox() is not None
    assert vector_panel.getchannel("A").getbbox() == raster_panel.getchannel("A").getbbox()


def test_auto_approved_scope_is_reused_without_retriage(monkeypatch) -> None:
    from mug_previewer.ui import production

    data = _fixture_data()
    street = data.get_street("0001")
    assert street is not None
    scope_dataset = replace(data, display_name="Hebden Bridge")
    burnley = replace(street, id="0036", street_name="Burnley Road", display_name="Burnley Road")
    monkeypatch.setattr(
        production, "select_production_placement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not retriage validated scope")),
    )
    metadata = ET.fromstring(render_face_svg(scope_dataset, burnley)).find(".//svg:metadata", NS)
    assert metadata is not None and metadata.text is not None
    assert json.loads(metadata.text)["placement_source"] == "validated-production-approved_standard"


def replace_face_area(data):
    from mug_previewer.rendering.face import FaceRenderOptions
    return FaceRenderOptions(area=data.display_name)
