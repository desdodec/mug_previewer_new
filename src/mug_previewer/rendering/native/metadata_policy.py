"""
Street Face Generator v23.

V23 retains v22's card-centred gallery rendering and gives batch single-face
exports concise, Windows-safe, readable filenames.  The four-digit batch
number in each name is the lookup key in ``batch_single_export_report.csv``
and ``single_face_manifest_v23.csv``; the latter also records the effective
GUI/export settings.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import geometry_policy as v22


v20 = v22.v20

FILENAME_VERSION = "SF23"
MAX_STREET_SLUG_LENGTH = 46
MANIFEST_CSV_NAME = "single_face_manifest_v23.csv"
MANIFEST_JSON_NAME = "single_face_manifest_v23.json"

_active_single_export_context: Optional[Dict[str, object]] = None


def provenance_payload(area_name: str, generated_on: Optional[str] = None) -> str:
    """Build a v23 provenance payload while retaining the checksum scheme."""
    area = re.sub(r"\s+", " ", str(area_name or "unknown area").strip())
    generated = generated_on or date.today().isoformat()
    base = f"SFV23|AREA={area}|DATE={generated}"
    checksum = hashlib.sha256(
        f"{v20.signature_secret_id()}|{base}".encode("utf-8", errors="replace")
    ).hexdigest()[:10].upper()
    return f"{base}|CHK={checksum}"


def render_attribution_provenance_mark(*args: object, **kwargs: object) -> str:
    markup = v22.render_attribution_provenance_mark(*args, **kwargs)
    return markup.replace("street-face-v22", "street-face-v23")


def gallery_output_stem(
    postcode: str,
    town_or_city: str,
    district: str,
    edition: str,
    paper_key: str,
    face_count: int,
    cols: int,
    palette_key: str,
    show_blush: bool,
    ear_mode: str,
    presentation_mode: str,
    show_note: bool,
    street_stroke_multiplier: float,
    road_types: Optional[Sequence[str]] = None,
    forced_street_name: Optional[str] = None,
    single_street_name: Optional[str] = None,
    sort_by: str | None = "happiness",
) -> str:
    """Use a numbered, readable, Windows-safe name for each single export."""
    if not single_street_name:
        area_slug = v20.filename_slug(postcode)[:36]
        edition_slug = v20.filename_slug(v20.normalize_edition_key(edition))
        paper_slug = v20.filename_slug(paper_key)
        face_part = f"{face_count}_face" if face_count == 1 else f"{face_count}_faces"
        return f"{area_slug}_{edition_slug}_gallery_{face_part}_{cols}_cols_{paper_slug}"

    sequence = int((_active_single_export_context or {}).get("sequence", 1))
    area_slug = v20.filename_slug(postcode)[:36]
    edition_slug = v20.filename_slug(v20.normalize_edition_key(edition))
    street_slug = v20.filename_slug(single_street_name)[:MAX_STREET_SLUG_LENGTH]
    paper_slug = v20.filename_slug(paper_key)
    return (
        f"{sequence:04d}_{area_slug}_{edition_slug}_gallery_street_"
        f"{street_slug}_road_{paper_slug}"
    )


def _manifest_paths(output: Path) -> Tuple[Path, Path]:
    root = output.parent if output.suffix.lower() == ".svg" else output
    return root / MANIFEST_CSV_NAME, root / MANIFEST_JSON_NAME


def _load_manifest(path: Path) -> Dict[str, Dict[str, object]]:
    if not path.is_file():
        return {}
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(records, list):
        return {}
    return {
        str(record["code"]): record
        for record in records
        if isinstance(record, dict) and record.get("code")
    }


def _write_manifest(output: Path, records: Sequence[Dict[str, object]]) -> None:
    csv_path, json_path = _manifest_paths(output)
    existing = _load_manifest(json_path)
    for record in records:
        if record.get("code"):
            existing[str(record["code"])] = record
    merged = sorted(existing.values(), key=lambda record: (str(record.get("street_name", "")).lower(), str(record.get("code", ""))))
    json_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    fields = [
        "code",
        "street_name",
        "source_file",
        "svg_file",
        "pdf_file",
        "png_file",
        "session_curation_report_csv",
        "settings_json",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {field: record.get(field, "") for field in fields} for record in merged
        )


def _write_session_curation_report(
    output: Path, session_id: str, records: Sequence[Dict[str, object]]
) -> Path:
    """Write one concise curation report for every face in this batch."""
    report_csv = output / f"single_face_session_{session_id}_curation_report.csv"
    report_json = report_csv.with_suffix(".json")
    report_json.write_text(json.dumps(list(records), indent=2), encoding="utf-8")

    fieldnames = ["code", "source_file", "svg_file"]
    for record in records:
        for field in record:
            if field not in fieldnames:
                fieldnames.append(field)
    with report_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    return report_csv


def generate_single_faces_batch(
    input_folder: Path,
    output: Path,
    write_png: bool = False,
    write_pdf: bool = False,
    show_blush: Optional[bool] = None,
    ear_mode: Optional[str] = None,
    presentation_mode: Optional[str] = None,
    street_stroke_multiplier: float = 1.18,
    palette_key: str = v20.DEFAULT_PALETTE_KEY,
    edition: str = "roads",
    paper_key: Optional[str] = None,
    show_note: Optional[bool] = None,
    print_title_font_size: Optional[float] = None,
    print_subtitle_font_size: Optional[float] = None,
    curatorial_note_font_size: Optional[float] = None,
    text_font_key: str = v20.DEFAULT_TEXT_FONT_KEY,
    road_types: Optional[Sequence[str]] = None,
    sort_by: str | None = "happiness",
) -> Tuple[List[Path], List[Tuple[str, str]]]:
    """Export single faces with compact names and a settings lookup manifest."""
    global _active_single_export_context

    if not input_folder.is_dir():
        raise ValueError(f"Batch input is not a folder: {input_folder}")
    excluded_names = {"streets.svg", "overlay.svg", "faces_overlay.svg"}
    all_svg_files = sorted(
        path
        for path in input_folder.iterdir()
        if path.is_file() and path.suffix.lower() == ".svg"
    )
    input_files = [path for path in all_svg_files if path.name.lower() not in excluded_names]
    skipped: List[Tuple[str, str]] = [
        (path.name, "aggregate overlay SVG, not an individual street glyph")
        for path in all_svg_files
        if path.name.lower() in excluded_names
    ]
    if not input_files:
        raise ValueError(f"No individual street SVG files found in: {input_folder}")

    defaults = v20.edition_defaults(edition)
    selected_road_types = v20.normalize_road_types(road_types)
    effective_settings: Dict[str, object] = {
        "generator": "street_face_generator_v23",
        "filename_scheme": "NNNN_area_edition_gallery_street_name_road_paper",
        "edition": v20.normalize_edition_key(edition),
        "paper": paper_key or defaults["paper"],
        "target_count": 1,
        "columns": 1,
        "palette": palette_key,
        "show_blush": bool(defaults["show_blush"]) if show_blush is None else bool(show_blush),
        "ear_mode": v20.normalize_ear_mode(str(defaults["ear_mode"]) if ear_mode is None else ear_mode),
        "presentation_mode": v20.normalize_presentation_mode(presentation_mode),
        "street_stroke_multiplier": float(street_stroke_multiplier),
        "show_note": bool(defaults["note"]) if show_note is None else bool(show_note),
        "print_title_font_size": print_title_font_size,
        "print_subtitle_font_size": print_subtitle_font_size,
        "curatorial_note_font_size": curatorial_note_font_size,
        "text_font": text_font_key,
        "road_types": selected_road_types if selected_road_types is not None else ["all"],
        "sort_by": v20.normalize_gallery_sort_mode(sort_by),
        "write_png": bool(write_png),
        "write_pdf": bool(write_pdf),
    }

    palette = v20.get_face_palette(palette_key)
    gallery_specs = v20.build_specs(input_files, palette=palette, road_types=selected_road_types)
    written: List[Path] = []
    batch_records: List[Dict[str, str]] = []
    manifest_records: List[Dict[str, object]] = []
    session_curation_records: List[Dict[str, object]] = []
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    session_curation_name = f"single_face_session_{session_id}_curation_report.csv"
    output.mkdir(parents=True, exist_ok=True)
    previous_stem_builder = v20.gallery_output_stem
    v20.gallery_output_stem = gallery_output_stem
    try:
        for sequence, source in enumerate(input_files, start=1):
            _active_single_export_context = {
                "sequence": sequence,
            }
            try:
                svg_path, png_path, report_path, specs = v20.generate_faces(
                    [source],
                    output,
                    cols=1,
                    write_png=write_png,
                    write_pdf=write_pdf,
                    show_blush=show_blush,
                    ear_mode=ear_mode,
                    presentation_mode=presentation_mode,
                    street_stroke_multiplier=street_stroke_multiplier,
                    palette_key=palette_key,
                    edition=edition,
                    paper_key=paper_key,
                    target_count=1,
                    show_note=show_note,
                    print_title_font_size=print_title_font_size,
                    print_subtitle_font_size=print_subtitle_font_size,
                    curatorial_note_font_size=curatorial_note_font_size,
                    text_font_key=text_font_key,
                    road_types=selected_road_types,
                    sort_by=sort_by,
                    gallery_context_specs=gallery_specs,
                    write_faces_overlay=False,
                )
                code = svg_path.stem.split("_", 1)[0]
                pdf_path = svg_path.with_suffix(".pdf") if write_pdf else None
                individual_curation_json = report_path.with_suffix(".json")
                curation_records = json.loads(
                    individual_curation_json.read_text(encoding="utf-8")
                )
                for curation_record in curation_records:
                    session_curation_records.append(
                        {
                            "code": code,
                            "source_file": source.name,
                            "svg_file": svg_path.name,
                            **curation_record,
                        }
                    )
                # These temporary one-face reports are folded into the session
                # report below, so they never clutter a singles output folder.
                report_path.unlink(missing_ok=True)
                individual_curation_json.unlink(missing_ok=True)
                written.append(svg_path)
                batch_records.append(
                    {
                        "item": source.name,
                        "status": "written",
                        "reason": "",
                        "code": code,
                        "svg": str(svg_path),
                        "session_curation_report": session_curation_name,
                    }
                )
                manifest_records.append(
                    {
                        "code": code,
                        "street_name": specs[0].name if specs else source.stem,
                        "source_file": str(source),
                        "svg_file": svg_path.name,
                        "pdf_file": pdf_path.name if pdf_path else "",
                        "png_file": png_path.name if png_path else "",
                        "session_curation_report_csv": session_curation_name,
                        "settings_json": json.dumps(effective_settings, sort_keys=True),
                    }
                )
            except Exception as exc:
                reason = str(exc)
                skipped.append((source.name, reason))
                batch_records.append(
                    {
                        "item": source.name,
                        "status": "skipped",
                        "reason": reason,
                        "code": "",
                        "svg": "",
                        "session_curation_report": "",
                    }
                )
    finally:
        _active_single_export_context = None
        v20.gallery_output_stem = previous_stem_builder

    for item, reason in skipped:
        if not any(record["item"] == item for record in batch_records):
            batch_records.append(
                {
                    "item": item,
                    "status": "skipped",
                    "reason": reason,
                    "code": "",
                    "svg": "",
                    "session_curation_report": "",
                }
            )
    report_path = output / "batch_single_export_report.csv"
    with report_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "item",
                "status",
                "reason",
                "code",
                "svg",
                "session_curation_report",
            ],
        )
        writer.writeheader()
        writer.writerows(batch_records)
    _write_session_curation_report(output, session_id, session_curation_records)
    _write_manifest(output, manifest_records)
    return written, skipped


def _install_v23_policy() -> None:
    v20.__doc__ = __doc__
    v20.DEFAULT_SIGNATURE_ID = "street-face-generator-v23-private-mark"
    v20.provenance_payload = provenance_payload
    v20.render_attribution_provenance_mark = render_attribution_provenance_mark
    v20.gallery_output_stem = gallery_output_stem
    v20.generate_single_faces_batch = generate_single_faces_batch


_install_v23_policy()


def __getattr__(name: str) -> object:
    """Expose v22's compatible public API through the v23 module."""
    return getattr(v20, name)


def main() -> None:
    v20.main()


if __name__ == "__main__":
    main()
