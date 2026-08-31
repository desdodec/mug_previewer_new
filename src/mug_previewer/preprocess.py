"""Resumable preparation and approval of editable face assets."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import cairosvg
from PIL import Image

from .datasets.models import Dataset, StreetRecord
from .diagnostics.front_candidates import ProductionTriageStatus
from .rendering.face import FACE_SVG_GENERATOR, FRONT_PANEL_PX, SOURCE_CANVAS_PX, FaceRenderOptions, render_face_svg
from .ui.production import production_triage_state

PREPROCESS_GENERATOR = "mug-previewer/preprocess"
PREPROCESS_GENERATOR_VERSION = "1"
FACE_GENERATOR_VERSION = "V28.1"
SVG_IMPORT_GENERATOR = "mug-previewer/manual-svg-import"
SVG_IMPORT_VERSION = "1"
INDEX_FILENAME = "preprocess_index.json"


class SvgApprovalError(ValueError):
    """Raised when an external SVG cannot become approved face artwork."""


@dataclass(frozen=True)
class PreprocessSummary:
    processed: int = 0
    reused: int = 0
    auto_approved: int = 0
    manual_review: int = 0
    manual_approved: int = 0
    unrenderable_input: int = 0
    unexpected_errors: int = 0


@dataclass(frozen=True)
class FaceSvgResolution:
    """The current face artwork and whether it is approved for production."""

    state: ProductionTriageStatus
    path: Path | None
    production_approved: bool


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
            existing = by_key.get(key)
            if _is_manually_approved(existing, root):
                try:
                    _ensure_approved_preview(existing, root)
                except Exception as error:
                    print(f"Warning {dataset.display_name} / {street.id}: approved preview could not be refreshed: {error}")
                summary = _count(summary, existing, reused=True)
                print(f"Reused protected manual approval {dataset.display_name} / {street.id} / {street.display_name}")
                continue
            try:
                state, reason_codes = production_triage_state(dataset, street)
                fingerprint = _fingerprint(dataset, street, state, reason_codes)
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


def approve_manual_svg(
    dataset: Dataset,
    street: StreetRecord,
    preprocessed: Path | str,
    supplied_svg: Path | str,
    *,
    approved_at: datetime | None = None,
) -> FaceSvgResolution:
    """Store a validated human edit as the authoritative artwork for one review street."""
    root = Path(preprocessed)
    index_path = root / INDEX_FILENAME
    records = _load_index(index_path)
    key = (dataset.id, street.id)
    record = next((item for item in records if _record_key(item) == key), None)
    if record is None:
        raise SvgApprovalError(f"No preprocessing index record exists for {dataset.display_name} / {street.id}.")
    state = _record_state(record)
    if state not in (ProductionTriageStatus.MANUAL_REVIEW, ProductionTriageStatus.MANUAL_APPROVED):
        raise SvgApprovalError("Only MANUAL_REVIEW or MANUAL_APPROVED records may receive an approved SVG.")

    source_path = Path(supplied_svg)
    try:
        payload = source_path.read_bytes()
    except OSError as error:
        raise SvgApprovalError(f"Cannot read supplied SVG: {error}") from error
    _validate_approved_svg(payload, dataset, street)

    generated_relative = _preserve_generated_svg(record, root)
    approved_relative = _approved_svg_relative(generated_relative)
    approved_path = root / approved_relative
    approved_path.parent.mkdir(parents=True, exist_ok=True)
    approved_path.write_bytes(payload)
    preview_relative = _preview_relative(record, generated_relative)
    _write_preview(payload, root / preview_relative)

    timestamp = approved_at or datetime.now(timezone.utc)
    record.update({
        "production_state": ProductionTriageStatus.MANUAL_APPROVED.value,
        "reason_detail": "Human-edited SVG accepted.",
        "generated_svg_path": generated_relative.as_posix(),
        "approved_svg_path": approved_relative.as_posix(),
        "svg_path": approved_relative.as_posix(),
        "preview_path": preview_relative.as_posix(),
        "approved_at": timestamp.astimezone(timezone.utc).isoformat(),
        "approved_svg_sha256": hashlib.sha256(payload).hexdigest(),
        "svg_import_generator": SVG_IMPORT_GENERATOR,
        "svg_import_version": SVG_IMPORT_VERSION,
        "success": True,
        "error_message": None,
    })
    _write_index(index_path, records)
    return FaceSvgResolution(ProductionTriageStatus.MANUAL_APPROVED, approved_path, True)


def resolve_authoritative_face_svg(
    preprocessed: Path | str,
    dataset: Dataset,
    street: StreetRecord,
) -> FaceSvgResolution:
    """Resolve the indexed face artwork without inferring manual edit transforms."""
    root = Path(preprocessed)
    record = next((item for item in _load_index(root / INDEX_FILENAME) if _record_key(item) == (dataset.id, street.id)), None)
    if record is None or not record.get("success"):
        return FaceSvgResolution(ProductionTriageStatus.UNRENDERABLE_INPUT, None, False)
    state = _record_state(record)
    if state is ProductionTriageStatus.UNRENDERABLE_INPUT:
        return FaceSvgResolution(state, None, False)
    relative = record.get("approved_svg_path") if state is ProductionTriageStatus.MANUAL_APPROVED else record.get("svg_path")
    path = root / relative if isinstance(relative, str) else None
    if path is not None and not path.is_file():
        path = None
    return FaceSvgResolution(state, path, state in (ProductionTriageStatus.AUTO_APPROVED, ProductionTriageStatus.MANUAL_APPROVED))


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
    suffix = ".generated" if state is ProductionTriageStatus.MANUAL_REVIEW else ""
    svg_relative = Path("faces") / relative_base.with_name(relative_base.name + suffix).with_suffix(".svg")
    preview_relative = Path("previews") / relative_base.with_suffix(".png")
    svg = render_face_svg(dataset, street, FaceRenderOptions(area=dataset.display_name))
    svg_path = root / svg_relative
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg, encoding="utf-8")
    _write_preview(svg, root / preview_relative)
    extra = {"generated_svg_path": svg_relative.as_posix()} if state is ProductionTriageStatus.MANUAL_REVIEW else {}
    return record | extra | {
        "success": True,
        "svg_path": svg_relative.as_posix(),
        "preview_path": preview_relative.as_posix(),
        "error_message": None,
    }


def _validate_approved_svg(payload: bytes, dataset: Dataset, street: StreetRecord) -> None:
    if not payload or len(payload) > 10 * 1024 * 1024:
        raise SvgApprovalError("Supplied SVG must be non-empty and no larger than 10 MiB.")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise SvgApprovalError(f"Supplied SVG is not valid XML: {error}") from error
    if _local_name(root.tag) != "svg":
        raise SvgApprovalError("Supplied XML document is not an SVG.")
    _validate_svg_dimensions(root)
    prohibited = {"script", "foreignObject", "iframe", "object", "embed", "applet"}
    for element in root.iter():
        if _local_name(element.tag) in prohibited:
            raise SvgApprovalError(f"Supplied SVG contains prohibited <{_local_name(element.tag)}> content.")
        for attribute, value in element.attrib.items():
            if _local_name(attribute).casefold().startswith("on"):
                raise SvgApprovalError("Supplied SVG contains an event-handler attribute.")
            _validate_svg_reference(str(value))
        if element.text:
            _validate_svg_reference(element.text)
    _validate_svg_metadata(root, dataset, street)


def _validate_svg_dimensions(root: ET.Element) -> None:
    expected_width, expected_height = SOURCE_CANVAS_PX
    width = _svg_number(root.get("width"))
    height = _svg_number(root.get("height"))
    if width != expected_width or height != expected_height:
        raise SvgApprovalError(f"Supplied SVG must retain {expected_width}x{expected_height} dimensions.")
    view_box = root.get("viewBox")
    if view_box is None:
        raise SvgApprovalError("Supplied SVG must retain its viewBox.")
    try:
        values = tuple(float(value) for value in re.split(r"[ ,]+", view_box.strip()))
    except ValueError as error:
        raise SvgApprovalError("Supplied SVG has an invalid viewBox.") from error
    if values != (0.0, 0.0, float(expected_width), float(expected_height)):
        raise SvgApprovalError(f"Supplied SVG must retain viewBox 0 0 {expected_width} {expected_height}.")


def _svg_number(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*", value)
    return float(match.group(1)) if match else None


def _validate_svg_reference(value: str) -> None:
    lowered = value.casefold()
    if any(token in lowered for token in ("javascript:", "http:", "https:", "file:", "data:", "@import", "expression(", "//")):
        raise SvgApprovalError("Supplied SVG contains a remote or executable reference.")


def _validate_svg_metadata(root: ET.Element, dataset: Dataset, street: StreetRecord) -> None:
    metadata = next((element for element in root.iter() if _local_name(element.tag) == "metadata" and element.get("id") == "mug-previewer-metadata"), None)
    if metadata is None:
        return
    try:
        values = json.loads(metadata.text or "")
    except json.JSONDecodeError as error:
        raise SvgApprovalError("Supplied SVG has invalid Mug Previewer metadata.") from error
    expected = {"dataset_id": dataset.id, "street_id": street.id, "street_name": street.display_name}
    for key, expected_value in expected.items():
        if values.get(key) != expected_value:
            raise SvgApprovalError(f"Supplied SVG metadata {key} does not match the selected street.")


def _preserve_generated_svg(record: dict[str, object], root: Path) -> Path:
    existing = record.get("generated_svg_path")
    if isinstance(existing, str) and (root / existing).is_file():
        return Path(existing)
    source = record.get("svg_path")
    if not isinstance(source, str) or not (root / source).is_file():
        raise SvgApprovalError("The existing generated review SVG is unavailable for preservation.")
    source_relative = Path(source)
    generated = source_relative.with_name(source_relative.stem + ".generated.svg")
    target = root / generated
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / source_relative, target)
    return generated


def _approved_svg_relative(generated: Path) -> Path:
    stem = generated.stem.removesuffix(".generated")
    return generated.with_name(stem + ".approved.svg")


def _preview_relative(record: dict[str, object], generated: Path) -> Path:
    existing = record.get("preview_path")
    if isinstance(existing, str):
        return Path(existing)
    return Path("previews") / generated.with_suffix(".png").name


def _write_preview(svg: str | bytes, path: Path) -> None:
    """Rasterise an editable SVG to the existing 495 x 462 screen panel."""
    payload = svg.encode("utf-8") if isinstance(svg, str) else svg
    png = cairosvg.svg2png(bytestring=payload, output_width=990, output_height=462)
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
        "dataset_id": dataset.id, "dataset_name": dataset.display_name, "street_id": street.id,
        "street_name": street.display_name, "production_state": None,
        "reason_detail": "Unexpected preprocessing error.", "source_fingerprint": None,
        "generator": PREPROCESS_GENERATOR, "generator_version": PREPROCESS_GENERATOR_VERSION,
        "face_generator": FACE_SVG_GENERATOR, "face_generator_version": FACE_GENERATOR_VERSION,
        "success": False, "svg_path": None, "preview_path": None,
        "error_message": f"{type(error).__name__}: {error}",
    }


def _fingerprint(dataset: Dataset, street: StreetRecord, state: ProductionTriageStatus, reason_codes: tuple[str, ...]) -> str:
    source = {
        "dataset_id": dataset.id, "dataset_name": dataset.display_name, "street_id": street.id,
        "street_name": street.display_name, "glyph_sha256": _file_digest(street.glyph_path),
        "production_state": state.value, "reason_codes": reason_codes,
        "generator": PREPROCESS_GENERATOR, "generator_version": PREPROCESS_GENERATOR_VERSION,
        "face_generator": FACE_SVG_GENERATOR, "face_generator_version": FACE_GENERATOR_VERSION,
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


def _is_manually_approved(record: dict[str, object] | None, root: Path) -> bool:
    if record is None or record.get("production_state") != ProductionTriageStatus.MANUAL_APPROVED.value:
        return False
    approved = record.get("approved_svg_path")
    return isinstance(approved, str) and (root / approved).is_file()


def _ensure_approved_preview(record: dict[str, object], root: Path) -> None:
    preview, approved = record.get("preview_path"), record.get("approved_svg_path")
    if isinstance(preview, str) and (root / preview).is_file():
        return
    if isinstance(preview, str) and isinstance(approved, str):
        _write_preview((root / approved).read_bytes(), root / preview)


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


def _record_state(record: dict[str, object]) -> ProductionTriageStatus:
    try:
        return ProductionTriageStatus(str(record.get("production_state")))
    except ValueError as error:
        raise SvgApprovalError("Preprocessing index record has no valid production state.") from error


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _slug(value: str) -> str:
    result = "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_")
    return result or "street"


def _count(summary: PreprocessSummary, record: dict[str, object], *, reused: bool = False) -> PreprocessSummary:
    state = record.get("production_state")
    return PreprocessSummary(
        processed=summary.processed + (0 if reused else 1), reused=summary.reused + int(reused),
        auto_approved=summary.auto_approved + int(state == ProductionTriageStatus.AUTO_APPROVED.value),
        manual_review=summary.manual_review + int(state == ProductionTriageStatus.MANUAL_REVIEW.value),
        manual_approved=summary.manual_approved + int(state == ProductionTriageStatus.MANUAL_APPROVED.value),
        unrenderable_input=summary.unrenderable_input + int(state == ProductionTriageStatus.UNRENDERABLE_INPUT.value),
        unexpected_errors=summary.unexpected_errors + int(not record.get("success")),
    )