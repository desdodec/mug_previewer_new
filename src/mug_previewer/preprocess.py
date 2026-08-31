"""Resumable preparation of editable face assets for the desktop previewer."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cairosvg
from PIL import Image

from .datasets.models import Dataset, StreetRecord
from .diagnostics.front_candidates import ProductionTriageStatus
from .rendering.face import FACE_SVG_GENERATOR, FRONT_PANEL_PX, FaceRenderOptions, render_face_svg
from .ui.production import production_triage_state

PREPROCESS_GENERATOR = "mug-previewer/preprocess"
PREPROCESS_GENERATOR_VERSION = "1"
FACE_GENERATOR_VERSION = "V28.1"
INDEX_FILENAME = "preprocess_index.json"


@dataclass(frozen=True)
class PreprocessSummary:
    processed: int = 0
    reused: int = 0
    auto_approved: int = 0
    manual_review: int = 0
    unrenderable_input: int = 0
    unexpected_errors: int = 0


def preprocess_datasets(
    datasets: Iterable[Dataset],
    output: Path | str,
    *,
    street_ids: Iterable[str] | None = None,
    force: bool = False,
) -> PreprocessSummary:
    """Prepare selected streets, recording an isolated outcome for each one."""
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / INDEX_FILENAME
    records = _load_index(index_path)
    by_key = {_record_key(record): record for record in records if _record_key(record) is not None}
    selected_ids = {str(item) for item in street_ids} if street_ids is not None else None
    summary = PreprocessSummary()

    for dataset in datasets:
        for street in dataset.streets:
            if selected_ids is not None and street.id not in selected_ids:
                continue
            key = (dataset.id, street.id)
            try:
                state, reason_codes = production_triage_state(dataset, street)
                fingerprint = _fingerprint(dataset, street, state, reason_codes)
                existing = by_key.get(key)
                if not force and _is_reusable(existing, root, fingerprint):
                    summary = _count(summary, existing, reused=True)
                    print(f"Reused {dataset.display_name} / {street.id} / {street.display_name}")
                    continue
                record = _preprocess_one(dataset, street, root, state, reason_codes, fingerprint)
                by_key[key] = record
                summary = _count(summary, record)
                print(f"Processed {dataset.display_name} / {street.id} / {street.display_name}: {record['production_state']}")
            except Exception as error:  # One bad street must not terminate a batch.
                record = _unexpected_error_record(dataset, street, error)
                by_key[key] = record
                summary = _count(summary, record)
                print(f"Error {dataset.display_name} / {street.id} / {street.display_name}: {error}")

    _write_index(index_path, by_key.values())
    return summary


def _preprocess_one(
    dataset: Dataset,
    street: StreetRecord,
    root: Path,
    state: ProductionTriageStatus,
    reason_codes: tuple[str, ...],
    fingerprint: str,
) -> dict[str, object]:
    record = _base_record(dataset, street, state, reason_codes, fingerprint)
    if state is ProductionTriageStatus.UNRENDERABLE_INPUT:
        return record | {"success": True, "svg_path": None, "preview_path": None, "error_message": None}

    relative_base = Path(_slug(dataset.id)) / f"{street.id}_{_slug(street.display_name)}"
    svg_relative = Path("faces") / relative_base.with_suffix(".svg")
    preview_relative = Path("previews") / relative_base.with_suffix(".png")
    svg = render_face_svg(dataset, street, FaceRenderOptions(area=dataset.display_name))
    svg_path = root / svg_relative
    preview_path = root / preview_relative
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg, encoding="utf-8")
    _write_preview(svg, preview_path)
    return record | {
        "success": True,
        "svg_path": svg_relative.as_posix(),
        "preview_path": preview_relative.as_posix(),
        "error_message": None,
    }


def _write_preview(svg: str, path: Path) -> None:
    """Rasterise the editable SVG to the existing 495 x 462 screen panel."""
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=990, output_height=462)
    with Image.open(io.BytesIO(png)) as rendered:
        preview = rendered.convert("RGBA").crop((0, 0, FRONT_PANEL_PX[0], FRONT_PANEL_PX[1]))
        path.parent.mkdir(parents=True, exist_ok=True)
        preview.save(path, format="PNG", optimize=True)


def _base_record(
    dataset: Dataset, street: StreetRecord, state: ProductionTriageStatus,
    reason_codes: tuple[str, ...], fingerprint: str,
) -> dict[str, object]:
    return {
        "dataset_id": dataset.id,
        "dataset_name": dataset.display_name,
        "street_id": street.id,
        "street_name": street.display_name,
        "production_state": state.value,
        "reason_detail": ";".join(reason_codes),
        "source_fingerprint": fingerprint,
        "generator": PREPROCESS_GENERATOR,
        "generator_version": PREPROCESS_GENERATOR_VERSION,
        "face_generator": FACE_SVG_GENERATOR,
        "face_generator_version": FACE_GENERATOR_VERSION,
    }


def _unexpected_error_record(dataset: Dataset, street: StreetRecord, error: Exception) -> dict[str, object]:
    return {
        "dataset_id": dataset.id,
        "dataset_name": dataset.display_name,
        "street_id": street.id,
        "street_name": street.display_name,
        "production_state": None,
        "reason_detail": "Unexpected preprocessing error.",
        "source_fingerprint": None,
        "generator": PREPROCESS_GENERATOR,
        "generator_version": PREPROCESS_GENERATOR_VERSION,
        "face_generator": FACE_SVG_GENERATOR,
        "face_generator_version": FACE_GENERATOR_VERSION,
        "success": False,
        "svg_path": None,
        "preview_path": None,
        "error_message": f"{type(error).__name__}: {error}",
    }


def _fingerprint(dataset: Dataset, street: StreetRecord, state: ProductionTriageStatus, reason_codes: tuple[str, ...]) -> str:
    source = {
        "dataset_id": dataset.id,
        "dataset_name": dataset.display_name,
        "street_id": street.id,
        "street_name": street.display_name,
        "glyph_sha256": _file_digest(street.glyph_path),
        "production_state": state.value,
        "reason_codes": reason_codes,
        "generator": PREPROCESS_GENERATOR,
        "generator_version": PREPROCESS_GENERATOR_VERSION,
        "face_generator": FACE_SVG_GENERATOR,
        "face_generator_version": FACE_GENERATOR_VERSION,
    }
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_reusable(record: dict[str, object] | None, root: Path, fingerprint: str) -> bool:
    if record is None or not record.get("success") or record.get("source_fingerprint") != fingerprint:
        return False
    if record.get("generator") != PREPROCESS_GENERATOR or record.get("generator_version") != PREPROCESS_GENERATOR_VERSION:
        return False
    if record.get("production_state") == ProductionTriageStatus.UNRENDERABLE_INPUT.value:
        return record.get("svg_path") is None and record.get("preview_path") is None
    svg_path, preview_path = record.get("svg_path"), record.get("preview_path")
    return isinstance(svg_path, str) and isinstance(preview_path, str) and (root / svg_path).is_file() and (root / preview_path).is_file()


def _load_index(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise ValueError("missing records list")
        return [item for item in payload["records"] if isinstance(item, dict)]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read preprocessing index {path}: {error}") from error


def _write_index(path: Path, records: Iterable[dict[str, object]]) -> None:
    ordered = sorted(records, key=lambda item: (str(item.get("dataset_name", "")).casefold(), str(item.get("street_id", ""))))
    payload = {"format": "mug-previewer-preprocess-index-v1", "records": ordered}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _record_key(record: dict[str, object]) -> tuple[str, str] | None:
    dataset_id, street_id = record.get("dataset_id"), record.get("street_id")
    return (dataset_id, street_id) if isinstance(dataset_id, str) and isinstance(street_id, str) else None


def _slug(value: str) -> str:
    result = "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_")
    return result or "street"


def _count(summary: PreprocessSummary, record: dict[str, object], *, reused: bool = False) -> PreprocessSummary:
    state = record.get("production_state")
    return PreprocessSummary(
        processed=summary.processed + (0 if reused else 1),
        reused=summary.reused + int(reused),
        auto_approved=summary.auto_approved + int(state == ProductionTriageStatus.AUTO_APPROVED.value),
        manual_review=summary.manual_review + int(state == ProductionTriageStatus.MANUAL_REVIEW.value),
        unrenderable_input=summary.unrenderable_input + int(state == ProductionTriageStatus.UNRENDERABLE_INPUT.value),
        unexpected_errors=summary.unexpected_errors + int(not record.get("success")),
    )