"""V28-compatible front-face rendering for typed workflow-v6 streets.

The returned PNG is the 495 x 462 front half of V28's 990 x 462 fast-preview
canvas. It is a direct crop, so glyph placement and all native face linework
retain their legacy pixel geometry.
"""

from __future__ import annotations

import base64
import io
import math
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import cairosvg
from PIL import Image

from ..datasets.models import StreetRecord
from .native import face_policy as native

SOURCE_CANVAS_PX = (990, 462)
FRONT_PANEL_PX = (495, 462)
FACE_ASSET_SIZE = (337, 315)
FACE_WIDTH_RATIO = 0.46
FACE_HEIGHT_RATIO = 0.70
FRONT_CENTER_RATIO = 0.25
TITLE_Y_RATIO = 0.141
AREA_Y_RATIO = 0.176
TEXT_CLEARANCE = 20.0
TITLE_WEIGHT = 625
LOCALITY_FONT_SIZE = 18.0
TITLE_FONT_SIZE_TIERS = (34.0, 30.0, 26.0)
TITLE_SAFE_WIDTH_PX = 400.0
STREET_STROKE_MULTIPLIER = 1.18
SUPPORTING_STROKE_WIDTH = 1.68
# Keep the established overall front composition frozen.
FRONT_GROUP_SCALE = 1.18
FRONT_GROUP_Y_OFFSET = 60.0
# Task 02N: shared locality-baseline adjustment within the text block.
FRONT_TITLE_LOCALITY_GAP_DELTA_PX = 4.0
# Task 02Q: move only the title/locality block in source-panel coordinates.
FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX = -12.0


class FaceRenderError(ValueError):
    """Raised when a street cannot be rendered as a V28 front face."""


@dataclass(frozen=True)
class FaceRenderOptions:
    """Display-area text and internal typography positioning for one front panel."""

    area: str = ""
    group_scale: float = FRONT_GROUP_SCALE
    group_y_offset: float = FRONT_GROUP_Y_OFFSET
    title_locality_gap_delta: float = FRONT_TITLE_LOCALITY_GAP_DELTA_PX
    typography_block_y_offset: float = FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX
    street_feature_stroke_multiplier: float = STREET_STROKE_MULTIPLIER


@dataclass(frozen=True)
class TitleFontChoice:
    """One approved title tier and its measured SVG text width."""

    size_px: float
    rendered_width_px: int


