from __future__ import annotations

from PIL import Image, ImageDraw
import pytest

from mug_previewer.preview.mockup import CANONICAL_WRAP_PREVIEW_GEOMETRY
from mug_previewer.preview_v2 import (
    CameraPose,
    GENERIC_11OZ_CALIBRATION,
    MugCalibration,
    MugPreviewV2Options,
    PreviewScene,
    PreviewV2Error,
    PreviewV2Mode,
    PreviewV2View,
    build_circumference_strip,
    circumference_width_px,
    project_wrap_v2,
    projection_diagnostics,
    render_mug_preview_v2,
    render_mug_preview_v2_result,
)


def _banded_wrap() -> Image.Image:
    g = CANONICAL_WRAP_PREVIEW_GEOMETRY
    image = Image.new("RGBA", (g.width_px, g.height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 944, g.height_px - 1), fill=(220, 40, 40, 255))
    draw.rectangle((945, 0, 1416, g.height_px - 1), fill=(40, 170, 70, 255))
    draw.rectangle((1417, 0, 2361, g.height_px - 1), fill=(45, 80, 210, 255))
    return image


def test_generic_calibration_has_outside_canvas_arc() -> None:
    g = CANONICAL_WRAP_PREVIEW_GEOMETRY
    calibration = GENERIC_11OZ_CALIBRATION
    full_width = circumference_width_px(calibration)
    assert full_width > g.width_px
    assert calibration.unprinted_span_degrees == 60.0
    assert full_width == round(g.width_px * 360 / 300)


def test_circumference_strip_preserves_wrap_and_adds_transparent_gap() -> None:
    wrap = _banded_wrap()
    strip = build_circumference_strip(wrap, GENERIC_11OZ_CALIBRATION)
    assert strip.height == wrap.height
    assert strip.width > wrap.width
    assert strip.crop((0, 0, wrap.width, wrap.height)).tobytes() == wrap.tobytes()
    assert strip.getpixel((strip.width - 1, strip.height // 2))[3] == 0


def test_front_and_rear_centres_put_handle_on_opposite_sides() -> None:
    calibration = GENERIC_11OZ_CALIBRATION
    front = projection_diagnostics(calibration, CameraPose(), PreviewV2View.FRONT)
    rear = projection_diagnostics(calibration, CameraPose(), PreviewV2View.REAR)
    assert front.handle_delta_degrees > 0
    assert rear.handle_delta_degrees < 0
    assert abs(abs(front.handle_delta_degrees) - 90) < 0.2
    assert abs(abs(rear.handle_delta_degrees) - 90) < 0.2


def test_camera_yaw_moves_projection_centre_without_moving_artwork() -> None:
    calibration = GENERIC_11OZ_CALIBRATION
    zero = projection_diagnostics(calibration, CameraPose(0), PreviewV2View.FRONT)
    turned = projection_diagnostics(calibration, CameraPose(12), PreviewV2View.FRONT)
    assert turned.source_centre_x == zero.source_centre_x
    assert turned.camera_centre_x > zero.camera_centre_x
    assert turned.handle_delta_degrees != zero.handle_delta_degrees


def test_v2_projection_keeps_selected_face_central() -> None:
    wrap = _banded_wrap()
    front, _ = project_wrap_v2(
        wrap, target_size=(500, 560), calibration=GENERIC_11OZ_CALIBRATION,
        camera=CameraPose(), view=PreviewV2View.FRONT,
    )
    rear, _ = project_wrap_v2(
        wrap, target_size=(500, 560), calibration=GENERIC_11OZ_CALIBRATION,
        camera=CameraPose(), view=PreviewV2View.REAR,
    )
    assert front.getpixel((250, 280))[0] > 150
    assert rear.getpixel((250, 280))[2] > 150


def test_customer_and_engineering_modes_are_distinct_and_deterministic() -> None:
    wrap = _banded_wrap()
    scene = PreviewScene(canvas_size=(700, 560), body_height_px=420)
    customer_options = MugPreviewV2Options(scene=scene, mode=PreviewV2Mode.CUSTOMER)
    engineering_options = MugPreviewV2Options(scene=scene, mode=PreviewV2Mode.ENGINEERING)
    customer = render_mug_preview_v2(wrap, customer_options)
    engineering = render_mug_preview_v2(wrap, engineering_options)
    assert customer.size == engineering.size == (700, 560)
    assert customer.mode == engineering.mode == "RGBA"
    assert customer.tobytes() == render_mug_preview_v2(wrap, customer_options).tobytes()
    assert customer.tobytes() != engineering.tobytes()


def test_engineering_result_reports_body_and_printable_bounds() -> None:
    result = render_mug_preview_v2_result(
        _banded_wrap(),
        MugPreviewV2Options(
            scene=PreviewScene(canvas_size=(800, 620), body_height_px=460),
            mode="engineering",
            view="rear",
        ),
    )
    left, top, right, bottom = result.body_bounds_xyxy
    pleft, ptop, pright, pbottom = result.printable_bounds_xyxy
    assert left == pleft and right == pright
    assert top <= ptop < pbottom <= bottom
    assert result.diagnostics.handle_delta_degrees < 0


def test_camera_rotation_changes_apparent_preview_not_canonical_wrap() -> None:
    wrap = _banded_wrap()
    before = wrap.tobytes()
    scene = PreviewScene(canvas_size=(700, 560), body_height_px=420)
    straight = render_mug_preview_v2(
        wrap, MugPreviewV2Options(scene=scene, camera=CameraPose(0), view="rear")
    )
    rotated = render_mug_preview_v2(
        wrap, MugPreviewV2Options(scene=scene, camera=CameraPose(14), view="rear")
    )
    assert straight.tobytes() != rotated.tobytes()
    assert wrap.tobytes() == before


def test_invalid_wrap_and_calibration_fail_explicitly() -> None:
    with pytest.raises(PreviewV2Error, match="requires canonical"):
        render_mug_preview_v2(Image.new("RGBA", (10, 10)))
    with pytest.raises(PreviewV2Error):
        MugCalibration(id="bad", label="bad", wrap_span_degrees=360)
