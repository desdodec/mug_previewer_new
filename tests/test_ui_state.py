from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace

from PIL import Image
import pytest

from mug_previewer.datasets.discovery import DatasetCandidate
from mug_previewer.design import DesignOptions, build_render_options
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.manual import ManualPlacementOverride
from mug_previewer.preview.mockup import PreviewOrientation
from mug_previewer.ui.state import PREVIEW_SIZE, UIDataError, dataset_options, display_image, filter_streets, render_preview_pair
from mug_previewer.providers import get_provider_profile
from mug_previewer.ui.state import INKTHREADABLE_PROFILE_ID, export_inkthreadable_png
import mug_previewer.ui.state as ui_state
import mug_previewer.ui.app as ui_app
from mug_previewer.ui.app import MugPreviewerApp

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
    assert [street.id for street in filter_streets(streets, "CAF\u00C9")] == ["0002"]


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


def test_inkthreadable_export_uses_current_selection_design_and_profile(tmp_path: Path) -> None:
    data = _dataset(tmp_path)
    def forbidden(*args):
        pytest.fail('Unreviewed live artwork must never render or export')
    with pytest.raises(UIDataError, match='current human QA pass'):
        export_inkthreadable_png(data, data.streets[0], tmp_path / 'blocked.png',
             wrap_renderer=forbidden, exporter=forbidden)
    assert not (tmp_path / 'blocked.png').exists()


def test_export_without_selection_shows_clear_error_without_save_dialog(monkeypatch) -> None:
    controller = MugPreviewerApp.__new__(MugPreviewerApp)
    controller.state = SimpleNamespace(selected_dataset=None, selected_street=None)
    errors: list[str] = []
    controller._show_error = errors.append
    called = False

    def fake_dialog(**_kwargs):
        nonlocal called
        called = True
        return ''

    monkeypatch.setattr(ui_app.filedialog, 'asksaveasfilename', fake_dialog)
    controller._start_inkthreadable_export()

    assert errors == ['Select a street before exporting.']
    assert not called


def test_cancelled_export_dialog_does_not_start_render_or_export(tmp_path: Path, monkeypatch) -> None:
    data = _dataset(tmp_path)
    controller = MugPreviewerApp.__new__(MugPreviewerApp)
    controller.root = object()
    controller.state = SimpleNamespace(
        selected_dataset=data, selected_street=data.streets[0], design_options=DesignOptions(),
    )
    controller._is_preprocessed_mode = lambda: True
    controller._qa_export_error = lambda: None
    dialog: dict[str, object] = {}

    def fake_dialog(**kwargs):
        dialog.update(kwargs)
        return ''

    monkeypatch.setattr(ui_app.filedialog, 'asksaveasfilename', fake_dialog)
    controller._start_inkthreadable_export()

    assert dialog['defaultextension'] == '.png'


def test_inkthreadable_export_writes_provider_png_at_profile_dimensions(tmp_path: Path) -> None:
    data = _dataset(tmp_path)
    source = Image.new('RGBA', (2362, 1063), (20, 40, 60, 128))
    destination = tmp_path / 'inkthreadable.png'

    saved = ui_state.save_provider_export(source, get_provider_profile(INKTHREADABLE_PROFILE_ID), destination)

    assert saved == destination
    with Image.open(destination) as exported:
        assert exported.format == 'PNG'
        assert exported.size == (2362, 1063)
        assert exported.mode == 'RGBA'
        assert round(exported.info['dpi'][0]) == round(exported.info['dpi'][1]) == 300
    return
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

def test_printify_export_uses_current_selection_design_and_profile(tmp_path: Path) -> None:
    data = _dataset(tmp_path)
    def forbidden(*args):
        pytest.fail('Unreviewed live artwork must never render or export')
    with pytest.raises(UIDataError, match='current human QA pass'):
        ui_state.export_printify_png(data, data.streets[0], tmp_path / 'blocked.png',
             wrap_renderer=forbidden, exporter=forbidden)
    assert not (tmp_path / 'blocked.png').exists()


