"""JSON-backed V2 mug calibration registry with explicit provenance."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import lru_cache
from importlib import resources
import json
import math
from typing import Iterable, Mapping

from .models import MugCalibration, PreviewV2Error


class CalibrationRegistryError(ValueError):
    """Raised when V2 calibration data is invalid or ambiguous."""


class CalibrationStatus(StrEnum):
    GENERIC = "generic"
    PROVIDER_DIMENSIONS = "provider-dimensions"
    ESTIMATED = "estimated"
    PROVISIONAL = "provisional"


@dataclass(frozen=True)
class MugCalibrationProfile:
    """One provider/SKU calibration plus the evidence used to derive it."""

    id: str
    provider_name: str | None
    product_name: str
    provider_profile_id: str | None
    sku: str | None
    status: CalibrationStatus
    calibration: MugCalibration
    default_front_yaw_degrees: float
    default_rear_yaw_degrees: float
    source_description: str
    source_url: str | None
    verified_date: str | None
    body_height_mm: float | None
    body_diameter_mm: float | None
    print_width_mm: float | None
    print_height_mm: float | None

    @property
    def display_label(self) -> str:
        provider = self.provider_name or "Generic"
        suffix = f" / {self.sku}" if self.sku else ""
        return f"{provider} — {self.product_name}{suffix} [{self.status.value}]"

    @property
    def is_provider_specific(self) -> bool:
        return self.provider_name is not None


def list_calibration_profiles() -> tuple[MugCalibrationProfile, ...]:
    """Return all packaged V2 calibration profiles in deterministic order."""
    return _builtin_profiles()


def get_calibration_profile(profile_id: str) -> MugCalibrationProfile:
    """Return one calibration by stable calibration ID."""
    try:
        return _builtin_profile_index()[profile_id]
    except KeyError as error:
        raise CalibrationRegistryError(f"Unknown V2 calibration profile ID: {profile_id!r}.") from error


def resolve_calibration_profile(
    *,
    provider_profile_id: str | None = None,
    provider_name: str | None = None,
    sku: str | None = None,
) -> MugCalibrationProfile:
    """Resolve the most specific calibration, falling back explicitly to generic.

    Matching priority is exact provider profile ID, then provider + SKU, then
    a provider-level profile with no SKU. A Prodigi runtime provider ID of the
    form ``prodigi__SKU`` is translated into provider + SKU automatically.
    """
    profiles = _builtin_profiles()
    generic = get_calibration_profile("generic_11oz_v2")

    profile_id = _clean_optional(provider_profile_id)
    provider = _clean_optional(provider_name)
    requested_sku = _normalise_optional_sku(sku)

    if profile_id:
        exact = [item for item in profiles if item.provider_profile_id == profile_id]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise CalibrationRegistryError(f"Multiple V2 calibrations match provider profile {profile_id!r}.")
        if profile_id.startswith("prodigi__"):
            provider = provider or "Prodigi"
            requested_sku = requested_sku or _normalise_optional_sku(profile_id[len("prodigi__"):])

    if provider and requested_sku:
        exact = [
            item for item in profiles
            if item.provider_name is not None
            and item.provider_name.casefold() == provider.casefold()
            and item.sku == requested_sku
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise CalibrationRegistryError(
                f"Multiple V2 calibrations match provider {provider!r} and SKU {requested_sku!r}."
            )

    if provider:
        provider_defaults = [
            item for item in profiles
            if item.provider_name is not None
            and item.provider_name.casefold() == provider.casefold()
            and item.sku is None
        ]
        if len(provider_defaults) == 1:
            return provider_defaults[0]
        if len(provider_defaults) > 1:
            raise CalibrationRegistryError(f"Multiple provider-level V2 calibrations match {provider!r}.")

    return generic


def build_calibration_registry(
    profiles: Iterable[MugCalibrationProfile],
) -> tuple[MugCalibrationProfile, ...]:
    indexed: dict[str, MugCalibrationProfile] = {}
    provider_ids: dict[str, str] = {}
    sku_keys: dict[tuple[str, str], str] = {}
    for profile in profiles:
        if profile.id in indexed:
            raise CalibrationRegistryError(f"Duplicate V2 calibration profile ID: {profile.id!r}.")
        indexed[profile.id] = profile
        if profile.provider_profile_id:
            previous = provider_ids.get(profile.provider_profile_id)
            if previous:
                raise CalibrationRegistryError(
                    f"Provider profile {profile.provider_profile_id!r} is matched by both {previous!r} and {profile.id!r}."
                )
            provider_ids[profile.provider_profile_id] = profile.id
        if profile.provider_name and profile.sku:
            key = (profile.provider_name.casefold(), profile.sku)
            previous = sku_keys.get(key)
            if previous:
                raise CalibrationRegistryError(
                    f"Provider/SKU {profile.provider_name!r}/{profile.sku!r} is matched by both {previous!r} and {profile.id!r}."
                )
            sku_keys[key] = profile.id
    return tuple(indexed[key] for key in sorted(indexed))


def load_calibration_mapping(
    data: Mapping[str, object],
    *,
    source: str,
) -> MugCalibrationProfile:
    """Parse one calibration mapping and derive geometry from physical data when possible."""
    try:
        profile_id = _required_string(data, "id", source)
        provider_name = _optional_string(data, "provider_name", source)
        product_name = _required_string(data, "product_name", source)
        provider_profile_id = _optional_string(data, "provider_profile_id", source)
        sku = _normalise_optional_sku(_optional_string(data, "sku", source))
        try:
            status = CalibrationStatus(_required_string(data, "status", source))
        except ValueError as error:
            allowed = ", ".join(item.value for item in CalibrationStatus)
            raise CalibrationRegistryError(f"{source} field 'status' must be one of: {allowed}.") from error

        body_height = _optional_positive_float(data, "body_height_mm", source)
        body_diameter = _optional_positive_float(data, "body_diameter_mm", source)
        print_width = _optional_positive_float(data, "print_width_mm", source)
        print_height = _optional_positive_float(data, "print_height_mm", source)

        ratio = _optional_positive_float(data, "body_width_to_height", source)
        printable_fraction = _optional_positive_float(data, "printable_height_fraction", source)
        wrap_span = _optional_positive_float(data, "wrap_span_degrees", source)

        if body_height is not None and body_diameter is not None:
            ratio = body_diameter / body_height
            if print_height is not None:
                printable_fraction = min(1.0, print_height / body_height)
            if print_width is not None:
                wrap_span = print_width / (math.pi * body_diameter) * 360.0

        if ratio is None or printable_fraction is None or wrap_span is None:
            raise CalibrationRegistryError(
                f"{source} must provide either physical body/print dimensions or explicit geometry values."
            )

        calibration = MugCalibration(
            id=profile_id,
            label=_optional_string(data, "label", source) or product_name,
            body_width_to_height=ratio,
            printable_height_fraction=printable_fraction,
            wrap_span_degrees=wrap_span,
            visible_angle_degrees=_optional_positive_float(data, "visible_angle_degrees", source) or 150.0,
            body_corner_fraction=_optional_positive_float(data, "body_corner_fraction", source) or 0.055,
            handle_width_fraction=_optional_positive_float(data, "handle_width_fraction", source) or 0.30,
            handle_height_fraction=_optional_positive_float(data, "handle_height_fraction", source) or 0.54,
            handle_stroke_fraction=_optional_positive_float(data, "handle_stroke_fraction", source) or 0.075,
        )

        verified_date = _optional_string(data, "verified_date", source)
        if verified_date:
            try:
                date.fromisoformat(verified_date)
            except ValueError as error:
                raise CalibrationRegistryError(f"{source} field 'verified_date' must be ISO YYYY-MM-DD.") from error

        return MugCalibrationProfile(
            id=profile_id,
            provider_name=provider_name,
            product_name=product_name,
            provider_profile_id=provider_profile_id,
            sku=sku,
            status=status,
            calibration=calibration,
            default_front_yaw_degrees=_optional_number(data, "default_front_yaw_degrees", source) or 0.0,
            default_rear_yaw_degrees=_optional_number(data, "default_rear_yaw_degrees", source) or 0.0,
            source_description=_required_string(data, "source_description", source),
            source_url=_optional_string(data, "source_url", source),
            verified_date=verified_date,
            body_height_mm=body_height,
            body_diameter_mm=body_diameter,
            print_width_mm=print_width,
            print_height_mm=print_height,
        )
    except PreviewV2Error as error:
        raise CalibrationRegistryError(f"V2 calibration {source}: {error}") from error


@lru_cache(maxsize=1)
def _builtin_profiles() -> tuple[MugCalibrationProfile, ...]:
    directory = resources.files("mug_previewer.preview_v2").joinpath("calibrations")
    profiles: list[MugCalibrationProfile] = []
    for resource in sorted(directory.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".json"):
            continue
        try:
            payload = json.loads(resource.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CalibrationRegistryError(
                f"Built-in V2 calibration {resource.name} is not valid JSON: {error.msg}."
            ) from error
        if not isinstance(payload, dict):
            raise CalibrationRegistryError(f"Built-in V2 calibration {resource.name} must be a JSON object.")
        profiles.append(load_calibration_mapping(payload, source=resource.name))
    registry = build_calibration_registry(profiles)
    if "generic_11oz_v2" not in {item.id for item in registry}:
        raise CalibrationRegistryError("Built-in V2 calibrations must include generic_11oz_v2.")
    return registry


@lru_cache(maxsize=1)
def _builtin_profile_index() -> dict[str, MugCalibrationProfile]:
    return {profile.id: profile for profile in _builtin_profiles()}


def _required_string(data: Mapping[str, object], field: str, source: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CalibrationRegistryError(f"{source} field {field!r} must be a non-empty string.")
    return value.strip()


def _optional_string(data: Mapping[str, object], field: str, source: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CalibrationRegistryError(f"{source} field {field!r} must be a non-empty string or null.")
    return value.strip()


def _optional_positive_float(data: Mapping[str, object], field: str, source: str) -> float | None:
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise CalibrationRegistryError(f"{source} field {field!r} must be a positive finite number or null.")
    return float(value)


def _optional_number(data: Mapping[str, object], field: str, source: str) -> float | None:
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CalibrationRegistryError(f"{source} field {field!r} must be a finite number or null.")
    return float(value)


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalise_optional_sku(value: str | None) -> str | None:
    cleaned = _clean_optional(value)
    return cleaned.upper() if cleaned else None
