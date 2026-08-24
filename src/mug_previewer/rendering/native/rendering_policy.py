"""
Street Face Generator v24.

V24 retains v23's compact output naming and session reports.  It corrects the
single-face A2 composition by preventing the reference gallery canvas from
growing taller than the real face card, and omits the ambiguous decorative
crown arcs that read as detached oversized eyebrows.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Optional

from . import metadata_policy as v23


v20 = v23.v20
v22 = v23.v22

SINGLE_FACE_MAX_LAYOUT_WIDTH_FRACTION = 0.92
SINGLE_FACE_MAX_LAYOUT_HEIGHT_FRACTION = 0.82
BROW_LAYOUT_DOWNWARD_FRACTION = 0.080
BROW_MAX_DOWNWARD_OFFSET = 110.0

_v23_render_grid = v20.render_grid


def _lower_brows(markup: str, offset: float) -> str:
    """Move only the rendered brow paths downward, preserving their shape."""
    if offset <= 0:
        return markup

    def move_path(match: re.Match[str]) -> str:
        path_data = match.group(2)
        moved_data = re.sub(
            r"(?<=,)([-+]?\d*\.?\d+)",
            lambda value: f"{float(value.group(1)) + offset:.2f}",
            path_data,
        )
        return f"{match.group(1)}{moved_data}{match.group(3)}"

    return re.sub(
        r'(<path d=")([^"]+)(" class="ink brow"/>)',
        move_path,
        markup,
    )


def provenance_payload(area_name: str, generated_on: Optional[str] = None) -> str:
    """Build a v24 provenance payload while retaining the checksum scheme."""
    area = re.sub(r"\s+", " ", str(area_name or "unknown area").strip())
    generated = generated_on or date.today().isoformat()
    base = f"SFV24|AREA={area}|DATE={generated}"
    checksum = hashlib.sha256(
        f"{v20.signature_secret_id()}|{base}".encode("utf-8", errors="replace")
    ).hexdigest()[:10].upper()
    return f"{base}|CHK={checksum}"


def render_attribution_provenance_mark(*args: object, **kwargs: object) -> str:
    markup = v23.render_attribution_provenance_mark(*args, **kwargs)
    return markup.replace("street-face-v23", "street-face-v24")


def render_grid(
    specs: object, out_svg: object, *args: object, **kwargs: object
) -> None:
    """Cap the single-face reference canvas to the actual printable card.

    V20's A2 single-print sizing multiplies a gallery card by the A2/A6 paper
    scale.  Its width is sensible, but its height can exceed the real card by
    more than twice.  The face renderer then uses that excessive height for
    eyebrow, hair and ear offsets.  Limit both dimensions before the original
    renderer calculates the feature positions.
    """
    # The optional feminine crown is drawn from two very shallow arcs.  With no
    # enclosing head outline it is consistently read as a second, detached pair
    # of brows.  Disable just that optional detail; normal brows, lashes, lips,
    # ears and presentation variation are unaffected.
    original_stable_unit = v20.stable_unit
    original_face_renderer = v22._v21_render_face_svg
    single_svg_mode = bool(kwargs.get("single_svg_mode", False))

    def stable_unit_without_crown(key: object) -> float:
        if str(key).endswith(":hair"):
            return 0.0
        return original_stable_unit(key)

    def face_renderer_with_compact_brows(
        spec: object, *face_args: object, **face_kwargs: object
    ) -> str:
        markup = original_face_renderer(spec, *face_args, **face_kwargs)
        if not single_svg_mode or len(face_args) < 4:
            return markup
        face_layout_height = float(face_args[3])
        # Brows occupy the lane immediately above the eyes.  This keeps them
        # readable as brows in both gallery cards and oversized single prints,
        # rather than drifting into the hairline area.
        offset = min(
            BROW_MAX_DOWNWARD_OFFSET,
            face_layout_height * BROW_LAYOUT_DOWNWARD_FRACTION,
        )
        return _lower_brows(markup, offset)

    v20.stable_unit = stable_unit_without_crown
    v22._v21_render_face_svg = face_renderer_with_compact_brows
    if not single_svg_mode:
        try:
            _v23_render_grid(specs, out_svg, *args, **kwargs)
        finally:
            v20.stable_unit = original_stable_unit
            v22._v21_render_face_svg = original_face_renderer
        return

    original_choose_grid = v20.choose_grid
    spec_count = len(specs)  # type: ignore[arg-type]
    paper_key = str(kwargs.get("paper_key", "a1"))
    paper = v20.PAPER_PRESETS.get(paper_key, v20.PAPER_PRESETS["a1"])
    reference_paper = v20.PAPER_PRESETS[v20.SINGLE_FACE_SCALE_REFERENCE_PAPER]
    paper_scale = float(paper["width_mm"]) / float(reference_paper["width_mm"])
    actual_card: Optional[tuple[float, float]] = None

    def capped_choose_grid(
        count: int,
        requested_paper: dict,
        requested_cols: Optional[int] = None,
        reserve_note: bool = True,
        reserve_title: bool = False,
        note_height_fraction: float = 0.035,
    ) -> tuple[int, int, float, float, float]:
        nonlocal actual_card
        result = original_choose_grid(
            count,
            requested_paper,
            requested_cols,
            reserve_note,
            reserve_title,
            note_height_fraction,
        )
        if count == spec_count:
            actual_card = (result[2], result[3])
            return result
        if (
            actual_card is not None
            and count == 36
            and requested_cols == 9
            and requested_paper is v20.PAPER_PRESETS["a2"]
        ):
            max_reference_w = actual_card[0] * SINGLE_FACE_MAX_LAYOUT_WIDTH_FRACTION / paper_scale
            max_reference_h = actual_card[1] * SINGLE_FACE_MAX_LAYOUT_HEIGHT_FRACTION / paper_scale
            return (
                result[0],
                result[1],
                min(result[2], max_reference_w),
                min(result[3], max_reference_h),
                result[4],
            )
        return result

    v20.choose_grid = capped_choose_grid
    try:
        _v23_render_grid(specs, out_svg, *args, **kwargs)
    finally:
        v20.choose_grid = original_choose_grid
        v20.stable_unit = original_stable_unit
        v22._v21_render_face_svg = original_face_renderer


def _install_v24_policy() -> None:
    v20.__doc__ = __doc__
    v20.DEFAULT_SIGNATURE_ID = "street-face-generator-v24-private-mark"
    v20.provenance_payload = provenance_payload
    v20.render_attribution_provenance_mark = render_attribution_provenance_mark
    v20.render_grid = render_grid


_install_v24_policy()


def __getattr__(name: str) -> object:
    """Expose v23's compatible public API through the v24 module."""
    return getattr(v20, name)


def main() -> None:
    v20.main()


if __name__ == "__main__":
    main()
