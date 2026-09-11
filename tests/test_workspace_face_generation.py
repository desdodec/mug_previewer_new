from dataclasses import replace
from pathlib import Path
import json
import shutil

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.preprocess import PreprocessSummary
from mug_previewer.ui.preprocess_ui import PreprocessWorker, WorkerFinished
from mug_previewer.ui.state import DatasetOption, PreprocessedCatalogue, PreprocessedRecord
from mug_previewer.ui.workspace_app import (
    face_generation_state,
    prepared_dataset_options,
    preprocess_summary_text,
    source_dataset_mapping,
)
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


def test_face_generation_ignores_stale_street_records(tmp_path):
    dataset = _dataset(tmp_path)
    catalogue = PreprocessedCatalogue(
        tmp_path,
        {
            (dataset.id, dataset.streets[0].id): _record(dataset.id, dataset.streets[0].id),
            (dataset.id, "stale-id"): _record(dataset.id, "stale-id"),
        },
    )
    state = face_generation_state(catalogue, dataset)
    assert state.prepared == 1
    assert state.total == len(dataset.streets)


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


def test_prepared_dataset_options_only_show_sets_present_in_catalogue(tmp_path):
    prepared = _dataset(tmp_path)
    other = replace(prepared, id="other-dataset", display_name="Other")
    options = [DatasetOption("Prepared", prepared), DatasetOption("Other", other)]
    catalogue = PreprocessedCatalogue(
        tmp_path,
        {(prepared.id, prepared.streets[0].id): _record(prepared.id, prepared.streets[0].id)},
    )
    (tmp_path / "faces" / prepared.id).mkdir(parents=True)

    result = prepared_dataset_options(options, catalogue)

    assert [option.dataset.id for option in result] == [prepared.id]
    assert [option.label for option in result] == [prepared.id]


def test_prepared_dropdown_follows_actual_preprocessed_dataset_folders(tmp_path):
    prepared = _dataset(tmp_path)
    visible_id = prepared.id
    hidden_id = "catalogue-only"
    records = {
        (visible_id, prepared.streets[0].id): _record(visible_id, prepared.streets[0].id),
        (hidden_id, prepared.streets[0].id): _record(hidden_id, prepared.streets[0].id),
    }
    catalogue = PreprocessedCatalogue(tmp_path, records)
    (tmp_path / "previews" / visible_id).mkdir(parents=True)
    (tmp_path / "preprocess_index.json").write_text(json.dumps({"records": [
        {"dataset_id": visible_id, "dataset_name": prepared.display_name,
         "street_id": prepared.streets[0].id, "street_name": prepared.streets[0].display_name},
        {"dataset_id": hidden_id, "dataset_name": "Hidden",
         "street_id": prepared.streets[0].id, "street_name": prepared.streets[0].display_name},
    ]}), encoding="utf-8")

    result = prepared_dataset_options([], catalogue)

    assert [item.label for item in result] == [visible_id]
    assert result[0].dataset.id == visible_id
    assert result[0].dataset.path == tmp_path / "previews" / visible_id
    assert not result[0].dataset.capabilities.context_rendering


def test_face_folders_hide_stale_preview_only_datasets(tmp_path):
    prepared = _dataset(tmp_path)
    visible_id = "20260911_102131_test_borough"
    stale_id = "20260905_150413_test_borough"
    street = prepared.streets[0]
    records = {
        (visible_id, street.id): _record(visible_id, street.id),
        (stale_id, street.id): _record(stale_id, street.id),
    }
    catalogue = PreprocessedCatalogue(tmp_path, records)
    (tmp_path / "faces" / visible_id).mkdir(parents=True)
    (tmp_path / "previews" / visible_id).mkdir(parents=True)
    (tmp_path / "previews" / stale_id).mkdir(parents=True)
    (tmp_path / "preprocess_index.json").write_text(json.dumps({"records": [
        {"dataset_id": visible_id, "dataset_name": prepared.display_name,
         "street_id": street.id, "street_name": street.display_name},
        {"dataset_id": stale_id, "dataset_name": prepared.display_name,
         "street_id": street.id, "street_name": street.display_name},
    ]}), encoding="utf-8")

    result = prepared_dataset_options([], catalogue)

    assert [item.label for item in result] == [visible_id]


