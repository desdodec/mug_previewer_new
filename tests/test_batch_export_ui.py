from pathlib import Path
import queue
import threading
from types import SimpleNamespace

import pytest

from mug_previewer.ui import batch_export_panel as ui


class Widget:
    def __init__(self, value=''):
        self.value = value
        self.options = {}
    def get(self):
        return self.value
    def set(self, value):
        self.value = value
    def configure(self, **kwargs):
        self.options.update(kwargs)


@pytest.fixture
def panel():
    p = ui.BatchExportPanel.__new__(ui.BatchExportPanel)
    p.app = SimpleNamespace(state=SimpleNamespace(selected_dataset=SimpleNamespace(id='chosen', display_name='Chosen'),
                            filtered_streets=[], design_options=None),
                            preprocessed_catalogue=SimpleNamespace(root=Path('prepared')),
                            dataset_box=Widget(), _shutting_down=False)
    p.plan = None
    p.busy = False
    p.events = queue.SimpleQueue()
    p.cancel_event = threading.Event()
    p.generation = 0
    p.report_path = None
    for name in ('provider_box', 'choose', 'policy_box', 'start', 'cancel', 'refresh',
                 'summary', 'progress', 'report_button'):
        setattr(p, name, Widget())
    p.provider = Widget('Printify')
    p.destination = Widget('output')
    p.policy = Widget('Skip existing')
    p.after = lambda *a: None
    return p


def planned(ready=3):
    return SimpleNamespace(dataset=SimpleNamespace(id='chosen', display_name='Chosen'),
        summary=SimpleNamespace(total=5, ready=ready, manual_review=1, qa_blocked=0, not_reviewed=0,
                                excluded=1, unrenderable=0, asset_errors=0, existing=0))


def capture_threads(monkeypatch):
    calls = []
    class Thread:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def start(self):
            pass
    monkeypatch.setattr(ui.threading, 'Thread', Thread)
    return calls


def test_plan_worker_scope_ignores_visible_filters_and_passes_provider(panel, monkeypatch):
    calls = capture_threads(monkeypatch)
    panel.app.state.filtered_streets = []
    panel.app.state.street_filter = 'nothing'
    panel.refresh_plan()
    assert len(calls) == 1 and panel.busy
    generation, args, options = calls[0]['args']
    assert args[1] is panel.app.state.selected_dataset
    assert args[2] == 'printify_generic_11oz_ceramic'
    assert args[3] == Path('output')
    assert options['replace_existing'] is False
    assert panel.start.options['state'] == 'disabled'
    assert panel.provider_box.options['state'] == 'disabled'
    assert panel.app.dataset_box.options['state'] == 'disabled'


@pytest.mark.parametrize('missing', ['dataset', 'destination'])
def test_plan_requires_dataset_and_destination(panel, monkeypatch, missing):
    calls = capture_threads(monkeypatch)
    if missing == 'dataset':
        panel.app.state.selected_dataset = None
    else:
        panel.destination.set('')
    panel.refresh_plan()
    assert not calls and panel.plan is None
    assert panel.start.options['state'] == 'disabled'


@pytest.mark.parametrize('ready', [0, 3])
def test_plan_result_updates_counts_and_start_safely(panel, ready):
    panel.events.put(('plan', 0, planned(ready)))
    panel.drain()
    assert panel.start.options['state'] == ('normal' if ready else 'disabled')
    assert panel.start.options['text'] == f'Export {ready} Production-Ready PNGs'
    assert 'Manual review: 1' in panel.summary.get()
    assert 'Do Not Use: 1' in panel.summary.get()


def test_export_thread_and_duplicate_launch_guard(panel, monkeypatch):
    calls = capture_threads(monkeypatch)
    panel.plan = planned()
    panel.start_batch()
    panel.start_batch()
    panel.refresh_plan()
    assert len(calls) == 1 and calls[0]['target'] == panel.export_worker
    assert calls[0]['args'][1] is panel.plan
    assert panel.busy and panel.cancel.options['state'] == 'normal'


