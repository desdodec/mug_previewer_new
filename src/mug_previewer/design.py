"""Application-level, reusable style controls for canonical mug artwork."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .rendering.artwork import WrapRenderOptions
from .rendering.context_map import ContextRenderOptions, REAR_STREET_HIGHLIGHT_SCALE
from .rendering.face import FaceRenderOptions, STREET_STROKE_MULTIPLIER

DESIGN_WEIGHT_MIN = 0.25
REAR_HIGHLIGHT_WEIGHT_MIN = 0.25
DESIGN_WEIGHT_MAX = 1.50
DESIGN_WEIGHT_STEP = 0.05


@dataclass(frozen=True)
class DesignOptions:
    """User-selectable style multipliers; automatic composition stays renderer-owned.

    A value of ``1.0`` always represents the validated production appearance.
    """

    front_feature_weight: float = 1.0
    rear_highlight_weight: float = 1.0

    def __post_init__(self) -> None:
        limits = (
            ("Front street feature weight", self.front_feature_weight, DESIGN_WEIGHT_MIN),
            ("Rear map highlight weight", self.rear_highlight_weight, REAR_HIGHLIGHT_WEIGHT_MIN),
        )
        for name, value, minimum in limits:
            if not math.isfinite(value) or not minimum <= value <= DESIGN_WEIGHT_MAX:
                raise ValueError(
                    f"{name} must be between {minimum:.2f} and {DESIGN_WEIGHT_MAX:.2f}."
                )


def build_render_options(design: DesignOptions, *, area: str) -> WrapRenderOptions:
    """Translate UI-independent style multipliers to existing renderer options."""
    return WrapRenderOptions(
        face_options=FaceRenderOptions(
            area=area,
            street_feature_stroke_multiplier=STREET_STROKE_MULTIPLIER * design.front_feature_weight,
        ),
        context_options=ContextRenderOptions(
            highlight_stroke_scale=REAR_STREET_HIGHLIGHT_SCALE * design.rear_highlight_weight,
        ),
    )
