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
