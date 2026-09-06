from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

import pytest
from mug_previewer.datasets.discovery import discover_datasets
from mug_previewer.preprocess import PreprocessProgress, PreprocessSummary
from mug_previewer.cli import DEFAULT_PREPROCESS_UI_DATASET_ROOT, DEFAULT_PREPROCESS_UI_OUTPUT_ROOT, main
import mug_previewer.ui.preprocess_ui as preprocess_ui
from mug_previewer.ui.preprocess_ui import PreprocessLocationError, PreprocessWorker, WorkerFinished, _counts_text, validate_preprocess_locations


FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def _make_dataset(path: Path, name: str) -> Path:
    shutil.copytree(FIXTURE, path)
    summary_path = path / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["place_name"] = f"{name.lower()}, United Kingdom"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    return path


def test_preprocess_locations_require_separate_explicit_input_and_output(tmp_path: Path) -> None:
    input_root, output_root = tmp_path / "input", tmp_path / "output"
    input_root.mkdir()
    discovered = [SimpleNamespace(dataset="Test Dataset")]

    resolved_input, resolved_output, datasets = validate_preprocess_locations(
        input_root, output_root, discover=lambda path: discovered,
    )

    assert (resolved_input, resolved_output, datasets) == (input_root, output_root, discovered)
    assert output_root.is_dir()
    with pytest.raises(PreprocessLocationError, match="separate"):
        validate_preprocess_locations(input_root, input_root, discover=lambda path: discovered)


def test_preprocess_locations_reject_invalid_input_and_output(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "output"
    with pytest.raises(PreprocessLocationError, match="Input Dataset Folder does not exist"):
        validate_preprocess_locations(tmp_path / "missing", output_root, discover=lambda path: [])

    input_root, output_file = tmp_path / "input", tmp_path / "output-file"
    input_root.mkdir()
    output_file.write_text("not a folder", encoding="utf-8")
    with pytest.raises(PreprocessLocationError, match="not a folder"):
        validate_preprocess_locations(input_root, output_file, discover=lambda path: [object()])

    unwritable = tmp_path / "unwritable"
    monkeypatch.setattr(preprocess_ui.tempfile, "NamedTemporaryFile", lambda **kwargs: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(PreprocessLocationError, match="not writable"):
        validate_preprocess_locations(input_root, unwritable, discover=lambda path: [object()])


def test_preprocess_locations_require_a_discoverable_dataset(tmp_path: Path) -> None:
    input_root, output_root = tmp_path / "input", tmp_path / "output"
    input_root.mkdir()
    with pytest.raises(PreprocessLocationError, match="No valid workflow datasets"):
        validate_preprocess_locations(input_root, output_root, discover=lambda path: [])


def test_discovery_accepts_dataset_root_and_direct_dataset_folder(tmp_path: Path) -> None:
    root = tmp_path / "workflow_outputs_v6"
    root.mkdir()
    glasgow = _make_dataset(root / "Glasgow", "Glasgow")
    godalming = _make_dataset(root / "Godalming", "Godalming")

    assert [item.dataset.display_name for item in discover_datasets(root)] == ["Glasgow", "Godalming"]
    direct = discover_datasets(godalming)
    assert [item.dataset.display_name for item in direct] == ["Godalming"]
    assert direct[0].dataset.path == godalming
    assert glasgow.is_dir()


def test_discovery_rejects_an_arbitrary_folder(tmp_path: Path) -> None:
    arbitrary = tmp_path / "arbitrary"
    arbitrary.mkdir()

    assert discover_datasets(arbitrary) == []


def test_preprocess_ui_command_passes_explicit_input_and_default_output(monkeypatch, tmp_path: Path) -> None:
    captured = {}
    monkeypatch.setattr(
        "mug_previewer.ui.preprocess_ui.launch",
        lambda *, dataset_root=None, output_root=None: captured.update(
            dataset_root=dataset_root,
            output_root=output_root,
        ) or 0,
    )

    assert main(["--dataset-root", str(tmp_path), "preprocess-ui"]) == 0
    assert captured == {
        "dataset_root": tmp_path,
        "output_root": DEFAULT_PREPROCESS_UI_OUTPUT_ROOT,
    }


def test_preprocess_ui_command_uses_requested_default_folders(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        "mug_previewer.ui.preprocess_ui.launch",
        lambda *, dataset_root=None, output_root=None: captured.update(
            dataset_root=dataset_root,
            output_root=output_root,
        ) or 0,
    )

    assert main(["preprocess-ui"]) == 0
    assert captured == {
        "dataset_root": DEFAULT_PREPROCESS_UI_DATASET_ROOT,
        "output_root": DEFAULT_PREPROCESS_UI_OUTPUT_ROOT,
    }


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Widget:
    def __init__(self):
        self.state = None
        self.values = None

    def configure(self, **kwargs):
        self.state = kwargs.get("state", self.state)

    def __setitem__(self, key, value):
        if key == "values":
            self.values = value


class _Root:
    def __init__(self):
        self.after_calls = []
        self.destroyed = False

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))

    def destroy(self):
        self.destroyed = True


