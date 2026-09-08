from types import SimpleNamespace
import queue
import threading

import pytest
from PIL import Image

from test_batch_export import prepared
from test_artwork_panel import panel
from mug_previewer.batch_export import Eligibility, build_batch_plan
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus as Status
from mug_previewer.review_index import save_review_record
from mug_previewer.ui.workspace import WORKFLOW_FILTERS, filter_workflow, load_workflow, workflow_counts
from mug_previewer.ui import state as state_module
from test_manual_svg_workspace import svg


@pytest.mark.parametrize('selected', WORKFLOW_FILTERS)
def test_workflow_filters_are_navigation_only(prepared, selected):
    root, data, records, write = prepared
    records[1]['production_state'] = 'MANUAL_REVIEW'
    records[2]['production_state'] = 'UNRENDERABLE_INPUT'
    write()
    save_review_record(root, data.id, '0003', 'Do Not Use', root / '0003.svg')
    save_review_record(root, data.id, '0004', 'overlap', root / '0004.svg')
    items = load_workflow(root, data)
    counts = workflow_counts(items)
    assert counts == {'All': 11, 'Included': 8, 'Excluded': 2, 'Edited': 0, 'Needs Attention': 2}
    visible = filter_workflow(data.streets, items, selected)
    assert len(visible) == counts[selected]
    plan = build_batch_plan(root, data, 'inkthreadable_11oz_white', root / 'out')
    assert len(plan.items) == 11 and plan.summary.ready == 8


def test_prepared_mug_preview_uses_existing_wrap_not_face_generator(monkeypatch):
    calls = []
    wrap = Image.new('RGBA', (990, 462))
    def compose(*args, **kwargs):
        calls.append((args, kwargs))
        return wrap
    monkeypatch.setattr('mug_previewer.preprocessed_export.render_preprocessed_wrap', compose)
    monkeypatch.setattr(state_module, 'render_wrap_result', lambda *a, **k: pytest.fail('face regeneration'))
    monkeypatch.setattr(state_module, 'render_mug_preview', lambda image, options: Image.new('RGBA', (512, 768)))
    pair = state_module.render_prepared_preview_pair('root', 'dataset', 'street')
    assert pair.wrap is wrap
    assert calls[0][0] == ('root', 'dataset', 'street')
    assert calls[0][1]['require_production_approved'] is False


def test_manual_review_pass_stays_blocked(tmp_path):
    app = panel(tmp_path, Status.MANUAL_REVIEW)
    save_review_record(tmp_path, 'area', '0001', 'pass', app._formal_review_svg())
    app._refresh_selected_artwork()
    assert app.export_button.state == 'normal'


def test_background_batch_allows_main_thread_work(monkeypatch):
    from test_batch_export_ui import panel as fixture
    from mug_previewer.ui import batch_export_panel as module
    p = fixture.__wrapped__()
    entered, release = threading.Event(), threading.Event()
    def planner(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return object()
    monkeypatch.setattr(module, 'build_batch_plan', planner)
    try:
        p.refresh_plan()
        assert entered.wait(2)
        assert p.busy
        p.app.state.filtered_streets = ['browsing remains available']
        p.cancel_event.set()
        assert p.app.state.filtered_streets
    finally:
        release.set()


def test_stale_workflow_worker_result_cannot_replace_new_dataset():
    from mug_previewer.ui.app import MugPreviewerApp
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    app._shutting_down = False
    app._workflow_generation = 2
    app._workflow_results = queue.SimpleQueue()
    app._workflow_results.put((1, {'old': object()}, None))
    app.workflow_items = {'new': object()}
    app._schedule_main_thread_poll = lambda callback: None
    app._drain_workflow_results()
    assert list(app.workflow_items) == ['new']


def test_mug_result_keeps_face_preview_separate(tmp_path):
    app = panel(tmp_path, Status.AUTO_APPROVED)
    face = app.state.current_front_preview
    pair = state_module.PreviewPair(Image.new('RGBA', (990, 462)),
                                   Image.new('RGBA', (512, 768)), Image.new('RGBA', (512, 768)), 'prepared')
    app._render_finished(pair, app.state.selected_dataset, app.state.selected_street)
    assert app.state.current_front_preview is face
    assert app._mug_front_image is pair.front
    assert app.state.current_rear_preview is pair.rear


def test_asset_error_is_separate_from_qa_and_does_not_change_scope(prepared):
    root, data, records, write = prepared
    items = load_workflow(root, data)
    ready = next(item for item in items.values() if item.eligibility == Eligibility.READY)
    from dataclasses import replace
    items[ready.street_id] = replace(ready, eligibility=Eligibility.ASSET_ERROR)
    counts = workflow_counts(items)
    assert counts['Needs Attention'] == 1
    assert [street.id for street in filter_workflow(data.streets, items, 'Needs Attention')] == [ready.street_id]
    assert len(build_batch_plan(root, data, 'inkthreadable_11oz_white', root / 'out').items) == 11
