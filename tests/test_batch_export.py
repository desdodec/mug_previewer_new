from dataclasses import asdict, replace
import json
from pathlib import Path
import threading

from PIL import Image
import pytest

from mug_previewer import batch_export as batch
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.review_index import save_review_record, svg_sha256
from mug_previewer.providers import get_provider_profile

PROVIDERS = ['inkthreadable_11oz_white', 'printify_generic_11oz_ceramic']


def svg(colour='red'):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462" '
            f'viewBox="0 0 990 462"><rect width="495" height="462" fill="{colour}"/></svg>')


@pytest.fixture
def prepared(tmp_path):
    original = load_dataset(Path(__file__).parent / 'fixtures/workflow_v6_valid')
    streets = tuple(replace(original.streets[0], id=f'{i:04}', display_name=f'Street {i}') for i in range(11))
    data = replace(original, streets=streets)
    records = []
    for street in streets:
        path = tmp_path / f'{street.id}.svg'
        path.write_text(svg(), encoding='utf-8')
        records.append(dict(dataset_id=data.id, street_id=street.id, production_state='AUTO_APPROVED',
                            success=True, svg_path=path.name))
    def write():
        (tmp_path / 'preprocess_index.json').write_text(json.dumps({'records': records}), encoding='utf-8')
    write()
    # Explicit human passes for export mechanics fixtures.
    for street in streets:
        save_review_record(tmp_path, data.id, street.id, 'pass', tmp_path / f'{street.id}.svg')
    return tmp_path, data, records, write


def plan(prepared, **kwargs):
    root, data, _, _ = prepared
    return batch.build_batch_plan(root, data, PROVIDERS[0], root / 'out', **kwargs)


def fake_export(calls, fail=None):
    def export(root, dataset, street, destination, *, profile_id, **kwargs):
        calls.append((street.id, destination.name, profile_id))
        destination.write_bytes(b'new PNG')
        if street.id == fail:
            raise OSError('test failure')
        return destination
    return export


def test_mixed_eligibility_and_exact_summary(prepared):
    root, data, records, write = prepared
    (root / 'svg_review_results.json').unlink()
    for index, status in [(1, 'pass'), (2, 'overlap'), (3, 'pass'), (4, 'Do Not Use'), (7, 'pass'), (8, 'pass')]:
        save_review_record(root, data.id, data.streets[index].id, status, root / f'{index:04}.svg')
    for index in (3, 8):
        (root / f'{index:04}.svg').write_text(svg('yellow'))
    records[5]['production_state'] = 'MANUAL_REVIEW'
    for index in (6, 7, 8):
        records[index]['production_state'] = 'MANUAL_APPROVED'
        records[index]['approved_svg_path'] = f'{index:04}.svg'
    records[9]['production_state'] = 'UNRENDERABLE_INPUT'
    (root / '0010.svg').unlink()
    write()
    result = plan(prepared)
    assert [i.eligibility.value for i in result.items] == [
        'QA_BLOCKED', 'READY', 'QA_BLOCKED', 'QA_BLOCKED', 'EXCLUDED', 'MANUAL_REVIEW',
        'QA_BLOCKED', 'READY', 'QA_BLOCKED', 'UNRENDERABLE', 'ASSET_ERROR']
    assert asdict(result.summary) == dict(total=11, ready=2, manual_review=1, qa_blocked=5,
                                        excluded=1, unrenderable=1, asset_errors=1, existing=0, not_reviewed=2)
    assert '(stale)' in result.items[3].reason


@pytest.mark.parametrize('payload', ['{bad', '{}', '{"records": [null]}'])
def test_invalid_global_qa_fails_closed(prepared, payload):
    (prepared[0] / 'review_index.json').write_text(payload)
    with pytest.raises(batch.BatchPlanningError, match='QA ledger is invalid'):
        plan(prepared)
    assert not (prepared[0] / 'out').exists()


