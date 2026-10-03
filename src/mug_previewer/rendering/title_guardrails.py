"""Automatic production-safe layout for long front street titles.

The established front renderer predates the print-style calibration lab and
chooses progressively smaller one-line title tiers.  That works for most names,
but on supplier mockups a long title can end up too close to the printable
edge after the whole front composition is scaled.  This module installs one
shared guard rail around the existing renderer:

* short titles keep the existing one-line pixels;
* a title may shrink only to the 30 px production tier before wrapping;
* multi-word titles that still do not fit are split over two balanced lines;
* both lines are measured with the same CairoSVG text path as production;
* the locality is moved down by half a title-line spacing so its gap from the
  second title line remains the same as the normal one-line gap.

The installer is called once by :mod:`mug_previewer.rendering`.  Keeping the
layout here makes the policy independently testable while the existing face
module remains the single source for native face geometry and styling.
"""
from __future__ import annotations

import io
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import wraps
from typing import Any

from PIL import Image


TITLE_FINAL_SAFE_WIDTH_PX = 440.0
TITLE_MIN_SINGLE_LINE_BASE_SIZE_PX = 30.0
TITLE_LINE_SPACING_RATIO = 0.92


@dataclass(frozen=True)
class TitleLayout:
    """Resolved front-title layout after production fit guard rails."""

    lines: tuple[str, ...]
    size_px: float
    rendered_widths_px: tuple[int, ...]
    safe_width_px: float
    status: str

    @property
    def wrapped(self) -> bool:
        return len(self.lines) > 1

    @property
    def rendered_width_px(self) -> int:
        return max(self.rendered_widths_px)

    @property
    def line_spacing_px(self) -> float:
        return self.size_px * TITLE_LINE_SPACING_RATIO if self.wrapped else 0.0


def title_safe_width_for_group(face: Any, group_scale: float) -> float:
    """Return source-coordinate width that remains safe after group scaling."""
    try:
        scale = float(group_scale)
    except (TypeError, ValueError) as error:
        raise face.FaceRenderError("Front composition scale must be positive and finite.") from error
    if not math.isfinite(scale) or scale <= 0:
        raise face.FaceRenderError("Front composition scale must be positive and finite.")
    # TITLE_SAFE_WIDTH_PX remains the original source-space cap.  The second
    # cap protects a final ~440 px painted width on the 495 px front panel,
    # leaving useful supplier/mockup edge tolerance after composition scaling.
    return min(float(face.TITLE_SAFE_WIDTH_PX), TITLE_FINAL_SAFE_WIDTH_PX / scale)


def layout_title_text(
    face: Any,
    text: str,
    font_stack: str,
    *,
    font_scale: float = 1.0,
    group_scale: float | None = None,
    safe_width_px: float | None = None,
) -> TitleLayout:
    """Resolve a one- or two-line title using measured production text widths."""
    title = str(text).strip()
    if not title:
        raise face.FaceRenderError("Street title produced no visible text.")
    scale = face._positive_style_multiplier(font_scale, "Title font scale")
    if safe_width_px is None:
        safe_width = title_safe_width_for_group(
            face,
            face.FRONT_GROUP_SCALE if group_scale is None else group_scale,
        )
    else:
        safe_width = float(safe_width_px)
        if not math.isfinite(safe_width) or safe_width <= 0:
            raise face.FaceRenderError("Title safe width must be positive and finite.")

    tiers = tuple(float(base) * scale for base in face.TITLE_FONT_SIZE_TIERS)

    # Preserve the established appearance for normal names, but do not make a
    # multi-word title weaker than the 30 px base tier merely to keep one line.
    minimum_single = TITLE_MIN_SINGLE_LINE_BASE_SIZE_PX * scale
    for size_px in tiers:
        if size_px + 1e-9 < minimum_single:
            continue
        width = face._measure_title_width(title, font_stack, size_px)
        if width <= safe_width:
            return TitleLayout((title,), size_px, (width,), safe_width, "safe")

    words = title.split()
    if len(words) >= 2:
        # Prefer the largest approved tier that permits a safe two-line split.
        # Within that tier choose the most visually balanced measured widths.
        for size_px in tiers:
            candidates: list[tuple[tuple[float, ...], tuple[str, str], tuple[int, int]]] = []
            for index in range(1, len(words)):
                lines = (" ".join(words[:index]), " ".join(words[index:]))
                widths = tuple(face._measure_title_width(line, font_stack, size_px) for line in lines)
                if max(widths) > safe_width:
                    continue
                score = (
                    float(abs(widths[0] - widths[1])),
                    float(max(widths)),
                    abs(index - len(words) / 2.0),
                )
                candidates.append((score, lines, widths))
            if candidates:
                _score, lines, widths = min(candidates, key=lambda item: item[0])
                return TitleLayout(lines, size_px, widths, safe_width, "wrapped")

    # A single unusually long word cannot be wrapped naturally.  Retain the
    # legacy final 26 px tier as a last safe fallback before requiring review.
    for size_px in tiers:
        width = face._measure_title_width(title, font_stack, size_px)
        if width <= safe_width:
            return TitleLayout((title,), size_px, (width,), safe_width, "reduced")

    raise face.FaceRenderError(
        f'Title cannot fit safely in one or two lines: "{title}" exceeds {safe_width:.0f}px.'
    )


