"""V28-compatible front-face rendering for typed workflow-v6 streets.

The returned PNG is the 495 x 462 front half of V28's 990 x 462 fast-preview
canvas. It is a direct crop, so glyph placement and all native face linework
retain their legacy pixel geometry.
"""

from __future__ import annotations

import base64
import io
import math
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import cairosvg
from PIL import Image, ImageColor

from ..datasets.models import StreetRecord
from ..manual import ManualPlacementOverride, ManualResolutionStatus
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
FRONT_TITLE_LOCALITY_GAP_DELTA_PX = 6.0
# Task 02Q: move only the title/locality block in source-panel coordinates.
FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX = -14.0
# Final-layout visual clearance for a normal lower street feature only.
NOSE_STREET_TARGET_CLEARANCE_PX = 8
NOSE_STREET_MAX_AUTOMATIC_SHIFT_PX = 20
NOSE_STREET_MIN_NORMAL_WIDTH_PX = 120
NOSE_STREET_MAX_NORMAL_HEIGHT_PX = 32
MASK_ALPHA_THRESHOLD = 128


class FaceRenderError(ValueError):
    """Raised when a street cannot be rendered as a V28 front face."""


@dataclass(frozen=True)
class FaceRenderOptions:
    """Front artwork style controls.

    Defaults preserve the established production appearance.  The additional
    multipliers are primarily exposed by the separate print-calibration lab so
    physical print tests can be performed without changing production defaults.
    """

    area: str = ""
    group_scale: float = FRONT_GROUP_SCALE
    group_y_offset: float = FRONT_GROUP_Y_OFFSET
    title_locality_gap_delta: float = FRONT_TITLE_LOCALITY_GAP_DELTA_PX
    typography_block_y_offset: float = FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX
    title_font_scale: float = 1.0
    locality_font_scale: float = 1.0
    facial_linework_multiplier: float = 1.0
    supporting_stroke_multiplier: float = 1.0
    street_feature_stroke_multiplier: float = STREET_STROKE_MULTIPLIER
    vertical_spread: float = 1.0
    manual_override: ManualPlacementOverride | None = None


@dataclass(frozen=True)
class TitleFontChoice:
    """One approved title tier and its measured SVG text width."""

    size_px: float
    rendered_width_px: int


class NoseStreetClearanceCorrection(NamedTuple):
    required_shift_px: int
    applied_shift_px: int
    outcome: str


def _normalise_feature_colour(value: str) -> str:
    """Return one opaque SVG colour as canonical #RRGGBB."""
    try:
        rgb = ImageColor.getrgb(str(value).strip())
    except (TypeError, ValueError) as error:
        raise FaceRenderError(f"Invalid facial street feature colour: {value!r}.") from error
    if len(rgb) < 3:
        raise FaceRenderError(f"Invalid facial street feature colour: {value!r}.")
    return "#{:02X}{:02X}{:02X}".format(*rgb[:3])


def _face_specs_for_glyph(glyph: Path):
    """Build the native face spec used by both front artwork and colour linking."""
    palette = native.get_face_palette(native.DEFAULT_PALETTE_KEY)
    specs = native.build_specs([glyph], palette=palette)
    native.apply_gallery_context_to_single_spec(specs, [glyph], palette, None)
    return palette, specs


def street_feature_colour(street: StreetRecord) -> str:
    """Return the exact colour used for this street's facial SVG feature."""
    glyph = street.glyph_path
    if not glyph.is_file():
        raise FaceRenderError(
            f'Cannot derive feature colour for street {street.id}: glyph file does not exist: {glyph}'
        )
    try:
        _palette, specs = _face_specs_for_glyph(glyph)
        return _normalise_feature_colour(specs[0].group_color)
    except FaceRenderError:
        raise
    except Exception as error:
        raise FaceRenderError(
            f'Could not derive facial street feature colour for {street.id} "{street.display_name}".'
        ) from error


