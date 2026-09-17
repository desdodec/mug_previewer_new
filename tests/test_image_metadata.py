from dataclasses import replace
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.datasets.validation import validate_dataset
from mug_previewer.rendering.face import FaceRenderOptions, _render_face_standard, render_face_svg
from mug_previewer.diagnostics.front_candidates import _typography_mask

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def test_metadata_matches_each_glyph_and_drives_both_renderers(tmp_path):
    path = tmp_path / "dataset"
    shutil.copytree(FIXTURE, path)
    original = load_dataset(path)
    images = {
        street.glyph_path.relative_to(path).as_posix(): {
            "ceremonial_county": " Merseyside ", "postcode_prefix": postcode,
        }
        for street, postcode in zip(original.streets, ("ch49", "CH48"))
    }
    (path / "image_metadata.json").write_text(json.dumps({"images": images}))
    data = load_dataset(path)
    assert data.display_name == original.display_name
    assert [s.locality_label for s in data.streets] == ["Merseyside, CH49", "Merseyside, CH48"]
    for street in data.streets:
        old_options = FaceRenderOptions(area="West Kirby")
        root = ET.fromstring(render_face_svg(data, street, old_options))
        assert root.find('.//{*}text[@class="mug-area"]').text == street.locality_label
        plain = replace(street, ceremonial_county="", postcode_prefix="")
        expected = FaceRenderOptions(area=street.locality_label)
        assert _render_face_standard(street, old_options).tobytes() == _render_face_standard(plain, expected).tobytes()
        assert _typography_mask(street, "West Kirby").tobytes() == _typography_mask(plain, street.locality_label).tobytes()


def test_missing_and_partial_metadata(tmp_path):
    path = tmp_path / "dataset"
    shutil.copytree(FIXTURE, path)
    data = load_dataset(path)
    assert all(not s.locality_label for s in data.streets)
    street = data.streets[0]
    key = street.glyph_path.relative_to(path).as_posix()
    (path / "image_metadata.json").write_text(json.dumps({"images": {key: {"ceremonial_county": "Merseyside", "postcode_prefix": None}}}))
    data = load_dataset(path)
    assert data.streets[0].locality_label == "Merseyside"
    assert data.streets[1].locality_label == ""


def test_invalid_metadata_reports_load_error(tmp_path):
    path = tmp_path / "dataset"
    shutil.copytree(FIXTURE, path)
    (path / "image_metadata.json").write_text('{"images": []}')
    assert not validate_dataset(path).valid
