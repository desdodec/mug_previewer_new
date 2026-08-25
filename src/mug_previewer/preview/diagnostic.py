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