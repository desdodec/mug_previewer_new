from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import mug_previewer.ui.app as app_module
from mug_previewer.cli import main
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus
from mug_previewer.ui.app import MugPreviewerApp
from mug_previewer.ui.state import load_preprocessed_catalogue


class _Var:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class _Widget:
    def __init__(self) -> None:
        self.state = "disabled"

    def configure(self, **kwargs: str) -> None:
        self.state = kwargs.get("state", self.state)


class _StreetList:
    def __init__(self) -> None:
        self.index = 0

    def curselection(self) -> tuple[int]:
        return (self.index,)


def _catalogue(tmp_path: Path, state: ProductionTriageStatus, *, preview: bool = True):
    preview_path = tmp_path / "previews" / "street.png"
    if preview:
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (24, 18), (24, 48, 96, 255)).save(preview_path)
    record: dict[str, object] = {
        "dataset_id": "area",
        "street_id": "0001",
        "production_state": state.value,
        "reason_detail": "missing_static_nose" if state is ProductionTriageStatus.UNRENDERABLE_INPUT else "stored reason",
        "success": True,
        "preview_path": "previews/street.png" if preview else None,
        "svg_path": "faces/street.svg",
    }
    if state is ProductionTriageStatus.MANUAL_REVIEW:
        record["generated_svg_path"] = "faces/street.generated.svg"
    if state is ProductionTriageStatus.MANUAL_APPROVED:
        record["approved_svg_path"] = "faces/street.approved.svg"
    (tmp_path / "preprocess_index.json").write_text(json.dumps({"records": [record]}), encoding="utf-8")
    return load_preprocessed_catalogue(tmp_path)


def _controller(catalogue, street: object) -> MugPreviewerApp:
    controller = MugPreviewerApp.__new__(MugPreviewerApp)
    controller.preprocessed_catalogue = catalogue
    controller.state = SimpleNamespace(
        selected_dataset=SimpleNamespace(id="area", display_name="Area"),
        selected_street=None,
        filtered_streets=[street],
        current_wrap=None,
        current_front_preview=None,
        current_rear_preview=None,
        framing_mode=None,
    )
    controller.street_list = _StreetList()
    controller._render_generation = 0
    controller.render_button = _Widget()
    controller.review_street_button = _Widget()
    controller.export_button = _Widget()
    controller.printify_export_button = _Widget()
    controller.status_var = _Var()
    controller.production_var = _Var()
    controller.framing_var = _Var()
    controller.cleared: list[bool] = []
    controller.refreshed: list[str] = []
    controller._clear_previews = lambda: controller.cleared.append(True)
    controller._refresh_preview_images = lambda: controller.refreshed.append(controller.state.selected_street.id)
    return controller


@pytest.mark.parametrize(
    ("state", "title", "export_allowed"),
    [
        (ProductionTriageStatus.AUTO_APPROVED, "Ready for Production", True),
        (ProductionTriageStatus.MANUAL_REVIEW, "Manual Review Required", False),
        (ProductionTriageStatus.MANUAL_APPROVED, "Manually Approved / Ready for Production", True),
    ],
)
def test_preprocessed_selection_loads_cached_preview_without_workers(tmp_path: Path, monkeypatch, state, title, export_allowed) -> None:
    catalogue = _catalogue(tmp_path, state)
    street = SimpleNamespace(id="0001", display_name="Cached Street")
    controller = _controller(catalogue, street)
    monkeypatch.setattr(app_module, "production_status", lambda *_args: pytest.fail("status worker must not run"))
    monkeypatch.setattr(app_module, "render_preview_pair", lambda *_args: pytest.fail("renderer must not run"))
    monkeypatch.setattr(app_module.threading, "Thread", lambda *_args, **_kwargs: pytest.fail("worker must not start"))

    controller._select_street()

    assert controller.state.current_front_preview is not None
    assert controller.refreshed == ["0001"]
    assert title in controller.production_var.value
    assert controller.export_button.state == ("normal" if export_allowed else "disabled")
    if state is ProductionTriageStatus.MANUAL_REVIEW:
        assert "Editable SVG:" in controller.production_var.value


def test_unrenderable_preprocessed_selection_clears_preview_without_renderer(tmp_path: Path, monkeypatch) -> None:
    catalogue = _catalogue(tmp_path, ProductionTriageStatus.UNRENDERABLE_INPUT, preview=False)
    street = SimpleNamespace(id="0001", display_name="Beith Way")
    controller = _controller(catalogue, street)
    controller.state.current_front_preview = Image.new("RGBA", (1, 1))
    monkeypatch.setattr(app_module, "render_preview_pair", lambda *_args: pytest.fail("renderer must not run"))

    controller._select_street()

    assert controller.state.current_front_preview is None
    assert controller.cleared == [True]
    assert "Cannot Render: missing_static_nose" == controller.production_var.value
    assert controller.refreshed == []


def test_missing_cached_preview_is_clear_non_rendering_state(tmp_path: Path, monkeypatch) -> None:
    catalogue = _catalogue(tmp_path, ProductionTriageStatus.AUTO_APPROVED, preview=False)
    street = SimpleNamespace(id="0001", display_name="Missing")
    controller = _controller(catalogue, street)
    monkeypatch.setattr(app_module, "render_preview_pair", lambda *_args: pytest.fail("renderer must not run"))

    controller._select_street()

    assert controller.cleared == [True]
    assert controller.status_var.value.startswith("Preview not prepared:")
    assert controller.production_var.value.startswith("Preview not prepared")
    assert controller.export_button.state == "disabled"


def test_rapid_preprocessed_switch_keeps_latest_preview(tmp_path: Path) -> None:
    first_preview = tmp_path / "previews" / "first.png"
    second_preview = tmp_path / "previews" / "second.png"
    first_preview.parent.mkdir(parents=True)
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(first_preview)
    Image.new("RGBA", (10, 10), (0, 0, 255, 255)).save(second_preview)
    records = [
        {"dataset_id": "area", "street_id": "0001", "production_state": "AUTO_APPROVED", "success": True, "preview_path": "previews/first.png"},
        {"dataset_id": "area", "street_id": "0002", "production_state": "AUTO_APPROVED", "success": True, "preview_path": "previews/second.png"},
    ]
    (tmp_path / "preprocess_index.json").write_text(json.dumps({"records": records}), encoding="utf-8")
    first = SimpleNamespace(id="0001", display_name="First")
    second = SimpleNamespace(id="0002", display_name="Second")
    controller = _controller(load_preprocessed_catalogue(tmp_path), first)
    controller.state.filtered_streets = [first, second]

    controller._select_street()
    controller.street_list.index = 1
    controller._select_street()

    assert controller.refreshed == ["0001", "0002"]
    assert controller.state.selected_street is second
    assert controller.state.current_front_preview.getpixel((0, 0)) == (0, 0, 255, 255)
    assert "0002 Second" in controller.status_var.value


def test_cli_passes_preprocessed_directory_to_ui(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def launch(*, dataset_root=None, preprocessed=None) -> int:
        captured.update(dataset_root=dataset_root, preprocessed=preprocessed)
        return 0

    monkeypatch.setattr("mug_previewer.ui.app.launch", launch)
    assert main(["--dataset-root", str(tmp_path), "--preprocessed", str(tmp_path), "ui"]) == 0
    assert captured == {"dataset_root": tmp_path, "preprocessed": tmp_path}
