from __future__ import annotations

import math

import pytest

from mug_previewer.preview_v2 import (
    CalibrationRegistryError,
    CalibrationStatus,
    get_calibration_profile,
    list_calibration_profiles,
    load_calibration_mapping,
    resolve_calibration_profile,
)


def test_packaged_calibrations_are_present_and_stably_ordered() -> None:
    profiles = list_calibration_profiles()
    ids = [profile.id for profile in profiles]
    assert ids == sorted(ids)
    assert {
        "generic_11oz_v2",
        "inkthreadable_11oz_white_v2",
        "printify_generic_11oz_ceramic_v2",
        "prodigi_h_mug_w_v2",
    }.issubset(ids)


def test_inkthreadable_geometry_is_derived_from_provider_dimensions() -> None:
    profile = resolve_calibration_profile(provider_profile_id="inkthreadable_11oz_white")
    assert profile.id == "inkthreadable_11oz_white_v2"
    assert profile.status is CalibrationStatus.PROVIDER_DIMENSIONS
    assert profile.calibration.body_width_to_height == pytest.approx(82 / 97)
    assert profile.calibration.printable_height_fraction == pytest.approx(90 / 97)
    assert profile.calibration.wrap_span_degrees == pytest.approx(
        200 / (math.pi * 82) * 360
    )


def test_printify_generic_calibration_is_explicitly_estimated() -> None:
    profile = resolve_calibration_profile(
        provider_profile_id="printify_generic_11oz_ceramic",
    )
    assert profile.id == "printify_generic_11oz_ceramic_v2"
    assert profile.status is CalibrationStatus.ESTIMATED
    assert profile.calibration.body_width_to_height == pytest.approx(82.042 / 96.52)
    assert profile.calibration.printable_height_fraction == 1.0
    assert profile.calibration.wrap_span_degrees == pytest.approx(
        209.55 / (math.pi * 82.042) * 360
    )


def test_prodigi_runtime_id_resolves_exact_sku_profile() -> None:
    profile = resolve_calibration_profile(provider_profile_id="prodigi__H-MUG-W")
    assert profile.id == "prodigi_h_mug_w_v2"
    assert profile.provider_name == "Prodigi"
    assert profile.sku == "H-MUG-W"
    assert profile.status is CalibrationStatus.PROVISIONAL
    assert profile.print_width_mm == 229.0
    assert profile.print_height_mm == 95.0
    assert profile.calibration.wrap_span_degrees == pytest.approx(
        229 / (math.pi * 82) * 360
    )


def test_unknown_prodigi_sku_falls_back_to_generic_not_h_mug_w() -> None:
    profile = resolve_calibration_profile(provider_profile_id="prodigi__SOME-OTHER-MUG")
    assert profile.id == "generic_11oz_v2"
    assert profile.status is CalibrationStatus.GENERIC


def test_provider_and_sku_matching_is_case_insensitive() -> None:
    profile = resolve_calibration_profile(provider_name="prodigi", sku="h-mug-w")
    assert profile.id == "prodigi_h_mug_w_v2"


def test_profile_display_label_exposes_calibration_quality() -> None:
    provisional = get_calibration_profile("prodigi_h_mug_w_v2")
    assert "Prodigi" in provisional.display_label
    assert "H-MUG-W" in provisional.display_label
    assert "[provisional]" in provisional.display_label


def test_mapping_requires_enough_geometry_to_render() -> None:
    with pytest.raises(CalibrationRegistryError, match="physical body/print dimensions"):
        load_calibration_mapping(
            {
                "id": "bad",
                "provider_name": None,
                "product_name": "Bad",
                "provider_profile_id": None,
                "sku": None,
                "status": "generic",
                "source_description": "test",
            },
            source="bad.json",
        )


def test_invalid_calibration_status_is_rejected() -> None:
    with pytest.raises(CalibrationRegistryError, match="status"):
        load_calibration_mapping(
            {
                "id": "bad",
                "provider_name": None,
                "product_name": "Bad",
                "provider_profile_id": None,
                "sku": None,
                "status": "certain",
                "body_width_to_height": 0.82,
                "printable_height_fraction": 0.9,
                "wrap_span_degrees": 300,
                "source_description": "test",
            },
            source="bad.json",
        )