def _controller(input_root: Path, output_root: Path):
    controller = preprocess_ui.PreprocessUiApp.__new__(preprocess_ui.PreprocessUiApp)
    controller.root = _Root()
    controller.dataset_root_var = _Var(str(input_root))
    controller.output_var = _Var(str(output_root))
    controller.scope_var = _Var("All datasets")
    controller.progress_var = _Var()
    controller.start_button = _Widget()
    controller.browser_button = _Widget()
    controller.scope_box = _Widget()
    controller.worker = None
    return controller


def test_input_selection_discovers_datasets_without_setting_output(monkeypatch, tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    controller = _controller(input_root, tmp_path / "output")
    controller.output_var.set("")
    candidates = [
        SimpleNamespace(dataset=SimpleNamespace(display_name="Glasgow", path=input_root / "Glasgow")),
        SimpleNamespace(dataset=SimpleNamespace(display_name="Godalming", path=input_root / "Godalming")),
    ]
    refreshed = []
    chooser_options = {}
    monkeypatch.setattr(
        preprocess_ui.filedialog,
        "askdirectory",
        lambda **kwargs: chooser_options.update(kwargs) or str(input_root),
    )
    monkeypatch.setattr(preprocess_ui, "discover_datasets", lambda path: candidates)
    controller._refresh_location_state = lambda: refreshed.append(True)
    controller._update_browser_button = lambda: None

    controller._choose_dataset_root()

    assert controller.dataset_root_var.get() == str(input_root)
    assert controller.output_var.get() == ""
    assert controller.scope_box.values == ("All datasets", "Glasgow", "Godalming")
    assert chooser_options["initialdir"] == str(input_root)
    assert refreshed == [True]


def test_direct_dataset_selection_sets_single_scope_and_keeps_output_independent(monkeypatch, tmp_path: Path) -> None:
    direct_dataset = tmp_path / "workflow_outputs_v6" / "Godalming"
    direct_dataset.mkdir(parents=True)
    controller = _controller(direct_dataset, tmp_path / "output")
    controller.output_var.set("")
    candidate = SimpleNamespace(dataset=SimpleNamespace(display_name="Godalming", path=direct_dataset))
    refreshed = []
    monkeypatch.setattr(preprocess_ui.filedialog, "askdirectory", lambda **kwargs: str(direct_dataset))
    monkeypatch.setattr(preprocess_ui, "discover_datasets", lambda path: [candidate])
    controller._refresh_location_state = lambda: refreshed.append(True)
    controller._update_browser_button = lambda: None

    controller._choose_dataset_root()

    assert controller.dataset_root_var.get() == str(direct_dataset)
    assert controller.output_var.get() == ""
    assert controller.scope_box.values == ("Godalming",)
    assert controller.scope_var.get() == "Godalming"
    assert refreshed == [True]


def test_direct_dataset_with_valid_output_enables_start(tmp_path: Path) -> None:
    direct_dataset = _make_dataset(tmp_path / "workflow_outputs_v6" / "Godalming", "Godalming")
    output = tmp_path / "preprocessed"
    controller = _controller(direct_dataset, output)

    controller._refresh_location_state()

    assert controller.start_button.state == "normal"
    assert controller.scope_box.values == ("Godalming",)
    assert controller.scope_var.get() == "Godalming"
    assert output.is_dir()


def test_output_selection_sets_only_the_explicitly_chosen_folder(monkeypatch, tmp_path: Path) -> None:
    controller = _controller(tmp_path / "input", tmp_path / "old-output")
    selected = tmp_path / "chosen-output"
    refreshed = []
    chooser_options = {}
    monkeypatch.setattr(
        preprocess_ui.filedialog,
        "askdirectory",
        lambda **kwargs: chooser_options.update(kwargs) or str(selected),
    )
    controller._refresh_location_state = lambda: refreshed.append(True)
    controller._update_browser_button = lambda: None

    controller._choose_output()

    assert controller.output_var.get() == str(selected)
    assert chooser_options["initialdir"] == str(tmp_path / "old-output")
    assert refreshed == [True]


def test_start_passes_exact_selected_paths_to_worker(monkeypatch, tmp_path: Path) -> None:
    input_root, output_root = tmp_path / "input", tmp_path / "output"
    glasgow = SimpleNamespace(display_name="Glasgow")
    godalming = SimpleNamespace(display_name="Godalming")
    candidates = [SimpleNamespace(dataset=glasgow), SimpleNamespace(dataset=godalming)]
    controller = _controller(input_root, output_root)
    controller.scope_var.set("Godalming")
    captured = {}

    class Worker:
        def start(self, datasets, output):
            captured.update(datasets=datasets, output=output)

    monkeypatch.setattr(preprocess_ui, "validate_preprocess_locations", lambda *args: (input_root, output_root, candidates))
    monkeypatch.setattr(preprocess_ui, "PreprocessWorker", Worker)

    controller._start()

    assert captured == {"datasets": [godalming], "output": output_root}
    assert controller.root.after_calls and controller.start_button.state == "disabled"


def test_existing_output_can_launch_browser_with_exact_paths(monkeypatch, tmp_path: Path) -> None:
    input_root, output_root = tmp_path / "input", tmp_path / "output"
    output_root.mkdir()
    (output_root / "preprocess_index.json").write_text('{"records": []}', encoding="utf-8")
    controller = _controller(input_root, output_root)
    captured = {}
    monkeypatch.setattr(preprocess_ui, "validate_preprocess_locations", lambda *args: (input_root, output_root, [object()]))
    monkeypatch.setattr("mug_previewer.ui.app.launch", lambda *, dataset_root=None, preprocessed=None: captured.update(dataset_root=dataset_root, preprocessed=preprocessed) or 0)

    controller._launch_browser()

    assert controller.root.destroyed is True
    assert captured == {"dataset_root": input_root, "preprocessed": output_root}


def test_preprocess_ui_worker_hands_progress_to_main_thread_queue(tmp_path: Path) -> None:
    summary = PreprocessSummary(processed=1, manual_review=1)

    def runner(_datasets, _output, *, street_ids, progress):
        assert street_ids is None
        progress(PreprocessProgress(1, 1, "Area", "0001", "Street", summary))
        return summary

    worker = PreprocessWorker(runner)
    worker.start([], tmp_path)
    deadline = time.monotonic() + 1
    events = []
    while time.monotonic() < deadline and not any(isinstance(event, WorkerFinished) for event in events):
        events.extend(worker.drain())
        time.sleep(0.01)

    assert isinstance(events[0], PreprocessProgress)
    assert events[0].summary.manual_review == 1
    assert events[-1] == WorkerFinished(summary, None)
    assert "MANUAL_REVIEW: 1" in _counts_text(summary)
