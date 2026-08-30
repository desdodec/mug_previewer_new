"""Production-readiness and summary helpers for the desktop application."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Iterable, Literal

from ..datasets.models import Dataset, StreetRecord
from ..diagnostics.front_candidates import ProductionTriageStatus, select_production_placement
from ..manual import DEFAULT_MANUAL_OVERRIDE_PATH, ManualResolutionStatus, load_manual_overrides

Readiness = Literal["ready", "manual_review", "unrenderable"]

_REASON_TEXT = {
    "missing_static_nose": "Required face anatomy is missing.",
    "missing_glyph": "The street artwork is missing.",
    "unsupported_title_width": "The street name is too long for the supported production layout.",
    "standard_nose_overlap": "The street-mouth placement needs human review.",
    "standard_typography_overlap": "The street overlaps required typography.",
    "unresolved_geometry": "No automatic placement was confident enough.",
}


@dataclass(frozen=True)
class ProductionStatus:
    readiness: Readiness
    title: str
    detail: str
    export_allowed: bool
    review_required: bool
    resolution_label: str | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductionSummary:
    auto_standard: int = 0
    auto_adapted: int = 0
    manual_standard: int = 0
    manual_override: int = 0
    pending_manual_review: int = 0
    unrenderable: int = 0

    @property
    def ready(self) -> int:
        return self.auto_standard + self.auto_adapted + self.manual_standard + self.manual_override


@dataclass(frozen=True)
class UnrenderableItem:
    dataset_name: str
    street_id: str
    street_name: str
    reason: str


def production_status(
    dataset: Dataset,
    street: StreetRecord,
    *,
    override_path: Path | str = DEFAULT_MANUAL_OVERRIDE_PATH,
) -> ProductionStatus:
    """Classify one selected street with concise user-facing wording."""
    scope_row = _production_scope_index().get((dataset.display_name, street.id))
    if scope_row is not None:
        return _status_from_triage(
            ProductionTriageStatus(scope_row["triage_status"]), scope_row["production_placement"] or None,
            _scope_reason_codes(scope_row), dataset, street, override_path,
        )
    decision = select_production_placement(street, area=dataset.display_name)
    return _status_from_triage(
        decision.triage_status, decision.placement_class, decision.reason_codes, dataset, street, override_path,
    )


def _status_from_triage(
    triage_status: ProductionTriageStatus,
    placement_class: str | None,
    reason_codes: tuple[str, ...],
    dataset: Dataset,
    street: StreetRecord,
    override_path: Path | str,
) -> ProductionStatus:
    """Map a validated triage result or an on-demand result to UI status."""
    if triage_status is ProductionTriageStatus.UNRENDERABLE_INPUT:
        return ProductionStatus("unrenderable", "Cannot Render", _reason_text(reason_codes), False, False, reason_codes=reason_codes)
    if triage_status is ProductionTriageStatus.MANUAL_REVIEW:
        override = load_manual_overrides(override_path).get(dataset.id, street.id)
        if override is not None and override.approved:
            if override.status is ManualResolutionStatus.APPROVED_STANDARD:
                label = "Manually approved — Standard placement"
            else:
                label = f"Manually approved — Edited placement {override.orientation_deg} degrees / {override.scale:.2f} / Y{override.y_offset:+d}"
            return ProductionStatus("ready", "Ready for Production", label, True, False, label, reason_codes)
        return ProductionStatus(
            "manual_review", "Manual Review Required", "Manual review is required before production export.",
            False, True, reason_codes=reason_codes,
        )
    label = "Ready for production"
    if placement_class == "ADAPTED":
        label = "Ready for production — automatic placement applied"
    return ProductionStatus("ready", "Ready for Production", label, True, False, reason_codes=reason_codes)


@lru_cache(maxsize=1)
def _production_scope_index() -> dict[tuple[str, str], dict[str, str]]:
    """Return the reviewed release manifest keyed by display name and street ID."""
    rows = json.loads(files("mug_previewer.ui").joinpath("manual_review_scope.json").read_text(encoding="utf-8"))
    return {(row["dataset"], row["street_id"]): row for row in rows}


def _scope_reason_codes(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(filter(None, row["reason_codes"].split(";")))

def production_summary(
    datasets: Iterable[Dataset],
    *,
    override_path: Path | str = DEFAULT_MANUAL_OVERRIDE_PATH,
) -> ProductionSummary:
    """Build summary counts from the accepted production scope and override store."""
    by_name = {dataset.display_name: dataset for dataset in datasets}
    overrides = load_manual_overrides(override_path)
    counts = ProductionSummary()
    for row in json.loads(files("mug_previewer.ui").joinpath("manual_review_scope.json").read_text(encoding="utf-8")):
        dataset = by_name.get(row["dataset"])
        if dataset is None:
            continue
        status = row["triage_status"]
        if status == ProductionTriageStatus.AUTO_APPROVED.value:
            if row["production_placement"] == "ADAPTED":
                counts = _replace(counts, auto_adapted=counts.auto_adapted + 1)
            else:
                counts = _replace(counts, auto_standard=counts.auto_standard + 1)
        elif status == ProductionTriageStatus.UNRENDERABLE_INPUT.value:
            counts = _replace(counts, unrenderable=counts.unrenderable + 1)
        else:
            override = overrides.get(dataset.id, row["street_id"])
            if override is None or not override.approved:
                counts = _replace(counts, pending_manual_review=counts.pending_manual_review + 1)
            elif override.status is ManualResolutionStatus.APPROVED_STANDARD:
                counts = _replace(counts, manual_standard=counts.manual_standard + 1)
            else:
                counts = _replace(counts, manual_override=counts.manual_override + 1)
    return counts


def production_unrenderable_items(datasets: Iterable[Dataset]) -> tuple[UnrenderableItem, ...]:
    """Return accepted-scope input failures without putting them in Manual Review."""
    available = {dataset.display_name for dataset in datasets}
    rows = json.loads(files("mug_previewer.ui").joinpath("manual_review_scope.json").read_text(encoding="utf-8"))
    return tuple(
        UnrenderableItem(
            row["dataset"], row["street_id"], row["street_name"],
            _reason_text(tuple(filter(None, row["reason_codes"].split(",")))),
        )
        for row in rows
        if row["dataset"] in available
        and row["triage_status"] == ProductionTriageStatus.UNRENDERABLE_INPUT.value
    )


def _replace(summary: ProductionSummary, **changes: int) -> ProductionSummary:
    values = summary.__dict__ | changes
    return ProductionSummary(**values)


def _reason_text(codes: tuple[str, ...]) -> str:
    return " ".join(_REASON_TEXT.get(code, code.replace("_", " ").capitalize()) for code in codes) or "This street cannot currently be rendered."
