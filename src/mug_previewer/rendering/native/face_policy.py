"""Street Face Generator v28.1 visual policy.

V28.1 retains V27's deterministic role selection and source-street geometry.
It refines the V28 face with role-aware optical placement and printable
supporting-line weights; V27 remains untouched.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from . import facial_hierarchy as v27


v20 = v27.v20
_V27_HIERARCHY = v27._hierarchy_markup

# Kept here rather than in the mug renderer so the native face SVG remains a
# useful V28.1 asset outside the mug toolkit too.
# V28.1's named optical controls. Keep these local to V28: importing V27
# must continue to produce the legacy artwork byte-for-byte.
V28_MOUTH_Y_OFFSET_RATIO = -0.060
V28_NOSE_STROKE_MULTIPLIER = 1.18
V28_EAR_STROKE_MULTIPLIER = 1.18
V28_BASE_FEATURE_STROKE_WIDTH = 1.42
V28_HAIR_WIDTH_MULTIPLIER = 0.90
V28_HAIR_STROKE_MULTIPLIER = 0.95
V28_BROW_STROKE_MULTIPLIER = 1.00


@dataclass(frozen=True)
class V28RoleAdjustment:
    """Role-specific optical placement that never reshapes source paths."""

    x_offset_ratio: float = 0.0
    y_offset_ratio: float = 0.0
    scale_multiplier: float = 1.0
    stroke_multiplier: float = 1.0


# Values are fractions of the native face layout height.  The mouth moves up
# 6%, while the remaining role entries make independent future tuning explicit.
V28_ROLE_ADJUSTMENTS = {
    "mouth": V28RoleAdjustment(y_offset_ratio=V28_MOUTH_Y_OFFSET_RATIO),
    "nose": V28RoleAdjustment(),
    "hairline": V28RoleAdjustment(),
    "brow": V28RoleAdjustment(),
}


def get_v28_role_adjustment(role: str) -> V28RoleAdjustment:
    """Return the deterministic V28.1 optical policy for one street role."""
    return V28_ROLE_ADJUSTMENTS.get(role, V28RoleAdjustment())


def provenance_payload(area_name: str, generated_on: Optional[str] = None) -> str:
    """Build a V28 provenance payload while retaining the checksum scheme."""
    area = re.sub(r"\s+", " ", str(area_name or "unknown area").strip())
    generated = generated_on or date.today().isoformat()
    base = f"SFV28|AREA={area}|DATE={generated}"
    checksum = hashlib.sha256(
        f"{v20.signature_secret_id()}|{base}".encode("utf-8", errors="replace")
    ).hexdigest()[:10].upper()
    return f"{base}|CHK={checksum}"


def render_attribution_provenance_mark(*args: object, **kwargs: object) -> str:
    return v27.render_attribution_provenance_mark(*args, **kwargs).replace(
        "street-face-v27", "street-face-v28"
    )


def _scaled_inline_stroke(markup: str, class_name: str, multiplier: float) -> str:
    pattern = rf'(class="ink {class_name}"\s+style="stroke-width:)([0-9.]+)'

    def replace(match: re.Match[str]) -> str:
        return f"{match.group(1)}{float(match.group(2)) * multiplier:.2f}"

    return re.sub(pattern, replace, markup)


def _face_layout_height(markup: str) -> float:
    """Read the native face card height from its own SVG markup."""
    card = re.search(
        r'<rect\b(?=[^>]*\bclass="card-frame")[^>]*\bheight="([0-9.]+)"',
        markup,
    )
    return float(card.group(1)) if card is not None else 0.0


def _append_svg_class(tag: str, class_name: str) -> str:
    classes = re.search(r'\bclass="([^"]*)"', tag)
    if classes is None:
        return tag[:-2] + f' class="{class_name}"/>' if tag.endswith("/>") else tag[:-1] + f' class="{class_name}">'
    existing = classes.group(1).split()
    if class_name in existing:
        return tag
    return tag[:classes.start(1)] + " ".join([*existing, class_name]) + tag[classes.end(1):]


def _with_feature_stroke(tag: str, multiplier: float) -> str:
    """Set the V27 feature stroke at a named V28.1 multiplier."""
    width = V28_BASE_FEATURE_STROKE_WIDTH * multiplier
    style = re.search(r'\bstyle="([^"]*)"', tag)
    if style is not None:
        revised = re.sub(r'stroke-width\s*:\s*[^;]+;?', '', style.group(1)).strip()
        return tag[:style.start(1)] + f"{revised} stroke-width:{width:.2f};" + tag[style.end(1):]
    insert_at = -2 if tag.endswith('/>') else -1
    return tag[:insert_at] + f' style="stroke-width:{width:.2f};"' + tag[insert_at:]


def _append_y_translation(tag: str, y_offset: float) -> str:
    if not y_offset:
        return tag
    translation = f"translate(0 {y_offset:.2f})"
    transform = re.search(r'\btransform="([^"]*)"', tag)
    if transform is not None:
        return tag[:transform.start(1)] + f"{transform.group(1)} {translation}" + tag[transform.end(1):]
    return tag[:-2] + f' transform="{translation}"/>' if tag.endswith("/>") else tag[:-1] + f' transform="{translation}">'


def _v28_role_adjusted_markup(spec: object, markup: str) -> str:
    """Apply only the role-specific V28.1 placement adjustment."""
    adjustment = get_v28_role_adjustment(spec.role_choice.role)
    card_height = _face_layout_height(markup)
    if card_height <= 0.0:
        return markup

    street_shift = card_height * adjustment.y_offset_ratio

    # The base renderer identifies ears and the drawn nose as feature paths.
    # Give only those marks V28-specific classes so neither eyes nor street
    # geometry inherit the heavier printable supporting-line treatment.
    feature_index = 0

    def support_path(match: re.Match[str]) -> str:
        nonlocal feature_index
        is_ear = feature_index < 2
        feature_index += 1
        tag = _append_svg_class(match.group(0), "v28-support")
        tag = _append_svg_class(tag, "v28-ear" if is_ear else "v28-nose")
        multiplier = V28_EAR_STROKE_MULTIPLIER if is_ear else V28_NOSE_STROKE_MULTIPLIER
        return _with_feature_stroke(tag, multiplier)

    markup = re.sub(r'<path\b[^>]*\bclass="ink feature(?: thin)?"[^>]*/>', support_path, markup)
    if adjustment.y_offset_ratio:
        markup = re.sub(
            r'<polyline\b[^>]*\bclass="street"[^>]*/>',
            lambda match: _append_y_translation(match.group(0), street_shift),
            markup,
        )
    return markup

def _v28_hierarchy_markup(spec: object, markup: str) -> str:
    """Apply restrained V28.1 print and role-aware placement refinements."""
    refined = _V27_HIERARCHY(spec, markup)
    refined = _scale_hair_width(refined, V28_HAIR_WIDTH_MULTIPLIER)
    refined = _scaled_inline_stroke(refined, "hierarchy-hair", V28_HAIR_STROKE_MULTIPLIER)
    refined = _scaled_inline_stroke(refined, "hierarchy-brow", V28_BROW_STROKE_MULTIPLIER)
    # A mouth street is the mouth. Soft details are V20's optional lip/chin
    # decoration and visually compete with that idea on ceramic at small size.
    if spec.role_choice.role == "mouth":
        refined = re.sub(r'\s*<path d="[^"]+" class="soft-detail"/>', "", refined)
    refined = _v28_role_adjusted_markup(spec, refined)
    return refined.replace(
        'class="face-content"', 'class="face-content v28-face-linework"', 1
    )


def _scale_hair_width(markup: str, multiplier: float) -> str:
    """Retract hierarchy hair about its centre without changing its rhythm."""
    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        d_match = re.search(r'\bd="([^"]+)"', tag)
        if d_match is None:
            return tag
        values = [float(value) for value in re.findall(r"[-+]?\d*\.?\d+", d_match.group(1))]
        if len(values) < 4:
            return tag
        centre = (min(values[0::2]) + max(values[0::2])) / 2
        position = 0
        def scale_number(number: re.Match[str]) -> str:
            nonlocal position
            value = float(number.group(0))
            if position % 2 == 0:
                value = centre + (value - centre) * multiplier
            position += 1
            return f"{value:.2f}"
        scaled_d = re.sub(r"[-+]?\d*\.?\d+", scale_number, d_match.group(1))
        return tag[:d_match.start(1)] + scaled_d + tag[d_match.end(1):]

    return re.sub(r'<path\b[^>]*\bclass="ink hierarchy-hair"[^>]*/>', replace, markup)


def render_grid(specs: object, out_svg: object, *args: object, **kwargs: object) -> None:
    """Render V27 geometry with V28.1's selective optical policy."""
    previous_hierarchy = v27._hierarchy_markup
    v27._hierarchy_markup = _v28_hierarchy_markup
    try:
        v27.render_grid(specs, out_svg, *args, **kwargs)
    finally:
        v27._hierarchy_markup = previous_hierarchy


def _install_v28_policy() -> None:
    v20.__doc__ = __doc__
    v20.DEFAULT_SIGNATURE_ID = "street-face-generator-v28-private-mark"
    v20.provenance_payload = provenance_payload
    v20.render_attribution_provenance_mark = render_attribution_provenance_mark
    v20.render_grid = render_grid


_install_v28_policy()


def __getattr__(name: str) -> object:
    """Expose V27's compatible public API through the V28 module."""
    return getattr(v20, name)


def main() -> None:
    v20.main()


if __name__ == "__main__":
    main()