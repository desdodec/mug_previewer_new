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


def discover_datasets(root_path: Path | str) -> list[DatasetCandidate]:
    root = Path(root_path)
    if not root.is_dir():
        return []
    direct_candidate = _load_candidate(root)
    if direct_candidate is not None:
        return [direct_candidate]
    results = []
    for child in root.iterdir():
        if child.is_dir():
            candidate = _load_candidate(child)
            if candidate is not None:
                results.append(candidate)
    return sorted(results, key=lambda item: (item.dataset.display_name.casefold(), item.dataset.id))
