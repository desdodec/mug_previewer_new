"""Canonical artwork transactions and private recovery history."""
from functools import wraps
from pathlib import Path
import tempfile
from threading import RLock
import xml.etree.ElementTree as ET

_lock = RLock()


def serialized(function):
    @wraps(function)
    def call(*args, **kwargs):
        with _lock:
            return function(*args, **kwargs)
    return call


def canonical_relative(record):
    source = Path(record['svg_path'])
    stem = source.stem.removesuffix('.approved').removesuffix('.generated').removesuffix('_edit')
    parent = source.parent.parent if source.parent.name == 'corrected' else source.parent
    return parent / (stem + '.svg')


def history_paths(canonical):
    folder = canonical.parent / '.history' / canonical.stem
    return folder / 'generated.svg', folder / 'last-good.svg'


def _layout_reference_candidates(record, root, canonical):
    """Return same-street legacy files that may carry canonical page placement."""
    root, canonical = Path(root), Path(canonical)
    original, good = history_paths(canonical)
    generated = record.get('generated_svg_path')
    candidates = [canonical]
    if isinstance(generated, str) and generated:
        candidates.append(root / generated)
    candidates.extend((
        good,
        original,
        canonical.with_name(canonical.stem + '.generated.svg'),
        canonical.with_name(canonical.stem + '_edit.svg'),
        canonical.parent / 'corrected' / (canonical.stem + '_edit.svg'),
        canonical.parent / 'corrected' / canonical.name,
    ))
    seen = set()
    for candidate in candidates:
        candidate = Path(candidate)
        marker = candidate.resolve()
        if marker in seen:
            continue
        seen.add(marker)
        yield candidate


def _canonicalized_payload(record, root, canonical, source_path, payload):
    """Return valid canonical bytes, repairing page drift from a safe layout reference."""
    from .preprocess import SvgApprovalError, validate_manual_svg
    try:
        validate_manual_svg(payload)
        return payload
    except SvgApprovalError as validation_error:
        validation_failure = validation_error

    try:
        edited = payload.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise validation_failure

    from .svg_edit_repair import corrected_svg
    source_path = Path(source_path).resolve()
    for reference in _layout_reference_candidates(record, root, canonical):
        if reference.resolve() == source_path or not reference.is_file():
            continue
        try:
            reference_payload = reference.read_bytes()
            validate_manual_svg(reference_payload)
            repaired = corrected_svg(
                reference_payload.decode('utf-8-sig'), edited
            ).encode('utf-8')
            validate_manual_svg(repaired)
        except (OSError, UnicodeDecodeError, ValueError, ET.ParseError):
            continue
        return repaired
    raise validation_failure


def preserve_original(record, root):
    from .preprocess import _atomic_asset_write, SvgApprovalError
    canonical = canonical_relative(record)
    original, _ = history_paths(root / canonical)
    if not original.is_file():
        source = root / str(record.get('generated_svg_path') or canonical)
        if not source.is_file():
            raise SvgApprovalError('Asset integrity error: generated original is unavailable.')
        _atomic_asset_write(original, source.read_bytes())
    return original.relative_to(root)


@serialized
def migrate_legacy_records(root, *, key=None):
    """Publish legacy authoritative bytes and preview before switching the index."""
    from .preprocess import (_load_index, INDEX_FILENAME, _write_index,
                             _atomic_asset_write, _write_preview)
    root = Path(root)
    records = _load_index(root / INDEX_FILENAME)
    count = 0
    for record in records:
        if key is not None and (record.get('dataset_id'), record.get('street_id')) != key:
            continue
        if not record.get('success') or not record.get('svg_path'):
            continue
        canonical = canonical_relative(record)
        source = record.get('approved_svg_path') if record.get('production_state') == 'MANUAL_APPROVED' else None
        source = source or record['svg_path']
        if source == canonical.as_posix() and record['svg_path'] == canonical.as_posix():
            continue
        source_path = root / source
        payload = _canonicalized_payload(record, root, root / canonical, source_path, source_path.read_bytes())
        original = preserve_original(record, root)
        preview = Path(record.get('preview_path') or (Path('previews') / canonical.with_suffix('.png').name))
        with tempfile.TemporaryDirectory(dir=root) as staging:
            staged = Path(staging) / 'preview.png'
            _write_preview(payload, staged)
            preview_bytes = staged.read_bytes()
        _, good = history_paths(root / canonical)
        targets = {root / canonical: payload, root / preview: preview_bytes, good: payload}
        previous = {path: path.read_bytes() if path.exists() else None for path in targets}
        old_record = record.copy()
        try:
            for path, content in targets.items():
                _atomic_asset_write(path, content)
            record.update(svg_path=canonical.as_posix(), generated_svg_path=original.as_posix(),
                          preview_path=preview.as_posix())
            record.pop('approved_svg_path', None)
            _write_index(root / INDEX_FILENAME, records)
        except Exception:
            record.clear()
            record.update(old_record)
            for path, content in previous.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_asset_write(path, content)
            raise
        count += 1
    return count


