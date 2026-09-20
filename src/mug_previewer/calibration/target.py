"""Generate deterministic calibration artwork for provider mug mockups and print files."""
from __future__ import annotations

from dataclasses import dataclass
from math import floor, isclose
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..preview.mockup import CANONICAL_WRAP_PREVIEW_GEOMETRY
from ..preview_v2.calibration_registry import MugCalibrationProfile
from ..providers import ProviderProfile

TARGET_SIZE = (
    CANONICAL_WRAP_PREVIEW_GEOMETRY.width_px,
    CANONICAL_WRAP_PREVIEW_GEOMETRY.height_px,
)


@dataclass(frozen=True)
class CalibrationTargetSpec:
    """Legacy V2 overlay target controls."""

    longitude_step_degrees: float = 15.0
    horizontal_bands: int = 10
    line_width_px: int = 5
    major_line_width_px: int = 9


@dataclass(frozen=True)
class ProviderCalibrationTargetSpec:
    """Native supplier print-target controls expressed in normalised units.

    The 2.5% longitude grid creates 40 equal segments around the flat wrap.
    The 5% vertical grid creates 20 equal height segments. Both are exact
    divisors of one, so every fiducial has a stable machine-readable index.
    """

    longitude_step_fraction: float = 0.025
    horizontal_step_fraction: float = 0.05
    label_every_longitude_steps: int = 5
    label_every_horizontal_steps: int = 5

    def __post_init__(self) -> None:
        for name, value in (
            ("longitude_step_fraction", self.longitude_step_fraction),
            ("horizontal_step_fraction", self.horizontal_step_fraction),
        ):
            if not 0.0 < value <= 0.25:
                raise ValueError(f"{name} must be greater than 0 and no more than 0.25.")
            count = round(1.0 / value)
            if count <= 0 or not isclose(count * value, 1.0, abs_tol=1e-9):
                raise ValueError(f"{name} must divide the full canvas exactly.")
        if self.label_every_longitude_steps <= 0 or self.label_every_horizontal_steps <= 0:
            raise ValueError("Calibration label intervals must be positive integers.")


