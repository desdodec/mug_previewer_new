from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus
from mug_previewer.manual import ManualPlacementOverride, clear_manual_override, save_manual_override
from mug_previewer.ui import production
from mug_previewer.ui.app import MugPreviewerApp


def _decision(status, placement=None, reasons=()):
    return SimpleNamespace(triage_status=status, placement_class=placement, reason_codes=reasons)


def test_production_status_maps_ready_manual_and_unrenderable(tmp_path, monkeypatch) -> None:
    dataset = load_dataset(Path(__file__).parent / "fixtures" / "workflow_v6_valid")
    street = dataset.streets[0]
    monkeypatch.setattr(production, "select_production_placement", lambda *_args, **_kwargs: _decision(ProductionTriageStatus.AUTO_APPROVED, "STANDARD"))
    ready = production.production_status(dataset, street, override_path=tmp_path / "store.json")
    assert ready.export_allowed and ready.title == "Ready for Production"

    monkeypatch.setattr(production, "select_production_placement", lambda *_args, **_kwargs: _decision(ProductionTriageStatus.MANUAL_REVIEW, reasons=("standard_nose_overlap",)))
    pending = production.production_status(dataset, street, override_path=tmp_path / "store.json")
    assert pending.review_required and not pending.export_allowed
    path = tmp_path / "store.json"
    save_manual_override(ManualPlacementOverride.approved_standard(dataset.id, street.id, street.display_name), path)
    approved = production.production_status(dataset, street, override_path=path)
    assert approved.export_allowed and approved.resolution_label == "Manually approved — Standard placement"

    monkeypatch.setattr(production, "select_production_placement", lambda *_args, **_kwargs: _decision(ProductionTriageStatus.UNRENDERABLE_INPUT, reasons=("missing_static_nose",)))
    blocked = production.production_status(dataset, street, override_path=path)
    assert blocked.title == "Cannot Render"
    assert "anatomy" in blocked.detail


def test_status_refreshes_from_pending_to_approved_and_back(tmp_path, monkeypatch) -> None:
    dataset = load_dataset(Path(__file__).parent / "fixtures" / "workflow_v6_valid")
    street = dataset.streets[0]
    path = tmp_path / "store.json"
    monkeypatch.setattr(
        production, "select_production_placement",
        lambda *_args, **_kwargs: _decision(ProductionTriageStatus.MANUAL_REVIEW, reasons=("standard_nose_overlap",)),
    )

    pending = production.production_status(dataset, street, override_path=path)
    save_manual_override(
        ManualPlacementOverride.approved_transform(
            dataset.id, street.id, street.display_name, orientation_deg=180, scale=0.90, y_offset=40,
        ),
        path,
    )
    approved = production.production_status(dataset, street, override_path=path)
    clear_manual_override(dataset.id, street.id, path)
    restored = production.production_status(dataset, street, override_path=path)

    assert (pending.export_allowed, pending.review_required) == (False, True)
    assert (approved.export_allowed, approved.review_required) == (True, False)
    assert "Edited placement" in approved.detail
    assert (restored.export_allowed, restored.review_required) == (False, True)


def test_scope_resource_and_unrenderable_items_are_available() -> None:
    items = production.production_unrenderable_items([SimpleNamespace(display_name="Glasgow")])
    assert [(item.street_id, item.street_name) for item in items] == [("0021", "Beith Way")]
    assert "face anatomy" in items[0].reason


def test_blocked_export_never_opens_a_save_dialog(monkeypatch, tmp_path) -> None:
    dataset = load_dataset(Path(__file__).parent / "fixtures" / "workflow_v6_valid")
    controller = MugPreviewerApp.__new__(MugPreviewerApp)
    controller.state = SimpleNamespace(selected_dataset=dataset, selected_street=dataset.streets[0])
    controller.current_production_status = SimpleNamespace(export_allowed=False)
    errors: list[str] = []
    controller._show_error = errors.append
    opened = False

    def fake_dialog(**_kwargs):
        nonlocal opened
        opened = True
        return str(tmp_path / "should-not-exist.png")

    monkeypatch.setattr("mug_previewer.ui.app.filedialog.asksaveasfilename", fake_dialog)
    controller._start_inkthreadable_export()

    assert errors == ["Production export is unavailable until this street is ready for production."]
    assert not opened


def test_provider_filename_is_safe_and_includes_dataset_and_street_id(tmp_path) -> None:
    dataset = load_dataset(Path(__file__).parent / "fixtures" / "workflow_v6_valid")
    assert MugPreviewerApp._provider_filename(dataset, dataset.streets[0], "inkthreadable") == (
        "test-borough_0001_st-john-s-road_inkthreadable.png"
    )
