from __future__ import annotations

import shutil
from pathlib import Path

from mug_previewer.config import DATASET_ROOT_ENV, load_settings
from mug_previewer.datasets.discovery import discover_datasets
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.datasets.validation import validate_dataset

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def make_dataset(path: Path) -> Path:
    shutil.copytree(FIXTURE, path)
    return path

def test_loader_preserves_ids_unicode_context_and_metrics(tmp_path: Path) -> None:
    data = load_dataset(make_dataset(tmp_path / "20260824_101707_test_borough_streets_parks_water_boundary_clip"))
    assert data.display_name == "Test Borough"
    assert len(data.streets) == 2
    assert data.get_street("0001").context_path is not None
    assert data.find_streets("café")[0].id == "0002"
    assert data.capabilities.metric_context_framing

def test_missing_glyph_warns_but_dataset_loads(tmp_path: Path) -> None:
    path = make_dataset(tmp_path / "dataset")
    (path / "glyphs" / "0002_Café Road.svg").unlink()
    data = load_dataset(path)
    assert len(data.streets) == 1
    assert "skipped" in data.warnings[0]

def test_old_dataset_and_failures(tmp_path: Path) -> None:
    path = make_dataset(tmp_path / "old")
    (path / "street_index_stats.json").unlink()
    index = path / "street_index.csv"
    index.write_text(index.read_text(encoding="utf-8").replace(",bbox_span_m", "").replace(",40", "").replace(",20", ""), encoding="utf-8")
    data = load_dataset(path)
    assert not data.capabilities.metric_context_framing
    assert not validate_dataset(tmp_path / "missing").valid
    bad = tmp_path / "bad"; bad.mkdir()
    assert not validate_dataset(bad).valid

def test_config_precedence(tmp_path: Path) -> None:
    project, local = tmp_path / "project.toml", tmp_path / "local.toml"
    project.write_text('[datasets]\nroot="project"\n'); local.write_text('[datasets]\nroot="local"\n')
    assert load_settings(environ={DATASET_ROOT_ENV:"environment"}, local_config_path=local, project_config_path=project).dataset_root == Path("environment")
    assert load_settings(dataset_root="cli", environ={DATASET_ROOT_ENV:"environment"}).dataset_root == Path("cli")


def test_optional_context_and_discovery(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    root.mkdir()
    dataset_path = make_dataset(root / "valid")
    shutil.rmtree(dataset_path / "glyphs_context")
    (root / "not-a-dataset").mkdir()
    data = load_dataset(dataset_path)
    assert data.capabilities.glyph_rendering
    assert not data.capabilities.context_rendering
    assert len(discover_datasets(root)) == 1
