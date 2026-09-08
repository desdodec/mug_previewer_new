"""Exact-artwork browser review ingestion; filenames never imply approval."""
from functools import lru_cache
import base64
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote_to_bytes
import xml.etree.ElementTree as ET


def discover_review_results(root):
    return tuple(sorted(p for p in Path(root).glob('svg_review_results*.json')
                        if p.name == 'svg_review_results.json' or p.name.startswith('svg_review_results_')))


def snapshot_bytes(value):
    if not isinstance(value, str) or ',' not in value:
        raise ValueError('Exact SVG snapshot missing; refresh/re-export the review.')
    header, content = value.split(',', 1)
    if header.split(';')[0].lower() != 'data:image/svg+xml':
        raise ValueError('Review snapshot must contain SVG, not a preview PNG.')
    payload = (base64.b64decode(content, validate=True) if ';base64' in header.lower()
               else unquote_to_bytes(content))
    if ET.fromstring(payload).tag.split('}')[-1] != 'svg':
        raise ValueError('Review snapshot is not SVG.')
    return payload


def review_hash(record):
    digest = record.get('reviewed_svg_sha256')
    snapshot = record.get('preview_data_url')
    derived = hashlib.sha256(snapshot_bytes(snapshot)).hexdigest() if snapshot else None
    if digest is not None and (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
        raise ValueError('Invalid reviewed_svg_sha256.')
    if digest and derived and digest != derived:
        raise ValueError('Review hash disagrees with embedded SVG snapshot.')
    if not (digest or derived):
        raise ValueError('Exact SVG hash missing; refresh/re-export the review.')
    return digest or derived


def _path(value):
    return str(value or '').replace('\\', '/').removeprefix('./')


def resolve_identity(record, prepared):
    did, sid = record.get('dataset_id'), record.get('street_id')
    if did is not None or sid is not None:
        if not all(isinstance(v, str) and v for v in (did, sid)):
            raise ValueError('INVALID_REVIEW: incomplete stable identity.')
        return did, sid
    path = _path(record.get('svg_path') or record.get('svg_name'))
    if not path:
        raise ValueError('INVALID_REVIEW: missing identity.')
    candidates = set()
    for item in prepared:
        paths = {_path(item.get(k)) for k in ('svg_path', 'generated_svg_path', 'approved_svg_path')}
        if path in paths or ('/' not in path and any(p.rsplit('/', 1)[-1] == path for p in paths)):
            candidates.add((item['dataset_id'], item['street_id']))
    if len(candidates) != 1:
        raise ValueError(('AMBIGUOUS_REVIEW' if candidates else 'INVALID_REVIEW') + ': unresolved identity ' + path)
    return candidates.pop()


def read_review_results(root):
    """Normalize sources in memory, returning entries, errors, source fingerprints.

    Unresolvable identities fail the source set closed; resolved invalid records
    block their own street. Source files are always read afresh, including bytes.
    """
    root = Path(root)
    sources = discover_review_results(root)
    index = root / 'preprocess_index.json'
    index_bytes = index.read_bytes() if sources and index.exists() else b'{}'
    return _normalize(index_bytes, tuple((str(source), source.read_bytes()) for source in sources))


@lru_cache(maxsize=4)
def _normalize(index_bytes, source_bytes):
    from .review_index import REVIEW_STATUSES
    payload = json.loads(index_bytes.decode('utf-8-sig'))
    prepared = payload.get('records', []) if isinstance(payload, dict) else []
    entries, errors, fingerprints = {}, {}, []
    for source_name, raw in source_bytes:
        source = Path(source_name)
        fingerprints.append((source.name, hashlib.sha256(raw).hexdigest()))
        records = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(records, list):
            raise ValueError('INVALID_REVIEW: review results must be a JSON array.')
        for record in records:
            if not isinstance(record, dict):
                raise ValueError('INVALID_REVIEW: review record must be an object.')
            key = resolve_identity(record, prepared)
            if key in entries:
                errors[key] = 'AMBIGUOUS_REVIEW: duplicate review identity.'
                continue
            entries[key] = (record, source)
            try:
                if record.get('status') not in REVIEW_STATUSES:
                    raise ValueError('Unsupported review status.')
                if not isinstance(record.get('note', ''), str):
                    raise ValueError('Invalid review note.')
            except (ValueError, ET.ParseError) as error:
                errors[key] = 'INVALID_REVIEW: ' + str(error)
    return entries, errors, tuple(fingerprints)
