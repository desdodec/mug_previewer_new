from __future__ import annotations

from pathlib import Path
import shutil

from PIL import Image

from mug_previewer.datasets.discovery import DatasetCandidate
from mug_previewer.design import DesignOptions, build_render_options
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.preview.mockup import PreviewOrientation
from mug_previewer.ui.state import PREVIEW_SIZE, dataset_options, display_image, filter_streets, render_preview_pair

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def _dataset(tmp_path: Path):
    copied = tmp_path / "Test Borough dataset"
    shutil.copytree(FIXTURE, copied)
    return load_dataset(copied)


def test_dataset_options_use_readable_stable_labels(tmp_path: Path) -> None:
    data = _dataset(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    options = dataset_options(root, discover=lambda _root: [DatasetCandidate(data, ())])
    assert [(item.label, item.dataset) for item in options] == [("Test Borough", data)]


def test_street_filter_matches_case_insensitive_names_ids_and_empty_query(tmp_path: Path) -> None:
    streets = _dataset(tmp_path).streets
    assert [street.id for street in filter_streets(streets, "")] == ["0001", "0002"]
    assert [street.id for street in filter_streets(streets, "john")] == ["0001"]
    assert [street.id for street in filter_streets(streets, "0002")] == ["0002"]
    assert [street.id for street in filter_streets(streets, "CAFÉ")] == ["0002"]


def test_preview_rendering_uses_one_wrap_and_both_production_orientations(tmp_path: Path) -> None:
    data = _dataset(tmp_path)
    calls: list[object] = []
    wrap = Image.new("RGBA", (2362, 1063))

    def fake_wrap(dataset, street, options=None):
        calls.append((dataset, street, options))
        return wrap

    def fake_preview(source, options):
        assert source is wrap
        calls.append(options.orientation)
        return Image.new("RGBA", PREVIEW_SIZE)

    result = render_preview_pair(data, data.streets[0], wrap_renderer=fake_wrap, preview_renderer=fake_preview)
    assert result.wrap is wrap
    assert result.front.size == result.rear.size == PREVIEW_SIZE
    assert calls == [
        (data, data.streets[0], build_render_options(DesignOptions(), area=data.display_name)),
        PreviewOrientation.FRONT_HANDLE_RIGHT,
        PreviewOrientation.REAR_HANDLE_LEFT,
    ]


def test_display_image_crops_and_fits_without_changing_aspect_ratio() -> None:
    source = Image.new("RGBA", (1000, 1500), (245, 245, 245, 255))
    for x in range(300, 700):
        for y in range(350, 1200):
            source.putpixel((x, y), (80, 100, 120, 255))
    displayed = display_image(source, (300, 200))
    assert displayed.width <= 300 and displayed.height <= 200
    assert abs(displayed.width / displayed.height - 504 / 954) < 0.005
