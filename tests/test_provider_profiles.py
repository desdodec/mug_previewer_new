from __future__ import annotations

from dataclasses import replace
from importlib import resources

import pytest

from mug_previewer.providers import (
    PixelBounds,
    ProviderProfileError,
    ProviderProfileRegistryError,
    build_provider_registry,
    get_provider_profile,
    list_provider_profiles,
    load_provider_profile_mapping,
)


def test_builtin_profiles_are_resource_backed_unique_and_deterministic() -> None:
    profiles = list_provider_profiles()
    assert [profile.id for profile in profiles] == [
        "inkthreadable_11oz_white",
        "printify_generic_11oz_ceramic",
        "prodigi_h_mug_w",
    ]
    assert len({profile.id for profile in profiles}) == len(profiles)
    assert resources.files("mug_previewer.providers").joinpath(
        "profiles", "inkthreadable_11oz_white.json",
    ).is_file()


def test_inkthreadable_profile_matches_supplied_specification() -> None:
    profile = get_provider_profile("inkthreadable_11oz_white")
    assert (profile.canvas_width_px, profile.canvas_height_px, profile.dpi) == (2550, 1125, 300)
    assert (profile.physical_width_mm, profile.physical_height_mm) == (None, None)
    assert (profile.front_centre_x, profile.rear_centre_x) == (0.25, 0.75)
    assert (profile.front_centre_y, profile.rear_centre_y) == (0.50, 0.50)
    assert (profile.front_scale, profile.rear_scale) == (1.0, 1.0)
    assert profile.inward_offset_mm == 0.0
    assert profile.colour_mode == "RGB"
    assert profile.colour_profile is None
    assert profile.accepted_formats == ("PNG",)
    assert profile.preferred_format == "PNG"
    assert profile.safe_bounds is None
    assert profile.printable_bounds == PixelBounds(0, 0, 2550, 1125)


def test_printify_generic_profile_matches_supplied_specification() -> None:
    profile = get_provider_profile("printify_generic_11oz_ceramic")
    assert (profile.canvas_width_px, profile.canvas_height_px, profile.dpi) == (2475, 1155, 300)
    assert profile.colour_profile == "sRGB"
    assert profile.accepted_formats == ("PNG", "JPEG")
    assert profile.preferred_format == "PNG"
    assert profile.printable_bounds is None
    assert profile.safe_bounds is None
    assert "not universal" in profile.source_description


def test_unknown_profile_lookup_is_clear() -> None:
    with pytest.raises(ProviderProfileRegistryError, match="Unknown provider profile ID"):
        get_provider_profile("unknown_provider")


def test_provider_model_rejects_invalid_canvas_dpi_formats_and_bounds() -> None:
    profile = get_provider_profile("inkthreadable_11oz_white")
    with pytest.raises(ProviderProfileError, match="canvas dimensions"):
        replace(profile, canvas_width_px=0)
    with pytest.raises(ProviderProfileError, match="DPI"):
        replace(profile, dpi=0)
    with pytest.raises(ProviderProfileError, match="preferred_format"):
        replace(profile, preferred_format="JPEG")
    with pytest.raises(ProviderProfileError, match="inside the canvas"):
        replace(profile, safe_bounds=PixelBounds(2300, 0, 100, 100))


def test_mapping_loader_rejects_malformed_required_fields() -> None:
    with pytest.raises(ProviderProfileError, match="provider_name"):
        load_provider_profile_mapping({"id": "broken"}, source="broken.json")


def test_registry_rejects_duplicate_profile_ids() -> None:
    profile = get_provider_profile("inkthreadable_11oz_white")
    with pytest.raises(ProviderProfileRegistryError, match="Duplicate provider profile ID"):
        build_provider_registry((profile, profile))

def test_prodigi_h_mug_w_profile_matches_product_specific_template() -> None:
    profile = get_provider_profile("prodigi_h_mug_w")
    assert (profile.canvas_width_px, profile.canvas_height_px, profile.dpi) == (2705, 1122, 300)
    assert (profile.physical_width_mm, profile.physical_height_mm) == (229.0, 95.0)
    assert (profile.front_centre_x, profile.rear_centre_x) == (0.25, 0.75)
    assert profile.inward_offset_mm == 0.0