def render_face(
    street: StreetRecord,
    options: FaceRenderOptions | None = None,
) -> Image.Image:
    """Return V28 front artwork for ``street`` as an RGBA 495 x 462 image."""
    options = options or FaceRenderOptions()
    glyph = street.glyph_path
    if not glyph.is_file():
        raise FaceRenderError(
            f'Cannot render street {street.id} "{street.display_name}": '
            f"glyph file does not exist: {glyph}"
        )
    if glyph.suffix.casefold() != ".svg":
        raise FaceRenderError(
            f'Cannot render street {street.id} "{street.display_name}": '
            f"glyph is not an SVG file: {glyph}"
        )

    width, height = SOURCE_CANVAS_PX
    panel_center = width * FRONT_CENTER_RATIO
    face_markup = _render_native_face(
        glyph, panel_center, width, height, options.street_feature_stroke_multiplier,
    )
    palette = native.get_face_palette(native.DEFAULT_PALETTE_KEY)
    font_stack = native.get_text_font_stack(native.DEFAULT_TEXT_FONT_KEY)
    street_name = street.display_name.strip() or street.street_name.strip() or street.id
    title = select_title_font(street_name, font_stack)
    area = options.area.strip()
    title_y, area_y = _front_text_y_positions(
        height, options.title_locality_gap_delta, options.typography_block_y_offset,
    )
    group_transform = _front_group_transform(
        panel_center, height, options.group_scale, options.group_y_offset,
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs><style>
    .mug-title {{ font-family:{font_stack}; font-size:{title.size_px:.1f}px; font-weight:{TITLE_WEIGHT}; fill:{palette.feature}; text-anchor:middle; }}
    .mug-area {{ font:500 {LOCALITY_FONT_SIZE:.1f}px {font_stack}; fill:{palette.feature}; text-anchor:middle; letter-spacing:0.6px; }}
  </style></defs>
  <g class="front-composition" transform="{group_transform}">
    <text class="mug-title" x="{panel_center:.1f}" y="{title_y:.1f}">{_escape(street_name)}</text>
    <text class="mug-area" x="{panel_center:.1f}" y="{area_y:.1f}">{_escape(area)}</text>
    {face_markup}
  </g>
</svg>'''
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width, output_height=height)
    with Image.open(io.BytesIO(png)) as rendered:
        return rendered.convert("RGBA").crop((0, 0, FRONT_PANEL_PX[0], FRONT_PANEL_PX[1])).copy()


def _front_group_transform(
    panel_center: float,
    panel_height: float,
    scale: float,
    y_offset: float,
) -> str:
    """Return the frozen shared front-group transform around its panel centre."""
    if scale <= 0:
        raise FaceRenderError("Front composition scale must be positive.")
    if not all(math.isfinite(float(value)) for value in (scale, y_offset)):
        raise FaceRenderError("Front composition transform must be finite.")
    anchor_y = panel_height / 2
    return (
        f"translate(0 {y_offset:.2f}) translate({panel_center:.2f} {anchor_y:.2f}) "
        f"scale({scale:.4f}) translate({-panel_center:.2f} {-anchor_y:.2f})"
    )


def _front_text_y_positions(
    panel_height: float,
    locality_gap_delta: float,
    typography_block_y_offset: float = 0.0,
) -> tuple[float, float]:
    """Return unified title/locality baselines with shared gap and top anchor."""
    if not all(
        math.isfinite(float(value))
        for value in (panel_height, locality_gap_delta, typography_block_y_offset)
    ):
        raise FaceRenderError("Front text layout must be finite.")
    return (
        panel_height * TITLE_Y_RATIO + typography_block_y_offset,
        panel_height * AREA_Y_RATIO + locality_gap_delta + typography_block_y_offset,
    )


def select_title_font(
    text: str,
    font_stack: str,
    *,
    safe_width_px: float = TITLE_SAFE_WIDTH_PX,
) -> TitleFontChoice:
    """Choose the first approved title tier whose measured SVG text fits."""
    if safe_width_px <= 0 or not math.isfinite(safe_width_px):
        raise FaceRenderError("Title safe width must be positive and finite.")
    for size_px in TITLE_FONT_SIZE_TIERS:
        rendered_width_px = _measure_title_width(text, font_stack, size_px)
        if rendered_width_px <= safe_width_px:
            return TitleFontChoice(size_px=size_px, rendered_width_px=rendered_width_px)
    raise FaceRenderError(
        f'Title cannot fit safely at the minimum approved size: "{text}" exceeds {safe_width_px:.0f}px.'
    )


def _measure_title_width(text: str, font_stack: str, size_px: float) -> int:
    """Measure the same SVG/Cairo text used by the production front renderer."""
    markup = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="120">
  <style>.title {{ font-family:{font_stack}; font-size:{size_px:.1f}px; font-weight:{TITLE_WEIGHT}; }}</style>
  <text class="title" x="20" y="80">{_escape(text)}</text>
</svg>'''
    png = cairosvg.svg2png(bytestring=markup.encode("utf-8"), output_width=1200, output_height=120)
    with Image.open(io.BytesIO(png)) as rendered:
        bounds = rendered.getchannel("A").getbbox()
    if bounds is None:
        raise FaceRenderError("Street title produced no visible text.")
    return bounds[2] - bounds[0]


def _render_native_face(
    glyph: Path,
    panel_center: float,
    width: int,
    height: int,
    street_feature_stroke_multiplier: float,
) -> str:
    if not math.isfinite(street_feature_stroke_multiplier) or street_feature_stroke_multiplier <= 0:
        raise FaceRenderError("Front street feature stroke multiplier must be positive and finite.")
    palette = native.get_face_palette(native.DEFAULT_PALETTE_KEY)
    specs = native.build_specs([glyph], palette=palette)
    native.apply_gallery_context_to_single_spec(specs, [glyph], palette, None)
    with tempfile.TemporaryDirectory(prefix="mug_v28_face_") as temporary:
        native_path = Path(temporary) / "face.svg"
        native.render_grid(
            specs, native_path, cols=1, show_blush=False, ear_mode="varied",
            presentation_mode="varied", street_stroke_multiplier=street_feature_stroke_multiplier,
            palette=palette, paper_key="a6", show_note=False, show_title=False,
            single_svg_mode=True,
        )
        native_svg = native_path.read_text(encoding="utf-8")
    try:
        root = ET.fromstring(native_svg)
    except ET.ParseError as error:
        raise FaceRenderError(f"Could not parse rendered face SVG for {glyph}") from error
    namespace = "{http://www.w3.org/2000/svg}"
    defs = root.find(f"{namespace}defs")
    face_group = next(
        (node for node in root.iter(f"{namespace}g") if "face-content" in node.get("class", "").split()),
        None,
    )
    if defs is None or face_group is None:
        raise FaceRenderError(f"Native V28 renderer produced no face artwork for {glyph}")
    parent_by_child = {child: parent for parent in face_group.iter() for child in parent}
    for text_node in list(face_group.iter(f"{namespace}text")):
        if "face-art-text" not in text_node.get("class", "").split():
            parent_by_child[text_node].remove(text_node)
    face_asset = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{FACE_ASSET_SIZE[0]}" height="{FACE_ASSET_SIZE[1]}" '
        f'viewBox="0 0 {FACE_ASSET_SIZE[0]} {FACE_ASSET_SIZE[1]}">'
        f"{ET.tostring(defs, encoding='unicode')}"
        '<g transform="translate(-30 -124)">'
        f"{ET.tostring(face_group, encoding='unicode')}</g></svg>"
    )
    face_asset = face_asset.replace("vector-effect: non-scaling-stroke;", "")
    face_asset = face_asset.replace(
        "</svg>",
        f'<style>.v28-face-linework .v28-support {{ stroke-width:{SUPPORTING_STROKE_WIDTH:.2f}px !important; }}</style></svg>',
    )
    href = "data:image/svg+xml;base64," + base64.b64encode(face_asset.encode("utf-8")).decode("ascii")
    face_width = width * FACE_WIDTH_RATIO
    face_height = height * FACE_HEIGHT_RATIO
    face_x = panel_center - face_width / 2
    top_text_bottom = height * AREA_Y_RATIO + TEXT_CLEARANCE
    face_y = _face_y_between_text(
        _face_content_bbox(face_asset), face_width, face_height,
        top_text_bottom, height - top_text_bottom,
    )
    face_y = min(max(face_y, 0.0), height - face_height)
    return (
        f'<image class="v28-face" href="{href}" x="{face_x:.1f}" y="{face_y:.1f}" '
        f'width="{face_width:.1f}" height="{face_height:.1f}" preserveAspectRatio="xMidYMid meet"/>'
    )


def _face_content_bbox(face_asset: str) -> tuple[int, int, int, int]:
    png = cairosvg.svg2png(
        bytestring=face_asset.encode("utf-8"), output_width=FACE_ASSET_SIZE[0], output_height=FACE_ASSET_SIZE[1],
    )
    with Image.open(io.BytesIO(png)) as rendered:
        bbox = rendered.getchannel("A").getbbox()
    if bbox is None:
        raise FaceRenderError("Native V28 renderer produced empty face artwork.")
    return bbox


def _face_y_between_text(
    face_bbox: tuple[int, int, int, int], face_width: float, face_height: float,
    top_text_bottom: float, bottom_text_top: float,
) -> float:
    scale = min(face_width / FACE_ASSET_SIZE[0], face_height / FACE_ASSET_SIZE[1])
    painted_height = (face_bbox[3] - face_bbox[1]) * scale
    painted_top = (top_text_bottom + bottom_text_top - painted_height) / 2
    return painted_top - face_bbox[1] * scale


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
