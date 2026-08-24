"""Typed domain objects independent of workflow file formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
import math


@dataclass(frozen=True)
class MetricBounds:
    """Axis-aligned bounds in the dataset's projected metric CRS."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        values = (self.min_x, self.min_y, self.max_x, self.max_y)
        if not all(math.isfinite(value) for value in values) or self.max_x < self.min_x or self.max_y < self.min_y:
            raise ValueError("Metric bounds must be finite and non-negative.")

    @property
    def width_m(self) -> float:
        return self.max_x - self.min_x

    @property
    def height_m(self) -> float:
        return self.max_y - self.min_y

    @property
    def span_m(self) -> float:
        return max(self.width_m, self.height_m)

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2, (self.min_y + self.max_y) / 2)

    def contains(self, other: "MetricBounds", *, tolerance: float = 1e-7) -> bool:
        return self.min_x <= other.min_x + tolerance and self.min_y <= other.min_y + tolerance and self.max_x >= other.max_x - tolerance and self.max_y >= other.max_y - tolerance


@dataclass(frozen=True)
class ContextSourceGeometry:
    """Authoritative workflow-v6 context-tile generation parameters."""

    crs: str
    frame_bounds_m: MetricBounds
    tile_zoom: int
    shared_reference_window_px: tuple[int, int]
    padding_fraction_per_side: float


@dataclass(frozen=True)
class DatasetPaths:
    root: Path
    summary_path: Path | None
    statistics_path: Path | None
    street_index_path: Path | None
    glyph_directory: Path | None
    context_directory: Path | None
    source_streets_path: Path | None
    parks_path: Path | None
    water_path: Path | None
    boundary_path: Path | None
    overlay_path: Path | None


@dataclass(frozen=True)
class DatasetCapabilities:
    glyph_rendering: bool = False
    context_rendering: bool = False
    metric_context_framing: bool = False
    parks_layer: bool = False
    water_layer: bool = False
    boundary_layer: bool = False


@dataclass(frozen=True)
class DatasetStatistics:
    context_scale: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StreetRecord:
    id: str
    group_id: str | None
    street_name: str
    display_name: str
    glyph_path: Path
    context_path: Path | None = None
    geometry_length_m: float | None = None
    bbox_min_x: float | None = None
    bbox_min_y: float | None = None
    bbox_max_x: float | None = None
    bbox_max_y: float | None = None
    bbox_width_m: float | None = None
    bbox_height_m: float | None = None
    bbox_span_m: float | None = None
    bbox_area_m2: float | None = None
    context_source_bounds: MetricBounds | None = None


@dataclass(frozen=True)
class Dataset:
    path: Path
    id: str
    display_name: str
    format_name: str
    paths: DatasetPaths
    summary: Mapping[str, Any]
    statistics: DatasetStatistics
    capabilities: DatasetCapabilities
    streets: tuple[StreetRecord, ...]
    index_row_count: int
    context_source_geometry: ContextSourceGeometry | None = None
    warnings: tuple[str, ...] = ()

    @property
    def glyph_directory(self) -> Path | None:
        return self.paths.glyph_directory

    @property
    def context_directory(self) -> Path | None:
        return self.paths.context_directory

    @property
    def street_index_path(self) -> Path | None:
        return self.paths.street_index_path

    def get_street(self, street_id: str) -> StreetRecord | None:
        """Return a street by its stable glyph/index identifier."""
        return next((item for item in self.streets if item.id == str(street_id)), None)

    def find_streets(self, query: str) -> list[StreetRecord]:
        """Find streets by a case-insensitive display-name substring."""
        needle = query.casefold().strip()
        return [item for item in self.streets if needle in item.display_name.casefold()]