@pytest.mark.parametrize('kind', ['missing', 'broken', 'duplicates'])
def test_corrupt_index_fails_planning(prepared, kind):
    root, data, records, write = prepared
    if kind == 'duplicates':
        records.append(records[0])
        write()
    elif kind == 'missing':
        (root / 'preprocess_index.json').unlink()
    else:
        (root / 'preprocess_index.json').write_text('{bad')
    with pytest.raises(batch.BatchPlanningError):
        plan(prepared)


def test_invalid_asset_and_unknown_state(prepared):
    root, data, records, write = prepared
    (root / '0000.svg').write_text('<broken')
    records[1]['production_state'] = 'UNKNOWN'
    write()
    assert plan(prepared).summary.asset_errors == 2


def test_executor_only_calls_authoritative_exporter_and_continues(prepared, monkeypatch):
    root, data, records, write = prepared
    records[3]['production_state'] = 'MANUAL_REVIEW'
    write()
    calls, progress = [], []
    monkeypatch.setattr(batch, 'export_preprocessed_provider_png', fake_export(calls, '0001'))
    def forbidden(*a, **k):
        pytest.fail('batch must not call a renderer or preprocessing')
    for target in ['mug_previewer.preprocessed_export.render_preprocessed_wrap',
                   'mug_previewer.preprocess.render_face_svg',
                   'mug_previewer.preprocess.production_triage_state']:
        monkeypatch.setattr(target, forbidden)
    p = plan(prepared)
    result = batch.execute_batch_export(p, on_progress=progress.append)
    expected = [i for i in p.items if i.eligibility == batch.Eligibility.READY]
    assert calls == [(i.street_id, i.destination.name, p.provider_id) for i in expected]
    assert result.summary['exported'] == 9 and result.summary['failed'] == 1
    assert result.results[0].result == 'EXPORTED'
    assert result.results[1].reason == 'test failure'
    assert result.results[2].result == 'EXPORTED'
    assert len(progress) == 10 and progress[-1].current == 10
    payload = json.loads(result.report_path.read_text(encoding='utf-8'))
    assert payload['version'] == 1 and payload['dataset_id'] == data.id
    assert payload['provider_id'] == PROVIDERS[0]
    assert payload['started_at'] <= payload['completed_at']
    assert len(payload['items']) == 11
    assert payload['items'][0]['authoritative_svg_sha256'] == svg_sha256(root / '0000.svg')
    assert payload['items'][3]['reason'] == 'Artwork requires manual approval'
    assert len(list(p.output_directory.glob('*.png'))) == 9
    assert not list(p.output_directory.glob('.batch-*'))


@pytest.mark.parametrize('change', ['qa', 'artwork', 'approval_path', 'ledger', 'production'])
def test_changes_after_plan_never_export_changed_item(prepared, monkeypatch, change):
    root, data, records, write = prepared
    records[0]['production_state'] = 'MANUAL_APPROVED'
    records[0]['approved_svg_path'] = '0000.svg'
    write()
    p = plan(prepared)
    if change == 'qa':
        save_review_record(root, data.id, '0000', 'overlap', root / '0000.svg')
    elif change == 'artwork':
        (root / '0000.svg').write_text(svg('yellow'))
    elif change == 'approval_path':
        (root / 'new.svg').write_text(svg('yellow'))
        records[0]['approved_svg_path'] = 'new.svg'
        write()
    elif change == 'ledger':
        (root / 'review_index.json').write_text('{broken')
    else:
        records[0]['production_state'] = 'MANUAL_REVIEW'
        write()
    calls = []
    monkeypatch.setattr(batch, 'export_preprocessed_provider_png', fake_export(calls))
    result = batch.execute_batch_export(p)
    assert result.results[0].result == 'FAILED'
    assert 'rebuild' in result.results[0].reason or 'QA ledger is invalid' in result.results[0].reason
    assert not any(c[0] == '0000' for c in calls)
    assert not p.items[0].destination.exists()


@pytest.mark.parametrize('replace_existing,fail,expected', [(False, False, 'SKIPPED_EXISTING'),
                                                          (True, False, 'EXPORTED'), (True, True, 'FAILED')])
