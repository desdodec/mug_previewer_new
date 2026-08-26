"""Typed provider print-specification domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Mapping


class ProviderProfileError(ValueError):
    """Raised when provider specification data is structurally invalid."""


@dataclass(frozen=True)
class PixelBounds:
    """A rectangular region in a provider canvas's pixel coordinate space."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise ProviderProfileError("Pixel bounds x and y must be non-negative.")
        if self.width <= 0 or self.height <= 0:
            raise ProviderProfileError("Pixel bounds width and height must be positive.")

    def fits_within(self, canvas_width_px: int, canvas_height_px: int) -> bool:
        """Return whether the bounds remain fully inside the given canvas."""
        return self.x + self.width <= canvas_width_px and self.y + self.height <= canvas_height_px


@dataclass(frozen=True)
class ProviderProfile:
    """A provider/product delivery contract, independent of artwork rendering."""

    id: str
    provider_name: str
    product_name: str
    variant_name: str | None
    canvas_width_px: int
    canvas_height_px: int
    dpi: int
    physical_width_mm: float | None
    physical_height_mm: float | None
    colour_mode: str
    colour_profile: str | None
    accepted_formats: tuple[str, ...]
    preferred_format: str
    printable_bounds: PixelBounds | None
    safe_bounds: PixelBounds | None
    background_policy: str
    profile_version: str
    verified_date: str | None
    source_description: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("id", self.id),
            ("provider_name", self.provider_name),
            ("product_name", self.product_name),
            ("colour_mode", self.colour_mode),
            ("background_policy", self.background_policy),
            ("profile_version", self.profile_version),
            ("source_description", self.source_description),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ProviderProfileError(f"Provider profile {field_name} must be non-empty.")
        if self.canvas_width_px <= 0 or self.canvas_height_px <= 0:
            raise ProviderProfileError("Provider profile canvas dimensions must be positive.")
        if self.dpi <= 0:
            raise ProviderProfileError("Provider profile DPI must be positive.")
        if (self.physical_width_mm is None) != (self.physical_height_mm is None):
            raise ProviderProfileError("Physical width and height must be supplied together.")
        if self.physical_width_mm is not None and self.physical_height_mm is not None:
            if not all(math.isfinite(value) and value > 0 for value in (self.physical_width_mm, self.physical_height_mm)):
                raise ProviderProfileError("Provider profile physical dimensions must be positive and finite.")
            _validate_physical_dimensions(self)
        if not self.accepted_formats:
            raise ProviderProfileError("Provider profile accepted_formats must not be empty.")
        if any(not value or value != value.upper() for value in self.accepted_formats):
            raise ProviderProfileError("Provider profile formats must be non-empty uppercase values.")
        if len(set(self.accepted_formats)) != len(self.accepted_formats):
            raise ProviderProfileError("Provider profile accepted_formats must be unique.")
        if self.preferred_format not in self.accepted_formats:
            raise ProviderProfileError("Provider profile preferred_format must be included in accepted_formats.")
        for name, bounds in (("printable_bounds", self.printable_bounds), ("safe_bounds", self.safe_bounds)):
            if bounds is not None and not bounds.fits_within(self.canvas_width_px, self.canvas_height_px):
                raise ProviderProfileError(f"Provider profile {name} must remain inside the canvas.")
        if self.verified_date is not None:
            try:
                date.fromisoformat(self.verified_date)
            except ValueError as error:
                raise ProviderProfileError("Provider profile verified_date must be ISO YYYY-MM-DD.") from error


def provider_profile_from_mapping(data: Mapping[str, object], *, source: str) -> ProviderProfile:
    """Parse one JSON-compatible mapping with field-specific actionable errors."""
    try:
        return ProviderProfile(
            id=_required_string(data, "id", source),
            provider_name=_required_string(data, "provider_name", source),
            product_name=_required_string(data, "product_name", source),
            variant_name=_optional_string(data, "variant_name", source),
            canvas_width_px=_required_positive_int(data, "canvas_width_px", source),
            canvas_height_px=_required_positive_int(data, "canvas_height_px", source),
            dpi=_required_positive_int(data, "dpi", source),
            physical_width_mm=_optional_positive_float(data, "physical_width_mm", source),
            physical_height_mm=_optional_positive_float(data, "physical_height_mm", source),
            colour_mode=_required_string(data, "colour_mode", source),
            colour_profile=_optional_string(data, "colour_profile", source),
            accepted_formats=_formats(data, source),
            preferred_format=_required_string(data, "preferred_format", source).upper(),
            printable_bounds=_optional_bounds(data, "printable_bounds", source),
            safe_bounds=_optional_bounds(data, "safe_bounds", source),
            background_policy=_required_string(data, "background_policy", source),
            profile_version=_required_string(data, "profile_version", source),
            verified_date=_optional_string(data, "verified_date", source),
            source_description=_required_string(data, "source_description", source),
        )
    except ProviderProfileError as error:
        raise ProviderProfileError(f"Provider profile {source}: {error}") from error


def _validate_physical_dimensions(profile: ProviderProfile) -> None:
    expected_width = profile.canvas_width_px / profile.dpi * 25.4
    expected_height = profile.canvas_height_px / profile.dpi * 25.4
    if (
        abs(profile.physical_width_mm - expected_width) > 1.0
        or abs(profile.physical_height_mm - expected_height) > 1.0
    ):
        raise ProviderProfileError(
            "Provider profile physical dimensions are inconsistent with canvas dimensions and DPI."
        )


def _required_string(data: Mapping[str, object], field: str, source: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProviderProfileError(f"{source} field '{field}' must be a non-empty string.")
    return value.strip()


def _optional_string(data: Mapping[str, object], field: str, source: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProviderProfileError(f"{source} field '{field}' must be a non-empty string or null.")
    return value.strip()


def _required_positive_int(data: Mapping[str, object], field: str, source: str) -> int:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProviderProfileError(f"{source} field '{field}' must be a positive integer.")
    return value


def _optional_positive_float(data: Mapping[str, object], field: str, source: str) -> float | None:
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ProviderProfileError(f"{source} field '{field}' must be a positive number or null.")
    return float(value)


def _formats(data: Mapping[str, object], source: str) -> tuple[str, ...]:
    values = data.get("accepted_formats")
    if not isinstance(values, list) or not values:
        raise ProviderProfileError(f"{source} field 'accepted_formats' must be a non-empty list.")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ProviderProfileError(f"{source} field 'accepted_formats' must contain only non-empty strings.")
    return tuple(value.strip().upper() for value in values)


def _optional_bounds(data: Mapping[str, object], field: str, source: str) -> PixelBounds | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ProviderProfileError(f"{source} field '{field}' must be an object or null.")
    values: dict[str, int] = {}
    for name in ("x", "y", "width", "height"):
        raw = value.get(name)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ProviderProfileError(f"{source} field '{field}.{name}' must be an integer.")
        values[name] = raw
    return PixelBounds(**values)