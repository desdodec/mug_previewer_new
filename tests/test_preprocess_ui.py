from __future__ import annotations

from pathlib import Path
import time

from mug_previewer.preprocess import PreprocessProgress, PreprocessSummary
from mug_previewer.cli import main
from mug_previewer.ui.preprocess_ui import PreprocessWorker, WorkerFinished, _counts_text, default_output_path


def test_preprocess_ui_default_output_is_under_dataset_root(tmp_path: Path) -> None:
    assert default_output_path(tmp_path) == tmp_path / "svg_previews"


def test_preprocess_ui_command_passes_dataset_root(monkeypatch, tmp_path: Path) -> None:
    captured = {}
    monkeypatch.setattr("mug_previewer.ui.preprocess_ui.launch", lambda *, dataset_root=None: captured.update(dataset_root=dataset_root) or 0)

    assert main(["--dataset-root", str(tmp_path), "preprocess-ui"]) == 0
    assert captured == {"dataset_root": tmp_path}


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