def _title_positions(face: Any, layout: TitleLayout, title_y: float, area_y: float, spread: float) -> tuple[tuple[float, ...], float]:
    if layout.wrapped:
        half = layout.line_spacing_px / 2.0
        title_positions = (title_y - half, title_y + half)
        # Shift locality by the same half-spacing as the second title baseline;
        # therefore the established second-title -> locality baseline gap is
        # exactly the same as the old title -> locality gap.
        area_y += half
    else:
        title_positions = (title_y,)
    return (
        tuple(face._spread_vertical_position(value, face.SOURCE_CANVAS_PX[1], spread) for value in title_positions),
        face._spread_vertical_position(area_y, face.SOURCE_CANVAS_PX[1], spread),
    )


def _title_markup(face: Any, layout: TitleLayout, panel_center: float, positions: tuple[float, ...], *, editable: bool = False) -> str:
    nodes = "\n    ".join(
        f'<text class="mug-title" x="{panel_center:.1f}" y="{y:.1f}">{face._escape(line)}</text>'
        for line, y in zip(layout.lines, positions)
    )
    return f'<g id="title">{nodes}</g>' if editable else nodes


def _render_face_standard(face: Any, street: Any, options: Any = None, *, face_markup: str | None = None) -> Image.Image:
    """Production front renderer with measured two-line title fallback."""
    options = options or face.FaceRenderOptions()
    glyph = street.glyph_path
    if not glyph.is_file():
        raise face.FaceRenderError(
            f'Cannot render street {street.id} "{street.display_name}": glyph file does not exist: {glyph}'
        )
    if glyph.suffix.casefold() != ".svg":
        raise face.FaceRenderError(
            f'Cannot render street {street.id} "{street.display_name}": glyph is not an SVG file: {glyph}'
        )

    width, height = face.SOURCE_CANVAS_PX
    panel_center = width * face.FRONT_CENTER_RATIO
    face_markup = face_markup or face._render_native_face(
        glyph,
        panel_center,
        width,
        height,
        options.street_feature_stroke_multiplier,
        group_scale=options.group_scale,
        group_y_offset=options.group_y_offset,
        supporting_stroke_multiplier=options.supporting_stroke_multiplier,
        facial_linework_multiplier=options.facial_linework_multiplier,
        vertical_spread=options.vertical_spread,
    )
    palette = face.native.get_face_palette(face.native.DEFAULT_PALETTE_KEY)
    font_stack = face.native.get_text_font_stack(face.native.DEFAULT_TEXT_FONT_KEY)
    street_name = street.display_name.strip() or street.street_name.strip() or street.id
    layout = layout_title_text(
        face,
        street_name,
        font_stack,
        font_scale=options.title_font_scale,
        group_scale=options.group_scale,
    )
    area = street.locality_label or options.area.strip()
    title_y, area_y = face._front_text_y_positions(
        height,
        options.title_locality_gap_delta,
        options.typography_block_y_offset,
    )
    title_positions, area_y = _title_positions(
        face,
        layout,
        title_y,
        area_y,
        options.vertical_spread,
    )
    locality_font_size = face.LOCALITY_FONT_SIZE * face._positive_style_multiplier(
        options.locality_font_scale,
        "Locality font scale",
    )
    group_transform = face._front_group_transform(
        panel_center,
        height,
        options.group_scale,
        options.group_y_offset,
    )
    title_markup = _title_markup(face, layout, panel_center, title_positions)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs><style>
    .mug-title {{ font-family:{font_stack}; font-size:{layout.size_px:.1f}px; font-weight:{face.TITLE_WEIGHT}; fill:{palette.feature}; text-anchor:middle; }}
    .mug-area {{ font:500 {locality_font_size:.1f}px {font_stack}; fill:{palette.feature}; text-anchor:middle; letter-spacing:0.6px; }}
  </style></defs>
  <g class="front-composition" transform="{group_transform}">
    {title_markup}
    <text class="mug-area" x="{panel_center:.1f}" y="{area_y:.1f}">{face._escape(area)}</text>
    {face_markup}
  </g>
