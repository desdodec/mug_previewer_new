"""
Street Face Generator v22.

V22 preserves v21's street-role hierarchy and stroke treatment.  It changes
gallery layout so each completed face (sacred street plus facial marks) is
centred as one group in its card.  This makes the visual centre stable when a
glyph-folder gallery is regenerated with a different number of columns.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Optional

from . import role_policy as v21


v20 = v21.v20

STREET_STROKE_DEFAULT = v21.STREET_STROKE_DEFAULT
STREET_STROKE_REFERENCE_CLAMP = v21.STREET_STROKE_REFERENCE_CLAMP
STREET_STROKE_REFERENCE_FACTOR = v21.STREET_STROKE_REFERENCE_FACTOR

_v20_render_grid = v21._v20_render_grid
_v21_render_face_svg = v21.render_face_svg
_v21_render_provenance = v21.render_attribution_provenance_mark


def provenance_payload(area_name: str, generated_on: Optional[str] = None) -> str:
    """Build the v22 provenance payload using the existing checksum scheme."""
    area = re.sub(r"\s+", " ", str(area_name or "unknown area").strip())
    generated = generated_on or date.today().isoformat()
    base = f"SFV22|AREA={area}|DATE={generated}"
    checksum = hashlib.sha256(
        f"{v20.signature_secret_id()}|{base}".encode("utf-8", errors="replace")
    ).hexdigest()[:10].upper()
    return f"{base}|CHK={checksum}"


def render_attribution_provenance_mark(*args: object, **kwargs: object) -> str:
    markup = _v21_render_provenance(*args, **kwargs)
    return markup.replace("street-face-v21", "street-face-v22")


def render_grid(
    specs: object,
    out_svg: object,
    *args: object,
    street_stroke_multiplier: float = STREET_STROKE_DEFAULT,
    **kwargs: object,
) -> None:
    """Render each face around a card-centred visual bounding box.

    The prior gallery renderer positioned the street in a role-specific target
    box and left the final composite at that position.  As the number of
    columns changes, card proportions change too, so that target can make an
    otherwise identical face appear to drift.  The original renderer already
    has robust bounds-aware centring for single prints; use it for every card.
    """
    requested = max(0.05, float(street_stroke_multiplier))
    previous_renderer = v20.render_face_svg

    def centred_scaled_renderer(
        spec: v20.FaceSpec, *face_args: object, **face_kwargs: object
    ) -> str:
        inherited_width = float(face_kwargs.get("street_stroke_multiplier", 1.0))
        inherited_scale = max(1.0, v20.clamp(requested, 0.90, 1.10))
        hierarchy_base = inherited_width / inherited_scale
        reference_width = max(
            2.0,
            hierarchy_base
            * STREET_STROKE_REFERENCE_CLAMP
            * STREET_STROKE_REFERENCE_FACTOR,
        )
        exact_width = reference_width * requested / STREET_STROKE_DEFAULT

        # Anchor both the feature construction and its completed visual bounds
        # to the card.  This keeps all layouts, including 6- and 9-column
        # glyph-folder galleries, centred by the same rule.
        face_kwargs = dict(face_kwargs)
        face_kwargs["center_face"] = True
        face_kwargs["auto_center_content"] = True
        markup = _v21_render_face_svg(spec, *face_args, **face_kwargs)
        return re.sub(
            r'(<polyline\b[^>]*\bclass="street"[^>]*style="stroke-width:)[0-9.]+',
            lambda match: f"{match.group(1)}{exact_width:.3f}",
            markup,
        )

    v20.render_face_svg = centred_scaled_renderer
    try:
        _v20_render_grid(
            specs,
            out_svg,
            *args,
            street_stroke_multiplier=requested,
            **kwargs,
        )
    finally:
        v20.render_face_svg = previous_renderer


def _install_v22_policy() -> None:
    v20.__doc__ = __doc__
    v20.DEFAULT_SIGNATURE_ID = "street-face-generator-v22-private-mark"
    v20.provenance_payload = provenance_payload
    v20.render_attribution_provenance_mark = render_attribution_provenance_mark
    v20.render_face_svg = v21.render_face_svg
    v20.render_grid = render_grid


_install_v22_policy()


def __getattr__(name: str) -> object:
    """Expose v21's compatible public API through the v22 module."""
    return getattr(v20, name)


def main() -> None:
    v20.main()


if __name__ == "__main__":
    main()