def test_existing_files_are_safe(prepared, monkeypatch, replace_existing, fail, expected):
    p = plan(prepared, replace_existing=replace_existing)
    p.output_directory.mkdir(parents=True)
    destination = p.items[0].destination
    destination.write_bytes(b'original')
    calls = []
    monkeypatch.setattr(batch, 'export_preprocessed_provider_png', fake_export(calls, '0000' if fail else None))
    result = batch.execute_batch_export(p)
    assert result.results[0].result == expected
    assert destination.read_bytes() == (b'new PNG' if expected == 'EXPORTED' else b'original')


def test_concurrent_destination_is_not_overwritten(prepared, monkeypatch):
    p = plan(prepared)
    def exporter(root, dataset, street, destination, **kwargs):
        destination.write_bytes(b'new')
        next(i.destination for i in p.items if i.street_id == street.id).write_bytes(b'racing writer')
    monkeypatch.setattr(batch, 'export_preprocessed_provider_png', exporter)
    result = batch.execute_batch_export(p)
    assert result.summary['skipped_existing'] == 11
    assert all(i.destination.read_bytes() == b'racing writer' for i in p.items)


def test_changes_during_render_do_not_publish(prepared, monkeypatch):
    p = plan(prepared)
    def exporter(root, dataset, street, destination, **kwargs):
        destination.write_bytes(b'new')
        (root / f'{street.id}.svg').write_text(svg('blue'))
    monkeypatch.setattr(batch, 'export_preprocessed_provider_png', exporter)
    result = batch.execute_batch_export(p)
    assert result.summary['failed'] == 11
    assert not list(p.output_directory.glob('*.png'))


@pytest.mark.parametrize('name', ["King's Road", "St. Mary's Close", 'A/B Street', 'Road: West',
                                 'CON', 'Trailing.', ' .. ', 'a<>:"/\\|?*b', 'Rue École', 'a__  b'])
def test_filename_is_safe_stable_and_retains_id(name):
    filename = batch.production_filename('dataset', '0146', name)
    assert filename == batch.production_filename('dataset', '0146', name)
    assert '0146' in filename and filename.endswith('.png')
    assert not any(c in filename for c in '<>:"/\\|?*')
    assert not batch.sanitize_filename(name).endswith(('.', ' '))
    assert batch.sanitize_filename('CON') == '_CON'
    assert batch.sanitize_filename('Rue École') == 'Rue_École'


def test_sanitization_collision_is_detected_before_writes(prepared):
    root, data, records, write = prepared
    records[0]['street_id'] = 'a/b'
    records[1]['street_id'] = 'a:b'
    write()
    data = replace(data, streets=(replace(data.streets[0], id='a/b', display_name='Same'),
                                 replace(data.streets[1], id='a:b', display_name='Same')))
    (root / 'svg_review_results.json').unlink()
    for street in data.streets:
        save_review_record(root, data.id, street.id, 'pass', root / ('0000.svg' if street.id == 'a/b' else '0001.svg'))
    with pytest.raises(batch.BatchPlanningError, match='a/b.*a:b'):
        batch.build_batch_plan(root, data, PROVIDERS[0], root / 'out')
    assert not (root / 'out').exists()


def test_scope_is_selected_dataset_only_and_plan_reloads(prepared):
    root, data, records, write = prepared
    records.append(dict(records[0], dataset_id='other'))
    write()
    assert plan(prepared).summary.total == 11
    records[0]['production_state'] = 'MANUAL_REVIEW'
    write()
    assert plan(prepared).summary.manual_review == 1


def test_cancel_keeps_finished_exports_and_writes_report(prepared, monkeypatch):
    calls = []
    monkeypatch.setattr(batch, 'export_preprocessed_provider_png', fake_export(calls))
    event = threading.Event()
    result = batch.execute_batch_export(plan(prepared), cancel_event=event,
                                        on_progress=lambda p: event.set())
    assert result.cancelled and result.summary['exported'] == 1
    assert result.summary['cancelled'] == 10 and result.report_path.is_file()


