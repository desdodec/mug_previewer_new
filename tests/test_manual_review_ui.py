from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus
from mug_previewer.manual import ManualOverrideError, load_manual_overrides
from mug_previewer.ui import manual_review


def _decision(status: ProductionTriageStatus):
    candidate = SimpleNamespace(orientation_deg=180, scale=0.95, y_offset=20)
    return SimpleNamespace(
        triage_status=status, diagnostic_class="UNRESOLVED", standard=candidate,
        best_candidate=candidate, reason_codes=("standard_nose_overlap", "adaptation_low_confidence"),
    )


def test_controller_queue_preview_save_clear_and_bounds(tmp_path: Path, monkeypatch) -> None:
    dataset = load_dataset(Path(__file__).parent / "fixtures" / "workflow_v6_valid")
    decisions = {
        dataset.streets[0].id: _decision(ProductionTriageStatus.MANUAL_REVIEW),
        dataset.streets[1].id: _decision(ProductionTriageStatus.UNRENDERABLE_INPUT),
    }
    monkeypatch.setattr(manual_review, "select_production_placement", lambda street, *, area: decisions[street.id])
    calls = []
    def render(street, options):
        calls.append((street.id, options.manual_override))
        return Image.new("RGBA", (495, 462))
    path = tmp_path / "manual_overrides.json"
    controller = manual_review.ManualReviewController([dataset], override_path=path, face_renderer=render)

    assert [record.street.id for record in controller.visible_records] == ["0001"]
    assert controller.current.reason_text == "Mouth overlaps nose; Automatic alternative remained visually uncertain"
    assert controller.transform == (180, 0.95, 20)
    controller.render_previews()
    assert not path.exists()
    assert calls[0][1] is None and calls[1][1].transform == (180, 0.95, 20)
    with pytest.raises(ValueError):
        controller.set_transform(90, 0.95, 20)

    controller.set_transform(180, 0.90, 40)
    assert controller.approve_current_edit() is None
    saved = load_manual_overrides(path).get(dataset.id, "0001")
    assert saved is not None and saved.transform == (180, 0.90, 40)
    assert controller.pending_count == 0
    controller.set_filter("resolved")
    assert controller.current.transform if False else controller.transform == (180, 0.90, 40)
    controller.clear_saved_decision()
    controller.set_filter("pending")
    assert controller.pending_count == 1 and controller.current.key == (dataset.id, "0001")


def test_save_failure_leaves_pending_selection_intact(tmp_path: Path, monkeypatch) -> None:
    dataset = load_dataset(Path(__file__).parent / "fixtures" / "workflow_v6_valid")
    monkeypatch.setattr(manual_review, "select_production_placement", lambda street, *, area: _decision(ProductionTriageStatus.MANUAL_REVIEW))
    controller = manual_review.ManualReviewController([dataset], override_path=tmp_path / "overrides.json", face_renderer=lambda *_: Image.new("RGBA", (495, 462)))
    monkeypatch.setattr(manual_review, "save_manual_override", lambda *_: (_ for _ in ()).throw(ManualOverrideError("disk unavailable")))
    selected = controller.current.key
    with pytest.raises(ManualOverrideError, match="disk unavailable"):
        controller.approve_standard()
    assert controller.current.key == selected and controller.pending_count == 2
