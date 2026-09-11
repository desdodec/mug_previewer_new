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


def _slug(value):
    result = "".join(char.lower() if char.isalnum() else "_" for char in str(value)).strip("_")
    return result or "street"


def _inside_preprocessed_root(root, path):
    """Return a normalized root-contained path, rejecting path escapes."""
    root = Path(root).resolve()
    path = Path(path)
    candidate = path if path.is_absolute() else root / path
    try:
        relative = candidate.resolve().relative_to(root)
    except (OSError, ValueError):
        return None
    return root / relative


def _is_prepared_face_path(root, path):
    path = _inside_preprocessed_root(root, path)
    if path is None:
        return False
    try:
        relative = path.relative_to(Path(root).resolve())
    except ValueError:
        return False
    return (len(relative.parts) >= 3 and relative.parts[0].casefold() == 'faces'
            and '.history' not in relative.parts)


def _relative_to_root(root, path):
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def _preview_canonical_target(record, root):
    preview = record.get('preview_path')
    if not isinstance(preview, str) or not preview:
        return None
    preview_path = _inside_preprocessed_root(root, preview)
    if preview_path is None:
        return None
    try:
        relative = preview_path.relative_to(Path(root).resolve())
    except ValueError:
        return None
    if len(relative.parts) < 3 or relative.parts[0].casefold() != 'previews':
        return None
    target = Path(root).resolve() / 'faces' / Path(*relative.parts[1:]).with_suffix('.svg')
    return target if _is_prepared_face_path(root, target) else None


def _prepared_face_folders(record, root):
    faces = Path(root).resolve() / 'faces'
    dataset_id = record.get('dataset_id')
    if not isinstance(dataset_id, str) or not dataset_id:
        return ()
    candidates = (faces / dataset_id, faces / _slug(dataset_id))
    result = []
    seen = set()
    for folder in candidates:
        marker = folder.resolve()
        if marker not in seen and folder.is_dir():
            seen.add(marker)
            result.append(folder)
    return tuple(result)


def _valid_canonical_svg(path):
    from .preprocess import validate_manual_svg
    try:
        validate_manual_svg(Path(path).read_bytes())
        return True
    except (OSError, ValueError):
        return False


def _canonical_file_candidates(record, root):
    street_id = record.get('street_id')
    if not isinstance(street_id, str) or not street_id:
        return ()
    result = []
    seen = set()
    for folder in _prepared_face_folders(record, root):
        try:
            entries = tuple(folder.iterdir())
        except OSError:
            continue
        for candidate in entries:
            if not candidate.is_file() or candidate.suffix.casefold() != '.svg':
                continue
            stem = candidate.stem
            if not (stem == street_id or stem.startswith(street_id + '_')):
                continue
            if stem.endswith('.approved') or stem.endswith('.generated') or stem.endswith('_edit'):
                continue
            marker = candidate.resolve()
            if marker in seen or not _valid_canonical_svg(candidate):
                continue
            seen.add(marker)
            result.append(candidate)
    return tuple(result)


def _history_targets(record, root):
    """Find unambiguous private last-good backups for this prepared street."""
    street_id = record.get('street_id')
    if not isinstance(street_id, str) or not street_id:
        return ()
    result = []
    seen = set()
    for folder in _prepared_face_folders(record, root):
        history = folder / '.history'
        if not history.is_dir():
            continue
        try:
            entries = tuple(history.iterdir())
        except OSError:
            continue
        for street_history in entries:
            if not street_history.is_dir():
                continue
            stem = street_history.name
            if not (stem == street_id or stem.startswith(street_id + '_')):
                continue
            target = folder / (stem + '.svg')
            good = street_history / 'last-good.svg'
            generated = street_history / 'generated.svg'
            state = str(record.get('production_state') or '')
            source = good if _valid_canonical_svg(good) else None
            if source is None and state != 'MANUAL_APPROVED' and _valid_canonical_svg(generated):
                source = generated
            if source is None:
                continue
            marker = target.resolve()
            if marker in seen:
                continue
            seen.add(marker)
            result.append((target, source, generated if generated.is_file() else None))
    return tuple(result)


