"""Human QA ledger, independent of production classification and artwork files."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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

    @property
    def export_blocked(self) -> bool:
        return self.record is not None and (self.stale or self.record.status != "pass")

    @property
    def label(self) -> str:
        if self.record is None:
            return "Review: not reviewed"
        suffix = " (stale) - artwork changed; QA attention required" if self.stale else ""
        return f"Review: {self.record.status}{suffix}"


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
        result[(record.dataset_id, record.street_id)] = record
    return result


def get_review_record(root: Path, dataset_id: str, street_id: str) -> ReviewRecord | None:
    return load_review_index(root).get((dataset_id, street_id))


def current_review_state(root: Path, dataset_id: str, street_id: str, svg: Path | None) -> ReviewState:
    record = get_review_record(root, dataset_id, street_id)
    if record is None:
        return ReviewState(None)
    try:
        current = svg_sha256(svg) if svg else None
    except OSError:
        current = None
    return ReviewState(record, record.reviewed_svg_sha256 != current)


def save_review_record(root: Path, dataset_id: str, street_id: str, status: str,
                       svg: Path, note: str = "") -> ReviewRecord:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Unsupported review status: {status}")
    record = ReviewRecord(dataset_id, street_id, status, svg_sha256(svg),
                          datetime.now(timezone.utc).isoformat(), note)
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
    return record