def test_printify_cancelled_export_uses_png_dialog_and_does_not_start_worker(tmp_path: Path, monkeypatch) -> None:
    data = _dataset(tmp_path)
    controller = MugPreviewerApp.__new__(MugPreviewerApp)
    controller.root = object()
    controller.state = SimpleNamespace(
        selected_dataset=data, selected_street=data.streets[0], design_options=DesignOptions(),
    )
    controller._is_preprocessed_mode = lambda: True
    controller._qa_export_error = lambda: None
    dialog: dict[str, object] = {}

    def fake_dialog(**kwargs):
        dialog.update(kwargs)
        return ""

    monkeypatch.setattr(ui_app.filedialog, "asksaveasfilename", fake_dialog)
    controller._start_printify_export()

    assert dialog == {
        "parent": controller.root,
        "title": "Export Printify PNG",
        "initialfile": "test-borough_0001_st-john-s-road_printify.png",
        "defaultextension": ".png",
        "filetypes": [("PNG files", "*.png")],
    }


def test_printify_export_writes_provider_png_with_transparent_padding(tmp_path: Path) -> None:
    from mug_previewer.ui.state import export_printify_png

    data = _dataset(tmp_path)
    source = Image.new("RGBA", (2362, 1063), (20, 40, 60, 128))
    destination = tmp_path / "printify.png"

    saved = ui_state.save_provider_export(source, get_provider_profile(ui_state.PRINTIFY_PROFILE_ID), destination)

    assert saved == destination
    with Image.open(destination) as exported:
        assert exported.format == "PNG"
        assert exported.size == (2475, 1155)
        assert exported.mode == "RGBA"
        assert round(exported.info["dpi"][0]) == round(exported.info["dpi"][1]) == 300
        assert exported.getpixel((0, 0))[3] == 0
        assert exported.getpixel((0, 1154))[3] == 0
        assert exported.getpixel((0, 20))[3] == 128
def test_printify_export_without_selection_shows_clear_error_without_save_dialog(monkeypatch) -> None:
    controller = MugPreviewerApp.__new__(MugPreviewerApp)
    controller.state = SimpleNamespace(selected_dataset=None, selected_street=None)
    errors: list[str] = []
    controller._show_error = errors.append
    called = False

    def fake_dialog(**_kwargs):
        nonlocal called
        called = True
        return ""

    monkeypatch.setattr(ui_app.filedialog, "asksaveasfilename", fake_dialog)
    controller._start_printify_export()

    assert errors == ["Select a street before exporting."]
    assert not called

def test_dataset_options_requires_explicit_configuration() -> None:
    with pytest.raises(UIDataError, match='No dataset root'):
        dataset_options(None)


def test_preview_uses_screen_mockup_layout_and_approved_placement(tmp_path: Path, monkeypatch) -> None:
    data = _dataset(tmp_path)
    street = data.streets[0]
    wrap = Image.new("RGBA", (2362, 1063))
    override = ManualPlacementOverride.approved_standard(data.id, street.id, street.display_name)
    captured: list[object] = []

    monkeypatch.setattr(ui_state, "approved_override_for_street", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ui_state, "preview_render_override", lambda *_args: override)

    def fake_wrap(_data, _street, options=None):
        captured.append(options)
        return wrap

    def fake_preview(_wrap, options):
        captured.append(options)
        return Image.new("RGBA", ui_state.PREVIEW_SIZE)

    render_preview_pair(data, street, wrap_renderer=fake_wrap, preview_renderer=fake_preview)

    options = captured[0]
    assert options.face_options.manual_override is override
    assert [item.layout.canvas_size for item in captured[1:]] == [ui_state.PREVIEW_SIZE, ui_state.PREVIEW_SIZE]