def extract_street_feature_colour(markup: str | bytes) -> str | None:
    """Extract the editable front SVG's coloured street feature, if present."""
    try:
        root = ET.fromstring(markup)
    except (ET.ParseError, TypeError, ValueError):
        return None
    for element in root.iter():
        if _svg_local_name(element.tag) not in {"polyline", "path"}:
            continue
        if "street" not in element.get("class", "").split():
            continue
        value = element.get("stroke")
        if value is None:
            style = element.get("style", "")
            match = re.search(r"(?:^|;)\s*stroke\s*:\s*([^;]+)", style)
            value = match.group(1).strip() if match is not None else None
        if not value or value.casefold() == "none":
            continue
        try:
            return _normalise_feature_colour(value)
        except FaceRenderError:
            continue

    # Some SVG editors move presentation attributes into a stylesheet. Keep
    # prepared artwork authoritative by accepting a .street CSS stroke too.
    for element in root.iter():
        if _svg_local_name(element.tag) != "style" or not (element.text or "").strip():
            continue
        for rule in re.finditer(r"([^{}]*\.street[^{}]*)\{([^{}]*)\}", element.text or "", re.DOTALL):
            match = re.search(r"(?:^|;)\s*stroke\s*:\s*([^;]+)", rule.group(2))
            if match is None:
                continue
            try:
                return _normalise_feature_colour(match.group(1).strip())
            except FaceRenderError:
                continue
    return None


def render_face(
    street: StreetRecord,
    options: FaceRenderOptions | None = None,
) -> Image.Image:
    # Public API: the placement decision remains internal.
    image, _decision = _render_face_with_decision(street, options)
    return image

def _render_face_with_decision(
    street: StreetRecord,
    options: FaceRenderOptions | None = None,
) -> tuple[Image.Image, object]:
    # Production triage only permits a transform for a high-confidence rescue.
    options = options or FaceRenderOptions()
    glyph = street.glyph_path
    if not glyph.is_file():
        raise FaceRenderError(f'Cannot render street {street.id} "{street.display_name}": glyph file does not exist: {glyph}')
    if glyph.suffix.casefold() != '.svg':
        raise FaceRenderError(f'Cannot render street {street.id}: glyph is not an SVG file: {glyph}')
    width, height = SOURCE_CANVAS_PX
    panel_center = width * FRONT_CENTER_RATIO
    face_markup = _render_native_face(
        glyph, panel_center, width, height, options.street_feature_stroke_multiplier,
        group_scale=options.group_scale, group_y_offset=options.group_y_offset,
        supporting_stroke_multiplier=options.supporting_stroke_multiplier,
        facial_linework_multiplier=options.facial_linework_multiplier,
        vertical_spread=options.vertical_spread,
    )
    standard = _render_face_standard(street, options, face_markup=face_markup)
    override = options.manual_override
    if override is not None:
        if not override.approved:
            raise FaceRenderError("Only an explicitly approved manual override may be rendered.")
        if override.status is ManualResolutionStatus.APPROVED_STANDARD:
            return standard, override
        from ..diagnostics.front_candidates import Candidate, render_production_masks
        masks = render_production_masks(street, area=options.area, face_markup=face_markup, base=standard)
        candidate = Candidate(override.orientation_deg, override.scale, 0, override.y_offset)
        return _render_transformed_street(standard, masks, candidate), override

    from ..diagnostics.front_candidates import render_production_masks, select_production_placement_from_masks
    masks = render_production_masks(street, area=options.area, face_markup=face_markup, base=standard)
    decision, _ranked = select_production_placement_from_masks(masks)
    if not decision.adapted:
        return standard, decision
    # A production decision stores its approved rescue as ``transform``.
    # ``rendered`` belongs only to the diagnostic decision type.
    assert decision.transform is not None
    return _render_transformed_street(standard, masks, decision.transform.candidate), decision


def _render_transformed_street(standard: Image.Image, masks: object, candidate: object) -> Image.Image:
    """Repaint a constrained placement using the same production mask pipeline."""
    from ..diagnostics.front_candidates import transform_street_mask

    palette = native.get_face_palette(native.DEFAULT_PALETTE_KEY)
    feature = ImageColor.getrgb(palette.feature) + (255,)
    adapted = standard.copy()
    adapted.info.update(standard.info)
    adapted.paste((255, 255, 255, 255), mask=masks.street_mouth)
    for protected in (masks.left_eye, masks.right_eye, masks.static_nose, masks.typography):
        adapted.paste(feature, mask=protected)
    street_mask, _clipped = transform_street_mask(masks.street_mouth, candidate)
    adapted.paste(feature, mask=street_mask)
    adapted.info["street_feature_colour"] = _normalise_feature_colour(palette.feature)
    return adapted

