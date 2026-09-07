"""Exact-artwork QA state; browser results are authoritative, ledger is a snapshot."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile

REVIEW_STATUSES = ("pass", "overlap", "duplicates", "missing", "other", "Do Not Use")
REVIEW_FILENAME = "review_index.json"


@dataclass(frozen=True)
class ReviewRecord:
    dataset_id: str
    street_id: str
    status: str
    reviewed_svg_sha256: str
    reviewed_at: str
    note: str = ""


@dataclass(frozen=True)
class ReviewState:
    record: ReviewRecord | None
    stale: bool = False
    error: str | None = None
    source_fingerprint: tuple = ()

    @property
    def export_blocked(self) -> bool:
        return not self.production_export_allowed

    @property
    def production_export_allowed(self) -> bool:
        return self.record is not None and not self.error and not self.stale and self.record.status == "pass"

    @property
    def state(self) -> str:
        if self.error:
            return "AMBIGUOUS_REVIEW" if "AMBIGUOUS_REVIEW" in self.error else "INVALID_REVIEW"
        if self.record is None:
            return "NO_REVIEW"
        if self.stale:
            return "STALE_REVIEW"
        return "PASS" if self.record.status == "pass" else "BLOCKED_STATUS"

    @property
    def label(self) -> str:
        if self.error:
            return f"QA: {self.error} - export blocked"
        if self.record is None:
            return "QA: NOT REVIEWED - no human QA review; export blocked"
        suffix = " (stale) - artwork changed; QA attention required" if self.stale else ""
        return f"QA: {self.record.status}{suffix} - " + ("production export allowed" if self.production_export_allowed else "export blocked; requires current pass")


def svg_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_review_index(root: Path) -> dict[tuple[str, str], ReviewRecord]:
    path = Path(root) / REVIEW_FILENAME
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Review index is invalid; repair the ledger before saving or exporting.")
    result = {}
    for item in payload["records"]:
        # Ignore unknown fields from newer versions, but never silently discard QA.
        if not isinstance(item, dict):
            raise ValueError("Invalid review record.")
        fields = {key: item[key] for key in ReviewRecord.__dataclass_fields__ if key in item}
        try:
            record = ReviewRecord(**fields)
        except TypeError as error:
            raise ValueError("Incomplete review record.") from error
        if record.status not in REVIEW_STATUSES:
            raise ValueError(f"Unsupported review status: {record.status}")
        fields = (record.dataset_id, record.street_id, record.reviewed_svg_sha256, record.reviewed_at)
        if not all(isinstance(v, str) and v for v in fields) or not isinstance(record.note, str):
            raise ValueError('Invalid review fields.')
        if (record.dataset_id, record.street_id) in result:
            raise ValueError('Duplicate review identity.')
        result[(record.dataset_id, record.street_id)] = record
    return result


def get_review_record(root: Path, dataset_id: str, street_id: str) -> ReviewRecord | None:
    return load_review_index(root).get((dataset_id, street_id))


def current_review_state(root: Path, dataset_id: str, street_id: str, svg: Path | None) -> ReviewState:
    from .review_results import read_review_results, review_hash
    try:
        entries, errors, fingerprint = read_review_results(root)
        key = dataset_id, street_id
        if key in errors:
            return ReviewState(None, error=errors[key], source_fingerprint=fingerprint)
        if key not in entries:
            return ReviewState(None, source_fingerprint=fingerprint)
        item, _ = entries[key]
        record = ReviewRecord(dataset_id, street_id, item['status'], review_hash(item),
                              item.get('reviewed_at', ''), item.get('note', ''))
        try:
            current = svg_sha256(svg) if svg else None
        except OSError:
            current = None
        return ReviewState(record, record.reviewed_svg_sha256 != current, source_fingerprint=fingerprint)
    except (OSError, ValueError) as error:
        return ReviewState(None, error='INVALID_REVIEW: ' + str(error))


def save_review_record(root: Path, dataset_id: str, street_id: str, status: str,
                       svg: Path, note: str = "", *, expected_sha256: str | None = None) -> ReviewRecord:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Unsupported review status: {status}")
    if not all(isinstance(v, str) and v for v in (dataset_id, street_id)) or not isinstance(note, str):
        raise ValueError('Invalid review identity or note.')
    svg = Path(svg)
    svg_bytes = svg.read_bytes()
    if expected_sha256 is not None and hashlib.sha256(svg_bytes).hexdigest() != expected_sha256:
        raise ValueError("Artwork changed since preview; preview it again before saving the review.")
    relative_svg = str(svg.resolve().relative_to(Path(root).resolve())).replace('\\', '/')
    record = ReviewRecord(dataset_id, street_id, status, hashlib.sha256(svg_bytes).hexdigest(),
                          datetime.now(timezone.utc).isoformat(), note)
    from .review_results import read_review_results
    entries, errors, _ = read_review_results(root)
    key = dataset_id, street_id
    if any(k != key or 'AMBIGUOUS_REVIEW' in error for k, error in errors.items()):
        raise ValueError('Review results are invalid; resolve errors before saving.')
    records = load_review_index(root)
    records[(dataset_id, street_id)] = record
    payload = {"version": 1, "records": [asdict(records[key]) for key in sorted(records)]}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                         prefix=".review-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, Path(root) / REVIEW_FILENAME)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    # Update the same human source used by browser ingestion. Never make a
    # competing review when a legacy path already identifies this street.
    existing = entries.get((dataset_id, street_id))
    source = existing[1] if existing else Path(root) / 'svg_review_results.json'
    items = json.loads(source.read_text(encoding='utf-8-sig')) if source.exists() else []
    value = (dict(existing[0]) if existing else {}) | asdict(record) | {
        'svg_name': svg.name, 'svg_path': relative_svg,
        'preview_data_url': 'data:image/svg+xml;base64,' + base64.b64encode(svg_bytes).decode('ascii')}
    if existing:
        items[items.index(existing[0])] = value
    else:
        items.append(value)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root,
                                         prefix='.review-results-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(items, stream, indent=2, ensure_ascii=False)
        os.replace(temporary, source)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return record
