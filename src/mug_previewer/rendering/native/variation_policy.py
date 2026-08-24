"""
Street Face Generator v25.

V25 retains v24's clear eye/brow hierarchy and adds restrained, deterministic
hair.  Street geometry chooses the broad hair language; a stable value derived
from the street name only varies the result within that language.  Some faces
remain bald, and a street assigned to the hairline role is never given an
extra procedural hair mark.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import List, Optional, Sequence, Tuple

from . import rendering_policy as v24


v20 = v24.v20
v22 = v24.v22

BALD_PROBABILITY = 0.27
_v24_render_grid = v20.render_grid
_v24_face_renderer = v22._v21_render_face_svg


def provenance_payload(area_name: str, generated_on: Optional[str] = None) -> str:
    """Build a v25 provenance payload while retaining the checksum scheme."""
    area = re.sub(r"\s+", " ", str(area_name or "unknown area").strip())
    generated = generated_on or date.today().isoformat()
    base = f"SFV25|AREA={area}|DATE={generated}"
    checksum = hashlib.sha256(
        f"{v20.signature_secret_id()}|{base}".encode("utf-8", errors="replace")
    ).hexdigest()[:10].upper()
    return f"{base}|CHK={checksum}"


def render_attribution_provenance_mark(*args: object, **kwargs: object) -> str:
    markup = v24.render_attribution_provenance_mark(*args, **kwargs)
    return markup.replace("street-face-v24", "street-face-v25")


def _eye_bounds(markup: str) -> Optional[Tuple[float, float, float]]:
    eyes = re.findall(
        r'<circle cx="([-+]?\d*\.?\d+)" cy="([-+]?\d*\.?\d+)" r="([-+]?\d*\.?\d+)" class="eye"/>',
        markup,
    )
    if not eyes:
        return None
    eye_values = [(float(x), float(y), float(radius)) for x, y, radius in eyes]
    return (
        min(x - radius for x, _y, radius in eye_values),
        max(x + radius for x, _y, radius in eye_values),
        min(y - radius for _x, y, radius in eye_values),
    )


def _hair_path(
    spec: object,
    markup: str,
    face_layout_height: float,
    frame_width: float,
    frame_height: float,
) -> str:
    """Return one legible, geometry-led hair mark or an intentional bald head."""
    if getattr(spec.role_choice, "role", "") == "hairline":
        return ""
    bounds = _eye_bounds(markup)
    if bounds is None:
        return ""

    seed = v20.stable_unit(f"{spec.name}:hair-style")
    if seed < BALD_PROBABILITY:
        return ""

    metrics = spec.role_choice.metrics
    eye_left, eye_right, eye_top = bounds
    face_unit = max(12.0, min(frame_width, frame_height))
    # The base is above the highest eye, so every hair style remains visibly
    # separate from the eye/brow lane even after the v24 brow adjustment.
    hair_base = eye_top - min(44.0, face_unit * 0.035)
    centre_x = (eye_left + eye_right) / 2
    hair_half_width = min((eye_right - eye_left) * 0.58, frame_width * 0.24)
    hair_height = min(
        54.0,
        face_unit * (0.026 + min(0.018, metrics.sinuosity * 0.008)),
    )

    # Angular streets make a small quiff; sinuous streets make a rounded wave.
    # The remaining styles are deliberately understated to avoid a second brow.
    if metrics.angularity >= 25.0:
        path = (
            f"M {centre_x-hair_half_width*0.72:.2f},{hair_base:.2f} "
            f"L {centre_x-hair_half_width*0.28:.2f},{hair_base-hair_height:.2f} "
            f"L {centre_x:.2f},{hair_base-hair_height*0.32:.2f} "
            f"L {centre_x+hair_half_width*0.30:.2f},{hair_base-hair_height*0.88:.2f} "
            f"L {centre_x+hair_half_width*0.72:.2f},{hair_base:.2f}"
        )
    elif metrics.sinuosity >= 1.12:
        path = (
            f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
            f"Q {centre_x-hair_half_width*0.55:.2f},{hair_base-hair_height:.2f} "
            f"{centre_x-hair_half_width*0.15:.2f},{hair_base-hair_height*0.38:.2f} "
            f"Q {centre_x+hair_half_width*0.20:.2f},{hair_base-hair_height*1.10:.2f} "
            f"{centre_x+hair_half_width:.2f},{hair_base-hair_height*0.24:.2f}"
        )
    elif seed >= 0.72:
        path = (
            f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
            f"Q {centre_x-hair_half_width*0.10:.2f},{hair_base-hair_height*1.05:.2f} "
            f"{centre_x+hair_half_width:.2f},{hair_base-hair_height*0.30:.2f}"
        )
    else:
        path = (
            f"M {centre_x-hair_half_width*0.75:.2f},{hair_base:.2f} "
            f"Q {centre_x:.2f},{hair_base-hair_height:.2f} "
            f"{centre_x+hair_half_width*0.75:.2f},{hair_base:.2f}"
        )

    stroke_width = max(1.15, min(3.0, face_unit * 0.0022))
    return f'<path d="{path}" class="ink" style="stroke-width:{stroke_width:.2f}"/>'


def _add_geometry_hair(
    spec: object, markup: str, face_args: Sequence[object], face_kwargs: dict
) -> str:
    if len(face_args) < 4:
        return markup
    face_layout_height = float(face_args[3])
    frame_width = float(face_kwargs.get("frame_w", face_args[2]))
    frame_height = float(face_kwargs.get("frame_h", face_args[3]))
    hair_markup = _hair_path(
        spec, markup, face_layout_height, frame_width, frame_height
    )
    if not hair_markup:
        return markup
    first_brow = markup.find('class="ink brow"')
    if first_brow < 0:
        return markup
    path_start = markup.rfind("<path", 0, first_brow)
    return markup[:path_start] + hair_markup + "\n      " + markup[path_start:]


def render_grid(
    specs: object, out_svg: object, *args: object, **kwargs: object
) -> None:
    """Add one controlled, geometry-led hairstyle before v24 compacts brows."""
    def face_renderer_with_hair(
        spec: object, *face_args: object, **face_kwargs: object
    ) -> str:
        markup = _v24_face_renderer(spec, *face_args, **face_kwargs)
        return _add_geometry_hair(spec, markup, face_args, face_kwargs)

    previous_face_renderer = v22._v21_render_face_svg
    v22._v21_render_face_svg = face_renderer_with_hair
    try:
        _v24_render_grid(specs, out_svg, *args, **kwargs)
    finally:
        v22._v21_render_face_svg = previous_face_renderer


def _install_v25_policy() -> None:
    v20.__doc__ = __doc__
    v20.DEFAULT_SIGNATURE_ID = "street-face-generator-v25-private-mark"
    v20.provenance_payload = provenance_payload
    v20.render_attribution_provenance_mark = render_attribution_provenance_mark
    v20.render_grid = render_grid


_install_v25_policy()


def __getattr__(name: str) -> object:
    """Expose v24's compatible public API through the v25 module."""
    return getattr(v20, name)


def main() -> None:
    v20.main()


if __name__ == "__main__":
    main()
