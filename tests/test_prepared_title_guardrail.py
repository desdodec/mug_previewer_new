from __future__ import annotations

import re

from mug_previewer.rendering.svg_raster import _apply_title_fit_guardrail, rasterize_face_svg


def _markup(title: str, *, size: float = 28.34, scale: float = 1.22) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462" viewBox="0 0 990 462">
  <style>
    .mug-title {{ font-family:Arial, sans-serif; font-size:{size}px; font-weight:625; fill:#333; text-anchor:middle; }}
    .mug-area {{ font:500 14.4px Arial, sans-serif; fill:#333; text-anchor:middle; }}
  </style>
  <g class="front-composition" transform="translate(0 60.00) translate(247.50 231.00) scale({scale:.4f}) translate(-247.50 -231.00)">
    <g id="title"><text class="mug-title" x="247.5" y="43.142">{title}</text></g>
    <g id="locality"><text class="mug-area" x="247.5" y="65.314">Greater London, N16</text></g>
  </g>
</svg>'''


def test_prepared_long_title_wraps_after_profile_styling() -> None:
    adjusted = _apply_title_fit_guardrail(_markup("Stoke Newington Church Street"))

    title_group = re.search(r'<g id="title">(.*?)</g>', adjusted, re.DOTALL)
    assert title_group is not None
    assert title_group.group(1).count('<text') == 2
    assert '>Stoke Newington</text>' in title_group.group(1)
    assert '>Church Street</text>' in title_group.group(1)

    locality_y = float(re.search(r'<g id="locality"><text[^>]* y="([^"]+)"', adjusted).group(1))
    assert locality_y > 65.314


def test_prepared_short_title_remains_one_line() -> None:
    source = _markup("Park Road", size=37.06)
    adjusted = _apply_title_fit_guardrail(source)
    assert adjusted == source


def test_guarded_prepared_svg_still_rasterises_to_front_panel() -> None:
    image = rasterize_face_svg(_markup("Stoke Newington Church Street"))
    assert image.size == (495, 462)
    assert image.mode == "RGBA"
