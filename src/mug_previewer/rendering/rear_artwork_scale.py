"""Production-style scaling metadata for the completed rear artwork group.

The rear renderer already owns map framing, attribution layout and native
high-resolution rasterisation.  This module adds one presentation-only scale
that is applied later by the provider compositor, so geography and map detail
remain unchanged while the complete rear group can occupy more physical space
on the mug.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import math
from typing import Any


REAR_ARTWORK_SCALE_INFO_KEY = "mug_previewer_rear_artwork_scale"
REAR_ARTWORK_SCALE_MIN = 0.50
REAR_ARTWORK_SCALE_MAX = 1.50


def _validated_scale(value: object) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Rear artwork scale must be numeric.") from error
    if not math.isfinite(scale) or not REAR_ARTWORK_SCALE_MIN <= scale <= REAR_ARTWORK_SCALE_MAX:
        raise ValueError(
            f"Rear artwork scale must be between {REAR_ARTWORK_SCALE_MIN:.2f} "
            f"and {REAR_ARTWORK_SCALE_MAX:.2f}."
        )
    return scale


def install(context: Any) -> None:
    """Extend ContextRenderOptions and stamp the completed rear image once."""
    if getattr(context, "_rear_artwork_scale_installed", False):
        return

    original_options = context.ContextRenderOptions

    @dataclass(frozen=True)
    class ContextRenderOptions(original_options):
        artwork_scale: float = 1.0

        def __post_init__(self) -> None:
            _validated_scale(self.artwork_scale)

    ContextRenderOptions.__name__ = "ContextRenderOptions"
    ContextRenderOptions.__qualname__ = "ContextRenderOptions"
    ContextRenderOptions.__module__ = context.__name__
    context.ContextRenderOptions = ContextRenderOptions

    original_result = context.render_context_map_result

    @wraps(original_result)
    def render_context_map_result(dataset, street, options=None):
        resolved = options or context.ContextRenderOptions()
        scale = _validated_scale(getattr(resolved, "artwork_scale", 1.0))
        result = original_result(dataset, street, resolved)
        result.image.info[REAR_ARTWORK_SCALE_INFO_KEY] = scale
        return result

    context.render_context_map_result = render_context_map_result
    context.REAR_ARTWORK_SCALE_INFO_KEY = REAR_ARTWORK_SCALE_INFO_KEY
    context.REAR_ARTWORK_SCALE_MIN = REAR_ARTWORK_SCALE_MIN
    context.REAR_ARTWORK_SCALE_MAX = REAR_ARTWORK_SCALE_MAX
    context._rear_artwork_scale_installed = True