def _restore_prepared_target(record, root, target):
    """Restore a missing canonical face from its private recovery history."""
    from .preprocess import _atomic_asset_write
    target = _inside_preprocessed_root(root, target)
    if target is None or not _is_prepared_face_path(root, target):
        return False
    generated, good = history_paths(target)
    state = str(record.get('production_state') or '')
    approved = _inside_preprocessed_root(root, record.get('approved_svg_path')) if record.get('approved_svg_path') else None
    indexed_generated = (_inside_preprocessed_root(root, record.get('generated_svg_path'))
                         if record.get('generated_svg_path') else None)
    candidates = []
    if state == 'MANUAL_APPROVED' and approved is not None:
        candidates.append(approved)
    candidates.append(good)
    if state != 'MANUAL_APPROVED':
        if indexed_generated is not None:
            candidates.append(indexed_generated)
        candidates.append(generated)
    source = next((candidate for candidate in candidates if candidate is not None and _valid_canonical_svg(candidate)), None)
    if source is None:
        return False
    _atomic_asset_write(target, source.read_bytes())
    record['svg_path'] = _relative_to_root(root, target)
    if generated.is_file():
        record['generated_svg_path'] = _relative_to_root(root, generated)
    record.pop('approved_svg_path', None)
    return True


def _recover_missing_prepared_face(record, root):
    """Repair stale/missing prepared-face paths without regenerating artwork."""
    if not record.get('success') or record.get('production_state') == 'UNRENDERABLE_INPUT':
        return False

    current_value = record.get('svg_path')
    current = _inside_preprocessed_root(root, current_value) if isinstance(current_value, str) and current_value else None
    if current is not None and current.is_file():
        normalized = _relative_to_root(root, current)
        if current_value != normalized:
            record['svg_path'] = normalized
            return True
        return False

    targets = []
    if current is not None and _is_prepared_face_path(root, current):
        try:
            canonical = _inside_preprocessed_root(root, canonical_relative(record))
        except (KeyError, TypeError, ValueError):
            canonical = None
        if canonical is not None and _is_prepared_face_path(root, canonical):
            targets.append(canonical)
    preview_target = _preview_canonical_target(record, root)
    if preview_target is not None and all(preview_target.resolve() != item.resolve() for item in targets):
        targets.append(preview_target)

    for target in targets:
        if target.is_file() and _valid_canonical_svg(target):
            record['svg_path'] = _relative_to_root(root, target)
            generated, _ = history_paths(target)
            if generated.is_file():
                record['generated_svg_path'] = _relative_to_root(root, generated)
            record.pop('approved_svg_path', None)
            return True
        if _restore_prepared_target(record, root, target):
            return True

    candidates = _canonical_file_candidates(record, root)
    if len(candidates) == 1:
        target = candidates[0]
        record['svg_path'] = _relative_to_root(root, target)
        generated, _ = history_paths(target)
        if generated.is_file():
            record['generated_svg_path'] = _relative_to_root(root, generated)
        record.pop('approved_svg_path', None)
        return True

    histories = _history_targets(record, root)
    if len(histories) == 1:
        target, source, generated = histories[0]
        from .preprocess import _atomic_asset_write
        _atomic_asset_write(target, source.read_bytes())
        record['svg_path'] = _relative_to_root(root, target)
        if generated is not None:
            record['generated_svg_path'] = _relative_to_root(root, generated)
        record.pop('approved_svg_path', None)
        return True
    return False


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
    recovered = 0
    for record in records:
        if key is not None and (record.get('dataset_id'), record.get('street_id')) != key:
            continue
        if _recover_missing_prepared_face(record, root):
            recovered += 1
        if not record.get('success') or not record.get('svg_path'):
            continue
        canonical = canonical_relative(record)
        source = record.get('approved_svg_path') if record.get('production_state') == 'MANUAL_APPROVED' else None
        source = source or record['svg_path']
        if source == canonical.as_posix() and record['svg_path'] == canonical.as_posix():
            continue
        source_path = root / source
        if not source_path.is_file():
            # A stale legacy path must block only this face, not the entire catalogue.
            continue
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
    if recovered:
        # Publish all recovered path updates together. Restored canonical files are
        # already complete atomic writes; one index write avoids 100s of rewrites
        # when opening an older prepared set.
        _write_index(root / INDEX_FILENAME, records)
    return count + recovered


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
