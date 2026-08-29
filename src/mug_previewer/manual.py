"""Durable, deterministic human decisions for manual-review streets."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

MANUAL_OVERRIDE_SCHEMA_VERSION = 1
DEFAULT_MANUAL_OVERRIDE_PATH = Path("data") / "manual_overrides.json"
ALLOWED_ORIENTATIONS = (0, 180)
ALLOWED_SCALES = (1.00, 0.95, 0.90, 0.85, 0.80)
ALLOWED_Y_OFFSETS = (-60, -40, -20, 0, 20, 40, 60)

class ManualOverrideError(ValueError):
    """Raised when durable manual-decision data is malformed or unsupported."""

class ManualResolutionStatus(str, Enum):
    PENDING = "pending"
    APPROVED_STANDARD = "approved_standard"
    APPROVED_OVERRIDE = "approved_override"
    REJECTED = "rejected"

@dataclass(frozen=True)
class ManualPlacementOverride:
    """One reviewer decision, keyed only by stable dataset/street identity."""
    dataset: str
    street_id: str
    street_name: str
    status: ManualResolutionStatus
    orientation_deg: int | None = None
    scale: float | None = None
    y_offset: int | None = None
    source: str = "manual-review"
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.dataset or not self.street_id or not self.street_name or not self.source:
            raise ManualOverrideError("Manual overrides require dataset, street_id, street_name, and source.")
        if self.status is ManualResolutionStatus.APPROVED_STANDARD:
            if self.transform != (0, 1.0, 0):
                raise ManualOverrideError("An approved STANDARD decision must use 0 degrees, 1.00 scale, and Y0.")
        elif self.status is ManualResolutionStatus.APPROVED_OVERRIDE:
            _validate_transform(self.orientation_deg, self.scale, self.y_offset)
        elif any(value is not None for value in self.transform):
            raise ManualOverrideError(f"{self.status.value} decisions must not include a transform.")

    @property
    def key(self) -> tuple[str, str]:
        return (self.dataset, self.street_id)

    @property
    def transform(self) -> tuple[int | None, float | None, int | None]:
        return (self.orientation_deg, self.scale, self.y_offset)

    @property
    def approved(self) -> bool:
        return self.status in {ManualResolutionStatus.APPROVED_STANDARD, ManualResolutionStatus.APPROVED_OVERRIDE}

    @classmethod
    def approved_standard(cls, dataset: str, street_id: str, street_name: str, *, note: str | None = None) -> "ManualPlacementOverride":
        return cls(dataset, street_id, street_name, ManualResolutionStatus.APPROVED_STANDARD, 0, 1.0, 0, note=note)

    @classmethod
    def approved_transform(cls, dataset: str, street_id: str, street_name: str, *, orientation_deg: int, scale: float, y_offset: int, note: str | None = None) -> "ManualPlacementOverride":
        return cls(dataset, street_id, street_name, ManualResolutionStatus.APPROVED_OVERRIDE, orientation_deg, scale, y_offset, note=note)

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset, "street_id": self.street_id, "street_name": self.street_name,
            "status": self.status.value, "orientation_deg": self.orientation_deg, "scale": self.scale,
            "y_offset": self.y_offset, "source": self.source, "note": self.note,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ManualPlacementOverride":
        if not isinstance(value, dict):
            raise ManualOverrideError("Each manual override must be a JSON object.")
        try:
            return cls(
                dataset=_string(value, "dataset"), street_id=_string(value, "street_id"),
                street_name=_string(value, "street_name"), status=ManualResolutionStatus(value["status"]),
                orientation_deg=_optional_int(value.get("orientation_deg"), "orientation_deg"),
                scale=_optional_float(value.get("scale"), "scale"),
                y_offset=_optional_int(value.get("y_offset"), "y_offset"), source=_string(value, "source"),
                note=_optional_string(value.get("note"), "note"),
            )
        except KeyError as error:
            raise ManualOverrideError(f"Manual override is missing required field {error.args[0]!r}.") from error
        except ValueError as error:
            raise ManualOverrideError(f"Unsupported manual resolution status: {value.get('status')!r}.") from error

@dataclass(frozen=True)
class ManualOverrideStore:
    """Immutable lookup wrapper preserving one decision per stable key."""
    overrides: tuple[ManualPlacementOverride, ...] = ()

    def __post_init__(self) -> None:
        keys = [item.key for item in self.overrides]
        if len(keys) != len(set(keys)):
            raise ManualOverrideError("Manual override store contains duplicate dataset/street_id decisions.")

    def get(self, dataset: str, street_id: str) -> ManualPlacementOverride | None:
        return next((item for item in self.overrides if item.key == (dataset, str(street_id))), None)

    def replacing(self, override: ManualPlacementOverride) -> "ManualOverrideStore":
        return ManualOverrideStore(tuple((*[item for item in self.overrides if item.key != override.key], override)))

    def without(self, dataset: str, street_id: str) -> "ManualOverrideStore":
        return ManualOverrideStore(tuple(item for item in self.overrides if item.key != (dataset, str(street_id))))

    def ordered(self) -> tuple[ManualPlacementOverride, ...]:
        return tuple(sorted(self.overrides, key=lambda item: (item.dataset, item.street_id)))

def load_manual_overrides(path: Path | str = DEFAULT_MANUAL_OVERRIDE_PATH) -> ManualOverrideStore:
    """Load schema-v1 overrides, failing clearly rather than guessing a migration."""
    source = Path(path)
    if not source.exists():
        return ManualOverrideStore()
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManualOverrideError(f"Could not read manual override store {source}: {error}") from error
    if not isinstance(document, dict):
        raise ManualOverrideError("Manual override store must be a JSON object.")
    if document.get("version") != MANUAL_OVERRIDE_SCHEMA_VERSION:
        raise ManualOverrideError(f"Unsupported manual override schema version {document.get('version')!r}; expected {MANUAL_OVERRIDE_SCHEMA_VERSION}.")
    rows = document.get("overrides")
    if not isinstance(rows, list):
        raise ManualOverrideError("Manual override store field 'overrides' must be a list.")
    return ManualOverrideStore(tuple(ManualPlacementOverride.from_dict(row) for row in rows))

def save_manual_override(override: ManualPlacementOverride, path: Path | str = DEFAULT_MANUAL_OVERRIDE_PATH) -> ManualOverrideStore:
    """Atomically replace the active decision for one stable identity."""
    store = load_manual_overrides(path).replacing(override)
    _write_store(Path(path), store)
    return store

def clear_manual_override(dataset: str, street_id: str, path: Path | str = DEFAULT_MANUAL_OVERRIDE_PATH) -> ManualOverrideStore:
    """Remove one decision so its manual-review record is pending again."""
    store = load_manual_overrides(path).without(dataset, street_id)
    _write_store(Path(path), store)
    return store

def _write_store(path: Path, store: ManualOverrideStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": MANUAL_OVERRIDE_SCHEMA_VERSION, "overrides": [item.as_dict() for item in store.ordered()]}
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError as error:
        raise ManualOverrideError(f"Could not save manual override store {path}: {error}") from error
    finally:
        if temporary.exists():
            temporary.unlink()

def _validate_transform(orientation: int | None, scale: float | None, y_offset: int | None) -> None:
    if orientation not in ALLOWED_ORIENTATIONS:
        raise ManualOverrideError(f"Manual orientation must be one of {ALLOWED_ORIENTATIONS}.")
    if scale is None or not any(abs(scale - allowed) < 1e-9 for allowed in ALLOWED_SCALES):
        raise ManualOverrideError(f"Manual scale must be one of {ALLOWED_SCALES}.")
    if y_offset not in ALLOWED_Y_OFFSETS:
        raise ManualOverrideError(f"Manual Y offset must be one of {ALLOWED_Y_OFFSETS}.")

def _string(value: dict[str, object], name: str) -> str:
    item = value[name]
    if not isinstance(item, str) or not item.strip():
        raise ManualOverrideError(f"Manual override field {name!r} must be a non-empty string.")
    return item

def _optional_string(item: object, name: str) -> str | None:
    if item is None:
        return None
    if not isinstance(item, str):
        raise ManualOverrideError(f"Manual override field {name!r} must be a string or null.")
    return item

def _optional_int(item: object, name: str) -> int | None:
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int):
        raise ManualOverrideError(f"Manual override field {name!r} must be an integer or null.")
    return item

def _optional_float(item: object, name: str) -> float | None:
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise ManualOverrideError(f"Manual override field {name!r} must be a number or null.")
    return float(item)

def approved_override_for_street(dataset: object, street: object, store: ManualOverrideStore, *, area: str) -> ManualPlacementOverride | None:
    """Return an approved decision only after the original input remains renderable.

    Imports stay local to avoid the face/diagnostic module cycle.  A manual
    transform never repairs missing anatomy or unsupported typography.
    """
    from .diagnostics.front_candidates import ProductionTriageStatus, select_production_placement

    override = store.get(dataset.id, street.id)
    if override is None or not override.approved:
        return None
    decision = select_production_placement(street, area=area)
    if decision.triage_status is ProductionTriageStatus.UNRENDERABLE_INPUT:
        raise ManualOverrideError("Unrenderable input cannot be approved with a manual override.")
    if decision.triage_status is not ProductionTriageStatus.MANUAL_REVIEW:
        raise ManualOverrideError("Manual overrides are valid only for streets in MANUAL_REVIEW.")
    if override.street_name != street.display_name:
        raise ManualOverrideError("Manual override street_name does not match the current stable street identity.")
    return override
