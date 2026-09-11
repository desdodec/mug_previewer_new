"""Inspectable batches; rendering belongs to the single-item exporter."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata

from .datasets.models import Dataset
from .design import DesignOptions
from .prepared_asset import resolve_prepared_face_source
from .preprocess import INDEX_FILENAME, resolve_authoritative_face_svg
from .preprocessed_export import export_preprocessed_provider_png
from .providers import get_provider_profile
from .review_index import current_review_state


class BatchPlanningError(ValueError):
    """The batch cannot safely be planned."""


class Eligibility(str, Enum):
    READY = 'READY'
    MANUAL_REVIEW = 'MANUAL_REVIEW'
    QA_BLOCKED = 'QA_BLOCKED'
    EXCLUDED = 'EXCLUDED'
    UNRENDERABLE = 'UNRENDERABLE'
    ASSET_ERROR = 'ASSET_ERROR'


@dataclass(frozen=True)
class BatchExportItem:
    dataset_id: str
    street_id: str
    street_name: str
    production_state: str
    eligibility: Eligibility
    reason: str | None = None
    authoritative_svg: Path | None = None
    authoritative_svg_sha256: str | None = None
    review_status: str | None = None
    review_state: str | None = None
    reviewed_svg_sha256: str | None = None
    review_stale: bool = False
    review_fingerprint: str | None = None
    record_fingerprint: str | None = None
    destination: Path | None = None
    destination_exists: bool = False


@dataclass(frozen=True)
class BatchExportSummary:
    total: int
    ready: int
    manual_review: int
    qa_blocked: int
    excluded: int
    unrenderable: int
    asset_errors: int
    existing: int
    not_reviewed: int = 0


@dataclass(frozen=True)
class BatchExportPlan:
    root: Path
    dataset: Dataset
    provider_id: str
    destination_root: Path
    items: tuple[BatchExportItem, ...]
    summary: BatchExportSummary
    replace_existing: bool = False
    design_options: DesignOptions | None = None

    @property
    def output_directory(self):
        return self.destination_root / self.provider_id


@dataclass(frozen=True)
class BatchItemResult:
    item: BatchExportItem
    result: str
    reason: str | None = None


@dataclass(frozen=True)
class BatchProgress:
    current: int
    total: int
    result: BatchItemResult


@dataclass(frozen=True)
class BatchExportResult:
    results: tuple[BatchItemResult, ...]
    report_path: Path
    summary: dict
    cancelled: bool


def sanitize_filename(value: str) -> str:
    """Keep Unicode, removing Windows separators, controls and devices."""
    value = unicodedata.normalize('NFC', value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', value)
    value = re.sub(r'[\s_]+', '_', value).strip(' ._') or 'unnamed'
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                *(f'LPT{i}' for i in range(1, 10))}
    return '_' + value if value.split('.')[0].upper() in reserved else value


def production_filename(dataset_id: str, street_id: str, street_name: str) -> str:
    return '_'.join(sanitize_filename(v) for v in (dataset_id, street_id, street_name)) + '.png'


def _records(root):
    try:
        payload = json.loads((root / INDEX_FILENAME).read_text(encoding='utf-8-sig'))
        if not isinstance(payload, dict) or not isinstance(payload.get('records'), list):
            raise ValueError('missing records list')
        records = {}
        for item in payload['records']:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k]
                                                    for k in ('dataset_id', 'street_id')):
                raise ValueError('record has no valid dataset/street identity')
            key = item['dataset_id'], item['street_id']
            if key in records:
                raise ValueError(f'duplicate record: {key}')
            records[key] = item
        return records
    except (OSError, ValueError) as error:
        raise BatchPlanningError(f'Preprocessing index is invalid: {error}') from error


def _validate_qa(root):
    from .review_results import read_review_results
    try:
        entries, errors, _ = read_review_results(root)
        if errors:
            raise ValueError('; '.join(errors.values()))
        return entries
    except (OSError, ValueError) as error:
        raise BatchPlanningError('QA ledger is invalid. Repair svg_review_results*.json before production export.') from error


def _inspect(root, dataset, street_id, record, directory):
    street = dataset.get_street(street_id)
    name = street.display_name if street else str(record.get('street_name') or street_id)
    item = BatchExportItem(dataset.id, street_id, name, str(record.get('production_state', '')),
                           Eligibility.ASSET_ERROR, record_fingerprint=json.dumps(record, sort_keys=True))
    try:
        if street is None:
            raise ValueError('Prepared street is missing from the selected source dataset')
        if record.get('production_state') == 'UNRENDERABLE_INPUT':
            return replace(item, eligibility=Eligibility.UNRENDERABLE, reason='Source/input unusable')
        if not record.get('success'):
            raise ValueError('Preprocessing record reports an asset error')
        resolution = resolve_authoritative_face_svg(root, dataset, street)
        record = _records(root)[(dataset.id, street_id)]
        item = replace(item, production_state=resolution.state.value,
                       record_fingerprint=json.dumps(record, sort_keys=True))
        if resolution.state.value == 'UNRENDERABLE_INPUT':
            return replace(item, eligibility=Eligibility.UNRENDERABLE, reason='Source/input unusable')
        source = resolve_prepared_face_source(root, record)
        review = current_review_state(root, dataset.id, street_id, source.review_path)
        item = replace(item, authoritative_svg=source.path,
                       authoritative_svg_sha256=source.digest,
                       review_status=review.record.status if review.record else None,
                       review_state=review.state,
                       reviewed_svg_sha256=review.record.reviewed_svg_sha256 if review.record else None,
                       review_stale=review.stale,
                       review_fingerprint=json.dumps(asdict(review), sort_keys=True))
        if review.export_blocked:
            return replace(item, eligibility=Eligibility.ASSET_ERROR if review.error else Eligibility.EXCLUDED,
                           reason=review.label)
        destination = directory / production_filename(dataset.id, street_id, name)
        if len(destination.name.encode('utf-16-le')) // 2 > 255:
            raise ValueError('Production filename exceeds the filesystem limit')
        return replace(item, eligibility=Eligibility.READY, destination=destination,
                       destination_exists=destination.exists())
    except Exception as error:
        return replace(item, eligibility=Eligibility.ASSET_ERROR, reason=str(error))


def build_batch_plan(root, dataset, provider_id, destination_root, *,
                     replace_existing=False, design_options=None):
    get_provider_profile(provider_id)
    if destination_root is None or not str(destination_root).strip():
        raise BatchPlanningError('Choose a destination folder first')
    root, destination_root = Path(root).resolve(), Path(destination_root).resolve()
    records = _records(root)
    _validate_qa(root)
    directory = destination_root / provider_id
    items = tuple(_inspect(root, dataset, sid, record, directory)
                  for (did, sid), record in sorted(records.items()) if did == dataset.id)
    destinations = {}
    for item in items:
        if item.destination is not None:
            key = str(item.destination).casefold()
            if key in destinations:
                other = destinations[key]
                raise BatchPlanningError(f'Filename collision: {other.street_id} ({other.street_name}) and '
                                         f'{item.street_id} ({item.street_name}): {item.destination.name}')
            destinations[key] = item
    counts = {c: sum(i.eligibility == c for i in items) for c in Eligibility}
    summary = BatchExportSummary(len(items), *(counts[c] for c in Eligibility),
                                 sum(i.destination_exists for i in items),
                                 sum(i.eligibility == Eligibility.QA_BLOCKED and i.review_state == "NO_REVIEW" for i in items))
    return BatchExportPlan(root, dataset, provider_id, destination_root, items, summary,
                           replace_existing, design_options)


def _recheck(plan, item):
    _validate_qa(plan.root)
    record = _records(plan.root).get((item.dataset_id, item.street_id))
    if record is None:
        raise ValueError('Artwork or QA changed after batch planning; rebuild the batch plan.')
    current = _inspect(plan.root, plan.dataset, item.street_id, record, plan.output_directory)
    fields = ('eligibility', 'production_state', 'authoritative_svg', 'authoritative_svg_sha256',
              'review_fingerprint', 'review_stale', 'record_fingerprint')
    if any(getattr(current, key) != getattr(item, key) for key in fields):
        raise ValueError('Artwork or QA changed after batch planning; rebuild the batch plan.')
    return current


def _atomic_report(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.batch-report-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def execute_batch_export(plan, *, on_progress=None, cancel_event=None):
    started = datetime.now(timezone.utc).isoformat()
    plan.output_directory.mkdir(parents=True, exist_ok=True)
    results = []
    progress_errors = []
    attempted = 0
    cancelled = False
    for item in plan.items:
        if item.eligibility != Eligibility.READY:
            results.append(BatchItemResult(item, 'NOT_ELIGIBLE', item.reason))
            continue
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            results.append(BatchItemResult(item, 'CANCELLED', 'Cancelled before this item started'))
            continue
        try:
            current = _recheck(plan, item)
            if item.destination.exists() and not plan.replace_existing:
                result = BatchItemResult(item, 'SKIPPED_EXISTING', 'Destination already exists; left unchanged')
            else:
                with tempfile.TemporaryDirectory(dir=plan.output_directory, prefix='.batch-') as staging:
                    temporary = Path(staging) / item.destination.name
                    export_preprocessed_provider_png(plan.root, plan.dataset,
                        plan.dataset.get_street(item.street_id), temporary,
                        profile_id=plan.provider_id, design_options=plan.design_options)
                    _recheck(plan, current)
                    if plan.replace_existing:
                        os.replace(temporary, item.destination)
                    else:
                        try:
                            os.link(temporary, item.destination)
                        except FileExistsError:
                            result = BatchItemResult(item, 'SKIPPED_EXISTING',
                                                     'Destination appeared during export; left unchanged')
                        else:
                            result = BatchItemResult(current, 'EXPORTED')
                    if plan.replace_existing:
                        result = BatchItemResult(current, 'EXPORTED')
        except Exception as error:
            result = BatchItemResult(item, 'FAILED', str(error))
        results.append(result)
        attempted += 1
        if on_progress is not None:
            try:
                on_progress(BatchProgress(attempted, plan.summary.ready, result))
            except Exception as error:
                progress_errors.append(str(error))
    summary = asdict(plan.summary) | {key.lower(): sum(r.result == key for r in results)
                                    for key in ('EXPORTED', 'FAILED', 'SKIPPED_EXISTING', 'CANCELLED')}
    payload = dict(version=1, provider_id=plan.provider_id, dataset_id=plan.dataset.id,
                   started_at=started, completed_at=datetime.now(timezone.utc).isoformat(),
                   cancelled=cancelled, replace_existing=plan.replace_existing, summary=summary,
                   progress_errors=progress_errors,
                   items=[asdict(r.item) | {'authoritative_svg': str(r.item.authoritative_svg) if r.item.authoritative_svg else None,
                          'destination': str(r.item.destination) if r.item.destination else None,
                          'result': r.result, 'reason': r.reason} for r in results])
    report_path = plan.output_directory / 'batch_export_report.json'
    _atomic_report(report_path, payload)
    return BatchExportResult(tuple(results), report_path, summary, cancelled)
