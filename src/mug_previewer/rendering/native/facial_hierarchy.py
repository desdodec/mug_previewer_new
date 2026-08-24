"""
Street Face Generator v27.

V27 keeps V26's clear facial hierarchy while moving the hair into its own
generous lane: hair -> brows -> eyes.  Hair is raised by approximately one
eye diameter above the brow lane.  The intensity and number of hair waves are
now driven by the measured sinuosity of the extracted street geometry.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import List, Optional, Sequence, Tuple

from . import hierarchy_policy as v26


v20 = v26.v20
v22 = v26.v22
v24 = v26.v24
v21 = v22.v21
BALD_PROBABILITY = v26.BALD_PROBABILITY
_v24_render_grid = v26._v24_render_grid
_base_face_renderer = v26._base_face_renderer

# The standard nose box is intentionally tall, but narrow road geometries are
# width-limited when fitted into it.  Without a cap they can become longer
# than the face marks around them.
NARROW_NOSE_MAX_ASPECT = 0.75
NARROW_NOSE_MIN_HEIGHT_OF_TARGET = 0.20
NARROW_NOSE_MAX_HEIGHT_OF_TARGET = 0.27


def provenance_payload(area_name: str, generated_on: Optional[str] = None) -> str:
    """Build a v27 provenance payload while retaining the checksum scheme."""
    area = re.sub(r"\s+", " ", str(area_name or "unknown area").strip())
    generated = generated_on or date.today().isoformat()
    base = f"SFV27|AREA={area}|DATE={generated}"
    checksum = hashlib.sha256(
        f"{v20.signature_secret_id()}|{base}".encode("utf-8", errors="replace")
    ).hexdigest()[:10].upper()
    return f"{base}|CHK={checksum}"


def render_attribution_provenance_mark(*args: object, **kwargs: object) -> str:
    markup = v26.render_attribution_provenance_mark(*args, **kwargs)
    return markup.replace("street-face-v26", "street-face-v27")


def _eye_anchors(markup: str) -> Optional[List[Tuple[float, float, float]]]:
    matches = re.findall(
        r'<circle cx="([-+]?\d*\.?\d+)" cy="([-+]?\d*\.?\d+)" r="([-+]?\d*\.?\d+)" class="eye"/>',
        markup,
    )
    if len(matches) < 2:
        return None
    return [(float(x), float(y), float(radius)) for x, y, radius in matches[:2]]


def _sinuosity_strength(sinuosity: float) -> float:
    """Normalise ordinary-to-highly-winding streets to a usable 0--1 range."""
    return max(0.0, min(1.0, (float(sinuosity) - 1.02) / 0.18))


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

    # Brows remain close enough to read with the eyes.  The hair baseline is
    # one full eye diameter above that lane, giving the top of the head room.
    brow_y = eye_top - max(average_radius * 0.48, 2.2)
    hair_base = brow_y - max(average_radius * 2.0, 8.0)
    brow_width = max(average_radius * 1.30, eye_span * 0.11)
    hair_half_width = max(eye_span * 0.65, average_radius * 3.7)
    hair_height = max(average_radius * 1.55, 7.5)
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
        sinuous = _sinuosity_strength(metrics.sinuosity)
        if sinuous >= 0.68:
            # Highly winding streets become a three-rhythm, lively hairline.
            hair_path = (
                f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
                f"Q {centre_x-hair_half_width*0.78:.2f},{hair_base-hair_height*(0.38+sinuous*.34):.2f} "
                f"{centre_x-hair_half_width*0.42:.2f},{hair_base-hair_height*(0.22+sinuous*.22):.2f} "
                f"Q {centre_x-hair_half_width*0.12:.2f},{hair_base-hair_height*(0.98+sinuous*.22):.2f} "
                f"{centre_x+hair_half_width*0.16:.2f},{hair_base-hair_height*(0.42+sinuous*.20):.2f} "
                f"Q {centre_x+hair_half_width*0.53:.2f},{hair_base-hair_height*(0.98+sinuous*.20):.2f} "
                f"{centre_x+hair_half_width:.2f},{hair_base:.2f}"
            )
        elif sinuous >= 0.25:
            # Moderate winding makes a broad, two-beat wave; amplitude scales
            # continuously with sinuosity rather than crossing one hard style boundary.
            crest = 0.62 + sinuous * 0.56
            dip = 0.18 + sinuous * 0.28
            hair_path = (
                f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
                f"Q {centre_x-hair_half_width*0.53:.2f},{hair_base-hair_height*crest:.2f} "
                f"{centre_x-hair_half_width*0.10:.2f},{hair_base-hair_height*dip:.2f} "
                f"Q {centre_x+hair_half_width*0.40:.2f},{hair_base-hair_height*(crest+0.16):.2f} "
                f"{centre_x+hair_half_width:.2f},{hair_base:.2f}"
            )
        elif metrics.angularity >= 25.0:
            # Angular geometry retains a restrained, graphic quiff.
            hair_path = (
                f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
                f"L {centre_x-hair_half_width*0.38:.2f},{hair_base-hair_height:.2f} "
                f"L {centre_x:.2f},{hair_base-hair_height*0.42:.2f} "
                f"L {centre_x+hair_half_width*0.40:.2f},{hair_base-hair_height*0.88:.2f} "
                f"L {centre_x+hair_half_width:.2f},{hair_base:.2f}"
            )
        elif seed >= 0.68:
            hair_path = (
                f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
                f"Q {centre_x-hair_half_width*0.05:.2f},{hair_base-hair_height:.2f} "
                f"{centre_x+hair_half_width:.2f},{hair_base-hair_height*0.30:.2f}"
            )
        else:
            hair_path = (
                f"M {centre_x-hair_half_width:.2f},{hair_base:.2f} "
                f"Q {centre_x:.2f},{hair_base-hair_height:.2f} "
                f"{centre_x+hair_half_width:.2f},{hair_base:.2f}"
            )
        hair_markup = (
            f'<path d="{hair_path}" class="ink hierarchy-hair" '
            f'style="stroke-width:{hair_stroke:.2f}"/>'
        )

    cleaned = re.sub(r'\s*<path d="[^"]+" class="ink brow"/>', "", markup)
    cleaned = re.sub(r'\s*<path d="[^"]+" class="ink thin"/>', "", cleaned)
    eye_match = re.search(
        r'<circle cx="[-+]?\d*\.?\d+" cy="[-+]?\d*\.?\d+" r="[-+]?\d*\.?\d+" class="eye"/>',
        cleaned,
    )
    if eye_match is None:
        return cleaned
    hierarchy = f'<g class="face-top-hierarchy">{hair_markup}{brow_markup}</g>'
    return cleaned[:eye_match.start()] + hierarchy + "\n      " + cleaned[eye_match.start():]


def _compact_narrow_nose_target(
    target: Tuple[float, float, float, float], aspect: float
) -> Tuple[float, float, float, float]:
    """Scale narrow noses to their geometry while retaining proportions."""
    x, y, width, height = target
    # The thinnest roads need the smallest nose box.  As a road approaches a
    # conventional nose aspect, progressively allow it more visual presence.
    aspect_progress = max(
        0.0,
        min(1.0, (aspect - 0.35) / (NARROW_NOSE_MAX_ASPECT - 0.35)),
    )
    height_budget = (
        NARROW_NOSE_MIN_HEIGHT_OF_TARGET
        + (NARROW_NOSE_MAX_HEIGHT_OF_TARGET - NARROW_NOSE_MIN_HEIGHT_OF_TARGET)
        * aspect_progress
    )
    capped_width = min(
        width,
        height * height_budget * max(aspect, 1e-9),
    )
    return x + (width - capped_width) / 2, y, capped_width, height


def render_grid(specs: object, out_svg: object, *args: object, **kwargs: object) -> None:
    """Render V24 layout with V27's hierarchy and compact U-mouth placement."""
    def hierarchy_face_renderer(spec: object, *face_args: object, **face_kwargs: object) -> str:
        return _hierarchy_markup(spec, _base_face_renderer(spec, *face_args, **face_kwargs))

    previous_face_renderer = v22._v21_render_face_svg
    previous_fitter = v20.fit_paths_to_feature_box
    compact_u_mouth_paths = {
        id(spec.original_paths)
        for spec in specs  # type: ignore[union-attr]
        if v21._uses_compact_u_mouth_placement(spec)
    }
    narrow_nose_aspects = {
        id(spec.original_paths): float(spec.role_choice.metrics.aspect)
        for spec in specs  # type: ignore[union-attr]
        if (
            spec.role_choice.role == "nose"
            and spec.role_choice.metrics.aspect < NARROW_NOSE_MAX_ASPECT
        )
    }

    def fit_with_compact_u_mouth(
        paths: Sequence[Sequence[v20.Point]],
        rotation: float,
        target: Tuple[float, float, float, float],
    ) -> Tuple[List[List[v20.Point]], str]:
        if id(paths) in compact_u_mouth_paths:
            target = v21._compact_u_mouth_target(target)
        elif id(paths) in narrow_nose_aspects:
            target = _compact_narrow_nose_target(
                target, narrow_nose_aspects[id(paths)]
            )
        return previous_fitter(paths, rotation, target)

    v22._v21_render_face_svg = hierarchy_face_renderer
    v20.fit_paths_to_feature_box = fit_with_compact_u_mouth
    try:
        _v24_render_grid(specs, out_svg, *args, **kwargs)
    finally:
        v22._v21_render_face_svg = previous_face_renderer
        v20.fit_paths_to_feature_box = previous_fitter


def _install_v27_policy() -> None:
    v20.__doc__ = __doc__
    v20.DEFAULT_SIGNATURE_ID = "street-face-generator-v27-private-mark"
    v20.provenance_payload = provenance_payload
    v20.render_attribution_provenance_mark = render_attribution_provenance_mark
    v20.choose_role_and_rotation = v21.choose_role_and_rotation
    # Keep the Add Street control active for every sort mode, including the
    # gallery spectrum selector.  Later rendering layers must not silently
    # fall back to an earlier curation policy.
    v20.curate_specs = v21.curate_specs
    v20.curate_specs_with_forced_street = v21.curate_specs_with_forced_street
    v20.render_grid = render_grid


_install_v27_policy()


def __getattr__(name: str) -> object:
    """Expose V26's compatible public API through the V27 module."""
    return getattr(v20, name)


def main() -> None:
    v20.main()


if __name__ == "__main__":
    main()
