"""Faithful rasterisation of canonical SVG front artwork."""
from __future__ import annotations

import html
import io
import re
from pathlib import Path

import cairosvg
from PIL import Image

from .face import (
    FRONT_PANEL_PX,
    SOURCE_CANVAS_PX,
    TITLE_LINE_SPACING_RATIO,
    title_safe_width_for_group,
)
from .native import face_policy as native


def _measure_title_width(text: str, size_px: float) -> int:
    """Measure title text through the same CairoSVG font path used by export."""
    font_stack = native.get_text_font_stack(native.DEFAULT_TEXT_FONT_KEY)
    markup = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="140">
  <style>.title {{ font-family:{font_stack}; font-size:{size_px:.3f}px; font-weight:625; }}</style>
  <text class="title" x="20" y="95">{html.escape(text)}</text>
</svg>'''
    stream = io.BytesIO()
    cairosvg.svg2png(
        bytestring=markup.encode("utf-8"),
        write_to=stream,
        output_width=1600,
        output_height=140,
    )
    with Image.open(io.BytesIO(stream.getvalue())) as rendered:
        rendered.load()
        bounds = rendered.getchannel("A").getbbox()
    return 0 if bounds is None else bounds[2] - bounds[0]


def _replace_y_attribute(opening_tag: str, value: float) -> str:
    if re.search(r'\by="[^"]*"', opening_tag):
        return re.sub(r'(\by=")[^"]*(")', rf'\g<1>{value:.3f}\g<2>', opening_tag, count=1)
    return opening_tag[:-1] + f' y="{value:.3f}">'


def _apply_title_fit_guardrail(markup: str) -> str:
    """Wrap an old one-line canonical title when its final styled width is unsafe.

    Prepared SVGs are authoritative and may pre-date the two-line title policy.
    The production style profile is applied to them immediately before this
    raster step.  Inspect that final styled SVG and add a temporary two-line
    presentation only when needed; never rewrite the authoritative SVG on disk.
    """
    title_group = re.search(r'<g\s+id="title"[^>]*>(?P<body>.*?)</g>', markup, re.IGNORECASE | re.DOTALL)
    if title_group is None:
        return markup
    text_nodes = list(re.finditer(r'(<text\b[^>]*>)(.*?)(</text>)', title_group.group("body"), re.IGNORECASE | re.DOTALL))
    if len(text_nodes) != 1:
        return markup

    title_text = html.unescape(re.sub(r'<[^>]+>', '', text_nodes[0].group(2))).strip()
    words = title_text.split()
    if len(words) < 2:
        return markup

    size_match = re.search(
        r'\.mug-title\s*\{[^}]*?font-size\s*:\s*([0-9.]+)px',
        markup,
        re.IGNORECASE | re.DOTALL,
    )
    if size_match is None:
        return markup
    size_px = float(size_match.group(1))

    scale_match = re.search(
        r'class="front-composition"\s+transform="[^"]*?scale\(([-+0-9.]+)\)',
        markup,
        re.IGNORECASE,
    )
    group_scale = float(scale_match.group(1)) if scale_match is not None else 1.0
    safe_width = title_safe_width_for_group(group_scale)
    if _measure_title_width(title_text, size_px) <= safe_width:
        return markup

    candidates: list[tuple[tuple[float, float, float], tuple[str, str], tuple[int, int]]] = []
    for index in range(1, len(words)):
        lines = (" ".join(words[:index]), " ".join(words[index:]))
        widths = tuple(_measure_title_width(line, size_px) for line in lines)
        if max(widths) > safe_width:
            continue
        score = (
            float(abs(widths[0] - widths[1])),
            float(max(widths)),
            abs(index - len(words) / 2.0),
        )
        candidates.append((score, lines, widths))
    if not candidates:
        return markup

    _score, lines, _widths = min(candidates, key=lambda item: item[0])
    opening = text_nodes[0].group(1)
    y_match = re.search(r'\by="([^"]+)"', opening)
    if y_match is None:
        return markup
    title_y = float(y_match.group(1))
    half = size_px * TITLE_LINE_SPACING_RATIO / 2.0
    y_values = (title_y - half, title_y + half)
    replacement = ''.join(
        _replace_y_attribute(opening, y) + html.escape(line) + '</text>'
        for line, y in zip(lines, y_values)
    )
    body = title_group.group("body")
    revised_body = body[:text_nodes[0].start()] + replacement + body[text_nodes[0].end():]
    revised = markup[:title_group.start("body")] + revised_body + markup[title_group.end("body"):]

    locality = re.search(r'(<g\s+id="locality"[^>]*>\s*<text\b[^>]*\by=")([^"]+)(")', revised, re.IGNORECASE | re.DOTALL)
    if locality is not None:
        locality_y = float(locality.group(2)) + half
        revised = revised[:locality.start(2)] + f"{locality_y:.3f}" + revised[locality.end(2):]
    return revised


def rasterize_face_svg(source: Path | str | bytes) -> Image.Image:
    """Render SVG bytes/text (or a Path) at canonical size and crop the front."""
    if isinstance(source, Path):
        payload = source.read_bytes()
    else:
        payload = source.encode("utf-8") if isinstance(source, str) else source

    try:
        markup = payload.decode("utf-8")
    except UnicodeDecodeError:
        markup = None
    if markup is not None:
        payload = _apply_title_fit_guardrail(markup).encode("utf-8")

    stream = io.BytesIO()
    cairosvg.svg2png(
        bytestring=payload,
        write_to=stream,
        output_width=SOURCE_CANVAS_PX[0],
        output_height=SOURCE_CANVAS_PX[1],
    )
    with Image.open(io.BytesIO(stream.getvalue())) as rendered:
        rendered.load()
        return rendered.convert("RGBA").crop((0, 0, *FRONT_PANEL_PX)).copy()
