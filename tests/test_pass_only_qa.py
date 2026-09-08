import base64
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import quote_from_bytes

import pytest
from PIL import Image

from test_authoritative_svg_export import prepared, svg, Status
from test_batch_export import prepared as prepared_batch, plan
from mug_previewer import preprocess, batch_export
from mug_previewer.preprocessed_export import export_preprocessed_provider_png
from mug_previewer.review_index import current_review_state, save_review_record, svg_sha256
from mug_previewer.review_results import read_review_results, snapshot_bytes

PROFILE = 'inkthreadable_11oz_white'


def source(root, data, street, status='pass', **extra):
    item = dict(dataset_id=data.id, street_id=street.id, status=status,
                reviewed_svg_sha256=svg_sha256(root / 'generated.svg')) | extra
    path = root / 'svg_review_results_test.json'
    path.write_text(json.dumps([item]), encoding='utf-8')
    return path


@pytest.mark.parametrize('manual', [False, True])
@pytest.mark.parametrize('status', [None, 'pass', 'overlap', 'duplicates', 'missing', 'other', 'Do Not Use'])
def test_single_pass_only_matrix(prepared, monkeypatch, manual, status):
    root, data, street, record = prepared
    record(Status.AUTO_APPROVED)
    artwork = root / 'generated.svg'
    if manual:
        record(Status.MANUAL_REVIEW)
        artwork = preprocess.approve_manual_svg(data, street, root, artwork).path
    if status:
        source(root, data, street, status, reviewed_svg_sha256=svg_sha256(artwork))
    calls = []
    def render(*a, **kw):
        calls.append(True)
        return Image.new('RGBA', (2362, 1063), 'red')
    monkeypatch.setattr('mug_previewer.preprocessed_export.render_preprocessed_wrap', render)
    destination = root / 'out.png'
    if status in (None, 'pass'):
        export_preprocessed_provider_png(root, data, street, destination, profile_id=PROFILE)
        assert destination.exists() and calls == [True]
    else:
        with pytest.raises(ValueError, match='export blocked'):
            export_preprocessed_provider_png(root, data, street, destination, profile_id=PROFILE)
        assert not destination.exists() and calls == []


@pytest.mark.parametrize('status', [Status.UNRENDERABLE_INPUT])
def test_pass_cannot_approve_production_state(prepared, status):
    root, data, street, record = prepared
    record(status)
    source(root, data, street)
    with pytest.raises(ValueError):
        export_preprocessed_provider_png(root, data, street, root / 'out.png', profile_id=PROFILE)
    assert not (root / 'out.png').exists()


@pytest.mark.parametrize('change', ['overlap', 'remove_record', 'delete_source', 'artwork', 'source_bytes'])
@pytest.mark.parametrize('during', [False, True])
def test_browser_source_batch_races(prepared_batch, monkeypatch, change, during):
    root, data, records, write = prepared_batch
    p = plan(prepared_batch)
    path = root / 'svg_review_results.json'
    def mutate():
        items = json.loads(path.read_text())
        if change == 'overlap':
            items[0]['status'] = 'overlap'
        elif change == 'remove_record':
            items.pop(0)
        elif change == 'delete_source':
            path.unlink()
            return
        elif change == 'artwork':
            (root / '0000.svg').write_text(svg('blue'))
            return
        elif change == 'source_bytes':
            path.write_text(path.read_text() + '\n')
            return
        path.write_text(json.dumps(items))
    calls = []
    def export(root, dataset, street, destination, **kw):
        calls.append(street.id)
        destination.write_bytes(b'PNG')
        if street.id == '0000':
            mutate()
    monkeypatch.setattr(batch_export, 'export_preprocessed_provider_png', export)
    if not during:
        mutate()
    result = batch_export.execute_batch_export(p)
    assert result.results[0].result == 'FAILED'
    assert not p.items[0].destination.exists()
    assert ('0000' in calls) == during


@pytest.mark.parametrize('kind', ['modern', 'snapshot', 'filename_only', 'ambiguous', 'malformed', 'unsupported', 'duplicate', 'png', 'wrong_snapshot_hash'])
def test_ingestion_formats_fail_closed(prepared, kind):
    root, data, street, record = prepared
    record(Status.AUTO_APPROVED)
    path = source(root, data, street)
    payload = json.loads(path.read_text())
    if kind in ('snapshot', 'filename_only', 'ambiguous'):
        payload = [dict(svg_name='generated.svg', status='pass')]
        if kind != 'filename_only':
            payload[0]['preview_data_url'] = 'data:image/svg+xml,' + quote_from_bytes((root / 'generated.svg').read_bytes())
        if kind == 'ambiguous':
            index = root / 'preprocess_index.json'
            prepared_records = json.loads(index.read_text())
            prepared_records['records'].append(dict(prepared_records['records'][0], dataset_id='other'))
            index.write_text(json.dumps(prepared_records))
    elif kind == 'unsupported':
        payload[0]['status'] = 'approved'
    elif kind == 'duplicate':
        payload.append(payload[0])
    elif kind == 'png':
        payload[0].pop('reviewed_svg_sha256')
        payload[0]['preview_data_url'] = 'data:image/png;base64,AAAA'
    elif kind == 'wrong_snapshot_hash':
        payload[0]['preview_data_url'] = 'data:image/svg+xml,' + quote_from_bytes(svg('blue').encode())
    path.write_text('{bad' if kind == 'malformed' else json.dumps(payload))
    state = current_review_state(root, data.id, street.id, root / 'generated.svg')
    assert state.production_export_allowed == (kind in ('modern', 'snapshot', 'filename_only', 'png', 'wrong_snapshot_hash'))
    if kind in ('ambiguous', 'duplicate'):
        assert state.state == 'AMBIGUOUS_REVIEW'


