"""Package-resource-backed provider profile discovery and lookup."""

from __future__ import annotations

from functools import lru_cache
import json
from importlib import resources
from typing import Iterable, Mapping

from .models import ProviderProfile, ProviderProfileError, provider_profile_from_mapping


class ProviderProfileRegistryError(ValueError):
    """Raised when profile registration or lookup cannot be completed."""


def list_provider_profiles() -> tuple[ProviderProfile, ...]:
    """Return all built-in profiles in deterministic stable-ID order."""
    return _builtin_profiles()


def get_provider_profile(profile_id: str) -> ProviderProfile:
    """Return one profile by exact stable ID or raise a clear domain error."""
    try:
        return _builtin_profile_index()[profile_id]
    except KeyError as error:
        raise ProviderProfileRegistryError(f"Unknown provider profile ID: {profile_id!r}.") from error


def build_provider_registry(profiles: Iterable[ProviderProfile]) -> tuple[ProviderProfile, ...]:
    """Validate unique IDs and return profiles ordered deterministically by ID."""
    indexed: dict[str, ProviderProfile] = {}
    for profile in profiles:
        if profile.id in indexed:
            raise ProviderProfileRegistryError(f"Duplicate provider profile ID: {profile.id!r}.")
        indexed[profile.id] = profile
    return tuple(indexed[profile_id] for profile_id in sorted(indexed))


def load_provider_profile_mapping(data: Mapping[str, object], *, source: str) -> ProviderProfile:
    """Expose data parsing for tests and future custom profile loaders."""
    return provider_profile_from_mapping(data, source=source)


@lru_cache(maxsize=1)
def _builtin_profiles() -> tuple[ProviderProfile, ...]:
    profile_directory = resources.files("mug_previewer.providers").joinpath("profiles")
    profiles: list[ProviderProfile] = []
    for resource in sorted(profile_directory.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".json"):
            continue
        try:
            payload = json.loads(resource.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ProviderProfileRegistryError(
                f"Built-in provider profile {resource.name} is not valid JSON: {error.msg}."
            ) from error
        if not isinstance(payload, dict):
            raise ProviderProfileRegistryError(f"Built-in provider profile {resource.name} must be a JSON object.")
        try:
            profiles.append(provider_profile_from_mapping(payload, source=resource.name))
        except ProviderProfileError as error:
            raise ProviderProfileRegistryError(str(error)) from error
    return build_provider_registry(profiles)


@lru_cache(maxsize=1)
def _builtin_profile_index() -> dict[str, ProviderProfile]:
    return {profile.id: profile for profile in _builtin_profiles()}