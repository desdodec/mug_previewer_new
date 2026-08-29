from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image

from mug_previewer.datasets.models import StreetRecord
from mug_previewer.diagnostics import manual_review
from mug_previewer.diagnostics.front_candidates import (
    CandidateGrid,
    FaceAnatomyMasks,
    ProductionPlacementDecision,
    ProductionTriageStatus,
    select_production_placement_from_masks,
)


def _mask(points: set[tuple[int, int]]) -> Image.Image:
    image = Image.new("L", (200, 200), 0)
    for point in points:
        image.putpixel(point, 255)
    return image


def _street(street_id: str = "0001") -> StreetRecord:
    return StreetRecord(street_id, None, "Review Road", "Review Road", Path("glyph.svg"))


def _manual_decision() -> ProductionPlacementDecision:
    masks = FaceAnatomyMasks(
        _mask({(100, 100)}), _mask({(5, 5)}), _mask({(195, 5)}),
        _mask({(100, 100)}), _mask({(195, 195)}), Image.new("RGBA", (200, 200)),
    )
    decision, _ranked = select_production_placement_from_masks(
        masks, grid=CandidateGrid(orientations_deg=(0,), scales=(1.0,), x_offsets=(0,), y_offsets=(0, 20)),
    )
    assert decision.triage_status is ProductionTriageStatus.MANUAL_REVIEW
    return decision


def test_manual_review_creates_first_class_item_with_candidate() -> None:
    item = manual_review.make_manual_review_item("Fixture", _street(), _manual_decision())
    assert item is not None
    assert item.standard_candidate.nose_overlap_pixels == 1
    assert item.candidate.y_offset == 20
    assert item.reason_codes == ("standard_nose_overlap", "adaptation_low_confidence")


def test_automatic_and_unrenderable_results_do_not_create_manual_items() -> None:
    manual = _manual_decision()
    automatic = ProductionPlacementDecision(
        ProductionTriageStatus.AUTO_APPROVED, "STANDARD", manual.standard,
        ("standard_healthy",), "STANDARD", manual.standard, manual.best_candidate,
    )
    unrenderable = ProductionPlacementDecision(
        ProductionTriageStatus.UNRENDERABLE_INPUT, None, None,
        ("missing_glyph",), None,
    )
    assert manual_review.make_manual_review_item("Fixture", _street(), automatic) is None
    assert manual_review.make_manual_review_item("Fixture", _street(), unrenderable) is None


def test_manifest_preserves_reason_codes_transform_and_deterministic_name(tmp_path: Path, monkeypatch) -> None:
    decision = _manual_decision()
    monkeypatch.setattr(manual_review, "select_production_placement", lambda _street, *, area: decision)
    monkeypatch.setattr(
        manual_review, "write_comparison_artifact",
        lambda _street, _item, path, *, area: path.write_bytes(b"comparison"),
    )
    batch = manual_review.write_manual_review_batch(
        "Fixture Dataset", (_street("0002"), _street("0001")), tmp_path, area="Fixture",
    )
    with batch.manifest_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["street_id"] for row in rows] == ["0001", "0002"]
    assert all(row["reason_codes"] == "standard_nose_overlap;adaptation_low_confidence" for row in rows)
    assert all(row["candidate_y_offset"] == "20" for row in rows)
    assert all((tmp_path / row["comparison_image"]).is_file() for row in rows)
    assert len(rows) == len(batch.items) == len(list(batch.comparison_dir.glob("*.png")))
    assert manual_review.comparison_filename(batch.items[0]) == "fixture_dataset_0001_review_road.png"
