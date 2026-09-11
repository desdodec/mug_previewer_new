from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from mug_previewer.batch_export import build_batch_plan, execute_batch_export
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.prepared_asset import resolve_prepared_face_source
from mug_previewer.preprocessed_export import export_preprocessed_provider_png, render_authoritative_face_panel
from mug_previewer.ui.app import MugPreviewerApp
from mug_previewer.ui.state import load_preprocessed_catalogue


FIXTURE = Path(__file__).parent / 'fixtures/workflow_v6_valid'
PROVIDERS = [('inkthreadable_11oz_white', (2362, 1063)),
             ('printify_generic_11oz_ceramic', (2475, 1155))]


def prepared(tmp_path, *, valid_preview=True, state='AUTO_APPROVED'):
    data = load_dataset(FIXTURE)
    data = replace(data, streets=data.streets[:1])
    street = data.streets[0]
    preview = tmp_path / 'previews' / data.id / f'{street.id}_cached.png'
    preview.parent.mkdir(parents=True)
    if valid_preview:
        Image.new('RGBA', (495, 462), (0, 0, 255, 255)).save(preview)
    else:
        Image.new('RGBA', (494, 462), (0, 0, 255, 255)).save(preview)
    record = {
        'dataset_id': data.id,
        'dataset_name': data.display_name,
        'street_id': street.id,
        'street_name': street.display_name,
        'production_state': state,
        'success': True,
        'svg_path': f'faces/{data.id}/{street.id}_missing.svg',
        'preview_path': preview.relative_to(tmp_path).as_posix(),
    }
    (tmp_path / 'preprocess_index.json').write_text(json.dumps({'records': [record]}), encoding='utf-8')
    return data, street, record, preview


def test_missing_svg_uses_exact_cached_preview_without_creating_svg(tmp_path):
    data, street, record, preview = prepared(tmp_path)
    source = resolve_prepared_face_source(tmp_path, record)
    assert source.kind == 'cached_preview' and source.path == preview
    before = (preview.read_bytes(), preview.stat().st_mtime_ns)
    panel = render_authoritative_face_panel(tmp_path, data, street, require_production_approved=True)
    assert panel.source.kind == 'cached_preview'
    assert panel.image.size == (495, 462)
    assert panel.image.getpixel((247, 231)) == (0, 0, 255, 255)
    assert not (tmp_path / record['svg_path']).exists()
    assert (preview.read_bytes(), preview.stat().st_mtime_ns) == before


@pytest.mark.parametrize('profile,size', PROVIDERS)
def test_single_provider_export_accepts_cached_preview_and_preserves_it(tmp_path, profile, size):
    data, street, record, preview = prepared(tmp_path)
    before = (preview.read_bytes(), preview.stat().st_mtime_ns)
    destination = tmp_path / f'{profile}.png'
    export_preprocessed_provider_png(tmp_path, data, street, destination, profile_id=profile)
    with Image.open(destination) as image:
        assert image.size == size
    assert not (tmp_path / record['svg_path']).exists()
    assert (preview.read_bytes(), preview.stat().st_mtime_ns) == before


def test_batch_plan_and_export_use_cached_preview(tmp_path):
    data, street, record, preview = prepared(tmp_path)
    before = (preview.read_bytes(), preview.stat().st_mtime_ns)
    plan = build_batch_plan(tmp_path, data, 'inkthreadable_11oz_white', tmp_path / 'out')
    assert plan.summary.ready == 1
    assert plan.summary.asset_errors == 0
    assert plan.items[0].authoritative_svg == preview
    result = execute_batch_export(plan)
    assert result.summary['exported'] == 1
    assert (preview.read_bytes(), preview.stat().st_mtime_ns) == before
    assert not (tmp_path / record['svg_path']).exists()


def test_changed_cached_preview_invalidates_existing_batch_plan(tmp_path):
    data, _, _, preview = prepared(tmp_path)
    plan = build_batch_plan(tmp_path, data, 'inkthreadable_11oz_white', tmp_path / 'out')
    Image.new('RGBA', (495, 462), (255, 0, 0, 255)).save(preview)
    result = execute_batch_export(plan)
    assert result.summary['failed'] == 1
    assert 'rebuild' in result.results[0].reason.lower()


def test_wrong_size_cached_preview_remains_blocked(tmp_path):
    data, _, _, _ = prepared(tmp_path, valid_preview=False)
    plan = build_batch_plan(tmp_path, data, 'inkthreadable_11oz_white', tmp_path / 'out')
    assert plan.summary.ready == 0
    assert plan.summary.asset_errors == 1
    assert '495x462' in plan.items[0].reason


def test_ui_labels_cached_recovery_and_allows_export(tmp_path):
    data, street, _, _ = prepared(tmp_path)
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    app.preprocessed_catalogue = load_preprocessed_catalogue(tmp_path)
    app.state = SimpleNamespace(selected_dataset=data, selected_street=street)
    app._preprocessed_status = lambda record: SimpleNamespace(export_allowed=True)
    app.current_face_var = SimpleNamespace(value='', set=lambda value: setattr(app.current_face_var, 'value', value))
    assert app._qa_export_error() is None
    app._refresh_current_face_label()
    assert 'Cached face recovery' in app.current_face_var.value
