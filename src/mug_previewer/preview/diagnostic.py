"""Development-only synthetic wraps for checking mockup source mapping."""

from __future__ import annotations

from PIL import Image, ImageDraw

from .mockup import CANONICAL_WRAP_PREVIEW_GEOMETRY


def create_mockup_diagnostic_wrap() -> Image.Image:
    """Return a colour-separated canonical wrap with directional zone markers."""
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    image = Image.new("RGBA", (geometry.width_px, geometry.height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    zones = (
        (geometry.front_left_px, geometry.front_width_px, "#d94b4b", ("F-L", "F-C", "F-R")),
        (geometry.seam_left_px, geometry.seam_width_px, "#4b9a64", ("S-L", "S-R")),
        (geometry.rear_left_px, geometry.rear_width_px, "#4d74c7", ("R-L", "R-C", "R-R")),
    )
    for left, width, colour, labels in zones:
        draw.rectangle((left, 0, left + width - 1, geometry.height_px - 1), fill=colour)
        for index, label in enumerate(labels):
            marker_x = left + round((index + 0.5) * width / len(labels))
            draw.line((marker_x, 0, marker_x, geometry.height_px - 1), fill="white", width=2)
            draw.text((marker_x - len(label) * 3, geometry.height_px // 2), label, fill="white")
    return image


def create_cylindrical_stripe_wrap(*, marker_count: int = 13) -> Image.Image:
    """Return evenly spaced transparent-background markers for projection QA."""
    if marker_count < 9 or marker_count % 2 == 0:
        raise ValueError("Cylindrical diagnostics need an odd marker count of at least nine.")
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    image = Image.new("RGBA", (geometry.width_px, geometry.height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    centre = round(geometry.front_centre_x)
    for index in range(-(marker_count // 2), marker_count // 2 + 1):
        x = centre + index * 75
        draw.rectangle((x - 5, 0, x + 5, geometry.height_px - 1), fill=(24, 91, 191, 255))
    return image


def create_projection_grid_wrap() -> Image.Image:
    """Return a labelled transparent grid for developer-only topology review."""
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    image = Image.new("RGBA", (geometry.width_px, geometry.height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for x in range(0, geometry.width_px, 118):
        draw.line((x, 0, x, geometry.height_px - 1), fill=(255, 116, 24, 255), width=3)
    for y in range(0, geometry.height_px, 106):
        draw.line((0, y, geometry.width_px - 1, y), fill=(24, 148, 255, 255), width=3)
    for x, label in (
        (round(geometry.front_centre_x), "FRONT"),
        (geometry.seam_left_px + geometry.seam_width_px // 2, "SEAM"),
        (round(geometry.rear_centre_x), "REAR"),
    ):
        draw.line((x, 0, x, geometry.height_px - 1), fill=(255, 255, 255, 255), width=5)
        draw.text((x + 8, 20), label, fill=(255, 255, 255, 255))
    return image
