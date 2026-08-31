from __future__ import annotations

from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from mug_previewer.preprocess import PreprocessProgress, PreprocessSummary
from mug_previewer.cli import main
import mug_previewer.ui.preprocess_ui as preprocess_ui
from mug_previewer.ui.preprocess_ui import PreprocessLocationError, PreprocessWorker, WorkerFinished, _counts_text, validate_preprocess_locations


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


def test_preprocess_ui_command_passes_dataset_root(monkeypatch, tmp_path: Path) -> None:
    captured = {}
    monkeypatch.setattr("mug_previewer.ui.preprocess_ui.launch", lambda *, dataset_root=None: captured.update(dataset_root=dataset_root) or 0)

    assert main(["--dataset-root", str(tmp_path), "preprocess-ui"]) == 0
    assert captured == {"dataset_root": tmp_path}


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
    candidate = SimpleNamespace(dataset=SimpleNamespace(display_name="Example Dataset"))
    refreshed = []
    monkeypatch.setattr(preprocess_ui.filedialog, "askdirectory", lambda **kwargs: str(input_root))
    monkeypatch.setattr(preprocess_ui, "discover_datasets", lambda path: [candidate])
    controller._refresh_location_state = lambda: refreshed.append(True)
    controller._update_browser_button = lambda: None

    controller._choose_dataset_root()

    assert controller.dataset_root_var.get() == str(input_root)
    assert controller.output_var.get() == ""
    assert controller.scope_box.values == ("All datasets", "Example Dataset")
    assert refreshed == [True]


def test_output_selection_sets_only_the_explicitly_chosen_folder(monkeypatch, tmp_path: Path) -> None:
    controller = _controller(tmp_path / "input", tmp_path / "old-output")
    selected = tmp_path / "chosen-output"
    refreshed = []
    monkeypatch.setattr(preprocess_ui.filedialog, "askdirectory", lambda **kwargs: str(selected))
    controller._refresh_location_state = lambda: refreshed.append(True)
    controller._update_browser_button = lambda: None

    controller._choose_output()

    assert controller.output_var.get() == str(selected)
    assert refreshed == [True]


def test_start_passes_exact_selected_paths_to_worker(monkeypatch, tmp_path: Path) -> None:
    input_root, output_root = tmp_path / "input", tmp_path / "output"
    candidate = SimpleNamespace(dataset="Selected Dataset")
    controller = _controller(input_root, output_root)
    captured = {}

    class Worker:
        def start(self, datasets, output):
            captured.update(datasets=datasets, output=output)

    monkeypatch.setattr(preprocess_ui, "validate_preprocess_locations", lambda *args: (input_root, output_root, [candidate]))
    monkeypatch.setattr(preprocess_ui, "PreprocessWorker", Worker)

    controller._start()

    assert captured == {"datasets": ["Selected Dataset"], "output": output_root}
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
