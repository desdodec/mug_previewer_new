from types import SimpleNamespace
import queue
import pytest

from test_preprocessed_ui import _catalogue, _controller
from test_batch_export_ui import panel, Widget, capture_threads, planned
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus as Status
from mug_previewer.ui.face_grid import FaceGrid
from mug_previewer.review_index import set_excluded


def controller(tmp_path):
    catalogue = _catalogue(tmp_path, Status.MANUAL_REVIEW, preview=False)
    path = tmp_path / 'faces/street.svg'
    path.parent.mkdir()
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462" viewBox="0 0 990 462"><rect width="495" height="462"/></svg>')
    street = SimpleNamespace(id='0001', display_name='Acre Villas')
    app = _controller(catalogue, street)
    app.status_var = Widget()
    app.export_state_var = Widget()
    app.selected_export_face_var = Widget()
    return app, street


@pytest.mark.parametrize('route', ['grid', 'list'])
def test_selection_enables_both_exports_without_cached_preview(tmp_path, route):
    app, street = controller(tmp_path)
    if route == 'grid':
        app.street_list.selection_clear = lambda *a: None
        app.street_list.selection_set = lambda *a: None
        app.street_list.activate = lambda *a: None
        app.street_list.see = lambda *a: None
        FaceGrid.select(SimpleNamespace(app=app), street)
    else:
        app._select_street()
    assert app.export_button.state == app.printify_export_button.state == 'normal'
    assert app.state.current_wrap is None
    assert app.selected_export_face_var.get() == '0001 \u2014 Acre Villas'


@pytest.mark.parametrize('blocked', ['selection', 'missing', 'excluded', 'busy', 'invalid'])
def test_disabled_exports_explain_why(tmp_path, blocked):
    app, street = controller(tmp_path)
    app.state.selected_street = street
    path = tmp_path / 'faces/street.svg'
    if blocked == 'selection':
        app.state.selected_street = None
    elif blocked == 'missing':
        path.unlink()
    elif blocked == 'invalid':
        path.write_text('<broken')
    elif blocked == 'excluded':
        set_excluded(tmp_path, 'area', street.id, True)
    else:
        app._single_export_busy = True
    app._set_export_buttons_state('normal')
    assert app.export_button.state == app.printify_export_button.state == 'disabled'
    assert 'BLOCKED - ' in app.export_state_var.get()
    assert 'temporarily unavailable' not in app.export_state_var.get()


def test_workflow_refresh_preserves_selected_street(tmp_path):
    app, street = controller(tmp_path)
    app.state.selected_street = street
    app.state.selected_dataset.streets = [street]
    app.search_var = Widget('')
    app._invalidate_active_production_status_request = lambda: None
    app.street_list.delete = lambda *a: None
    app.street_list.insert = lambda *a: None
    app.street_list.selection_set = lambda *a: None
    app._shutting_down = False
    app._workflow_generation = 1
    app._workflow_results = queue.SimpleQueue()
    app._workflow_results.put((1, {}, None))
    app.workflow_counts_var = Widget()
    app._schedule_main_thread_poll = lambda *a: None
    app._drain_workflow_results()
    assert app.state.selected_street is street
    assert app.export_button.state == app.printify_export_button.state == 'normal'


def test_valid_inputs_automatically_plan_and_enable_batch(panel, monkeypatch):
    callbacks = []
    panel.after = lambda delay, callback: callbacks.append(callback)
    calls = capture_threads(monkeypatch)
    panel.invalidate()
    callbacks.pop(0)()
    assert len(calls) == 1
    panel.events.put(('plan', panel.generation, planned()))
    panel.drain()
    assert panel.start.options['state'] == 'normal'
    assert panel.start.options['text'] == 'Export 3 Printify PNGs'


def test_width_change_automatically_rebuilds(panel, monkeypatch):
    from test_rear_highlight_control import design_controller
    app = design_controller()
    app.batch_panel = panel
    panel.app.state.design_options = app.state.design_options
    callbacks = []
    panel.after = lambda delay, callback: callbacks.append(callback)
    calls = capture_threads(monkeypatch)
    panel.plan = planned()
    app.rear_weight_var.set(0.75)
    app._design_changed()
    panel.app.state.design_options = app.state.design_options
    assert panel.plan is None
    callbacks.pop(0)()
    assert calls[0]['args'][2]['design_options'].rear_highlight_weight == 0.75


from test_batch_export import prepared, PROVIDERS


@pytest.mark.parametrize('provider', PROVIDERS)
def test_single_worker_and_batch_preserve_svgs_without_preview(prepared, monkeypatch, provider):
    from dataclasses import replace
    from mug_previewer.ui.app import MugPreviewerApp
    from mug_previewer.ui.state import load_preprocessed_catalogue
    from mug_previewer.design import DesignOptions
    from mug_previewer.batch_export import build_batch_plan, execute_batch_export
    from test_rear_highlight_control import svg_snapshot
    root, data, records, write = prepared
    data = replace(data, streets=data.streets[:1])
    (root / 'svg_review_results.json').unlink()
    records[0]['production_state'] = 'MANUAL_REVIEW'
    write()
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    app.preprocessed_catalogue = load_preprocessed_catalogue(root)
    app.state = SimpleNamespace(selected_dataset=data, selected_street=data.streets[0],
                                design_options=DesignOptions(), current_wrap=None)
    app.current_production_status = None
    app.status_var = Widget()
    app.export_button = Widget()
    app.printify_export_button = Widget()
    app.root = SimpleNamespace(after=lambda delay, callback: callback())
    app._show_error = lambda message: pytest.fail(message)
    destination = root / 'single.png'
    monkeypatch.setattr('mug_previewer.ui.app.filedialog.asksaveasfilename', lambda **kwargs: str(destination))
    calls = capture_threads(monkeypatch)
    before = svg_snapshot(root)
    app._start_provider_export(profile_id=provider, provider_label='Test', filename_suffix='test')
    assert len(calls) == 1
    calls[0]['target'](*calls[0]['args'])
    assert destination.is_file()
    assert svg_snapshot(root) == before
    plan = build_batch_plan(root, data, provider, root / 'batch')
    result = execute_batch_export(plan)
    assert result.summary['exported'] == 1
    assert svg_snapshot(root) == before
