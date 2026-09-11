from dataclasses import dataclass
from pathlib import Path

from .models import Dataset
from .workflow_v6 import WorkflowV6DatasetLoader


@dataclass(frozen=True)
class DatasetCandidate:
    dataset: Dataset
    warnings: tuple[str, ...]


def _load_candidate(path: Path) -> DatasetCandidate | None:
    loaded = WorkflowV6DatasetLoader(path).load_result()
    if loaded.dataset is None:
        return None
    return DatasetCandidate(loaded.dataset, loaded.dataset.warnings)


def _discover_within(root: Path, *, max_depth: int = 2) -> list[DatasetCandidate]:
    """Discover dataset directories below ``root`` without depending on a version folder name.

    A source root may be either a dataset itself, a container of dataset runs, or a
    stable parent containing versioned workflow-output folders.  We intentionally
    stop after two directory levels so choosing a broad parent does not turn into
    an unbounded filesystem crawl.
    """
    if not root.is_dir():
        return []

    direct_candidate = _load_candidate(root)
    if direct_candidate is not None:
        return [direct_candidate]

    results: list[DatasetCandidate] = []
    seen_paths: set[Path] = set()
    frontier: list[tuple[Path, int]] = [(root, 0)]

    while frontier:
        folder, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        try:
            children = sorted(
                (child for child in folder.iterdir() if child.is_dir()),
                key=lambda item: item.name.casefold(),
            )
        except OSError:
            continue

        for child in children:
            try:
                marker = child.resolve()
            except OSError:
                marker = child.absolute()
            if marker in seen_paths:
                continue
            seen_paths.add(marker)

            # Avoid invoking the full loader for ordinary container directories.
            # Every supported source dataset is anchored by street_index.csv.
            if (child / "street_index.csv").is_file():
                candidate = _load_candidate(child)
                if candidate is not None:
                    results.append(candidate)
                    continue

            frontier.append((child, depth + 1))

    return sorted(results, key=lambda item: (item.dataset.display_name.casefold(), item.dataset.id))


def discover_datasets(root_path: Path | str) -> list[DatasetCandidate]:
    """Discover datasets from a configured folder, tolerating renamed version containers.

    The configured path is tried first.  If it is stale or contains no usable
    datasets, its parent is tried as a stable discovery root.  This means a move
    from e.g. ``workflow_outputs_v6`` to another versioned folder does not require
    a code change or a hard-coded new default.
    """
    root = Path(root_path)
    results = _discover_within(root)
    if results:
        return results

    parent = root.parent
    if parent != root and parent.is_dir():
        return _discover_within(parent)
    return []
