"""Adapter for the workflow-v6 directory contract."""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import ContextSourceGeometry, Dataset, DatasetCapabilities, DatasetPaths, DatasetStatistics, MetricBounds, StreetRecord

PREFIX = re.compile(r"^(\d+)_")
WEB_MERCATOR_HALF_WORLD = math.pi * 6378137.0
TILE_SIZE = 256


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
    value = row.get(name)
    try:
        return float(value) if value is not None and str(value).strip() else None
    except ValueError:
        return None


def metric_bounds_from_raster_crop(
    source_bounds: MetricBounds,
    source_size_px: tuple[int, int],
    crop_rect_px: tuple[int, int, int, int],
) -> MetricBounds:
    """Map a raster crop to projected metres; raster Y grows down, metric Y up."""
    source_width, source_height = source_size_px
    left, top, width, height = crop_rect_px
    if source_width <= 0 or source_height <= 0 or width <= 0 or height <= 0:
        raise ValueError("Raster source and crop dimensions must be positive.")
    metres_per_px_x = source_bounds.width_m / source_width
    metres_per_px_y = source_bounds.height_m / source_height
    return MetricBounds(
        source_bounds.min_x + left * metres_per_px_x,
        source_bounds.max_y - (top + height) * metres_per_px_y,
        source_bounds.min_x + (left + width) * metres_per_px_x,
        source_bounds.max_y - top * metres_per_px_y,
    )


def _context_source_geometry(summary: Mapping[str, Any]) -> ContextSourceGeometry | None:
    """Load the v6 tile-crop parameters persisted by the upstream generator."""
    try:
        context = summary["context_glyphs"]
        frame = summary["frame_bbox"]["frame_bounds_3857"]
        reference = context["shared_reference_window_px"]
        geometry = ContextSourceGeometry(
            crs=str(summary["working_crs"]),
            frame_bounds_m=MetricBounds(float(frame["minx"]), float(frame["miny"]), float(frame["maxx"]), float(frame["maxy"])),
            tile_zoom=int(context["tile_zoom"]),
            shared_reference_window_px=(int(reference["width"]), int(reference["height"])),
            padding_fraction_per_side=float(context["padding_fraction_per_side"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if geometry.crs != "EPSG:3857" or geometry.tile_zoom < 0 or min(geometry.shared_reference_window_px) <= 0 or geometry.padding_fraction_per_side < 0:
        return None
    return geometry


def _context_crop_bounds(row: Mapping[str, str], geometry: ContextSourceGeometry | None) -> MetricBounds | None:
    """Recreate v6's global-tile crop exactly from typed street bounds."""
    if geometry is None:
        return None
    values = tuple(_number(row, name) for name in ("bbox_min_x", "bbox_min_y", "bbox_max_x", "bbox_max_y"))
    if any(value is None for value in values):
        return None
    try:
        street = MetricBounds(*(float(value) for value in values))
        world_size = TILE_SIZE * (2 ** geometry.tile_zoom)
        to_world_x = lambda value: (value + WEB_MERCATOR_HALF_WORLD) / (2 * WEB_MERCATOR_HALF_WORLD) * world_size
        to_world_y = lambda value: (WEB_MERCATOR_HALF_WORLD - value) / (2 * WEB_MERCATOR_HALF_WORLD) * world_size
        min_x, max_x = to_world_x(street.min_x), to_world_x(street.max_x)
        min_y, max_y = to_world_y(street.max_y), to_world_y(street.min_y)
        street_width = max(1, math.ceil(max_x) - math.floor(min_x))
        street_height = max(1, math.ceil(max_y) - math.floor(min_y))
        width = math.ceil(max(street_width, geometry.shared_reference_window_px[0]) * (1 + 2 * geometry.padding_fraction_per_side))
        height = math.ceil(max(street_height, geometry.shared_reference_window_px[1]) * (1 + 2 * geometry.padding_fraction_per_side))
        left = math.floor((min_x + max_x) / 2 - width / 2)
        top = math.floor((min_y + max_y) / 2 - height / 2)
        world_bounds = MetricBounds(-WEB_MERCATOR_HALF_WORLD, -WEB_MERCATOR_HALF_WORLD, WEB_MERCATOR_HALF_WORLD, WEB_MERCATOR_HALF_WORLD)
        return metric_bounds_from_raster_crop(world_bounds, (world_size, world_size), (left, top, width, height))
    except (ValueError, OverflowError):
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
    return {path.name: path for path in directory.iterdir() if path.is_file() and path.suffix.casefold() == ".svg"}


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
        context_geometry = _context_source_geometry(summary)
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
                context_source_bounds=_context_crop_bounds(row, context_geometry),
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
            summary, DatasetStatistics(scale, stats), capabilities, tuple(streets), len(rows), context_geometry, tuple(warnings))
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