def test_no_prepared_folders_means_no_prepared_dropdown_entries(tmp_path):
    prepared = _dataset(tmp_path)
    street = prepared.streets[0]
    catalogue = PreprocessedCatalogue(
        tmp_path, {(prepared.id, street.id): _record(prepared.id, street.id)}
    )

    assert prepared_dataset_options([], catalogue) == []


def test_prepared_folder_stays_visible_when_source_run_is_missing(tmp_path):
    prepared = _dataset(tmp_path)
    prepared_id = "20260905_150413_test_borough_streets_parks_water_boundary_clip"
    street = prepared.streets[0]
    catalogue = PreprocessedCatalogue(
        tmp_path, {(prepared_id, street.id): _record(prepared_id, street.id)}
    )
    (tmp_path / "previews" / prepared_id).mkdir(parents=True)
    (tmp_path / "preprocess_index.json").write_text(json.dumps({"records": [{
        "dataset_id": prepared_id,
        "dataset_name": prepared.display_name,
        "street_id": street.id,
        "street_name": street.display_name,
        "success": True,
        "production_state": "AUTO_APPROVED",
    }]}), encoding="utf-8")

    result = prepared_dataset_options([], catalogue)

    assert [item.label for item in result] == [prepared_id]
    assert result[0].dataset.get_street(street.id) is not None
    assert result[0].dataset.get_street(street.id).context_path is None


def test_source_dropdown_labels_are_real_paths_below_dataset_root(tmp_path):
    root = tmp_path / "OS_Mail_Addresses"
    dataset_path = root / "workflow_outputs_v7" / "20260911_102131_test_borough"
    dataset_path.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE, dataset_path)
    dataset = load_dataset(dataset_path)

    mapping = source_dataset_mapping([DatasetOption("Friendly label", dataset)], root)

    assert list(mapping) == [str(Path("workflow_outputs_v7") / "20260911_102131_test_borough")]
    assert next(iter(mapping.values())).path == dataset_path


def test_prepared_dataset_relinks_to_new_source_run_by_street_names(tmp_path):
    original = _dataset(tmp_path)
    prepared_id = "20260905_150413_test_borough_streets_parks_water_boundary_clip"
    source_id = "20260911_102131_test_borough_streets_parks_water_boundary_clip"
    source_streets = tuple(
        replace(street, id=f"9{index:03d}")
        for index, street in enumerate(original.streets)
    )
    source = replace(original, id=source_id, streets=source_streets)
    records = {
        (prepared_id, street.id): _record(prepared_id, street.id)
        for street in original.streets
    }
    catalogue = PreprocessedCatalogue(tmp_path, records)
    (tmp_path / "faces" / prepared_id).mkdir(parents=True)
    (tmp_path / "preprocess_index.json").write_text(json.dumps({"records": [
        {
            "dataset_id": prepared_id,
            "dataset_name": original.display_name,
            "street_id": street.id,
            "street_name": street.display_name,
            "success": True,
            "production_state": "AUTO_APPROVED",
        }
        for street in original.streets
    ]}), encoding="utf-8")

    result = prepared_dataset_options([DatasetOption("Current source", source)], catalogue)

    assert len(result) == 1
    assert result[0].label == prepared_id
    linked = result[0].dataset
    assert linked.id == prepared_id
    assert linked.display_name == original.display_name
    assert linked.path == source.path
    assert [street.id for street in linked.streets] == [street.id for street in original.streets]
    assert [street.display_name for street in linked.streets] == [street.display_name for street in original.streets]
    assert [street.context_path for street in linked.streets] == [street.context_path for street in source_streets]
    assert any("relinked to source map dataset" in warning for warning in linked.warnings)


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
