"""Application-level, reusable style controls for canonical mug artwork."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path

from .rendering.artwork import WrapRenderOptions
from .rendering.context_map import ContextRenderOptions, REAR_STREET_HIGHLIGHT_SCALE
from .rendering.face import (
    FRONT_GROUP_SCALE,
    FRONT_GROUP_Y_OFFSET,
    FRONT_TITLE_LOCALITY_GAP_DELTA_PX,
    FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX,
    FaceRenderOptions,
    STREET_STROKE_MULTIPLIER,
)

DESIGN_WEIGHT_MIN = 0.25
REAR_HIGHLIGHT_WEIGHT_MIN = 0.25
DESIGN_WEIGHT_MAX = 4.00
REAR_HIGHLIGHT_WEIGHT_MAX = 4.00
DESIGN_WEIGHT_STEP = 0.05


@dataclass(frozen=True)
class DesignOptions:
    """Complete production presentation style.

    The two legacy weight fields remain relative multipliers.  The remaining
    values mirror the calibration helper's production renderer controls so one
    saved profile can drive previews, mockups and print exports consistently.
    """

    front_feature_weight: float = 1.0
    rear_highlight_weight: float = 1.0

    front_title_font_scale: float = 1.0
    front_locality_font_scale: float = 1.0
    front_title_locality_gap_delta: float = FRONT_TITLE_LOCALITY_GAP_DELTA_PX
    front_typography_block_y_offset: float = FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX
    front_facial_linework_multiplier: float = 1.0
    front_supporting_stroke_multiplier: float = 1.0
    front_vertical_spread: float = 1.0
    front_group_scale: float = FRONT_GROUP_SCALE
    front_group_y_offset: float = FRONT_GROUP_Y_OFFSET

    rear_attribution_line1_font_scale: float = 1.0
    rear_attribution_line2_font_scale: float = 1.0
    rear_attribution_line_spacing_scale: float = 1.0
    rear_attribution_line1_y_offset: float = 0.0
    rear_attribution_line2_y_offset: float = 0.0

    def __post_init__(self) -> None:
        limits = (
            ("Front street feature weight", self.front_feature_weight, DESIGN_WEIGHT_MIN, DESIGN_WEIGHT_MAX),
            ("Rear map highlight weight", self.rear_highlight_weight, REAR_HIGHLIGHT_WEIGHT_MIN, REAR_HIGHLIGHT_WEIGHT_MAX),
        )
        for name, value, minimum, maximum in limits:
            if not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum:.2f} and {maximum:.2f}.")

        positive = (
            ("Front title font scale", self.front_title_font_scale),
            ("Front locality font scale", self.front_locality_font_scale),
            ("Front facial linework multiplier", self.front_facial_linework_multiplier),
            ("Front supporting stroke multiplier", self.front_supporting_stroke_multiplier),
            ("Front vertical spread", self.front_vertical_spread),
            ("Front group scale", self.front_group_scale),
            ("Rear attribution line 1 font scale", self.rear_attribution_line1_font_scale),
            ("Rear attribution line 2 font scale", self.rear_attribution_line2_font_scale),
            ("Rear attribution line-spacing scale", self.rear_attribution_line_spacing_scale),
        )
        for name, value in positive:
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite.")

        for name, value in (
            ("Front title/locality gap", self.front_title_locality_gap_delta),
            ("Front typography block Y offset", self.front_typography_block_y_offset),
            ("Front group Y offset", self.front_group_y_offset),
            ("Rear attribution line 1 Y offset", self.rear_attribution_line1_y_offset),
            ("Rear attribution line 2 Y offset", self.rear_attribution_line2_y_offset),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite.")


def build_render_options(design: DesignOptions, *, area: str) -> WrapRenderOptions:
    """Translate one production style into the independent renderers."""
    return WrapRenderOptions(
        face_options=FaceRenderOptions(
            area=area,
            group_scale=design.front_group_scale,
            group_y_offset=design.front_group_y_offset,
            title_locality_gap_delta=design.front_title_locality_gap_delta,
            typography_block_y_offset=design.front_typography_block_y_offset,
            title_font_scale=design.front_title_font_scale,
            locality_font_scale=design.front_locality_font_scale,
            facial_linework_multiplier=design.front_facial_linework_multiplier,
            supporting_stroke_multiplier=design.front_supporting_stroke_multiplier,
            street_feature_stroke_multiplier=STREET_STROKE_MULTIPLIER * design.front_feature_weight,
            vertical_spread=design.front_vertical_spread,
        ),
        context_options=ContextRenderOptions(
            highlight_stroke_scale=REAR_STREET_HIGHLIGHT_SCALE * design.rear_highlight_weight,
            attribution_line1_font_scale=design.rear_attribution_line1_font_scale,
            attribution_line2_font_scale=design.rear_attribution_line2_font_scale,
            attribution_line_spacing_scale=design.rear_attribution_line_spacing_scale,
            attribution_line1_y_offset=design.rear_attribution_line1_y_offset,
            attribution_line2_y_offset=design.rear_attribution_line2_y_offset,
        ),
    )


def load_design_profile(path: Path | str) -> DesignOptions:
    """Load a calibration-helper JSON profile as production DesignOptions.

    Profiles created by the helper before the production-profile integration
    remain valid: missing rear/profile fields simply retain production defaults.
    """
    profile_path = Path(path)
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read production style profile {profile_path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("Production style profile must be a JSON object.")

    face = payload.get("face_render_options", {})
    rear = payload.get("rear_render_options", {})
    if not isinstance(face, dict) or not isinstance(rear, dict):
        raise ValueError("Production style profile renderer options must be JSON objects.")

    defaults = DesignOptions()

    # The helper stores the street multiplier in the face renderer's absolute
    # units; DesignOptions retains the historical relative weight.
    absolute_street = float(
        face.get("street_feature_stroke_multiplier", STREET_STROKE_MULTIPLIER)
    )
    front_weight = absolute_street / STREET_STROKE_MULTIPLIER

    return DesignOptions(
        front_feature_weight=front_weight,
        rear_highlight_weight=float(
            payload.get(
                "rear_highlight_weight",
                (
                    float(rear.get("highlight_stroke_scale", REAR_STREET_HIGHLIGHT_SCALE))
                    / REAR_STREET_HIGHLIGHT_SCALE
                ),
            )
        ),
        front_title_font_scale=float(face.get("title_font_scale", defaults.front_title_font_scale)),
        front_locality_font_scale=float(face.get("locality_font_scale", defaults.front_locality_font_scale)),
        front_title_locality_gap_delta=float(face.get("title_locality_gap_delta", defaults.front_title_locality_gap_delta)),
        front_typography_block_y_offset=float(face.get("typography_block_y_offset", defaults.front_typography_block_y_offset)),
        front_facial_linework_multiplier=float(face.get("facial_linework_multiplier", defaults.front_facial_linework_multiplier)),
        front_supporting_stroke_multiplier=float(face.get("supporting_stroke_multiplier", defaults.front_supporting_stroke_multiplier)),
        front_vertical_spread=float(face.get("vertical_spread", defaults.front_vertical_spread)),
        front_group_scale=float(face.get("group_scale", defaults.front_group_scale)),
        front_group_y_offset=float(face.get("group_y_offset", defaults.front_group_y_offset)),
        rear_attribution_line1_font_scale=float(
            rear.get(
                "attribution_line1_font_scale",
                rear.get("attribution_font_scale", defaults.rear_attribution_line1_font_scale),
            )
        ),
        rear_attribution_line2_font_scale=float(
            rear.get(
                "attribution_line2_font_scale",
                rear.get("attribution_font_scale", defaults.rear_attribution_line2_font_scale),
            )
        ),
        rear_attribution_line_spacing_scale=float(
            rear.get("attribution_line_spacing_scale", defaults.rear_attribution_line_spacing_scale)
        ),
        rear_attribution_line1_y_offset=float(
            rear.get("attribution_line1_y_offset", defaults.rear_attribution_line1_y_offset)
        ),
        rear_attribution_line2_y_offset=float(
            rear.get("attribution_line2_y_offset", defaults.rear_attribution_line2_y_offset)
        ),
    )


def design_profile_fingerprint(options: DesignOptions | None) -> str:
    """Stable short fingerprint used to invalidate stale PNG/mockup outputs."""
    resolved = options or DesignOptions()
    canonical = json.dumps(asdict(resolved), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def design_profile_summary(options: DesignOptions | None) -> str:
    resolved = options or DesignOptions()
    return (
        f"Front text {resolved.front_title_font_scale:.2f}×/"
        f"{resolved.front_locality_font_scale:.2f}× · "
        f"linework {resolved.front_facial_linework_multiplier:.2f}× · "
        f"street {resolved.front_feature_weight:.2f}× · "
        f"Rear text {resolved.rear_attribution_line1_font_scale:.2f}×/"
        f"{resolved.rear_attribution_line2_font_scale:.2f}× · "
        f"spacing {resolved.rear_attribution_line_spacing_scale:.2f}×"
    )
