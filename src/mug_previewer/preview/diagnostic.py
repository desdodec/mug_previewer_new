"""Development-only synthetic wraps for checking mockup source mapping."""

from __future__ import annotations

from PIL import Image, ImageDraw

from .mockup import CANONICAL_WRAP_PREVIEW_GEOMETRY


def create_mockup_diagnostic_wrap() -> Image.Image:
    """Return a colour-separated canonical-size FRONT / SEAM / REAR test wrap."""
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    image = Image.new("RGBA", (geometry.width_px, geometry.height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    zones = (
        (geometry.front_left_px, geometry.front_width_px, "#d94b4b", "FRONT"),
        (geometry.seam_left_px, geometry.seam_width_px, "#4b9a64", "SEAM"),
        (geometry.rear_left_px, geometry.rear_width_px, "#4d74c7", "REAR"),
    )
    for left, width, colour, label in zones:
        draw.rectangle((left, 0, left + width - 1, geometry.height_px - 1), fill=colour)
        draw.text((left + width // 2 - len(label) * 3, geometry.height_px // 2), label, fill="white")
    return image