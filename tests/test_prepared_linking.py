from pathlib import Path

from mug_previewer.ui.prepared_linking import prepared_dataset_options
from mug_previewer.ui.state import PreprocessedCatalogue


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

    face_folder = tmp_path / "faces" / "20260905_150413_hebden"
    face_folder.mkdir(parents=True)
    catalogue = PreprocessedCatalogue(tmp_path, {})

    street = SimpleNamespace(
        id="0042",
        display_name="Aspinall Street",
        street_name="Aspinall Street",
    )
    source = SimpleNamespace(
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
