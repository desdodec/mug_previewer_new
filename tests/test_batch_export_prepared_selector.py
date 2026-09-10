from pathlib import Path
from types import SimpleNamespace

from mug_previewer.ui import batch_export_panel as ui


class Value:
    def __init__(self, value=''):
        self.value = value
        self.options = {}

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def configure(self, **kwargs):
        self.options.update(kwargs)


class Box(Value):
    def __setitem__(self, key, value):
        self.options[key] = value

    def __getitem__(self, key):
        return self.options[key]


def dataset(identifier, name):
    return SimpleNamespace(id=identifier, display_name=name)


def option(label, data):
    return SimpleNamespace(label=label, dataset=data)


def make_panel():
    hebden = dataset('20260905_150413_hebden', 'Hebden Bridge')
    stoke_a = dataset('20260907_193512_stoke', 'Stoke Newington')
    stoke_b = dataset('20260908_124435_stoke', 'Stoke Newington')
    prepared = [
        option('Hebden Bridge (20260905_150413_hebden)', hebden),
        option('Stoke Newington (20260907_193512_stoke)', stoke_a),
        option('Stoke Newington (20260908_124435_stoke)', stoke_b),
    ]

    panel = ui.BatchExportPanel.__new__(ui.BatchExportPanel)
    panel.plan = None
    panel.busy = False
    panel.generation = 0
    panel.report_path = None
    panel.dataset = Value()
    panel.dataset_by_label = {}
    panel.dataset_box = Box()
    panel.provider = Value('Inkthreadable')
    panel.destination = Value()
    panel.policy = Value('Skip existing')
    panel.start = Value()
    panel.summary = Value()
    panel.refresh = Value()
    panel.folder_button = Value()
    panel.provider_box = Value()
    panel.policy_box = Value()
    panel.choose = Value()
    panel.cancel = Value()
    panel.progress = Value()

    state = SimpleNamespace(
        datasets=prepared,
        selected_dataset=None,
        selected_street=None,
        filtered_streets=[],
        design_options=None,
    )
    app = SimpleNamespace(
        state=state,
        preprocessed_catalogue=SimpleNamespace(root=Path('prepared')),
        dataset_by_label={item.label: item.dataset for item in prepared},
        dataset_var=Value(),
        dataset_box=Value(),
        # Raw source discovery deliberately contains extra historical runs.
        source_dataset_by_label={
            'old raw hebden': dataset('20260905_144653_raw', 'Hebden Bridge'),
            'old raw hebden 2': dataset('20260905_145906_raw', 'Hebden Bridge'),
        },
    )

    def select_main_dataset():
        app.state.selected_dataset = app.dataset_by_label.get(app.dataset_var.get())

    app._select_dataset = select_main_dataset
    panel.app = app
    return panel, prepared


def test_export_selector_lists_prepared_face_sets_not_raw_source_runs():
    panel, prepared = make_panel()

    panel.refresh_dataset_options()

    assert panel.dataset_box['values'] == [item.label for item in prepared]
    assert 'old raw hebden' not in panel.dataset_box['values']
    assert 'old raw hebden 2' not in panel.dataset_box['values']


def test_export_selector_follows_current_prepared_workspace_dataset():
    panel, prepared = make_panel()
    panel.app.state.selected_dataset = prepared[1].dataset

    panel.refresh_dataset_options()

    assert panel.dataset.get() == prepared[1].label
    assert panel._selected_dataset() is prepared[1].dataset


def test_batch_export_uses_explicit_prepared_face_set_without_street_selection(monkeypatch):
    panel, prepared = make_panel()
    panel.refresh_dataset_options()
    panel.dataset.set(prepared[2].label)
    panel.select_export_dataset()
    panel.destination.set('output')

    calls = []

    class Thread:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(ui.threading, 'Thread', Thread)
    panel.refresh_plan()

    assert panel.app.state.selected_dataset is prepared[2].dataset
    assert panel.app.state.selected_street is None
    assert len(calls) == 1
    generation, args, options = calls[0]['args']
    assert args[1] is prepared[2].dataset
    assert args[2] == 'inkthreadable_11oz_white'
    assert args[3] == Path('output')
    assert options['replace_existing'] is False
    assert panel.start.options['state'] == 'disabled'
