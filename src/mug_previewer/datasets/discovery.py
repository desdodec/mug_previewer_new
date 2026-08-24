from dataclasses import dataclass
from pathlib import Path

from .models import Dataset
from .workflow_v6 import WorkflowV6DatasetLoader

@dataclass(frozen=True)
class DatasetCandidate:
    dataset: Dataset
    warnings: tuple[str, ...]

def discover_datasets(root_path: Path | str) -> list[DatasetCandidate]:
    root = Path(root_path)
    if not root.is_dir():
        return []
    results = []
    for child in root.iterdir():
        if child.is_dir():
            loaded = WorkflowV6DatasetLoader(child).load_result()
            if loaded.dataset:
                results.append(DatasetCandidate(loaded.dataset, loaded.dataset.warnings))
    return sorted(results, key=lambda item: (item.dataset.display_name.casefold(), item.dataset.id))
