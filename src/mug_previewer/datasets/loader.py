from pathlib import Path

from .models import Dataset
from .workflow_v6 import DatasetLoadError, WorkflowV6DatasetLoader

def load_dataset(path: Path | str) -> Dataset:
    result = WorkflowV6DatasetLoader(Path(path)).load_result()
    if result.dataset is None:
        raise DatasetLoadError("; ".join(result.errors))
    return result.dataset
