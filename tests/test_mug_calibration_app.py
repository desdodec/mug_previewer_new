from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops
import pytest

from mug_previewer.calibration import (
    CalibrationFit,
    ImageBounds,
    ViewFit,
    candidate_profile_mapping,
    load_fit_session,
    render_calibration_target,
    render_provider_calibration_target,
    render_target_overlay,
    save_candidate_profile,
    save_calibration_target,
    save_provider_calibration_target,
    save_fit_session,
)
from mug_previewer.preview.mockup import CANONICAL_WRAP_PREVIEW_GEOMETRY
from mug_previewer.providers import get_provider_profile
from mug_previewer.preview_v2 import (
    get_calibration_profile,
    load_calibration_mapping,
)


def test_calibration_target_is_canonical_wrap_with_distinct_face_centres() -> None:
    profile = get_calibration_profile("inkthreadable_11oz_white_v2")
    image = render_calibration_target(profile)
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY

    assert image.size == (2362, 1063)
    assert image.mode == "RGBA"

    front = image.getpixel((round(geometry.front_centre_x), geometry.height_px // 2))
    rear = image.getpixel((round(geometry.rear_centre_x), geometry.height_px // 2))
    seam = image.getpixel(
        (round(geometry.seam_left_px + geometry.seam_width_px / 2), geometry.height_px // 2)
    )

    assert front[:3] != (255, 255, 255)
    assert rear[:3] != (255, 255, 255)
    assert seam[:3] != (255, 255, 255)


def test_save_calibration_target_writes_exact_canonical_png(tmp_path: Path) -> None:
    profile = get_calibration_profile("generic_11oz_v2")
    destination = tmp_path / "target.png"

    saved = save_calibration_target(destination, profile)

    assert saved == destination
    with Image.open(saved) as image:
        assert image.size == (2362, 1063)
        assert image.format == "PNG"


def test_native_prodigi_target_uses_exact_supplier_canvas_and_profile_anchors() -> None:
    profile = get_provider_profile("prodigi_h_mug_w")
    image = render_provider_calibration_target(profile)

    assert image.size == (2705, 1122)
    assert image.mode == "RGB"
    assert image.info["dpi"] == (300, 300)

    front = (
        round((image.width - 1) * profile.front_centre_x),
        round((image.height - 1) * profile.front_centre_y),
    )
    midpoint = (
        round((image.width - 1) * 0.5),
        round((image.height - 1) * 0.5),
    )
    rear = (
        round((image.width - 1) * profile.rear_centre_x),
        round((image.height - 1) * profile.rear_centre_y),
    )

    assert image.getpixel(front) != (255, 255, 255)
    assert image.getpixel(midpoint) != (255, 255, 255)
    assert image.getpixel(rear) != (255, 255, 255)


def test_native_supplier_target_save_preserves_dimensions_and_dpi(tmp_path: Path) -> None:
    profile = get_provider_profile("prodigi_h_mug_w")
    destination = tmp_path / "prodigi_native.png"

    saved = save_provider_calibration_target(destination, profile)

    assert saved == destination
    with Image.open(saved) as image:
        assert image.size == (2705, 1122)
        assert image.mode == "RGB"
        assert image.format == "PNG"
        assert image.info["dpi"] == pytest.approx((300, 300), abs=0.1)


def test_native_supplier_target_rejects_non_png_destination(tmp_path: Path) -> None:
    profile = get_provider_profile("prodigi_h_mug_w")
    with pytest.raises(ValueError, match="\.png"):
        save_provider_calibration_target(tmp_path / "target.jpg", profile)


def test_overlay_keeps_mockup_size_and_changes_only_when_bounds_exist() -> None:
    profile = get_calibration_profile("inkthreadable_11oz_white_v2")
    target = render_calibration_target(profile)
    mockup = Image.new("RGBA", (900, 700), (245, 245, 245, 255))

    empty_fit = CalibrationFit(profile_id=profile.id)
    unchanged = render_target_overlay(
        mockup,
        target,
        profile=profile,
        fit=empty_fit,
        view="front",
    )
    assert ImageChops.difference(mockup, unchanged).getbbox() is None

    fit = CalibrationFit(
        profile_id=profile.id,
        front=ViewFit(
            bounds=ImageBounds(220, 70, 460, 560),
            camera_yaw_degrees=8.0,
        ),
    )
    overlay = render_target_overlay(
        mockup,
        target,
        profile=profile,
        fit=fit,
        view="front",
        opacity=0.5,
    )

    assert overlay.size == mockup.size
    assert ImageChops.difference(mockup, overlay).getbbox() is not None


def test_overlay_rejects_bounds_outside_mockup() -> None:
    profile = get_calibration_profile("generic_11oz_v2")
    target = render_calibration_target(profile)
    mockup = Image.new("RGBA", (500, 400), "white")
    fit = CalibrationFit(
        profile_id=profile.id,
        rear=ViewFit(bounds=ImageBounds(450, 10, 100, 200)),
    )

    with pytest.raises(ValueError, match="fit inside"):
        render_target_overlay(
            mockup,
            target,
            profile=profile,
            fit=fit,
            view="rear",
        )


def test_calibration_session_round_trips(tmp_path: Path) -> None:
    fit = CalibrationFit(
        profile_id="prodigi_h_mug_w_v2",
        artwork_offset_degrees=-2.25,
        visible_angle_degrees=146.5,
        print_arc_degrees=318.0,
        front=ViewFit(
            bounds=ImageBounds(100, 60, 400, 520),
            camera_yaw_degrees=-7.5,
            vertical_offset_fraction=0.02,
            vertical_scale=0.98,
        ),
        rear=ViewFit(
            bounds=ImageBounds(120, 55, 410, 525),
            camera_yaw_degrees=8.0,
            vertical_offset_fraction=-0.01,
            vertical_scale=1.01,
        ),
    )
    path = tmp_path / "session.json"

    save_fit_session(path, fit)
    loaded = load_fit_session(path)

    assert loaded == fit


def test_candidate_export_is_provisional_and_registry_parseable(tmp_path: Path) -> None:
    profile = get_calibration_profile("prodigi_h_mug_w_v2")
    fit = CalibrationFit(
        profile_id=profile.id,
        artwork_offset_degrees=-1.75,
        visible_angle_degrees=148.0,
        print_arc_degrees=319.0,
        front=ViewFit(
            bounds=ImageBounds(10, 20, 300, 400),
            camera_yaw_degrees=-6.5,
        ),
        rear=ViewFit(
            bounds=ImageBounds(20, 25, 305, 405),
            camera_yaw_degrees=7.0,
        ),
    )

    mapping = candidate_profile_mapping(
        profile,
        fit,
        calibration_id="prodigi_h_mug_w_fitted",
        source_description="provider mockup fit",
    )
    destination = tmp_path / "candidate.json"
    save_candidate_profile(destination, mapping)

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["status"] == "provisional"
    assert payload["artwork_registration_offset_degrees"] == -1.75
    assert payload["default_front_yaw_degrees"] == -6.5
    assert payload["default_rear_yaw_degrees"] == 7.0
    assert payload["fit_notes"]["front_bounds"]["width"] == 300

    parsed = load_calibration_mapping(payload, source="candidate.json")
    assert parsed.id == "prodigi_h_mug_w_fitted"
    assert parsed.calibration.wrap_span_degrees == pytest.approx(319.0)
    assert parsed.calibration.visible_angle_degrees == pytest.approx(148.0)
