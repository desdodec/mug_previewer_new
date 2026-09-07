from pathlib import Path
import shutil

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.preprocess import PreprocessSummary
from mug_previewer.ui.preprocess_ui import PreprocessWorker, WorkerFinished
from mug_previewer.ui.state import PreprocessedCatalogue, PreprocessedRecord
from mug_previewer.ui.workspace_app import face_generation_state, preprocess_summary_text
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def _dataset(tmp_path):
    copied = tmp_path / "dataset"
    shutil.copytree(FIXTURE, copied)
    return load_dataset(copied)


def _record(dataset_id, street_id):
    return PreprocessedRecord(
        dataset_id=dataset_id,
        street_id=street_id,
        state=ProductionTriageStatus.AUTO_APPROVED,
        reason_detail="",
        success=True,
        preview_path=None,
        svg_path=None,
        editable_svg_path=None,
    )


def test_unprepared_dataset_offers_generate_faces(tmp_path):
    dataset = _dataset(tmp_path)
    catalogue = PreprocessedCatalogue(tmp_path, {})
    state = face_generation_state(catalogue, dataset)
    assert (state.prepared, state.total, state.button_text, state.enabled) == (
        0, len(dataset.streets), "Generate Faces", True
    )


def test_partially_prepared_dataset_offers_resumable_generation(tmp_path):
    dataset = _dataset(tmp_path)
    first = dataset.streets[0]
    catalogue = PreprocessedCatalogue(
        tmp_path, {(dataset.id, first.id): _record(dataset.id, first.id)}
    )
    state = face_generation_state(catalogue, dataset)
    assert state.prepared == 1
    assert state.button_text == "Generate Missing Faces"
    assert state.enabled


def test_fully_prepared_dataset_does_not_offer_regeneration(tmp_path):
    dataset = _dataset(tmp_path)
    catalogue = PreprocessedCatalogue(
        tmp_path,
        {(dataset.id, street.id): _record(dataset.id, street.id) for street in dataset.streets},
    )
    state = face_generation_state(catalogue, dataset)
    assert state.prepared == len(dataset.streets)
    assert state.button_text == "Faces Generated"
    assert not state.enabled


def test_busy_generation_blocks_duplicate_start(tmp_path):
    dataset = _dataset(tmp_path)
    state = face_generation_state(PreprocessedCatalogue(tmp_path, {}), dataset, busy=True)
    assert state.button_text == "Generating Faces..."
    assert not state.enabled


def test_preprocess_worker_uses_resumable_backend_without_force(tmp_path):
    dataset = _dataset(tmp_path)
    calls = []

    def runner(datasets, output, *, street_ids=None, progress=None):
        calls.append((datasets, output, street_ids, progress))
        return PreprocessSummary(processed=1, reused=1, auto_approved=1)

    worker = PreprocessWorker(runner=runner)
    worker.start((dataset,), tmp_path)
    finished = None
    for _ in range(1000):
        for event in worker.drain():
            if isinstance(event, WorkerFinished):
                finished = event
        if finished is not None:
            break
    assert finished is not None and finished.error is None
    assert calls and calls[0][0] == (dataset,)
    assert calls[0][1] == tmp_path
    assert calls[0][2] is None


def test_summary_text_shows_production_outcomes():
    text = preprocess_summary_text(
        PreprocessSummary(
            processed=4,
            reused=2,
            auto_approved=3,
            manual_review=2,
            manual_approved=1,
            unrenderable_input=0,
        )
    )
    assert "Processed: 4" in text
    assert "Reused: 2" in text
    assert "AUTO_APPROVED: 3" in text
    assert "MANUAL_REVIEW: 2" in text
    assert "MANUAL_APPROVED: 1" in text
    assert "UNRENDERABLE: 0" in text