@pytest.mark.parametrize('payload', [b'<svg/>', b'\xef\xbb\xbf<svg>\r\n<!-- Caf\xc3\xa9 -->\r\n</svg>', b'<svg>\n</svg>'])
def test_browser_python_byte_exact_hashes(tmp_path, payload):
    path = tmp_path / 'face.svg'
    path.write_bytes(payload)
    helper = Path('tools/svg_reviewer/qa_hash.js').resolve()
    js = "const h=require(process.argv[1]);const b=Buffer.from(process.argv[2],'base64');h.reviewedSvgSha256(b).then(hash=>console.log(JSON.stringify({hash,url:h.svgBytesDataUrl(b)})));"
    result = subprocess.run(['node', '-e', js, str(helper), base64.b64encode(payload).decode()], check=True, capture_output=True, text=True)
    value = json.loads(result.stdout)
    assert value['hash'] == svg_sha256(path)
    assert snapshot_bytes(value['url']) == payload
    assert snapshot_bytes('data:image/svg+xml;charset=utf-8,' + quote_from_bytes(payload)) == payload


@pytest.mark.parametrize('change', ['review', 'artwork', 'delete'])
def test_single_recheck_preserves_destination(prepared, monkeypatch, change):
    root, data, street, record = prepared
    record(Status.AUTO_APPROVED)
    review = source(root, data, street)
    destination = root / 'out.png'
    destination.write_bytes(b'previous PNG')
    def render(*a, **kw):
        if change == 'review':
            source(root, data, street, 'overlap')
        elif change == 'delete':
            review.unlink()
        else:
            (root / 'generated.svg').write_text(svg('blue'))
        return Image.new('RGBA', (2362, 1063), 'red')
    monkeypatch.setattr('mug_previewer.preprocessed_export.render_preprocessed_wrap', render)
    with pytest.raises(ValueError):
        export_preprocessed_provider_png(root, data, street, destination, profile_id=PROFILE)
    assert destination.read_bytes() == b'previous PNG'
    assert not list(root.glob('.single-export-*'))


def test_old_ledger_never_restores_deleted_browser_pass(prepared):
    root, data, street, record = prepared
    record(Status.AUTO_APPROVED)
    save_review_record(root, data.id, street.id, 'pass', root / 'generated.svg')
    (root / 'svg_review_results.json').unlink()
    assert (root / 'review_index.json').exists()
    assert current_review_state(root, data.id, street.id, root / 'generated.svg').production_export_allowed


def test_reviewer_controller_load_save_and_stale_reset():
    result = subprocess.run(['node', 'tests/reviewer_controller.cjs'], check=True, capture_output=True, text=True)
    assert 'stale pass reset' in result.stdout


def test_stable_identity_precedes_ambiguous_names_and_discovery_labels(prepared):
    root, data, street, record = prepared
    record(Status.AUTO_APPROVED)
    path = source(root, data, street, svg_name='Same_Name.svg', svg_path='unrelated/name.svg')
    path.rename(root / 'svg_review_results_display_name_not_dataset_id.json')
    state = current_review_state(root, data.id, street.id, root / 'generated.svg')
    assert state.production_export_allowed


def test_duplicate_identity_across_source_files_blocks(prepared):
    root, data, street, record = prepared
    record(Status.AUTO_APPROVED)
    path = source(root, data, street)
    (root / 'svg_review_results_other_label.json').write_bytes(path.read_bytes())
    assert current_review_state(root, data.id, street.id, root / 'generated.svg').state == 'AMBIGUOUS_REVIEW'


def test_desktop_save_updates_existing_legacy_source_without_duplicate(prepared):
    root, data, street, record = prepared
    record(Status.AUTO_APPROVED)
    path = root / 'svg_review_results_legacy.json'
    path.write_text(json.dumps([dict(svg_path='generated.svg', status='pass')]))
    assert current_review_state(root, data.id, street.id, root / 'generated.svg').production_export_allowed
    save_review_record(root, data.id, street.id, 'pass', root / 'generated.svg')
    assert current_review_state(root, data.id, street.id, root / 'generated.svg').production_export_allowed
    assert not (root / 'svg_review_results.json').exists()
    item = json.loads(path.read_text())[0]
    assert item['dataset_id'] == data.id and item['street_id'] == street.id
    assert snapshot_bytes(item['preview_data_url']) == (root / 'generated.svg').read_bytes()


def test_review_save_compares_hash_of_actual_read_bytes(prepared):
    root, data, street, record = prepared
    previous = svg_sha256(root / 'generated.svg')
    (root / 'generated.svg').write_text(svg('blue'))
    with pytest.raises(ValueError, match='changed since preview'):
        save_review_record(root, data.id, street.id, 'pass', root / 'generated.svg', expected_sha256=previous)
    assert not (root / 'svg_review_results.json').exists()
    assert not (root / 'review_index.json').exists()
