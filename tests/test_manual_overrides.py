from __future__ import annotations

import json
from pathlib import Path

import pytest

from mug_previewer.manual import (
    ManualOverrideError,
    ManualPlacementOverride,
    ManualResolutionStatus,
    clear_manual_override,
    load_manual_overrides,
    save_manual_override,
)


def test_save_reload_and_replace_are_exact_and_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "manual_overrides.json"
    first = ManualPlacementOverride.approved_transform("dataset-a", "0001", "Same Road", orientation_deg=180, scale=0.95, y_offset=20)
    second = ManualPlacementOverride.approved_standard("dataset-a", "0001", "Same Road")
    other = ManualPlacementOverride.approved_transform("dataset-b", "0001", "Same Road", orientation_deg=0, scale=0.80, y_offset=-60)
    save_manual_override(first, path)
    save_manual_override(other, path)
    save_manual_override(second, path)

    store = load_manual_overrides(path)
    assert store.get("dataset-a", "0001") == second
    assert store.get("dataset-a", "0001").transform == (0, 1.0, 0)
    assert store.get("dataset-b", "0001") == other
    assert len(store.overrides) == 2
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert [row["dataset"] for row in document["overrides"]] == ["dataset-a", "dataset-b"]


@pytest.mark.parametrize(
    ("orientation", "scale", "y_offset"),
    ((90, 1.0, 0), (0, 0.87, 0), (0, 1.0, 10)),
)
def test_invalid_manual_transform_is_rejected(orientation: int, scale: float, y_offset: int) -> None:
    with pytest.raises(ManualOverrideError):
        ManualPlacementOverride.approved_transform(
            "dataset", "0001", "Review Road", orientation_deg=orientation, scale=scale, y_offset=y_offset,
        )


def test_clear_removes_decision_and_returns_pending_lookup(tmp_path: Path) -> None:
    path = tmp_path / "manual_overrides.json"
    save_manual_override(ManualPlacementOverride.approved_standard("dataset", "0001", "Review Road"), path)
    clear_manual_override("dataset", "0001", path)
    assert load_manual_overrides(path).get("dataset", "0001") is None


def test_unsupported_schema_and_duplicate_keys_fail_clearly(tmp_path: Path) -> None:
    path = tmp_path / "manual_overrides.json"
    path.write_text('{"version": 2, "overrides": []}', encoding="utf-8")
    with pytest.raises(ManualOverrideError, match="schema version"):
        load_manual_overrides(path)

    approved = ManualPlacementOverride.approved_standard("dataset", "0001", "Review Road").as_dict()
    path.write_text(json.dumps({"version": 1, "overrides": [approved, approved]}), encoding="utf-8")
    with pytest.raises(ManualOverrideError, match="duplicate"):
        load_manual_overrides(path)