@serialized
def prepare_edit(root, dataset_id, street_id):
    from .preprocess import (_load_index, INDEX_FILENAME, _write_index, _atomic_asset_write,
                             _write_preview, validate_manual_svg)
    root = Path(root)
    migrate_legacy_records(root, key=(dataset_id, street_id))
    records = _load_index(root / INDEX_FILENAME)
    record = next(r for r in records if (r.get('dataset_id'), r.get('street_id')) == (dataset_id, street_id))
    canonical = root / canonical_relative(record)
    original, good = history_paths(canonical)

    # Older Inkscape sessions could save an A-series document page around otherwise
    # valid face artwork. Repair only the page/outer placement from a same-street
    # canonical reference, keeping the edited artwork itself.
    payload = canonical.read_bytes()
    canonical_payload = _canonicalized_payload(record, root, canonical, canonical, payload)
    if canonical_payload != payload:
        preview_value = record.get('preview_path')
        preview = root / preview_value if isinstance(preview_value, str) and preview_value else None
        preview_bytes = None
        if preview is not None:
            with tempfile.TemporaryDirectory(dir=root) as staging:
                staged = Path(staging) / 'preview.png'
                _write_preview(canonical_payload, staged)
                preview_bytes = staged.read_bytes()
        previous = {
            path: path.read_bytes() if path.exists() else None
            for path in (canonical, original, good, *((preview,) if preview is not None else ()))
        }
        old_record = record.copy()
        try:
            _atomic_asset_write(canonical, canonical_payload)
            generated_relative = preserve_original(record, root)
            _atomic_asset_write(good, canonical_payload)
            if preview is not None and preview_bytes is not None:
                _atomic_asset_write(preview, preview_bytes)
            record['generated_svg_path'] = generated_relative.as_posix()
            _write_index(root / INDEX_FILENAME, records)
        except Exception:
            record.clear()
            record.update(old_record)
            for path, old_payload in previous.items():
                if old_payload is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_asset_write(path, old_payload)
            raise

    # Never replace recovery bytes with an unprocessed save on reopening.
    if not good.exists():
        payload = canonical.read_bytes()
        validate_manual_svg(payload)
        preserve_original(record, root)
        _atomic_asset_write(good, payload)
    if not original.is_file():
        raise ValueError('Asset integrity error: generated original backup is missing.')
    record['generated_svg_path'] = original.relative_to(root).as_posix()
    _write_index(root / INDEX_FILENAME, records)
    return canonical


@serialized
def accept_saved_svg(root, dataset, street):
    """Validate saved bytes unchanged; restore recovery bytes on any failure."""
    from .preprocess import (_load_index, INDEX_FILENAME, approve_manual_svg,
                             _atomic_asset_write, SvgApprovalError)
    root = Path(root)
    records = _load_index(root / INDEX_FILENAME)
    record = next((r for r in records if (r.get('dataset_id'), r.get('street_id')) == (dataset.id, street.id)), None)
    if not record or not record.get('svg_path'):
        return False
    canonical = root / record['svg_path']
    _, good = history_paths(canonical)
    if not good.is_file():
        return False
    previous = good.read_bytes()
    payload = canonical.read_bytes() if canonical.exists() else b''
    if payload == previous:
        return False
    try:
        approve_manual_svg(dataset, street, root, canonical)
    except Exception as error:
        current = canonical.read_bytes() if canonical.exists() else b''
        if current != payload:
            raise SvgApprovalError('SVG changed during validation; waiting for the latest save.') from error
        _atomic_asset_write(canonical, previous)
        raise SvgApprovalError(f'Save rejected; last-known-good SVG restored and existing preview kept: {error}') from error
    return True
