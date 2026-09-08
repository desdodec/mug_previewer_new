import json
import hashlib

import pytest

from mug_previewer.review_index import (REVIEW_STATUSES, current_review_state, load_review_index,
                                        get_review_record, save_review_record)


def test_missing_ledger_means_no_review(tmp_path):
    assert load_review_index(tmp_path) == {}
    assert get_review_record(tmp_path, 'area', 'street') is None
    assert not current_review_state(tmp_path, 'area', 'street', None).export_blocked


@pytest.mark.parametrize('status', REVIEW_STATUSES)
def test_save_load_hash_and_production_independence(tmp_path, status):
    svg = tmp_path / 'face.svg'
    svg.write_text('<svg/>')
    index = tmp_path / 'preprocess_index.json'
    index.write_text('{"production_state":"AUTO_APPROVED"}')
    before = index.read_bytes()
    saved = save_review_record(tmp_path, 'area', 'street', status, svg, 'Human note')
    assert saved == get_review_record(tmp_path, 'area', 'street')
    assert saved.note == 'Human note' and saved.reviewed_at
    assert saved.reviewed_svg_sha256 == hashlib.sha256(svg.read_bytes()).hexdigest()
    state = current_review_state(tmp_path, 'area', 'street', svg)
    assert not state.stale
    assert state.export_blocked == (status != 'pass')
    assert index.read_bytes() == before
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('status', ['pass', 'overlap', 'Do Not Use'])
def test_changed_or_missing_artwork_marks_review_stale(tmp_path, status):
    svg = tmp_path / 'face.svg'
    svg.write_text('<svg/>')
    save_review_record(tmp_path, 'area', 'street', status, svg)
    svg.write_text('<svg><!--changed--></svg>')
    state = current_review_state(tmp_path, 'area', 'street', svg)
    assert state.stale and state.export_blocked == (status != 'pass')
    svg.unlink()
    assert current_review_state(tmp_path, 'area', 'street', svg).stale


def test_separate_dataset_ids_and_optional_note_future_fields(tmp_path):
    svg = tmp_path / 'face.svg'
    svg.write_text('<svg/>')
    save_review_record(tmp_path, 'one', 'street', 'pass', svg)
    save_review_record(tmp_path, 'two', 'street', 'missing', svg)
    path = tmp_path / 'review_index.json'
    payload = json.loads(path.read_text())
    payload['records'][0].pop('note')
    payload['records'][0]['future_field'] = True
    path.write_text(json.dumps(payload))
    assert len(load_review_index(tmp_path)) == 2
    assert get_review_record(tmp_path, 'one', 'street').note == ''
    assert get_review_record(tmp_path, 'two', 'street').status == 'missing'


def test_invalid_ledger_not_silently_overwritten(tmp_path):
    path = tmp_path / 'review_index.json'
    path.write_text('broken')
    svg = tmp_path / 'face.svg'
    svg.write_text('<svg/>')
    with pytest.raises(ValueError):
        save_review_record(tmp_path, 'area', 'street', 'pass', svg)
    assert path.read_text() == 'broken'
