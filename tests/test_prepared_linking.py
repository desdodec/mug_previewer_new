from dataclasses import dataclass
import json
from pathlib import Path

from mug_previewer.ui.prepared_linking import prepared_dataset_options
from mug_previewer.ui.state import PreprocessedCatalogue, PreprocessedRecord


def test_every_faces_folder_is_visible_even_without_index_records(tmp_path: Path) -> None:
    faces = tmp_path / "faces"
    (faces / "hebden_bridge").mkdir(parents=True)
    (faces / "stoke_newington").mkdir()
    catalogue = PreprocessedCatalogue(tmp_path, {})

    options = prepared_dataset_options([], catalogue)

    assert [item.label for item in options] == ["hebden_bridge", "stoke_newington"]
    assert [item.dataset.id for item in options] == ["hebden_bridge", "stoke_newington"]
    assert all(item.dataset.format_name == "prepared-only" for item in options)
    assert all(item.dataset.path == faces / item.dataset.id for item in options)


def test_faces_folders_remain_authoritative_over_preview_cache(tmp_path: Path) -> None:
    (tmp_path / "faces" / "current_set").mkdir(parents=True)
    (tmp_path / "previews" / "old_cached_set").mkdir(parents=True)
    catalogue = PreprocessedCatalogue(tmp_path, {})

    options = prepared_dataset_options([], catalogue)

    assert [item.label for item in options] == ["current_set"]


def test_prepared_folder_without_index_relinks_to_matching_source_and_loads_streets(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from mug_previewer.ui.state import DatasetOption

    @dataclass(frozen=True)
    class SourceDataset:
        id: str
        display_name: str
        streets: tuple
        warnings: tuple
        path: Path
        index_row_count: int = 0

    face_folder = tmp_path / "faces" / "20260905_150413_hebden"
    face_folder.mkdir(parents=True)
    catalogue = PreprocessedCatalogue(tmp_path, {})

    street = SimpleNamespace(
        id="0042",
        display_name="Aspinall Street",
        street_name="Aspinall Street",
    )
    source = SourceDataset(
        id="20260905_150413_hebden",
        display_name="Hebden",
        streets=(street,),
        warnings=(),
        path=Path("source"),
    )
    options = prepared_dataset_options([DatasetOption("source", source)], catalogue)

    assert len(options) == 1
    assert options[0].dataset.id == "20260905_150413_hebden"
    assert [item.id for item in options[0].dataset.streets] == ["0042"]


def test_prepared_only_folder_scans_canonical_svg_names_for_streets(tmp_path: Path) -> None:
    face_folder = tmp_path / "faces" / "orphan_set"
    face_folder.mkdir(parents=True)
    (face_folder / "0042_aspinall_street.svg").write_text("<svg/>", encoding="utf-8")
    (face_folder / "0043_market_street.svg").write_text("<svg/>", encoding="utf-8")
    catalogue = PreprocessedCatalogue(tmp_path, {})

    options = prepared_dataset_options([], catalogue)

    assert len(options) == 1
    assert [street.id for street in options[0].dataset.streets] == ["0042", "0043"]
    assert [street.display_name for street in options[0].dataset.streets] == [
        "Aspinall Street",
        "Market Street",
    ]


def test_slugged_faces_folder_keeps_index_dataset_id_for_preview_lookup(tmp_path: Path) -> None:
    logical_id = "Hebden Bridge 2026!"
    folder_id = "hebden_bridge_2026"
    street_id = "0042"
    face_folder = tmp_path / "faces" / folder_id
    preview_folder = tmp_path / "previews" / folder_id
    face_folder.mkdir(parents=True)
    preview_folder.mkdir(parents=True)
    svg_path = face_folder / "0042_aspinall_street.svg"
    preview_path = preview_folder / "0042_aspinall_street.png"
    svg_path.write_text("<svg/>", encoding="utf-8")
    preview_path.write_bytes(b"preview")

    raw = {
        "dataset_id": logical_id,
        "dataset_name": "Hebden Bridge",
        "street_id": street_id,
        "street_name": "Aspinall Street",
        "success": True,
        "svg_path": f"faces/{folder_id}/{svg_path.name}",
        "preview_path": f"previews/{folder_id}/{preview_path.name}",
    }
    (tmp_path / "preprocess_index.json").write_text(
        json.dumps({"records": [raw]}), encoding="utf-8"
    )
    record = PreprocessedRecord(
        dataset_id=logical_id,
        street_id=street_id,
        state=None,
        reason_detail="",
        success=True,
        preview_path=preview_path,
        svg_path=svg_path,
        editable_svg_path=svg_path,
    )
    catalogue = PreprocessedCatalogue(tmp_path, {(logical_id, street_id): record})

    options = prepared_dataset_options([], catalogue)

    assert [item.label for item in options] == [folder_id]
    dataset = options[0].dataset
    assert dataset.id == logical_id
    assert [street.id for street in dataset.streets] == [street_id]
    assert catalogue.find(dataset, dataset.streets[0]) is record
