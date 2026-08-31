from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus
import pytest

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

def test_manual_svg_approval_preserves_review_artwork_and_preprocess_protects_it(tmp_path: Path, monkeypatch) -> None:
    data = _dataset()
    street = data.get_street("0001")
    assert street is not None
    monkeypatch.setattr(preprocess, "production_triage_state", _states(ProductionTriageStatus.MANUAL_REVIEW))
    preprocess.preprocess_datasets([data], tmp_path, street_ids=[street.id])
    record = json.loads((tmp_path / preprocess.INDEX_FILENAME).read_text(encoding="utf-8"))["records"][0]
    generated = tmp_path / record["generated_svg_path"]
    original_review_svg = generated.read_text(encoding="utf-8")
    preview = tmp_path / record["preview_path"]
    original_preview = preview.read_bytes()
    edited = tmp_path / "edited.svg"
    edited_svg = original_review_svg.replace("</svg>", '<rect id="manual-test-mark" x="4" y="4" width="12" height="12" fill="#008000"/></svg>')
    edited.write_text(edited_svg, encoding="utf-8")

    resolution = preprocess.approve_manual_svg(data, street, tmp_path, edited)

    index_record = json.loads((tmp_path / preprocess.INDEX_FILENAME).read_text(encoding="utf-8"))["records"][0]
    approved = tmp_path / index_record["approved_svg_path"]
    assert resolution == preprocess.FaceSvgResolution(ProductionTriageStatus.MANUAL_APPROVED, approved, True)
    assert index_record["production_state"] == "MANUAL_APPROVED"
    assert generated.read_text(encoding="utf-8") == original_review_svg
    assert approved.read_text(encoding="utf-8") == edited_svg
    assert index_record["approved_svg_sha256"] == hashlib.sha256(edited.read_bytes()).hexdigest()
    assert index_record["svg_import_generator"] == preprocess.SVG_IMPORT_GENERATOR
    assert index_record["approved_at"]
    assert preview.is_file() and preview.read_bytes() != original_preview
    assert preprocess.resolve_authoritative_face_svg(tmp_path, data, street) == resolution

    monkeypatch.setattr(preprocess, "production_triage_state", _states(ProductionTriageStatus.MANUAL_REVIEW))
    rerun = preprocess.preprocess_datasets([data], tmp_path, street_ids=[street.id], force=True)
    assert (rerun.processed, rerun.reused, rerun.manual_approved) == (0, 1, 1)
    assert approved.read_text(encoding="utf-8") == edited_svg
    assert generated.read_text(encoding="utf-8") == original_review_svg


def test_invalid_and_cross_street_manual_svg_are_rejected(tmp_path: Path, monkeypatch) -> None:
    data = _dataset()
    street = data.get_street("0001")
    other = data.get_street("0002")
    assert street is not None and other is not None
    monkeypatch.setattr(preprocess, "production_triage_state", _states(ProductionTriageStatus.MANUAL_REVIEW))
    preprocess.preprocess_datasets([data], tmp_path, street_ids=[street.id])

    invalid = tmp_path / "invalid.svg"
    invalid.write_text('<svg width="990" height="462" viewBox="0 0 990 462"><script/></svg>', encoding="utf-8")
    with pytest.raises(preprocess.SvgApprovalError, match="prohibited"):
        preprocess.approve_manual_svg(data, street, tmp_path, invalid)

    cross_street = tmp_path / "other.svg"
    remote = tmp_path / "remote.svg"
    remote.write_text(
        '<svg width="990" height="462" viewBox="0 0 990 462"><image href="//example.invalid/face.png"/></svg>',
        encoding="utf-8",
    )
    with pytest.raises(preprocess.SvgApprovalError, match="remote"):
        preprocess.approve_manual_svg(data, street, tmp_path, remote)

    cross_street.write_text(preprocess.render_face_svg(data, other), encoding="utf-8")
    with pytest.raises(preprocess.SvgApprovalError, match="street_id"):
        preprocess.approve_manual_svg(data, street, tmp_path, cross_street)