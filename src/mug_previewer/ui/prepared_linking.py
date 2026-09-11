"""Relink prepared artwork to discoverable source-map datasets.

The prepared workspace and the source workspace are intentionally separate:

* prepared dropdowns reflect dataset folders that actually exist below the
  configured ``--preprocessed`` root;
* the Create Faces source dropdown reflects datasets discovered below the
  configured ``--dataset-root``;
* source datasets are used only to supply map/glyph geometry for a prepared
  set.  They never decide whether an existing prepared set is visible.

This keeps the UI truthful after an upstream source run is moved, renamed or
replaced.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import json
from pathlib import Path
import re
from typing import Sequence

from ..datasets.models import (
    Dataset,
    DatasetCapabilities,
    DatasetPaths,
    DatasetStatistics,
    StreetRecord,
)
from ..preprocess import INDEX_FILENAME
from .state import DatasetOption, PreprocessedCatalogue

_TIMESTAMP_PREFIX = re.compile(r"^\d{8}_\d{6}_")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DATASET_SUFFIX = re.compile(
    r"_streets(?:_classified)?(?:_layers_centroid)?(?:_parks)?(?:_water)?"
    r"(?:_frame_bbox)?(?:_boundary)?(?:_clip)?(?:_color_streets)?$",
    re.IGNORECASE,
)


def _normalise(value: object) -> str:
    return _NON_ALNUM.sub("", str(value or "").casefold())


def _dataset_key(value: object) -> str:
    text = _TIMESTAMP_PREFIX.sub("", str(value or ""))
    text = _DATASET_SUFFIX.sub("", text)
    return _normalise(text)


def _prepared_records(catalogue: PreprocessedCatalogue) -> dict[str, list[dict[str, object]]]:
    """Load identity metadata for records already accepted into the catalogue."""
    valid_keys = set(catalogue.records)
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    try:
        payload = json.loads((catalogue.root / INDEX_FILENAME).read_text(encoding="utf-8-sig"))
        raw_records = payload.get("records", []) if isinstance(payload, dict) else []
    except (OSError, ValueError, json.JSONDecodeError):
        raw_records = []

    for item in raw_records:
        if not isinstance(item, dict):
            continue
        dataset_id, street_id = item.get("dataset_id"), item.get("street_id")
        if not isinstance(dataset_id, str) or not isinstance(street_id, str):
            continue
        if (dataset_id, street_id) in valid_keys:
            groups[dataset_id].append(item)

    # Exact-id source datasets can still be used if an old/partial index omitted
    # display metadata. Cross-run relinking is deliberately not guessed without
    # street names.
    for dataset_id, street_id in valid_keys:
        if not any(str(item.get("street_id")) == street_id for item in groups[dataset_id]):
            groups[dataset_id].append({"dataset_id": dataset_id, "street_id": street_id})

    for records in groups.values():
        records.sort(key=lambda item: str(item.get("street_id", "")))
    return dict(groups)


def _dataset_directories(container: Path) -> set[str]:
    if not container.is_dir():
        return set()
    try:
        return {
            child.name
            for child in container.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        }
    except OSError:
        return set()


def _prepared_folder_ids(catalogue: PreprocessedCatalogue) -> set[str]:
    """Return the dataset folders that should appear in prepared dropdowns.

    ``faces`` is the authoritative prepared-workspace directory.  If it contains
    any dataset folders, the UI mirrors those folders exactly and ignores stale
    historical preview-cache directories.

    ``previews`` is used only as a disaster-recovery fallback when ``faces`` has
    no dataset folders at all.  This preserves the cached-preview recovery path
    for old workspaces without allowing old preview folders to pollute a fresh
    prepared-face dropdown.
    """
    face_ids = _dataset_directories(catalogue.root / "faces")
    if face_ids:
        return face_ids
    return _dataset_directories(catalogue.root / "previews")


def _source_name_index(dataset: Dataset) -> dict[str, list[StreetRecord]]:
    result: dict[str, list[StreetRecord]] = defaultdict(list)
    for street in dataset.streets:
        result[_normalise(street.display_name)].append(street)
    return dict(result)


def _record_names(records: Sequence[dict[str, object]]) -> set[str]:
    return {
        name
        for name in (_normalise(item.get("street_name")) for item in records)
        if name
    }


def _prepared_display_name(prepared_id: str, records: Sequence[dict[str, object]]) -> str:
    for item in records:
        value = item.get("dataset_name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    text = _TIMESTAMP_PREFIX.sub("", prepared_id)
    text = _DATASET_SUFFIX.sub("", text)
    return text.replace("_", " ").strip().title() or prepared_id


def _best_source(
    prepared_id: str,
    records: Sequence[dict[str, object]],
    source_options: Sequence[DatasetOption],
) -> Dataset | None:
    exact = next((item.dataset for item in source_options if item.dataset.id == prepared_id), None)
    if exact is not None:
        return exact

    prepared_names = _record_names(records)
    if not prepared_names:
        return None
    prepared_name = _prepared_display_name(prepared_id, records)
    prepared_area = _normalise(prepared_name)
    prepared_key = _dataset_key(prepared_id)

    scored: list[tuple[tuple[float, ...], str, Dataset]] = []
    for option in source_options:
        source = option.dataset
        source_names = {_normalise(street.display_name) for street in source.streets}
        overlap = len(prepared_names & source_names)
        if not overlap:
            continue
        coverage = overlap / len(prepared_names)
        same_area = prepared_area == _normalise(source.display_name)
        same_key = bool(prepared_key) and prepared_key == _dataset_key(source.id)

        # A renamed place/run may still be safely linked when the street-name
        # fingerprint overwhelmingly agrees. Small prepared sets require a
        # place/key match to avoid accidental matches on common road names.
        strong_fingerprint = len(prepared_names) >= 10 and coverage >= 0.80
        if not (same_area or same_key or strong_fingerprint):
            continue

        count_delta = abs(len(source.streets) - len(records))
        score = (
            1.0 if same_area else 0.0,
            1.0 if same_key else 0.0,
            coverage,
            float(overlap),
            -float(count_delta),
        )
        scored.append((score, source.id, source))

    if not scored:
        return None
    return max(scored, key=lambda item: (item[0], item[1]))[2]


def _match_source_street(
    record: dict[str, object],
    source: Dataset,
    by_name: dict[str, list[StreetRecord]],
) -> StreetRecord | None:
    street_id = str(record.get("street_id") or "")
    name = _normalise(record.get("street_name"))
    candidates = by_name.get(name, []) if name else []
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        exact_id = next((street for street in candidates if street.id == street_id), None)
        if exact_id is not None:
            return exact_id
        return None

    exact_id = source.get_street(street_id)
    if exact_id is not None and (not name or _normalise(exact_id.display_name) == name):
        return exact_id
    return None


def _linked_dataset(
    prepared_id: str,
    records: Sequence[dict[str, object]],
    source: Dataset,
) -> Dataset | None:
    by_name = _source_name_index(source)
    linked: list[StreetRecord] = []
    for record in records:
        source_street = _match_source_street(record, source, by_name)
        if source_street is None:
            continue
        prepared_street_id = str(record.get("street_id") or source_street.id)
        prepared_name = str(record.get("street_name") or source_street.display_name)
        linked.append(
            replace(
                source_street,
                id=prepared_street_id,
                street_name=prepared_name,
                display_name=prepared_name,
            )
        )

    if not linked:
        if source.id == prepared_id:
            linked = list(source.streets)
        else:
            return None

    display_name = _prepared_display_name(prepared_id, records)
    warnings = list(source.warnings)
    if source.id != prepared_id:
        warnings.append(
            f"Prepared set {prepared_id} relinked to source map dataset {source.id}; "
            f"matched {len(linked)} of {len(records)} prepared streets by name."
        )
    return replace(
        source,
        id=prepared_id,
        display_name=display_name,
        streets=tuple(linked),
        index_row_count=len(records),
        warnings=tuple(warnings),
    )


def _prepared_only_dataset(
    catalogue: PreprocessedCatalogue,
    prepared_id: str,
    records: Sequence[dict[str, object]],
) -> Dataset | None:
    """Expose an existing prepared folder even when source-map data is absent."""
    streets: list[StreetRecord] = []
    seen: set[str] = set()
    for record in records:
        street_id = str(record.get("street_id") or "").strip()
        if not street_id or street_id in seen:
            continue
        seen.add(street_id)
        street_name = str(record.get("street_name") or street_id).strip() or street_id
        missing_source = catalogue.root / ".source-unavailable" / prepared_id / f"{street_id}.svg"
        streets.append(
            StreetRecord(
                id=street_id,
                group_id=None,
                street_name=street_name,
                display_name=street_name,
                glyph_path=missing_source,
                context_path=None,
            )
        )
    if not streets:
        return None

    physical_root = next(
        (
            catalogue.root / container / prepared_id
            for container in ("faces", "previews")
            if (catalogue.root / container / prepared_id).is_dir()
        ),
        catalogue.root,
    )
    paths = DatasetPaths(
        root=physical_root,
        summary_path=None,
        statistics_path=None,
        street_index_path=None,
        glyph_directory=None,
        context_directory=None,
        source_streets_path=None,
        parks_path=None,
        water_path=None,
        boundary_path=None,
        overlay_path=None,
    )
    return Dataset(
        path=physical_root,
        id=prepared_id,
        display_name=_prepared_display_name(prepared_id, records),
        format_name="prepared-only",
        paths=paths,
        summary={},
        statistics=DatasetStatistics(),
        capabilities=DatasetCapabilities(),
        streets=tuple(streets),
        index_row_count=len(records),
        warnings=("Prepared artwork is available, but source map data is not currently linked.",),
    )


def prepared_dataset_options(
    source_options: Sequence[DatasetOption],
    catalogue: PreprocessedCatalogue,
) -> list[DatasetOption]:
    """Build prepared dropdowns from actual ``--preprocessed`` folder content.

    ``faces`` folders are authoritative for normal workspaces.  Preview-only
    folders are considered only when there are no face dataset folders at all.
    Source matching enriches a visible prepared set with map geometry when
    possible, but never adds extra prepared entries.
    """
    groups = _prepared_records(catalogue)
    folder_ids = _prepared_folder_ids(catalogue)
    prepared_ids = sorted(set(groups) & folder_ids)

    options: list[DatasetOption] = []
    for prepared_id in prepared_ids:
        records = groups[prepared_id]
        source = _best_source(prepared_id, records, source_options)
        dataset = _linked_dataset(prepared_id, records, source) if source is not None else None
        if dataset is None:
            dataset = _prepared_only_dataset(catalogue, prepared_id, records)
        if dataset is not None:
            options.append(DatasetOption(prepared_id, dataset))
    return options
