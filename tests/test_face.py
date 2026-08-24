from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from mug_previewer.cli import main
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.rendering.face import FRONT_PANEL_PX, FaceRenderError, FaceRenderOptions, render_face

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def dataset_copy(tmp_path: Path) -> Path:
    path = tmp_path / "workflow_v6_valid"
    shutil.copytree(FIXTURE, path)
    return path


def test_render_face_returns_v28_front_panel(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    image = render_face(data.get_street("0001"), FaceRenderOptions(area=data.display_name))
    assert image.size == FRONT_PANEL_PX
    assert image.mode == "RGBA"


def test_render_face_missing_glyph_is_clear(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    missing = replace(data.get_street("0001"), glyph_path=tmp_path / "missing.svg")
    with pytest.raises(FaceRenderError, match=r'Cannot render street 0001 "St John\'s Road": glyph file does not exist:'):
        render_face(missing)


def test_render_face_accepts_unicode_street_name(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    image = render_face(data.get_street("0002"), FaceRenderOptions(area=data.display_name))
    assert image.size == FRONT_PANEL_PX


def test_cli_renders_fixture_face(tmp_path: Path) -> None:
    path = dataset_copy(tmp_path)
    output = tmp_path / "face.png"
    assert main(["render", "face", "--dataset", str(path), "--street-id", "0001", "--output", str(output)]) == 0
    assert output.is_file()
    with Image.open(output) as image:
        assert image.size == FRONT_PANEL_PX