</svg>'''
    png = face.cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=width,
        output_height=height,
    )
    with Image.open(io.BytesIO(png)) as rendered:
        panel = rendered.convert("RGBA").crop((0, 0, face.FRONT_PANEL_PX[0], face.FRONT_PANEL_PX[1])).copy()
    feature_colour = face.extract_street_feature_colour(face._decode_native_face_asset(face_markup))
    if feature_colour is not None:
        panel.info["street_feature_colour"] = feature_colour
    panel.info["title_layout_lines"] = layout.lines
    panel.info["title_layout_status"] = layout.status
    panel.info["title_layout_size_px"] = layout.size_px
    panel.info["title_layout_safe_width_px"] = layout.safe_width_px
    return panel


def _render_face_svg(face: Any, dataset: Any, street: Any, options: Any = None) -> str:
    """Editable canonical SVG using the same guarded title layout as PNG output."""
    options = options or face.FaceRenderOptions(area=dataset.display_name)
    placement_source = face._validated_svg_placement_source(dataset, street, options)
    glyph = street.glyph_path
    if not glyph.is_file() or glyph.suffix.casefold() != ".svg":
        face.render_face(street, options)
        raise AssertionError("render_face unexpectedly returned for an invalid glyph")

    width, height = face.SOURCE_CANVAS_PX
    panel_center = width * face.FRONT_CENTER_RATIO
    face_markup = face._render_native_face(
        glyph,
        panel_center,
        width,
        height,
        options.street_feature_stroke_multiplier,
        group_scale=options.group_scale,
        group_y_offset=options.group_y_offset,
        supporting_stroke_multiplier=options.supporting_stroke_multiplier,
        facial_linework_multiplier=options.facial_linework_multiplier,
        vertical_spread=options.vertical_spread,
    )
    asset = face._decode_native_face_asset(face_markup)
    asset_root = ET.fromstring(asset)
    face_content = next(
        (
            node
            for node in asset_root.iter()
            if face._svg_local_name(node.tag) == "g"
            and "face-content" in node.get("class", "").split()
        ),
        None,
    )
    if face_content is None:
        raise face.FaceRenderError(f"Native V28 renderer produced no editable face artwork for {glyph}")
    face._add_editable_face_groups(face_content)

    palette = face.native.get_face_palette(face.native.DEFAULT_PALETTE_KEY)
    font_stack = face.native.get_text_font_stack(face.native.DEFAULT_TEXT_FONT_KEY)
    street_name = street.display_name.strip() or street.street_name.strip() or street.id
    layout = layout_title_text(
        face,
        street_name,
        font_stack,
        font_scale=options.title_font_scale,
        group_scale=options.group_scale,
    )
    area = street.locality_label or options.area.strip()
    title_y, area_y = face._front_text_y_positions(
        height,
        options.title_locality_gap_delta,
        options.typography_block_y_offset,
    )
    title_positions, area_y = _title_positions(
        face,
        layout,
        title_y,
        area_y,
        options.vertical_spread,
    )
    locality_font_size = face.LOCALITY_FONT_SIZE * face._positive_style_multiplier(
        options.locality_font_scale,
        "Locality font scale",
    )
    group_transform = face._front_group_transform(
        panel_center,
        height,
        options.group_scale,
        options.group_y_offset,
    )
    face_x, face_y, face_width, face_height = face._native_face_markup_placement(face_markup)
    asset_scale = min(face_width / face.FACE_ASSET_SIZE[0], face_height / face.FACE_ASSET_SIZE[1])
    asset_x = face_x + (face_width - face.FACE_ASSET_SIZE[0] * asset_scale) / 2
    asset_y = face_y + (face_height - face.FACE_ASSET_SIZE[1] * asset_scale) / 2
    asset_body = next((node for node in asset_root if face._svg_local_name(node.tag) == "g"), None)
    defs = next((node for node in asset_root if face._svg_local_name(node.tag) == "defs"), None)
    if asset_body is None or defs is None:
        raise face.FaceRenderError("Native V28 renderer produced incomplete editable artwork.")

    metadata = {
        "dataset_id": dataset.id,
        "dataset_name": dataset.display_name,
        "generator": face.FACE_SVG_GENERATOR,
        "generator_version": "V28.1",
        "placement_source": placement_source,
        "street_id": street.id,
        "street_name": street_name,
        "title_layout": {
            "lines": list(layout.lines),
            "size_px": round(layout.size_px, 3),
            "status": layout.status,
        },
    }
    title_markup = _title_markup(face, layout, panel_center, title_positions, editable=True)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <metadata id="mug-previewer-metadata">{face._escape(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")))}</metadata>
  {ET.tostring(defs, encoding="unicode")}
  <style>
    .mug-title {{ font-family:{font_stack}; font-size:{layout.size_px:.1f}px; font-weight:{face.TITLE_WEIGHT}; fill:{palette.feature}; text-anchor:middle; }}
    .mug-area {{ font:500 {locality_font_size:.1f}px {font_stack}; fill:{palette.feature}; text-anchor:middle; letter-spacing:0.6px; }}
    .v28-face-linework .v28-support {{ stroke-width:{face.SUPPORTING_STROKE_WIDTH * options.supporting_stroke_multiplier * options.facial_linework_multiplier:.2f}px !important; }}
  </style>
  <g class="front-composition" transform="{group_transform}">
    {title_markup}
    <g id="locality"><text class="mug-area" x="{panel_center:.1f}" y="{area_y:.1f}">{face._escape(area)}</text></g>
    <g class="v28-face-vector" transform="translate({asset_x:.4f} {asset_y:.4f}) scale({asset_scale:.8f})">{ET.tostring(asset_body, encoding="unicode")}</g>
  </g>
</svg>'''