@pytest.mark.parametrize('reason', ['empty', 'zero', 'dataset_changed'])
def test_invalid_start_does_not_spawn(panel, monkeypatch, reason):
    calls = capture_threads(monkeypatch)
    panel.plan = planned(0 if reason == 'zero' else 3)
    if reason == 'empty':
        panel.destination.set('')
    if reason == 'dataset_changed':
        panel.app.state.selected_dataset = SimpleNamespace(id='other')
    panel.start_batch()
    assert not calls


def test_worker_only_queues_progress_and_actual_result(panel, monkeypatch):
    result = SimpleNamespace(report_path=Path('output/report.json'), cancelled=False,
        summary=dict(exported=1, failed=1, skipped_existing=1, manual_review=1,
                     qa_blocked=0, not_reviewed=0, excluded=1, unrenderable=0, asset_errors=0, cancelled=0))
    progress = SimpleNamespace(current=1, total=3,
        result=SimpleNamespace(item=SimpleNamespace(street_id='0001', street_name='One'), result='EXPORTED'))
    def execute(plan, **kwargs):
        kwargs['on_progress'](progress)
        return result
    monkeypatch.setattr(ui, 'execute_batch_export', execute)
    panel.export_worker(0, planned())
    assert panel.summary.get() == '' and panel.progress.get() == ''
    panel.drain()
    assert '1 exported, 1 failed' in panel.summary.get()
    assert '1 existing' in panel.summary.get()
    assert panel.report_path == result.report_path and panel.plan is None
    assert panel.start.options['state'] == 'disabled'


def test_stale_plan_message_is_ignored(panel):
    panel.generation = 3
    panel.events.put(('plan', 2, planned()))
    panel.drain()
    assert panel.plan is None


def test_plan_error_clears_previous_plan(panel):
    panel.plan = planned()
    panel.events.put(('error', 0, 'QA ledger is invalid'))
    panel.drain()
    assert panel.plan is None and 'QA ledger is invalid' in panel.summary.get()


def test_shutdown_requests_cooperative_cancel(panel):
    panel.app._shutting_down = True
    panel.drain()
    assert panel.cancel_event.is_set()

def test_batch_window_is_preprocessed_only(monkeypatch):
    from mug_previewer.ui.app import MugPreviewerApp
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    app.preprocessed_catalogue = None
    monkeypatch.setattr('mug_previewer.ui.app.tk.Toplevel', lambda *a: pytest.fail('live mode must not open batch UI'))
    app._open_batch_window()
    assert 'batch_window' not in app.__dict__


def test_open_batch_selects_integrated_panel():
    from mug_previewer.ui.app import MugPreviewerApp
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    app.preprocessed_catalogue = object()
    actions = []
    app.batch_panel = object()
    app.workflow_tabs = SimpleNamespace(select=lambda panel: actions.append(panel))
    app._open_batch_window()
    assert actions == [app.batch_panel]


def test_app_shutdown_cancels_running_batch():
    from mug_previewer.ui.app import MugPreviewerApp
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    app._shutting_down = False
    app._resize_pending = None
    app._production_status_generation = 0
    app._render_generation = 0
    app.batch_panel = SimpleNamespace(cancel_event=threading.Event())
    app.root = SimpleNamespace(destroy=lambda: None)
    app._shutdown()
    assert app.batch_panel.cancel_event.is_set()


def test_normal_app_poll_does_not_cancel_batch():
    from mug_previewer.ui.app import MugPreviewerApp
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    app._shutting_down = False
    app.batch_panel = SimpleNamespace(cancel_event=threading.Event())
    scheduled = []
    app.root = SimpleNamespace(after=lambda *args: scheduled.append(args))
    app._schedule_main_thread_poll(lambda: None)
    assert scheduled and not app.batch_panel.cancel_event.is_set()
