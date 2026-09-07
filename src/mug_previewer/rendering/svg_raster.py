"""Faithful rasterisation of canonical SVG front artwork."""
import io
from pathlib import Path

import cairosvg
from PIL import Image

from .face import FRONT_PANEL_PX, SOURCE_CANVAS_PX


def rasterize_face_svg(source: Path | str | bytes) -> Image.Image:
    """Render SVG bytes/text (or a Path) at canonical size and crop the front."""
    if isinstance(source, Path):
        payload = source.read_bytes()
    else:
        payload = source.encode("utf-8") if isinstance(source, str) else source
    png = cairosvg.svg2png(
        bytestring=payload,
        output_width=SOURCE_CANVAS_PX[0], output_height=SOURCE_CANVAS_PX[1],
    )
    with Image.open(io.BytesIO(png)) as rendered:
        return rendered.convert("RGBA").crop((0, 0, *FRONT_PANEL_PX))
