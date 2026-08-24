from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    information: tuple[str, ...] = ()

def validate_dataset(path: Path | str) -> ValidationResult:
    from .workflow_v6 import WorkflowV6DatasetLoader
    result = WorkflowV6DatasetLoader(Path(path)).load_result()
    return ValidationResult(result.dataset is not None, result.errors, result.warnings, result.information)
