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
    assert counts == {'All': 11, 'Production Ready': 7, 'Manual Review': 1,
                      'QA Attention': 1, 'Do Not Use': 1, 'Unrenderable': 1}
    visible = filter_workflow(data.streets, items, selected)
    assert len(visible) == counts[selected]
    plan = build_batch_plan(root, data, 'inkthreadable_11oz_white', root / 'out')
    assert len(plan.items) == 11 and plan.summary.ready == 7


def test_refresh_reloads_external_qa_without_selection_change(tmp_path):
    app = panel(tmp_path, Status.AUTO_APPROVED)
    assert app.export_button.state == 'disabled'
    save_review_record(tmp_path, 'area', '0001', 'pass', app._formal_review_svg())
    app._refresh_selected_artwork()
    assert app.export_button.state == 'normal'
    save_review_record(tmp_path, 'area', '0001', 'other', app._formal_review_svg())
    app._refresh_selected_artwork()
    assert app.export_button.state == 'disabled'
    assert 'other' in app.qa_display_var.value


def test_edit_again_reapproval_stales_old_pass_immediately(tmp_path):
    app = panel(tmp_path, Status.MANUAL_REVIEW)
    app._workspace().create_or_get_working_edit()
    app._workspace().repair()
    app._approve_artwork()
    save_review_record(tmp_path, 'area', '0001', 'pass', app._formal_review_svg())
    app._refresh_selected_artwork()
    assert app.export_button.state == 'normal'
    app._workspace().create_or_get_working_edit().write_text(svg('blue'))
    app._workspace().repair()
    app._approve_artwork()
    assert app._selected_artwork_record().state == Status.MANUAL_APPROVED
    assert '(stale)' in app.qa_display_var.value
    assert app.export_button.state == 'disabled'


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
    assert app.export_button.state == 'disabled'


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
