"""Generate deterministic full-wrap artwork for calibrating provider mug mockups."""
from __future__ import annotations

from dataclasses import dataclass
from math import floor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..preview.mockup import CANONICAL_WRAP_PREVIEW_GEOMETRY
from ..preview_v2.calibration_registry import MugCalibrationProfile

TARGET_SIZE = (
    CANONICAL_WRAP_PREVIEW_GEOMETRY.width_px,
    CANONICAL_WRAP_PREVIEW_GEOMETRY.height_px,
)


@dataclass(frozen=True)
class CalibrationTargetSpec:
    longitude_step_degrees: float = 15.0
    horizontal_bands: int = 10
    line_width_px: int = 5
    major_line_width_px: int = 9


def render_calibration_target(
    profile: MugCalibrationProfile,
    spec: CalibrationTargetSpec | None = None,
) -> Image.Image:
    """Render a provider-profile-aware calibration target on the canonical wrap."""
    spec = spec or CalibrationTargetSpec()
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    calibration = profile.calibration
    image = Image.new("RGBA", TARGET_SIZE, (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    # Horizontal bands expose vertical scaling, pitch and taper.
    for index in range(spec.horizontal_bands + 1):
        y = round(index * (geometry.height_px - 1) / spec.horizontal_bands)
        major = index in {0, spec.horizontal_bands // 2, spec.horizontal_bands}
        width = spec.major_line_width_px if major else spec.line_width_px
        colour = (0, 0, 0, 255) if major else (150, 150, 150, 255)
        draw.line((0, y, geometry.width_px - 1, y), fill=colour, width=width)
        if index not in {0, spec.horizontal_bands}:
            draw.text((8, max(0, y - 12)), f"{index * 10}%", fill=(0, 0, 0, 255), font=font)

    # The central seam/handle exclusion is deliberately unmistakable.
    seam_left = geometry.seam_left_px
    seam_right = geometry.seam_left_px + geometry.seam_width_px - 1
    draw.rectangle(
        (seam_left, 0, seam_right, geometry.height_px - 1),
        fill=(235, 235, 235, 255),
        outline=(0, 0, 0, 255),
        width=4,
    )
    draw.line((seam_left, 0, seam_right, geometry.height_px - 1), fill=(90, 90, 90, 255), width=3)
    draw.line((seam_right, 0, seam_left, geometry.height_px - 1), fill=(90, 90, 90, 255), width=3)
    _centred_label(draw, "HANDLE / SEAM EXCLUSION", (seam_left + seam_right) // 2, 35, font)

    _draw_face_target(
        draw,
        centre_x=geometry.front_centre_x,
        zone_left=geometry.front_left_px,
        zone_width=geometry.front_width_px,
        face_name="FRONT",
        calibration=calibration,
        spec=spec,
        font=font,
        left_symbol="TRIANGLE",
        right_symbol="SQUARE",
    )
    _draw_face_target(
        draw,
        centre_x=geometry.rear_centre_x,
        zone_left=geometry.rear_left_px,
        zone_width=geometry.rear_width_px,
        face_name="REAR",
        calibration=calibration,
        spec=spec,
        font=font,
        left_symbol="CIRCLE",
        right_symbol="DIAMOND",
    )

    # Flat-canvas references are valuable if a provider applies a non-cylindrical warp.
    for x, label in (
        (geometry.front_centre_x, "F0"),
        (geometry.seam_left_px + geometry.seam_width_px / 2, "SEAM"),
        (geometry.rear_centre_x, "R0"),
    ):
        xi = round(x)
        draw.line((xi, 0, xi, geometry.height_px - 1), fill=(220, 0, 0, 255), width=spec.major_line_width_px)
        _centred_label(draw, label, xi, geometry.height_px - 28, font)

    footer = (
        f"MUG PREVIEWER V2 CALIBRATION TARGET | {profile.id} | "
        f"print arc {calibration.wrap_span_degrees:.2f} deg | "
        f"longitude step {spec.longitude_step_degrees:g} deg"
    )
    draw.rectangle((0, geometry.height_px - 23, geometry.width_px - 1, geometry.height_px - 1), fill=(255, 255, 255, 230))
    draw.text((8, geometry.height_px - 19), footer, fill=(0, 0, 0, 255), font=font)
    return image


def save_calibration_target(
    destination: Path | str,
    profile: MugCalibrationProfile,
    spec: CalibrationTargetSpec | None = None,
) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    render_calibration_target(profile, spec).save(path, "PNG")
    return path


def _draw_face_target(
    draw: ImageDraw.ImageDraw,
    *,
    centre_x: float,
    zone_left: int,
    zone_width: int,
    face_name: str,
    calibration,
    spec: CalibrationTargetSpec,
    font: ImageFont.ImageFont,
    left_symbol: str,
    right_symbol: str,
) -> None:
    # Convert angular offsets into flat-canvas x offsets using the selected
    # calibration's print arc. The provider mockup then reveals the effective
    # camera yaw and cylindrical compression.
    pixels_per_degree = CANONICAL_WRAP_PREVIEW_GEOMETRY.width_px / calibration.wrap_span_degrees
    max_half_zone_deg = (zone_width / 2) / pixels_per_degree
    max_step = floor(max_half_zone_deg / spec.longitude_step_degrees)

    for step in range(-max_step, max_step + 1):
        angle = step * spec.longitude_step_degrees
        x = round(centre_x + angle * pixels_per_degree)
        if not (zone_left <= x < zone_left + zone_width):
            continue
        major = step == 0 or step % 2 == 0
        colour = (215, 0, 0, 255) if step == 0 else ((0, 75, 210, 255) if step < 0 else (0, 145, 65, 255))
        width = spec.major_line_width_px if major else spec.line_width_px
        draw.line((x, 0, x, CANONICAL_WRAP_PREVIEW_GEOMETRY.height_px - 1), fill=colour, width=width)
        _centred_label(draw, f"{angle:+g}°", x, 8, font)

    cx = round(centre_x)
    cy = CANONICAL_WRAP_PREVIEW_GEOMETRY.height_px // 2
    _draw_bullseye(draw, cx, cy)
    _centred_label(draw, f"{face_name} 0°", cx, cy + 46, font)

    offset_x = round(zone_width * 0.28)
    offset_y = round(CANONICAL_WRAP_PREVIEW_GEOMETRY.height_px * 0.22)
    _draw_fiducial(draw, cx - offset_x, cy - offset_y, left_symbol)
    _draw_fiducial(draw, cx + offset_x, cy - offset_y, right_symbol)
    _draw_fiducial(draw, cx - offset_x, cy + offset_y, right_symbol)
    _draw_fiducial(draw, cx + offset_x, cy + offset_y, left_symbol)


def _draw_bullseye(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    for radius, colour, width in (
        (34, (0, 0, 0, 255), 6),
        (22, (255, 255, 255, 255), 6),
        (10, (215, 0, 0, 255), 10),
    ):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=colour, width=width)
    draw.line((x - 45, y, x + 45, y), fill=(0, 0, 0, 255), width=3)
    draw.line((x, y - 45, x, y + 45), fill=(0, 0, 0, 255), width=3)


def _draw_fiducial(draw: ImageDraw.ImageDraw, x: int, y: int, kind: str) -> None:
    r = 22
    if kind == "TRIANGLE":
        draw.polygon(((x, y - r), (x - r, y + r), (x + r, y + r)), outline=(0, 0, 0, 255), fill=(245, 205, 0, 255))
    elif kind == "SQUARE":
        draw.rectangle((x - r, y - r, x + r, y + r), outline=(0, 0, 0, 255), fill=(0, 190, 210, 255), width=3)
    elif kind == "CIRCLE":
        draw.ellipse((x - r, y - r, x + r, y + r), outline=(0, 0, 0, 255), fill=(230, 70, 160, 255), width=3)
    else:
        draw.polygon(((x, y - r), (x - r, y), (x, y + r), (x + r, y)), outline=(0, 0, 0, 255), fill=(125, 90, 210, 255))


def _centred_label(draw: ImageDraw.ImageDraw, text: str, x: int | float, y: int, font: ImageFont.ImageFont) -> None:
    box = draw.textbbox((0, 0), text, font=font)
    width = box[2] - box[0]
    draw.rectangle((round(x - width / 2) - 3, y - 2, round(x + width / 2) + 3, y + 12), fill=(255, 255, 255, 225))
    draw.text((round(x - width / 2), y), text, fill=(0, 0, 0, 255), font=font)