def install(face: Any) -> None:
    """Install the shared guard rails on the existing face renderer module."""
    if getattr(face, "_title_guardrails_installed", False):
        return

    @wraps(face._render_face_standard)
    def render_standard(street: Any, options: Any = None, *, face_markup: str | None = None) -> Image.Image:
        return _render_face_standard(face, street, options, face_markup=face_markup)

    @wraps(face.render_face_svg)
    def render_svg(dataset: Any, street: Any, options: Any = None) -> str:
        return _render_face_svg(face, dataset, street, options)

    def public_layout(
        text: str,
        font_stack: str,
        *,
        font_scale: float = 1.0,
        group_scale: float | None = None,
        safe_width_px: float | None = None,
    ) -> TitleLayout:
        return layout_title_text(
            face,
            text,
            font_stack,
            font_scale=font_scale,
            group_scale=group_scale,
            safe_width_px=safe_width_px,
        )

    def public_safe_width(group_scale: float) -> float:
        return title_safe_width_for_group(face, group_scale)

    face.TitleLayout = TitleLayout
    face.TITLE_FINAL_SAFE_WIDTH_PX = TITLE_FINAL_SAFE_WIDTH_PX
    face.TITLE_MIN_SINGLE_LINE_BASE_SIZE_PX = TITLE_MIN_SINGLE_LINE_BASE_SIZE_PX
    face.TITLE_LINE_SPACING_RATIO = TITLE_LINE_SPACING_RATIO
    face.layout_title_text = public_layout
    face.title_safe_width_for_group = public_safe_width
    face._render_face_standard = render_standard
    face.render_face_svg = render_svg
    face._title_guardrails_installed = True
