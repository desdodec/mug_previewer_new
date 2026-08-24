"""Adapter for the workflow-v6 directory contract."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import Dataset, DatasetCapabilities, DatasetPaths, DatasetStatistics, StreetRecord

PREFIX = re.compile(r"^(\d+)_")

class DatasetLoadError(ValueError):
    pass

@dataclass(frozen=True)
class LoadResult:
    dataset: Dataset | None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    information: tuple[str, ...] = ()

def _json(path: Path | None) -> Mapping[str, Any]:
    if path is None:
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DatasetLoadError(f"Could not read JSON metadata: {path}") from error
    if not isinstance(loaded, dict):
        raise DatasetLoadError(f"JSON metadata must be an object: {path}")
    return loaded

def _number(row: Mapping[str, str], name: str) -> float | None:
    try:
        return float(row[name]) if row.get(name, "").strip() else None
    except ValueError:
        return None

def _svg_ids(directory: Path | None) -> dict[str, Path]:
    if directory is None:
        return {}
    result: dict[str, Path] = {}
    for path in directory.iterdir():
        match = PREFIX.match(path.name)
        if path.is_file() and path.suffix.casefold() == ".svg" and match:
            result.setdefault(match.group(1), path)
    return result

def _svg_names(directory: Path | None) -> dict[str, Path]:
    """Index SVG filenames exactly; numeric IDs remain the compatibility fallback."""
    if directory is None:
        return {}
    return {
        path.name: path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.casefold() == ".svg"
    }

def _name(identifier: str, summary: Mapping[str, Any]) -> str:
    place = summary.get("place_name")
    if isinstance(place, str) and place.strip():
        return place.split(",", 1)[0].strip().title()
    value = re.sub(r"^\d{8}_\d{6}_", "", identifier)
    value = re.sub(r"_streets(?:_parks)?(?:_water)?(?:_boundary)?(?:_clip)?$", "", value)
    return value.replace("_", " ").title()

class WorkflowV6DatasetLoader:
    """Centralises v6 filenames, metadata and ID-based SVG matching."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_result(self) -> LoadResult:
        if not self.path.is_dir():
            return LoadResult(None, (f"Dataset directory does not exist: {self.path}",))
        paths = self._paths()
        if paths.street_index_path is None:
            return LoadResult(None, (f"Required street index is missing: {self.path / 'street_index.csv'}",))
        try:
            summary, stats = _json(paths.summary_path), _json(paths.statistics_path)
            with paths.street_index_path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                fields = set(reader.fieldnames or ())
                if "glyph_file" not in fields or not {"street_name", "requested_street"} & fields:
                    return LoadResult(None, (f"Street index needs glyph_file and street_name or requested_street columns: {paths.street_index_path}",))
                rows = list(reader)
        except (OSError, csv.Error, DatasetLoadError) as error:
            return LoadResult(None, (str(error),))
        glyph_ids = _svg_ids(paths.glyph_directory)
        glyph_names = _svg_names(paths.glyph_directory)
        context_ids = _svg_ids(paths.context_directory)
        streets: list[StreetRecord] = []
        warnings: list[str] = []
        skipped = 0
        for row in rows:
            match = PREFIX.match(row.get("glyph_file", ""))
            street_id = match.group(1) if match else row.get("group_id", "").strip()
            glyph = glyph_names.get(row.get("glyph_file", "")) or glyph_ids.get(street_id)
            if not street_id or glyph is None:
                skipped += 1
                continue
            street_name = row.get("street_name", "").strip() or row.get("requested_street", "").strip() or street_id
            streets.append(StreetRecord(
                id=street_id, group_id=row.get("group_id", "").strip() or None,
                street_name=street_name, display_name=street_name, glyph_path=glyph,
                context_path=context_ids.get(street_id), geometry_length_m=_number(row, "geometry_length_m"),
                bbox_min_x=_number(row, "bbox_min_x"), bbox_min_y=_number(row, "bbox_min_y"),
                bbox_max_x=_number(row, "bbox_max_x"), bbox_max_y=_number(row, "bbox_max_y"),
                bbox_width_m=_number(row, "bbox_width_m"), bbox_height_m=_number(row, "bbox_height_m"),
                bbox_span_m=_number(row, "bbox_span_m"), bbox_area_m2=_number(row, "bbox_area_m2"),
            ))
        if not streets:
            return LoadResult(None, ("No usable streets: no street-index rows resolved to glyph SVG files.",))
        if skipped:
            warnings.append(f"{skipped} street index row(s) were skipped because their glyph SVG could not be resolved.")
        if paths.context_directory is None:
            warnings.append("Context glyph directory is missing; front glyph rendering remains available.")
        else:
            missing = sum(item.context_path is None for item in streets)
            if missing:
                warnings.append(f"{missing} usable street record(s) have no matching context SVG.")
        scale = stats.get("context_scale_statistics", {})
        scale = scale if isinstance(scale, dict) else {}
        capabilities = DatasetCapabilities(
            glyph_rendering=True, context_rendering=any(item.context_path for item in streets),
            metric_context_framing=bool(scale) or any(item.bbox_span_m is not None for item in streets),
            parks_layer=paths.parks_path is not None, water_layer=paths.water_path is not None,
            boundary_layer=paths.boundary_path is not None,
        )
        dataset = Dataset(self.path, self.path.name, _name(self.path.name, summary), "workflow-v6", paths,
            summary, DatasetStatistics(scale, stats), capabilities, tuple(streets), len(rows), tuple(warnings))
        return LoadResult(dataset, warnings=tuple(warnings), information=(f"Index rows: {len(rows)}", f"Usable streets: {len(streets)}"))

    def _paths(self) -> DatasetPaths:
        def first_file(*names: str) -> Path | None:
            return next((self.path / name for name in names if (self.path / name).is_file()), None)

        def first_directory(*names: str) -> Path | None:
            return next((self.path / name for name in names if (self.path / name).is_dir()), None)

        return DatasetPaths(
            self.path,
            first_file("summary.json"),
            first_file("street_index_stats.json"),
            first_file("street_index.csv"),
            first_directory("glyphs", "glyph"),
            first_directory("glyphs_context", "glyph_context"),
            first_file("streets.svg"),
            first_file("parks.svg"),
            first_file("water.svg"),
            first_file("boundary.svg"),
            first_file("overlay.svg"),
        )
