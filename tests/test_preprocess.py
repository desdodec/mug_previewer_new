from __future__ import annotations

import json
from pathlib import Path

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus
from mug_previewer import preprocess


FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def _dataset():
    return load_dataset(FIXTURE)


def _states(*states: ProductionTriageStatus):
    values = iter(states)
    return lambda *_args: (next(values), ("existing_reason",))


def test_auto_and_manual_generate_assets_and_index(tmp_path: Path, monkeypatch) -> None:
    data = _dataset()
    monkeypatch.setattr(
        preprocess, "production_triage_state",
        _states(ProductionTriageStatus.AUTO_APPROVED, ProductionTriageStatus.MANUAL_REVIEW),
    )

    result = preprocess.preprocess_datasets([data], tmp_path, street_ids=["0001", "0002"])

    assert (result.processed, result.auto_approved, result.manual_review, result.unexpected_errors) == (2, 1, 1, 0)
    index = json.loads((tmp_path / preprocess.INDEX_FILENAME).read_text(encoding="utf-8"))
    records = {record["street_id"]: record for record in index["records"]}
    for street_id, state in (("0001", "AUTO_APPROVED"), ("0002", "MANUAL_REVIEW")):
        record = records[street_id]
        assert record["production_state"] == state
        assert record["success"] is True
        assert (tmp_path / record["svg_path"]).is_file()
        assert (tmp_path / record["preview_path"]).is_file()
        assert record["source_fingerprint"]


def test_unrenderable_continues_and_does_not_claim_assets(tmp_path: Path, monkeypatch) -> None:
    data = _dataset()
    monkeypatch.setattr(
        preprocess, "production_triage_state",
        _states(ProductionTriageStatus.UNRENDERABLE_INPUT, ProductionTriageStatus.AUTO_APPROVED),
    )

    result = preprocess.preprocess_datasets([data], tmp_path, street_ids=["0001", "0002"])

    assert (result.processed, result.unrenderable_input, result.auto_approved) == (2, 1, 1)
    records = json.loads((tmp_path / preprocess.INDEX_FILENAME).read_text(encoding="utf-8"))["records"]
    unrenderable = next(record for record in records if record["street_id"] == "0001")
    assert unrenderable["svg_path"] is None
    assert unrenderable["preview_path"] is None
    assert unrenderable["success"] is True


def test_record_exception_isolated_resume_and_force(tmp_path: Path, monkeypatch) -> None:
    data = _dataset()
    calls = 0
    monkeypatch.setattr(
        preprocess, "production_triage_state",
        _states(ProductionTriageStatus.AUTO_APPROVED, ProductionTriageStatus.AUTO_APPROVED),
    )
    original = preprocess._preprocess_one

    def fail_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("broken test street")
        return original(*args, **kwargs)

    monkeypatch.setattr(preprocess, "_preprocess_one", fail_first)
    first = preprocess.preprocess_datasets([data], tmp_path, street_ids=["0001", "0002"])
    assert (first.processed, first.unexpected_errors, first.auto_approved) == (2, 1, 1)

    monkeypatch.setattr(preprocess, "_preprocess_one", original)
    monkeypatch.setattr(
        preprocess, "production_triage_state",
        _states(ProductionTriageStatus.AUTO_APPROVED, ProductionTriageStatus.AUTO_APPROVED),
    )
    second = preprocess.preprocess_datasets([data], tmp_path, street_ids=["0002"])
    assert (second.processed, second.reused) == (0, 1)

    forced = preprocess.preprocess_datasets([data], tmp_path, street_ids=["0002"], force=True)
    assert (forced.processed, forced.reused, forced.unexpected_errors) == (1, 0, 0)