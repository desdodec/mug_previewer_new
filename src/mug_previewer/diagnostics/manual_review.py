"""Operational handoff for conservative front-placement manual review."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageColor, ImageDraw

from ..datasets.models import StreetRecord
from ..rendering import face
from ..rendering.native import face_policy as native
from .front_candidates import (
    CandidateResult,
    FaceAnatomyMasks,
    ProductionPlacementDecision,
    ProductionTriageStatus,
    render_production_masks,
    select_production_placement,
    transform_street_mask,
)

MANIFEST_FIELDS = (
    "dataset", "street_id", "street_name", "triage_status", "diagnostic_class",
    "standard_orientation", "standard_scale", "standard_x_offset", "standard_y_offset",
    "candidate_orientation", "candidate_scale", "candidate_x_offset", "candidate_y_offset",
    "reason_codes", "standard_score", "candidate_score", "score_delta",
    "standard_nose_overlap", "standard_nose_clearance",
    "candidate_nose_overlap", "candidate_nose_clearance",
    "standard_typography_clearance", "candidate_typography_clearance",
    "comparison_image",
)


@dataclass(frozen=True)
class ManualReviewItem:
    """All information required for a later constrained human edit."""

    dataset: str
    street_id: str
    street_name: str
    triage_status: ProductionTriageStatus
    diagnostic_class: str | None
    standard_candidate: CandidateResult
    best_candidate: CandidateResult | None
    reason_codes: tuple[str, ...]
    comparison_artifact: Path | None = None

    @property
    def candidate(self) -> CandidateResult:
        return self.best_candidate or self.standard_candidate


@dataclass(frozen=True)
class ManualReviewBatch:
    items: tuple[ManualReviewItem, ...]
    counts: dict[ProductionTriageStatus, int]
    auto_standard: int
    auto_adapted: int
    manifest_path: Path
    comparison_dir: Path
    summary_path: Path
    unrenderable_path: Path


def make_manual_review_item(
    dataset: str, street: StreetRecord, decision: ProductionPlacementDecision,
) -> ManualReviewItem | None:
    """Make a review item only for a genuine MANUAL_REVIEW outcome."""
    if decision.triage_status is not ProductionTriageStatus.MANUAL_REVIEW:
        return None
    if decision.standard is None:
        raise ValueError("MANUAL_REVIEW must retain its standard candidate.")
    return ManualReviewItem(
        dataset, street.id, street.display_name, decision.triage_status,
        decision.diagnostic_class, decision.standard, decision.best_candidate,
        decision.reason_codes,
    )


def comparison_filename(item: ManualReviewItem) -> str:
    """Return a stable filesystem-safe name based only on street identity."""
    return "_".join((_slug(item.dataset), _slug(item.street_id), _slug(item.street_name))) + ".png"


def write_manual_review_batch(
    dataset: str, streets: Iterable[StreetRecord], output_dir: Path, *, area: str = "",
) -> ManualReviewBatch:
    """Triage all input, retaining review and unrenderable paths separately."""
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_dir = output_dir / "comparisons"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    counts = {status: 0 for status in ProductionTriageStatus}
    auto_standard = 0
    auto_adapted = 0
    items: list[ManualReviewItem] = []
    unrenderable_rows: list[dict[str, str]] = []

    for street in streets:
        decision = select_production_placement(street, area=area)
        counts[decision.triage_status] += 1
        auto_standard += decision.placement_class == "STANDARD"
        auto_adapted += decision.placement_class == "ADAPTED"
        item = make_manual_review_item(dataset, street, decision)
        if item is not None:
            artifact = comparison_dir / comparison_filename(item)
            write_comparison_artifact(street, item, artifact, area=area)
            items.append(replace(item, comparison_artifact=artifact))
        elif decision.triage_status is ProductionTriageStatus.UNRENDERABLE_INPUT:
            unrenderable_rows.append({
                "dataset": dataset, "street_id": street.id, "street_name": street.display_name,
                "triage_status": decision.triage_status.value,
                "reason_codes": ";".join(decision.reason_codes),
            })

    ordered_items = tuple(sorted(items, key=lambda item: (item.dataset, item.street_id, item.street_name)))
    manifest_path = output_dir / "manual_review_manifest.csv"
    _write_csv(manifest_path, (_manifest_row(item, output_dir) for item in ordered_items), MANIFEST_FIELDS)
    unrenderable_path = output_dir / "unrenderable_input.csv"
    _write_csv(unrenderable_path, unrenderable_rows, ("dataset", "street_id", "street_name", "triage_status", "reason_codes"))
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps({
        "counts": {status.value: counts[status] for status in ProductionTriageStatus},
        "manual_review_manifest": manifest_path.name,
        "comparison_directory": comparison_dir.name,
        "unrenderable_input": unrenderable_path.name,
        "manual_review_items": len(ordered_items),
        "comparison_artifacts": sum(item.comparison_artifact is not None for item in ordered_items),
        "auto_standard": auto_standard,
        "auto_adapted": auto_adapted,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ManualReviewBatch(
        ordered_items, counts, auto_standard, auto_adapted,
        manifest_path, comparison_dir, summary_path, unrenderable_path,
    )


def write_comparison_artifact(
    street: StreetRecord, item: ManualReviewItem, path: Path, *, area: str = "",
) -> None:
    """Show two non-approved renderings: canonical STANDARD and best candidate."""
    standard_image = face._render_face_standard(street, face.FaceRenderOptions(area=area))
    masks = render_production_masks(street, area=area, base=standard_image)
    image = _comparison_canvas(
        item,
        _render_candidate_preview(masks.base, masks, item.standard_candidate),
        _render_candidate_preview(masks.base, masks, item.candidate),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def _render_candidate_preview(base: Image.Image, masks: FaceAnatomyMasks, result: CandidateResult) -> Image.Image:
    image = base.copy()
    feature = ImageColor.getrgb(native.get_face_palette(native.DEFAULT_PALETTE_KEY).feature) + (255,)
    image.paste((255, 255, 255, 255), mask=masks.street_mouth)
    for protected in (masks.left_eye, masks.right_eye, masks.static_nose, masks.typography):
        image.paste(feature, mask=protected)
    street_mask, _clipped = transform_street_mask(masks.street_mouth, result.candidate)
    image.paste(feature, mask=street_mask)
    return image


def _comparison_canvas(item: ManualReviewItem, standard: Image.Image, candidate: Image.Image) -> Image.Image:
    top, footer = 38, 148
    canvas = Image.new("RGBA", (standard.width + candidate.width, top + standard.height + footer), "white")
    canvas.alpha_composite(standard, (0, top))
    canvas.alpha_composite(candidate, (standard.width, top))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, canvas.width - 1, canvas.height - 1), outline="black")
    draw.line((standard.width, 0, standard.width, top + standard.height), fill="black")
    draw.text((8, 10), "STANDARD - NOT AUTO-APPROVED", fill="black")
    draw.text((standard.width + 8, 10), "BEST DIAGNOSTIC CANDIDATE - NOT AUTO-APPROVED", fill="black")
    lines = (
        f"Street: {item.dataset} / {item.street_id} / {item.street_name}",
        "Reason codes: " + ", ".join(item.reason_codes),
        _candidate_text("STANDARD", item.standard_candidate),
        _candidate_text("CANDIDATE", item.candidate),
    )
    for index, line in enumerate(lines):
        draw.text((8, top + standard.height + 8 + index * 31), line, fill="black")
    return canvas


def _candidate_text(label: str, result: CandidateResult) -> str:
    return (
        f"{label}: {result.orientation_deg} deg scale {result.scale:.2f} x {result.x_offset:+d} y {result.y_offset:+d}; "
        f"score {result.score:.2f}; nose overlap {result.nose_overlap_pixels}; "
        f"nose clearance {result.nose_min_distance_px:.2f}; mouth-role {result.mouth_role_penalty:.2f}"
    )


def _manifest_row(item: ManualReviewItem, output_dir: Path) -> dict[str, str | float | int]:
    standard, candidate = item.standard_candidate, item.candidate
    comparison = "" if item.comparison_artifact is None else item.comparison_artifact.relative_to(output_dir).as_posix()
    return {
        "dataset": item.dataset, "street_id": item.street_id, "street_name": item.street_name,
        "triage_status": item.triage_status.value, "diagnostic_class": item.diagnostic_class or "",
        "standard_orientation": standard.orientation_deg, "standard_scale": standard.scale,
        "standard_x_offset": standard.x_offset, "standard_y_offset": standard.y_offset,
        "candidate_orientation": candidate.orientation_deg, "candidate_scale": candidate.scale,
        "candidate_x_offset": candidate.x_offset, "candidate_y_offset": candidate.y_offset,
        "reason_codes": ";".join(item.reason_codes),
        "standard_score": standard.score, "candidate_score": candidate.score,
        "score_delta": candidate.score - standard.score,
        "standard_nose_overlap": standard.nose_overlap_pixels,
        "standard_nose_clearance": standard.nose_min_distance_px,
        "candidate_nose_overlap": candidate.nose_overlap_pixels,
        "candidate_nose_clearance": candidate.nose_min_distance_px,
        "standard_typography_clearance": standard.typography_min_distance_px,
        "candidate_typography_clearance": candidate.typography_min_distance_px,
        "comparison_image": comparison,
    }


def _write_csv(path: Path, rows: Iterable[dict[str, object]], fields: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "street"