def render_provider_calibration_target(
    profile: ProviderProfile,
    spec: ProviderCalibrationTargetSpec | None = None,
) -> Image.Image:
    """Render a metrology-style target directly on a supplier's native canvas.

    This target is deliberately independent of preview-camera geometry. It is
    intended to be uploaded as the provider print file itself so that provider
    fitting, cropping and mockup behaviour can be measured without first
    resampling a canonical Mug Previewer image.
    """
    spec = spec or ProviderCalibrationTargetSpec()
    width, height = profile.canvas_width_px, profile.canvas_height_px
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)

    small = _load_font(max(12, round(height * 0.015)))
    normal = _load_font(max(14, round(height * 0.020)))
    bold = _load_font(max(16, round(height * 0.024)), bold=True)
    title_font = _load_font(max(20, round(height * 0.032)), bold=True)
    line = max(1, round(min(width, height) * 0.0015))
    major_line = max(line + 1, round(min(width, height) * 0.0025))

    seam_px = round(width * profile.edge_safe_fraction)
    if seam_px:
        seam_fill = (255, 238, 238)
        draw.rectangle((0, 0, seam_px - 1, height - 1), fill=seam_fill)
        draw.rectangle((width - seam_px, 0, width - 1, height - 1), fill=seam_fill)
        _draw_rotated_zone_label(image, "SEAM / HANDLE\nZONE", seam_px // 2, height // 2, bold)
        _draw_rotated_zone_label(
            image,
            "SEAM / HANDLE\nZONE",
            width - max(1, seam_px // 2),
            height // 2,
            bold,
        )

    # Horizontal 5% metrology bands.
    y_count = round(1.0 / spec.horizontal_step_fraction)
    for index in range(y_count + 1):
        fraction = index * spec.horizontal_step_fraction
        y = _fraction_pixel(height, fraction)
        major = index % spec.label_every_horizontal_steps == 0
        colour = (115, 115, 115) if major else (205, 205, 205)
        draw.line((0, y, width - 1, y), fill=colour, width=major_line if major else line)
        _draw_horizontal_code(draw, width, height, index, fraction, y, small, major)

    # Longitude 2.5% grid. Quarter-face and midpoint axes are colour-coded.
    x_count = round(1.0 / spec.longitude_step_fraction)
    front_index = round(profile.front_centre_x / spec.longitude_step_fraction)
    midpoint_index = round(0.5 / spec.longitude_step_fraction)
    rear_index = round(profile.rear_centre_x / spec.longitude_step_fraction)
    for index in range(x_count + 1):
        fraction = index * spec.longitude_step_fraction
        x = _fraction_pixel(width, fraction)
        labelled = index % spec.label_every_longitude_steps == 0
        if index == front_index or index == rear_index:
            colour = (205, 25, 25)
            stroke = major_line + 1
        elif index == midpoint_index:
            colour = (0, 0, 0)
            stroke = major_line + 1
        else:
            colour = (115, 115, 115) if labelled else (215, 215, 215)
            stroke = major_line if labelled else line
        draw.line((x, 0, x, height - 1), fill=colour, width=stroke)
        _draw_longitude_code(draw, width, height, index, fraction, x, small, labelled)

    # Outer and inset borders expose clipping and unintended scaling.
    border = max(2, major_line)
    inset = max(border * 4, round(min(width, height) * 0.012))
    draw.rectangle((0, 0, width - 1, height - 1), outline=(0, 0, 0), width=border)
    draw.rectangle(
        (inset, inset, width - 1 - inset, height - 1 - inset),
        outline=(35, 35, 35),
        width=max(1, line),
    )

    # Ruler ticks every 1% around the top and bottom.
    _draw_rulers(draw, width, height, line)

    # Primary metrology targets.
    _draw_provider_target(
        draw,
        width,
        height,
        profile.front_centre_x,
        profile.front_centre_y,
        "FRONT",
        (205, 25, 25),
        normal,
        bold,
    )
    _draw_provider_target(
        draw,
        width,
        height,
        0.5,
        0.5,
        "MIDPOINT / OPPOSITE HANDLE",
        (0, 0, 0),
        normal,
        bold,
    )
    _draw_provider_target(
        draw,
        width,
        height,
        profile.rear_centre_x,
        profile.rear_centre_y,
        "REAR",
        (205, 25, 25),
        normal,
        bold,
    )

    # Asymmetric orientation cues make mirroring and rotation obvious.
    _draw_orientation_cues(draw, width, height, normal, bold)
    _draw_colour_patches(draw, width, height)
    _draw_sample_baselines(draw, width, height, profile, normal, bold)

    header = "NATIVE SUPPLIER MUG CALIBRATION"
    _centred_text(draw, header, width / 2, round(height * 0.018), title_font, fill=(0, 0, 0))
    detail = (
        f"{profile.provider_name} | {profile.variant_name or profile.product_name} | "
        f"{profile.id} | {width} x {height}px @ {profile.dpi} DPI"
    )
    _centred_text(draw, detail, width / 2, round(height * 0.057), normal, fill=(0, 0, 0))

    footer = (
        "UPLOAD THIS EXACT PNG | DO NOT CROP | DO NOT REPOSITION | "
        "IF THE PROVIDER MUST FIT IT, RECORD THAT BEHAVIOUR"
    )
    _centred_text(draw, footer, width / 2, round(height * 0.952), normal, fill=(0, 0, 0))
    image.info["dpi"] = (profile.dpi, profile.dpi)
    return image


def save_provider_calibration_target(
    destination: Path | str,
    profile: ProviderProfile,
    spec: ProviderCalibrationTargetSpec | None = None,
) -> Path:
    """Save a native supplier calibration PNG with explicit DPI metadata."""
    path = Path(destination)
    if path.suffix.casefold() != ".png":
        raise ValueError("Provider calibration targets must use a .png destination.")
    path.parent.mkdir(parents=True, exist_ok=True)
    image = render_provider_calibration_target(profile, spec)
    image.save(path, "PNG", dpi=(profile.dpi, profile.dpi))
    return path


def render_calibration_target(
    profile: MugCalibrationProfile,
    spec: CalibrationTargetSpec | None = None,
) -> Image.Image:
    """Render the legacy V2 provider-preview target on the canonical wrap."""
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


def _draw_provider_target(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    x_fraction: float,
    y_fraction: float,
    label: str,
    accent: tuple[int, int, int],
    normal: ImageFont.ImageFont,
    bold: ImageFont.ImageFont,
) -> None:
    x = _fraction_pixel(width, x_fraction)
    y = _fraction_pixel(height, y_fraction)
    radius = max(24, round(min(width, height) * 0.052))
    ring_width = max(2, round(radius * 0.08))
    for factor in (1.0, 0.66, 0.33):
        r = round(radius * factor)
        draw.ellipse((x - r, y - r, x + r, y + r), outline=(0, 0, 0), width=ring_width)
    centre_r = max(5, round(radius * 0.14))
    draw.ellipse((x - centre_r, y - centre_r, x + centre_r, y + centre_r), fill=accent)
    draw.line((x - radius - 18, y, x + radius + 18, y), fill=(0, 0, 0), width=ring_width)
    draw.line((x, y - radius - 18, x, y + radius + 18), fill=(0, 0, 0), width=ring_width)
    _centred_text(draw, label, x, y + radius + round(height * 0.025), bold, fill=(0, 0, 0))
    _centred_text(
        draw,
        f"({x_fraction * 100:.2f}%, {y_fraction * 100:.2f}%)",
        x,
        y + radius + round(height * 0.055),
        normal,
        fill=(0, 0, 0),
    )


def _draw_longitude_code(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    index: int,
    fraction: float,
    x: int,
    font: ImageFont.ImageFont,
    labelled: bool,
) -> None:
    marker_y = round(height * (0.135 if index % 2 == 0 else 0.865))
    size = max(6, round(min(width, height) * 0.007))
    _draw_index_shape(draw, x, marker_y, size, index)
    code = f"X{index:02d}"
    _centred_text(draw, code, x, marker_y + size + 3, font, fill=(0, 0, 0))
    if labelled:
        pct_y = round(height * (0.090 if index % 2 == 0 else 0.905))
        _centred_text(draw, f"{fraction * 100:g}%", x, pct_y, font, fill=(0, 0, 0))


def _draw_horizontal_code(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    index: int,
    fraction: float,
    y: int,
    font: ImageFont.ImageFont,
    labelled: bool,
) -> None:
    size = max(5, round(min(width, height) * 0.006))
    left_x = round(width * 0.072)
    right_x = round(width * 0.928)
    _draw_index_shape(draw, left_x, y, size, index)
    _draw_index_shape(draw, right_x, y, size, index + 1)
    if labelled:
        text = f"Y{index:02d} {fraction * 100:g}%"
        safe_y = max(2, min(height - size * 2 - 2, y - size))
        draw.text((left_x + size + 4, safe_y), text, fill=(0, 0, 0), font=font)
        box = draw.textbbox((0, 0), text, font=font)
        tw = box[2] - box[0]
        draw.text((right_x - size - 4 - tw, safe_y), text, fill=(0, 0, 0), font=font)


def _draw_index_shape(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, index: int) -> None:
    kind = index % 4
    if kind == 0:
        draw.rectangle((x - size, y - size, x + size, y + size), fill=(0, 0, 0))
    elif kind == 1:
        draw.ellipse((x - size, y - size, x + size, y + size), fill=(0, 0, 0))
    elif kind == 2:
        draw.polygon(((x, y - size), (x - size, y + size), (x + size, y + size)), fill=(0, 0, 0))
    else:
        draw.polygon(((x, y - size), (x - size, y), (x, y + size), (x + size, y)), fill=(0, 0, 0))


def _draw_rulers(draw: ImageDraw.ImageDraw, width: int, height: int, line_width: int) -> None:
    top_base = round(height * 0.105)
    bottom_base = round(height * 0.895)
    for index in range(101):
        x = _fraction_pixel(width, index / 100)
        major = index % 5 == 0
        length = round(height * (0.018 if major else 0.010))
        draw.line((x, top_base, x, top_base + length), fill=(0, 0, 0), width=line_width)
        draw.line((x, bottom_base, x, bottom_base - length), fill=(0, 0, 0), width=line_width)


def _draw_orientation_cues(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    normal: ImageFont.ImageFont,
    bold: ImageFont.ImageFont,
) -> None:
    pad_x = round(width * 0.025)
    pad_y = round(height * 0.025)
    draw.text((pad_x, pad_y), "TL >", fill=(0, 0, 0), font=bold)
    tr = "< TR"
    box = draw.textbbox((0, 0), tr, font=bold)
    draw.text((width - pad_x - (box[2] - box[0]), pad_y), tr, fill=(0, 0, 0), font=bold)
    draw.text((pad_x, height - pad_y - 28), "BL /////", fill=(0, 0, 0), font=normal)
    br = "BR " + "\\" * 5
    box = draw.textbbox((0, 0), br, font=normal)
    draw.text((width - pad_x - (box[2] - box[0]), height - pad_y - 28), br, fill=(0, 0, 0), font=normal)


def _draw_colour_patches(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    colours = (
        (0, 0, 0),
        (128, 128, 128),
        (220, 30, 30),
        (30, 170, 70),
        (40, 75, 200),
        (30, 190, 210),
        (205, 40, 180),
        (245, 210, 20),
    )
    patch = max(9, round(min(width, height) * 0.018))
    gap = max(3, round(patch * 0.22))
    start_y = round(height * 0.31)
    for x in (round(width * 0.10), round(width * 0.90)):
        for index, colour in enumerate(colours):
            y = start_y + index * (patch + gap)
            draw.rectangle((x - patch // 2, y, x + patch // 2, y + patch), fill=colour, outline=(0, 0, 0))


def _draw_sample_baselines(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    profile: ProviderProfile,
    normal: ImageFont.ImageFont,
    bold: ImageFont.ImageFont,
) -> None:
    y = round(height * 0.235)
    for x_fraction, label, direction in (
        (profile.front_centre_x, "FRONT TEXT 0123456789", 1),
        (profile.rear_centre_x, "REAR TEXT 0123456789", -1),
    ):
        x = _fraction_pixel(width, x_fraction)
        _centred_text(draw, label, x, y, bold, fill=(0, 0, 0))
        half = round(width * 0.055)
        draw.line((x - half, y + 30, x + half, y + 30), fill=(0, 0, 0), width=2)
        arrow = ">>>>" if direction > 0 else "<<<<"
        _centred_text(draw, arrow, x, y + 38, normal, fill=(0, 0, 0))


def _draw_rotated_zone_label(
    image: Image.Image,
    text: str,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
) -> None:
    bbox = font.getbbox(text.replace("\n", " "))
    tw = max(1, bbox[2] - bbox[0] + 20)
    th = max(1, (bbox[3] - bbox[1] + 8) * 2)
    layer = Image.new("RGBA", (tw, th), (255, 255, 255, 0))
    layer_draw = ImageDraw.Draw(layer)
    lines = text.splitlines()
    line_height = max(10, round(th / max(1, len(lines))))
    for index, line_text in enumerate(lines):
        box = layer_draw.textbbox((0, 0), line_text, font=font)
        line_width = box[2] - box[0]
        layer_draw.text(((tw - line_width) / 2, index * line_height), line_text, fill=(170, 0, 0, 255), font=font)
    rotated = layer.rotate(90, expand=True)
    px = max(0, min(image.width - rotated.width, round(x - rotated.width / 2)))
    py = max(0, min(image.height - rotated.height, round(y - rotated.height / 2)))
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
    overlay.alpha_composite(rotated, (px, py))
    composited = Image.alpha_composite(image.convert("RGBA"), overlay).convert(image.mode)
    image.paste(composited)


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        ("arialbd.ttf" if bold else "arial.ttf"),
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fraction_pixel(length: int, fraction: float) -> int:
    return round((length - 1) * fraction)


def _centred_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int | float,
    y: int,
    font: ImageFont.ImageFont,
    *,
    fill,
) -> None:
    box = draw.textbbox((0, 0), text, font=font)
    tw = box[2] - box[0]
    th = box[3] - box[1]
    left = round(x - tw / 2)
    draw.rectangle((left - 4, y - 2, left + tw + 4, y + th + 3), fill=(255, 255, 255))
    draw.text((left, y), text, fill=fill, font=font)


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