def _render_face_standard(
    street: StreetRecord,
    options: FaceRenderOptions | None = None,
    *,
    face_markup: str | None = None,
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
    face_markup = face_markup or _render_native_face(
        glyph, panel_center, width, height, options.street_feature_stroke_multiplier,
        group_scale=options.group_scale, group_y_offset=options.group_y_offset,
        supporting_stroke_multiplier=options.supporting_stroke_multiplier,
        facial_linework_multiplier=options.facial_linework_multiplier,
        vertical_spread=options.vertical_spread,
    )
    palette = native.get_face_palette(native.DEFAULT_PALETTE_KEY)
    font_stack = native.get_text_font_stack(native.DEFAULT_TEXT_FONT_KEY)
    street_name = street.display_name.strip() or street.street_name.strip() or street.id
    title = select_title_font(street_name, font_stack, font_scale=options.title_font_scale)
    area = street.locality_label or options.area.strip()
    title_y, area_y = _front_text_y_positions(
        height, options.title_locality_gap_delta, options.typography_block_y_offset,
    )
    title_y = _spread_vertical_position(title_y, height, options.vertical_spread)
    area_y = _spread_vertical_position(area_y, height, options.vertical_spread)
    locality_font_size = LOCALITY_FONT_SIZE * _positive_style_multiplier(
        options.locality_font_scale, "Locality font scale",
    )
    group_transform = _front_group_transform(
        panel_center, height, options.group_scale, options.group_y_offset,
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs><style>
    .mug-title {{ font-family:{font_stack}; font-size:{title.size_px:.1f}px; font-weight:{TITLE_WEIGHT}; fill:{palette.feature}; text-anchor:middle; }}
    .mug-area {{ font:500 {locality_font_size:.1f}px {font_stack}; fill:{palette.feature}; text-anchor:middle; letter-spacing:0.6px; }}
  </style></defs>
  <g class="front-composition" transform="{group_transform}">
    <text class="mug-title" x="{panel_center:.1f}" y="{title_y:.1f}">{_escape(street_name)}</text>
    <text class="mug-area" x="{panel_center:.1f}" y="{area_y:.1f}">{_escape(area)}</text>
    {face_markup}
  </g>
</svg>'''
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width, output_height=height)
    with Image.open(io.BytesIO(png)) as rendered:
        panel = rendered.convert("RGBA").crop((0, 0, FRONT_PANEL_PX[0], FRONT_PANEL_PX[1])).copy()
    feature_colour = extract_street_feature_colour(_decode_native_face_asset(face_markup))
    if feature_colour is not None:
        panel.info["street_feature_colour"] = feature_colour
    return panel


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
    font_scale: float = 1.0,
) -> TitleFontChoice:
    """Choose the first approved title tier, optionally scaled for print calibration."""
    if safe_width_px <= 0 or not math.isfinite(safe_width_px):
        raise FaceRenderError("Title safe width must be positive and finite.")
    scale = _positive_style_multiplier(font_scale, "Title font scale")
    for base_size_px in TITLE_FONT_SIZE_TIERS:
        size_px = base_size_px * scale
        rendered_width_px = _measure_title_width(text, font_stack, size_px)
        if rendered_width_px <= safe_width_px:
            return TitleFontChoice(size_px=size_px, rendered_width_px=rendered_width_px)
    raise FaceRenderError(
        f'Title cannot fit safely at the minimum approved size: "{text}" exceeds {safe_width_px:.0f}px.'
    )


def _positive_style_multiplier(value: float, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise FaceRenderError(f"{label} must be positive and finite.") from error
    if not math.isfinite(numeric) or numeric <= 0:
        raise FaceRenderError(f"{label} must be positive and finite.")
    return numeric


def _spread_vertical_position(position: float, panel_height: float, spread: float) -> float:
    """Move a component away from the vertical centre without changing its size."""
    factor = _positive_style_multiplier(spread, "Front vertical spread")
    anchor = panel_height / 2
    return anchor + (position - anchor) * factor


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
    *,
    group_scale: float = FRONT_GROUP_SCALE,
    group_y_offset: float = FRONT_GROUP_Y_OFFSET,
    supporting_stroke_multiplier: float = 1.0,
    facial_linework_multiplier: float = 1.0,
    vertical_spread: float = 1.0,
) -> str:
    _positive_style_multiplier(street_feature_stroke_multiplier, "Front street feature stroke multiplier")
    support_scale = _positive_style_multiplier(supporting_stroke_multiplier, "Supporting facial stroke multiplier")
    linework_scale = _positive_style_multiplier(facial_linework_multiplier, "Facial linework multiplier")
    spread = _positive_style_multiplier(vertical_spread, "Front vertical spread")
    palette, specs = _face_specs_for_glyph(glyph)
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
    face_asset = _scale_face_linework(face_asset, linework_scale)
    face_asset = face_asset.replace(
        "</svg>",
        f'<style>.v28-face-linework .v28-support {{ stroke-width:{SUPPORTING_STROKE_WIDTH * support_scale * linework_scale:.2f}px !important; }}</style></svg>',
    )
    face_asset = _spread_face_component_rows(face_asset, spread)
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
    face_center_y = face_y + face_height / 2
    face_center_y = _spread_vertical_position(face_center_y, height, spread)
    face_y = min(max(face_center_y - face_height / 2, 0.0), height - face_height)
    face_asset, _clearance = _apply_tiny_nose_street_clearance(
        face_asset,
        panel_center=panel_center,
        panel_height=height,
        face_x=face_x,
        face_y=face_y,
        face_width=face_width,
        face_height=face_height,
        group_scale=group_scale,
        group_y_offset=group_y_offset,
    )
    href = "data:image/svg+xml;base64," + base64.b64encode(face_asset.encode("utf-8")).decode("ascii")
    return (
        f'<image class="v28-face" href="{href}" x="{face_x:.1f}" y="{face_y:.1f}" '
        f'width="{face_width:.1f}" height="{face_height:.1f}" preserveAspectRatio="xMidYMid meet"/>'
    )


def _extract_css_stroke_width(markup: str, selector: str) -> float | None:
    pattern = re.compile(
        rf"{re.escape(selector)}\s*\{{[^}}]*?stroke-width\s*:\s*([-+]?\d*\.?\d+)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(markup)
    return None if match is None else float(match.group(1))


def _extract_inline_class_stroke_width(markup: str, class_name: str) -> float | None:
    pattern = re.compile(
        rf'<[^>]+class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*'
        rf'style="[^"]*stroke-width\s*:\s*([-+]?\d*\.?\d+)',
        re.IGNORECASE,
    )
    match = pattern.search(markup)
    return None if match is None else float(match.group(1))


def _scale_face_linework(face_asset: str, multiplier: float) -> str:
    """Scale every stroked facial role while preserving its native hierarchy.

    Native artwork mixes class-based and inline widths. We read those real
    widths and append higher-specificity overrides. The coloured street
    polyline is excluded because it has its own calibration control.
    """
    factor = _positive_style_multiplier(multiplier, "Facial linework multiplier")
    if factor == 1.0:
        return face_asset

    rules: list[str] = []
    for selector in (".ink", ".feature", ".thin", ".brow", ".soft-detail"):
        width = _extract_css_stroke_width(face_asset, selector)
        if width is not None:
            rules.append(
                f".v28-face-linework {selector} "
                f"{{ stroke-width:{width * factor:.3f}px !important; }}"
            )

    for class_name in ("hierarchy-hair", "hierarchy-brow"):
        width = _extract_inline_class_stroke_width(face_asset, class_name)
        if width is not None:
            rules.append(
                f".v28-face-linework .{class_name} "
                f"{{ stroke-width:{width * factor:.3f}px !important; }}"
            )

    if not rules:
        return face_asset

    calibration_style = (
        '<style id="mug-print-linework-calibration">'
        + "".join(rules)
        + "</style>"
    )
    return face_asset.replace("</svg>", calibration_style + "</svg>")



def _primitive_vertical_centre(element: ET.Element) -> float | None:
    """Return an approximate centre Y for one native face primitive."""
    tag = _svg_local_name(element.tag)
    try:
        if tag in {"circle", "ellipse"}:
            return float(element.get("cy", ""))
        if tag == "line":
            return (float(element.get("y1", "")) + float(element.get("y2", ""))) / 2
        if tag == "polyline":
            values = [
                float(value)
                for value in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", element.get("points", ""))
            ]
            ys = values[1::2]
            return (min(ys) + max(ys)) / 2 if ys else None
        if tag == "path":
            values = [
                float(value)
                for value in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", element.get("d", ""))
            ]
            ys = values[1::2]
            return (min(ys) + max(ys)) / 2 if ys else None
    except ValueError:
        return None
    return None


def _primitive_vertical_centre_markup(tag_markup: str) -> float | None:
    """Return an approximate Y centre from one SVG primitive opening tag."""
    name_match = re.match(r"<(?:[A-Za-z0-9_]+:)?([A-Za-z0-9_]+)\b", tag_markup)
    if name_match is None:
        return None
    tag = name_match.group(1).casefold()

    def attribute(name: str) -> str | None:
        match = re.search(rf'\b{re.escape(name)}="([^"]*)"', tag_markup)
        return None if match is None else match.group(1)

    try:
        if tag in {"circle", "ellipse"}:
            value = attribute("cy")
            return None if value is None else float(value)
        if tag == "line":
            y1, y2 = attribute("y1"), attribute("y2")
            if y1 is None or y2 is None:
                return None
            return (float(y1) + float(y2)) / 2
        if tag == "polyline":
            values = [
                float(value)
                for value in re.findall(
                    r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?",
                    attribute("points") or "",
                )
            ]
            ys = values[1::2]
            return (min(ys) + max(ys)) / 2 if ys else None
        if tag == "path":
            values = [
                float(value)
                for value in re.findall(
                    r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?",
                    attribute("d") or "",
                )
            ]
            ys = values[1::2]
            return (min(ys) + max(ys)) / 2 if ys else None
    except ValueError:
        return None
    return None


def _translate_svg_opening_tag(tag_markup: str, shift: float) -> str:
    """Append a Y translation without parsing/reserialising the SVG document."""
    transform = re.search(r'\btransform="([^"]*)"', tag_markup)
    if transform is not None:
        revised = _with_downward_translation(transform.group(1), shift)
        return (
            tag_markup[:transform.start(1)]
            + revised
            + tag_markup[transform.end(1):]
        )
    insertion = -2 if tag_markup.endswith("/>") else -1
    return (
        tag_markup[:insertion]
        + f\' transform="translate(0 {shift:.4f})"\'
        + tag_markup[insertion:]
    )


def _spread_face_component_rows(face_asset: str, spread: float) -> str:
    """Separate native facial rows without rewriting the embedded SVG XML.

    CairoSVG can fail on namespace changes introduced by ElementTree.tostring
    for a nested SVG data URI. Work directly on the original opening tags so
    document structure, namespaces and stylesheet text remain intact.
    """
    factor = _positive_style_multiplier(spread, "Front vertical spread")
    if factor == 1.0:
        return face_asset

    primitive_pattern = re.compile(
        r"<(?:[A-Za-z0-9_]+:)?(?:circle|ellipse|line|polyline|path)\b[^>]*>",
        re.IGNORECASE,
    )
    matches = list(primitive_pattern.finditer(face_asset))
    centres = [_primitive_vertical_centre_markup(match.group(0)) for match in matches]
    finite = [
        centre for centre in centres
        if centre is not None and math.isfinite(centre)
    ]
    if not finite:
        return face_asset

    anchor = (min(finite) + max(finite)) / 2
    index = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal index
        centre = centres[index]
        index += 1
        if centre is None or not math.isfinite(centre):
            return match.group(0)
        shift = (centre - anchor) * (factor - 1.0)
        if abs(shift) < 1e-9:
            return match.group(0)
        return _translate_svg_opening_tag(match.group(0), shift)

    return primitive_pattern.sub(replace, face_asset)


def assess_nose_street_clearance(nose_mask: Image.Image, street_mask: Image.Image) -> NoseStreetClearanceCorrection:
    if nose_mask.size != street_mask.size:
        raise FaceRenderError("Nose and lower street masks must share one coordinate space.")
    nose = nose_mask.convert("L")
    street = street_mask.convert("L")
    nose_bounds = nose.point(lambda value: 255 if value >= MASK_ALPHA_THRESHOLD else 0).getbbox()
    street_binary = street.point(lambda value: 255 if value >= MASK_ALPHA_THRESHOLD else 0)
    street_bounds = street_binary.getbbox()
    if nose_bounds is None or street_bounds is None:
        return NoseStreetClearanceCorrection(0, 0, "manual-review")
    street_width = street_bounds[2] - street_bounds[0]
    street_height = street_bounds[3] - street_bounds[1]
    normal_lower_feature = (
        street_width >= NOSE_STREET_MIN_NORMAL_WIDTH_PX
        and street_height <= NOSE_STREET_MAX_NORMAL_HEIGHT_PX
    )
    nose_pixels = nose.load()
    street_pixels = street_binary.load()
    required_shift = 0
    for x in range(nose.width):
        nose_bottom = next((y for y in range(nose.height - 1, -1, -1) if nose_pixels[x, y] >= MASK_ALPHA_THRESHOLD), None)
        street_top = next((y for y in range(street.height) if street_pixels[x, y] >= MASK_ALPHA_THRESHOLD), None)
        if nose_bottom is not None and street_top is not None:
            required_shift = max(required_shift, nose_bottom + NOSE_STREET_TARGET_CLEARANCE_PX - street_top)
    if not normal_lower_feature:
        return NoseStreetClearanceCorrection(max(required_shift, 0), 0, "manual-review")
    if required_shift <= 0:
        return NoseStreetClearanceCorrection(0, 0, "clear")
    if required_shift > NOSE_STREET_MAX_AUTOMATIC_SHIFT_PX:
        return NoseStreetClearanceCorrection(required_shift, 0, "manual-review")
    return NoseStreetClearanceCorrection(required_shift, required_shift, "auto-corrected")


def _apply_tiny_nose_street_clearance(
    face_asset: str,
    *,
    panel_center: float,
    panel_height: float,
    face_x: float,
    face_y: float,
    face_width: float,
    face_height: float,
    group_scale: float,
    group_y_offset: float,
) -> tuple[str, NoseStreetClearanceCorrection]:
    if group_scale <= 0 or not math.isfinite(group_scale):
        raise FaceRenderError("Front composition scale must be positive and finite.")
    nose_mask = _native_role_mask(
        face_asset, {"v28-nose"}, panel_center, panel_height, face_x, face_y,
        face_width, face_height, group_scale, group_y_offset,
    )
    street_mask = _native_role_mask(
        face_asset, {"street"}, panel_center, panel_height, face_x, face_y,
        face_width, face_height, group_scale, group_y_offset,
    )
    correction = assess_nose_street_clearance(nose_mask, street_mask)
    if correction.applied_shift_px == 0:
        return face_asset, correction
    asset_scale = min(face_width / FACE_ASSET_SIZE[0], face_height / FACE_ASSET_SIZE[1])
    native_shift = correction.applied_shift_px / (asset_scale * group_scale)
    root = ET.fromstring(face_asset)
    changed = False
    for node in root.iter():
        if _svg_local_name(node.tag) == "polyline" and "street" in node.get("class", "").split():
            node.set("transform", _with_downward_translation(node.get("transform", ""), native_shift))
            changed = True
    if not changed:
        raise FaceRenderError("Native V28 renderer produced no lower street feature for clearance correction.")
    return ET.tostring(root, encoding="unicode"), correction


def _native_role_mask(
    face_asset: str,
    classes: set[str],
    panel_center: float,
    panel_height: float,
    face_x: float,
    face_y: float,
    face_width: float,
    face_height: float,
    group_scale: float,
    group_y_offset: float,
) -> Image.Image:
    root = ET.fromstring(face_asset)
    namespace = "{http://www.w3.org/2000/svg}"
    parent_by_child = {child: parent for parent in root.iter() for child in parent}
    content = next((node for node in root.iter(f"{namespace}g") if "face-content" in node.get("class", "").split()), None)
    if content is None or not _retain_svg_role(content, classes):
        return Image.new("L", FRONT_PANEL_PX, 0)
    selected = content
    ancestor = parent_by_child.get(content)
    while ancestor is not None and ancestor is not root:
        wrapper = ET.Element(ancestor.tag, ancestor.attrib)
        wrapper.append(selected)
        selected = wrapper
        ancestor = parent_by_child.get(ancestor)
    defs = root.find(f"{namespace}defs")
    defs_markup = "" if defs is None else ET.tostring(defs, encoding="unicode")
    filtered_asset = f'<svg xmlns="http://www.w3.org/2000/svg" width="{FACE_ASSET_SIZE[0]}" height="{FACE_ASSET_SIZE[1]}" viewBox="0 0 {FACE_ASSET_SIZE[0]} {FACE_ASSET_SIZE[1]}">{defs_markup}{ET.tostring(selected, encoding="unicode")}</svg>'
    href = "data:image/svg+xml;base64," + base64.b64encode(filtered_asset.encode("utf-8")).decode("ascii")
    transform = _front_group_transform(panel_center, panel_height, group_scale, group_y_offset)
    markup = f'<svg xmlns="http://www.w3.org/2000/svg" width="{SOURCE_CANVAS_PX[0]}" height="{SOURCE_CANVAS_PX[1]}"><g transform="{transform}"><image href="{href}" x="{face_x:.1f}" y="{face_y:.1f}" width="{face_width:.1f}" height="{face_height:.1f}" preserveAspectRatio="xMidYMid meet"/></g></svg>'
    png = cairosvg.svg2png(bytestring=markup.encode("utf-8"), output_width=SOURCE_CANVAS_PX[0], output_height=SOURCE_CANVAS_PX[1])
    with Image.open(io.BytesIO(png)) as rendered:
        return rendered.getchannel("A").crop((0, 0, *FRONT_PANEL_PX)).copy()


def _retain_svg_role(node: ET.Element, classes: set[str]) -> bool:
    keep = bool(set(node.get("class", "").split()) & classes)
    for child in list(node):
        if _retain_svg_role(child, classes):
            keep = True
        else:
            node.remove(child)
    return keep


def _with_downward_translation(transform: str, shift: float) -> str:
    return f"{transform} translate(0 {shift:.4f})".strip()


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
FACE_SVG_GENERATOR = "mug-previewer/canonical-face-svg-v1"


def render_face_svg(dataset: "Dataset", street: StreetRecord, options: FaceRenderOptions | None = None) -> str:
    """Return editable canonical SVG front artwork for one selected street.

    The SVG keeps the frozen V28 front panel dimensions and composition.  It
    emits the native face linework directly as SVG rather than preserving the
    PNG renderer's temporary embedded SVG image.
    """
    import json
    import re

    options = options or FaceRenderOptions(area=dataset.display_name)
    placement_source = _validated_svg_placement_source(dataset, street, options)
    glyph = street.glyph_path
    if not glyph.is_file() or glyph.suffix.casefold() != ".svg":
        # Keep the public error wording aligned with the PNG renderer.
        render_face(street, options)
        raise AssertionError("render_face unexpectedly returned for an invalid glyph")

    width, height = SOURCE_CANVAS_PX
    panel_center = width * FRONT_CENTER_RATIO
    face_markup = _render_native_face(
        glyph, panel_center, width, height, options.street_feature_stroke_multiplier,
        group_scale=options.group_scale, group_y_offset=options.group_y_offset,
        supporting_stroke_multiplier=options.supporting_stroke_multiplier,
        facial_linework_multiplier=options.facial_linework_multiplier,
        vertical_spread=options.vertical_spread,
    )
    asset = _decode_native_face_asset(face_markup)
    asset_root = ET.fromstring(asset)
    face_content = next(
        (node for node in asset_root.iter() if _svg_local_name(node.tag) == "g" and "face-content" in node.get("class", "").split()),
        None,
    )
    if face_content is None:
        raise FaceRenderError(f"Native V28 renderer produced no editable face artwork for {glyph}")
    _add_editable_face_groups(face_content)

    palette = native.get_face_palette(native.DEFAULT_PALETTE_KEY)
    font_stack = native.get_text_font_stack(native.DEFAULT_TEXT_FONT_KEY)
    street_name = street.display_name.strip() or street.street_name.strip() or street.id
    title = select_title_font(street_name, font_stack, font_scale=options.title_font_scale)
    area = street.locality_label or options.area.strip()
    title_y, area_y = _front_text_y_positions(
        height, options.title_locality_gap_delta, options.typography_block_y_offset,
    )
    title_y = _spread_vertical_position(title_y, height, options.vertical_spread)
    area_y = _spread_vertical_position(area_y, height, options.vertical_spread)
    locality_font_size = LOCALITY_FONT_SIZE * _positive_style_multiplier(
        options.locality_font_scale, "Locality font scale",
    )
    group_transform = _front_group_transform(
        panel_center, height, options.group_scale, options.group_y_offset,
    )
    face_x, face_y, face_width, face_height = _native_face_markup_placement(face_markup)
    asset_scale = min(face_width / FACE_ASSET_SIZE[0], face_height / FACE_ASSET_SIZE[1])
    asset_x = face_x + (face_width - FACE_ASSET_SIZE[0] * asset_scale) / 2
    asset_y = face_y + (face_height - FACE_ASSET_SIZE[1] * asset_scale) / 2
    asset_body = next((node for node in asset_root if _svg_local_name(node.tag) == "g"), None)
    defs = next((node for node in asset_root if _svg_local_name(node.tag) == "defs"), None)
    if asset_body is None or defs is None:
        raise FaceRenderError("Native V28 renderer produced incomplete editable artwork.")
    metadata = {
        "dataset_id": dataset.id,
        "dataset_name": dataset.display_name,
        "generator": FACE_SVG_GENERATOR,
        "generator_version": "V28.1",
        "placement_source": placement_source,
        "street_id": street.id,
        "street_name": street_name,
    }
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <metadata id="mug-previewer-metadata">{_escape(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")))}</metadata>
  {ET.tostring(defs, encoding="unicode")}
  <style>
    .mug-title {{ font-family:{font_stack}; font-size:{title.size_px:.1f}px; font-weight:{TITLE_WEIGHT}; fill:{palette.feature}; text-anchor:middle; }}
    .mug-area {{ font:500 {locality_font_size:.1f}px {font_stack}; fill:{palette.feature}; text-anchor:middle; letter-spacing:0.6px; }}
    .v28-face-linework .v28-support {{ stroke-width:{SUPPORTING_STROKE_WIDTH * options.supporting_stroke_multiplier * options.facial_linework_multiplier:.2f}px !important; }}
  </style>
  <g class="front-composition" transform="{group_transform}">
    <g id="title"><text class="mug-title" x="{panel_center:.1f}" y="{title_y:.1f}">{_escape(street_name)}</text></g>
    <g id="locality"><text class="mug-area" x="{panel_center:.1f}" y="{area_y:.1f}">{_escape(area)}</text></g>
    <g class="v28-face-vector" transform="translate({asset_x:.4f} {asset_y:.4f}) scale({asset_scale:.8f})">{ET.tostring(asset_body, encoding="unicode")}</g>
  </g>
</svg>'''


def write_face_svg(
    dataset: "Dataset", street: StreetRecord, output: Path | str, options: FaceRenderOptions | None = None,
) -> Path:
    """Write :func:`render_face_svg` output and return its destination path."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_face_svg(dataset, street, options), encoding="utf-8")
    return path


def _decode_native_face_asset(face_markup: str) -> str:
    import re

    match = re.compile(r'href="data:image/svg\+xml;base64,([^"]+)"').search(face_markup)
    if match is None:
        raise FaceRenderError("Native V28 renderer produced no SVG face asset.")
    return base64.b64decode(match.group(1)).decode("utf-8")


def _native_face_markup_placement(face_markup: str) -> tuple[float, float, float, float]:
    image = ET.fromstring(face_markup)
    return tuple(float(image.attrib[name]) for name in ("x", "y", "width", "height"))


def _svg_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _add_editable_face_groups(face_content: ET.Element) -> None:
    """Give the direct native vectors stable, editor-friendly parent groups."""
    namespace = "{http://www.w3.org/2000/svg}"
    groups = {
        "eyes": ET.Element(namespace + "g", {"id": "eyes"}),
        "nose-street": ET.Element(namespace + "g", {"id": "nose-street"}),
        "mouth": ET.Element(namespace + "g", {"id": "mouth"}),
    }
    children = list(face_content)
    for child in children:
        face_content.remove(child)
        classes = set(child.get("class", "").split())
        tag = _svg_local_name(child.tag)
        if tag == "polyline" or "street" in classes or "v28-nose" in classes:
            groups["nose-street"].append(child)
        elif "soft-detail" in classes:
            groups["mouth"].append(child)
        else:
            groups["eyes"].append(child)
    for role in ("eyes", "nose-street", "mouth"):
        face_content.append(groups[role])
def _validated_svg_placement_source(dataset: "Dataset", street: StreetRecord, options: FaceRenderOptions) -> str:
    """Use an accepted production placement without running candidate scoring."""
    if options.manual_override is not None:
        return "explicit-manual-override"
    from ..ui.production import preview_render_override

    approved = preview_render_override(dataset, street)
    if approved is None:
        return "standard-layout"
    return "validated-production-{}".format(approved.status.value.casefold())
