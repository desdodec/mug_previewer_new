"""Apply profile-driven rear artwork enlargement at provider composition time."""
from __future__ import annotations

from dataclasses import replace
from functools import wraps
import math
from typing import Any

from ..rendering.rear_artwork_scale import (
    REAR_ARTWORK_SCALE_INFO_KEY,
    REAR_ARTWORK_SCALE_MAX,
    REAR_ARTWORK_SCALE_MIN,
)


def install(compositor: Any) -> None:
    """Scale the completed rear group without changing its raster/detail source."""
    if getattr(compositor, "_rear_artwork_scale_installed", False):
        return

    original = compositor.compose_provider_artwork

    @wraps(original)
    def compose_provider_artwork(front_artwork, rear_artwork, profile, *, debug=False):
        raw = rear_artwork.info.get(REAR_ARTWORK_SCALE_INFO_KEY, 1.0)
        try:
            scale = float(raw)
        except (TypeError, ValueError) as error:
            raise compositor.ProviderCompositionError("Rear artwork scale must be numeric.") from error
        if not math.isfinite(scale) or not REAR_ARTWORK_SCALE_MIN <= scale <= REAR_ARTWORK_SCALE_MAX:
            raise compositor.ProviderCompositionError(
                f"Rear artwork scale must be between {REAR_ARTWORK_SCALE_MIN:.2f} "
                f"and {REAR_ARTWORK_SCALE_MAX:.2f}."
            )
        scaled_profile = profile if scale == 1.0 else replace(
            profile,
            rear_scale=profile.rear_scale * scale,
        )
        return original(front_artwork, rear_artwork, scaled_profile, debug=debug)

    compositor.compose_provider_artwork = compose_provider_artwork
    compositor._rear_artwork_scale_installed = True