@pytest.mark.parametrize('provider', PROVIDERS)
def test_real_authoritative_red_and_approved_blue_batch(prepared, monkeypatch, provider):
    root, data, records, write = prepared
    del records[2:]
    records[1]['production_state'] = 'MANUAL_APPROVED'
    records[1]['approved_svg_path'] = 'approved.svg'
    (root / 'approved.svg').write_text(svg('blue'))
    save_review_record(root, data.id, '0001', 'pass', root / 'approved.svg')
    (root / '0001_edit.svg').write_text(svg('yellow'))
    write()
    def forbidden(*a, **k):
        pytest.fail('live face rendering must not run')
    monkeypatch.setattr('mug_previewer.rendering.artwork.render_face', forbidden)
    monkeypatch.setattr('mug_previewer.preprocess.render_face_svg', forbidden)
    p = batch.build_batch_plan(root, data, provider, root / 'out')
    result = batch.execute_batch_export(p)
    assert result.summary['exported'] == 2, result
    profile = get_provider_profile(provider)
    for item, colour in zip(p.items, [(255, 0, 0, 255), (0, 0, 255, 255)]):
        with Image.open(item.destination) as image:
            assert image.mode == 'RGBA'
            assert abs(image.info['dpi'][0] - 300) < 0.1
            assert image.getpixel((image.width // 5, image.height // 2)) == colour
            expected = (profile.canvas_width_px, profile.canvas_height_px)
            assert image.size == expected

@pytest.mark.parametrize('status', ['overlap', 'duplicates', 'missing', 'other', 'Do Not Use', 'pass'])
@pytest.mark.parametrize('stale', [False, True])
def test_all_qa_statuses_and_staleness(prepared, status, stale):
    root, data, records, write = prepared
    save_review_record(root, data.id, '0000', status, root / '0000.svg')
    if stale:
        (root / '0000.svg').write_text(svg('yellow'))
    category = plan(prepared).items[0].eligibility
    expected = ('EXCLUDED' if status == 'Do Not Use' and not stale else
                'READY' if status == 'pass' and not stale else 'QA_BLOCKED')
    assert category.value == expected


@pytest.mark.parametrize('kind', ['identity_type', 'duplicate', 'note_type'])
def test_structurally_invalid_qa_cannot_disappear(prepared, kind):
    root, data, records, write = prepared
    save_review_record(root, data.id, '0000', 'overlap', root / '0000.svg')
    path = root / 'review_index.json'
    payload = json.loads(path.read_text())
    if kind == 'identity_type':
        payload['records'][0]['dataset_id'] = 123
    elif kind == 'duplicate':
        payload['records'].append(payload['records'][0])
    else:
        payload['records'][0]['note'] = []
    path.write_text(json.dumps(payload))
    with pytest.raises(batch.BatchPlanningError, match='QA ledger is invalid'):
        plan(prepared)


def test_progress_observer_failure_still_produces_report(prepared, monkeypatch):
    monkeypatch.setattr(batch, 'export_preprocessed_provider_png', fake_export([]))
    def observer(p):
        raise RuntimeError('observer failed')
    result = batch.execute_batch_export(plan(prepared), on_progress=observer)
    assert result.summary['exported'] == 11
    assert len(json.loads(result.report_path.read_text())['progress_errors']) == 11


def test_duplicate_names_keep_street_identity(prepared):
    root, data, records, write = prepared
    data = replace(data, streets=tuple(replace(s, display_name='Same street') for s in data.streets))
    p = batch.build_batch_plan(root, data, PROVIDERS[0], root / 'out')
    assert len({i.destination.name for i in p.items}) == 11


def test_exporter_file_exists_error_is_runtime_failure(prepared, monkeypatch):
    def exporter(*args, **kwargs):
        raise FileExistsError('internal exporter failure')
    monkeypatch.setattr(batch, 'export_preprocessed_provider_png', exporter)
    result = batch.execute_batch_export(plan(prepared))
    assert result.summary['failed'] == 11 and result.summary['skipped_existing'] == 0
