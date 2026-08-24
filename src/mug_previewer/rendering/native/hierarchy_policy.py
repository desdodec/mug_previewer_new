"""
Street Face Generator v26.

V26 gives every face a fixed, legible upper-face hierarchy:

    hair (or an intentional bald head) -> brows -> eyes

Hair style is led by street geometry and varied deterministically by the street
name.  The old overlapping crown, lash and eyebrow decorations are replaced by
one hair cap and a pair of compact brows in dedicated vertical lanes.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import List, Optional, Sequence, Tuple

from . import variation_policy as v25


v20 = v25.v20
v22 = v25.v22
v24 = v25.v24

BALD_PROBABILITY = 0.12
_v24_render_grid = v25._v24_render_grid
_base_face_renderer = v25._v24_face_renderer


def provenance_payload(area_name: str, generated_on: Optional[str] = None) -> str:
    """Build a v26 provenance payload while retaining the checksum scheme."""
    area = re.sub(r"\s+", " ", str(area_name or "unknown area").strip())
    generated = generated_on or date.today().isoformat()
    base = f"SFV26|AREA={area}|DATE={generated}"
    checksum = hashlib.sha256(
        f"{v20.signature_secret_id()}|{base}".encode("utf-8", errors="replace")
    ).hexdigest()[:10].upper()
    return f"{base}|CHK={checksum}"


def render_attribution_provenance_mark(*args: object, **kwargs: object) -> str:
    markup = v25.render_attribution_provenance_mark(*args, **kwargs)
    return markup.replace("street-face-v25", "street-face-v26")


def _eye_anchors(markup: str) -> Optional[List[Tuple[float, float, float]]]:
    matches = re.findall(
        r'<circle cx="([-+]?\d*\.?\d+)" cy="([-+]?\d*\.?\d+)" r="([-+]?\d*\.?\d+)" class="eye"/>',
        markup,
    )
    if len(matches) < 2:
        return None
    return [(float(x), float(y), float(radius)) for x, y, radius in matches[:2]]


def _hierarchy_markup(spec: object, markup: str) -> str:
    eyes = _eye_anchors(markup)
    if eyes is None:
        return markup
    eyes = sorted(eyes, key=lambda eye: eye[0])
    (left_x, left_y, left_r), (right_x, right_y, right_r) = eyes
    eye_top = min(left_y - left_r, right_y - right_r)
    average_radius = (left_r + right_r) / 2
    eye_span = right_x - left_x
    centre_x = (left_x + right_x) / 2

    # These lanes are expressed in eye radii, so they remain consistent on a
    # small gallery card and an A2 single print alike.
    brow_y = eye_top - max(average_radius * 0.48, 2.2)
    hair_base = brow_y - max(average_radius * 0.85, 4.5)
    brow_width = max(average_radius * 1.30, eye_span * 0.11)
    hair_half_width = max(eye_span * 0.62, average_radius * 3.4)
    hair_height = max(average_radius * 1.45, 7.0)
    brow_stroke = max(0.95, average_radius * 0.13)
    hair_stroke = max(1.35, average_radius * 0.20)

    left_brow = (
        f"M {left_x-brow_width:.2f},{brow_y:.2f} "
        f"Q {left_x:.2f},{brow_y-average_radius*0.24:.2f} "
        f"{left_x+brow_width:.2f},{brow_y:.2f}"
    )
    right_brow = (
        f"M {right_x-brow_width:.2f},{brow_y:.2f} "
        f"Q {right_x:.2f},{brow_y-average_radius*0.24:.2f} "
        f"{right_x+brow_width:.2f},{brow_y:.2f}"
    )
    brow_markup = (
        f'<path d="{left_brow}" class="ink hierarchy-brow" '
        f'style="stroke-width:{brow_stroke:.2f}"/>'
        f'<path d="{right_brow}" class="ink hierarchy-brow" '
        f'style="stroke-width:{brow_stroke:.2f}"/>'
    )

    hair_markup = ""
    seed = v20.stable_unit(f"{spec.name}:hierarchy-hair")
    if spec.role_choice.role != "hairline" and seed >= BALD_PROBABILITY:
        metrics = spec.role_choice.metrics
        if metrics.angularity >= 25.0:
            # A compact angular quiff for angular street geometry.
            hair_path = (
                f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
                f"L {centre_x-hair_half_width*0.38:.2f},{hair_base-hair_height:.2f} "
                f"L {centre_x:.2f},{hair_base-hair_height*0.42:.2f} "
                f"L {centre_x+hair_half_width*0.40:.2f},{hair_base-hair_height*0.88:.2f} "
                f"L {centre_x+hair_half_width:.2f},{hair_base:.2f}"
            )
        elif metrics.sinuosity >= 1.12:
            # A soft, shallow wave for sinuous geometry.
            hair_path = (
                f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
                f"Q {centre_x-hair_half_width*0.48:.2f},{hair_base-hair_height:.2f} "
                f"{centre_x:.2f},{hair_base-hair_height*0.38:.2f} "
                f"Q {centre_x+hair_half_width*0.46:.2f},{hair_base-hair_height*1.02:.2f} "
                f"{centre_x+hair_half_width:.2f},{hair_base:.2f}"
            )
        elif seed >= 0.68:
            # A side sweep supplies the name-seeded variation for simple roads.
            hair_path = (
                f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
                f"Q {centre_x-hair_half_width*0.05:.2f},{hair_base-hair_height:.2f} "
                f"{centre_x+hair_half_width:.2f},{hair_base-hair_height*0.30:.2f}"
            )
        else:
            # The default is a calm fringe, clearly broader than either brow.
            hair_path = (
                f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
                f"Q {centre_x:.2f},{hair_base-hair_height:.2f} "
                f"{centre_x+hair_half_width:.2f},{hair_base:.2f}"
            )
        hair_markup = (
            f'<path d="{hair_path}" class="ink hierarchy-hair" '
            f'style="stroke-width:{hair_stroke:.2f}"/>'
        )

    # Remove procedural top details that use the same thin-line vocabulary as
    # hair, then insert the dedicated hair/brow group immediately before eyes.
    cleaned = re.sub(r'\s*<path d="[^"]+" class="ink brow"/>', "", markup)
    cleaned = re.sub(r'\s*<path d="[^"]+" class="ink thin"/>', "", cleaned)
    eye_match = re.search(r'<circle cx="[-+]?\d*\.?\d+" cy="[-+]?\d*\.?\d+" r="[-+]?\d*\.?\d+" class="eye"/>', cleaned)
    if eye_match is None:
        return cleaned
    hierarchy = f'<g class="face-top-hierarchy">{hair_markup}{brow_markup}</g>'
    return cleaned[:eye_match.start()] + hierarchy + "\n      " + cleaned[eye_match.start():]


def render_grid(
    specs: object, out_svg: object, *args: object, **kwargs: object
) -> None:
    """Render v24 layout with a single explicit hair/brow/eye hierarchy."""
    def hierarchy_face_renderer(
        spec: object, *face_args: object, **face_kwargs: object
    ) -> str:
        markup = _base_face_renderer(spec, *face_args, **face_kwargs)
        return _hierarchy_markup(spec, markup)

    previous_face_renderer = v22._v21_render_face_svg
    v22._v21_render_face_svg = hierarchy_face_renderer
    try:
        _v24_render_grid(specs, out_svg, *args, **kwargs)
    finally:
        v22._v21_render_face_svg = previous_face_renderer


def _install_v26_policy() -> None:
    v20.__doc__ = __doc__
    v20.DEFAULT_SIGNATURE_ID = "street-face-generator-v26-private-mark"
    v20.provenance_payload = provenance_payload
    v20.render_attribution_provenance_mark = render_attribution_provenance_mark
    v20.render_grid = render_grid


_install_v26_policy()


def __getattr__(name: str) -> object:
    """Expose v25's compatible public API through the v26 module."""
    return getattr(v20, name)


def main() -> None:
    v20.main()


if __name__ == "__main__":
    main()
