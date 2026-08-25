"""Small, explicit descriptions of supported provider print profiles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ScalingPolicy(StrEnum):
    """Uniform transformations supported by the provider export layer."""

    CONTAIN = "contain"
    COVER = "cover"
    EXACT_NO_RESIZE = "exact-no-resize"


class CroppingPolicy(StrEnum):
    """How any unavoidable cover overflow is selected."""

    NONE = "none"
    CENTRE = "centre"


class PaddingPolicy(StrEnum):
    """How unused contain space is represented."""

    NONE = "none"
    TRANSPARENT = "transparent"
    SOLID = "solid"


class AlphaHandling(StrEnum):
    """Whether source transparency is retained or explicitly flattened."""

    PRESERVE = "preserve"
    FLATTEN = "flatten"


RGBColour = tuple[int, int, int]


@dataclass(frozen=True)
class ProviderExportSpec:
    """A provider/product print-file contract.

    ``background_rgb`` is required whenever alpha is flattened. It makes RGB
    conversion intentional, avoiding Pillow's implicit black background.
    """

    provider: str
    product: str
    target_width_px: int
    target_height_px: int
    dpi: int
    required_mode: str
    alpha_handling: AlphaHandling
    background_rgb: RGBColour | None
    scaling: ScalingPolicy
    cropping: CroppingPolicy
    padding: PaddingPolicy
    output_format: str
    specification_url: str

    def __post_init__(self) -> None:
        if not self.provider or not self.product:
            raise ValueError("Provider and product names must be non-empty.")
        if self.target_width_px <= 0 or self.target_height_px <= 0 or self.dpi <= 0:
            raise ValueError("Provider target dimensions and DPI must be positive.")
        if self.required_mode not in {"RGBA", "RGB"}:
            raise ValueError("Provider output mode must be RGB or RGBA.")
        if self.output_format != "PNG":
            raise ValueError("Only PNG provider exports are supported.")
        if self.alpha_handling is AlphaHandling.PRESERVE and self.required_mode != "RGBA":
            raise ValueError("Preserved alpha requires RGBA output.")
        if self.alpha_handling is AlphaHandling.FLATTEN and self.background_rgb is None:
            raise ValueError("Flattened output requires an explicit RGB background.")
        if self.padding is PaddingPolicy.TRANSPARENT and self.required_mode != "RGBA":
            raise ValueError("Transparent padding requires RGBA output.")
        if self.scaling is ScalingPolicy.CONTAIN and self.cropping is not CroppingPolicy.NONE:
            raise ValueError("Contain scaling cannot specify cropping.")
        if self.scaling is ScalingPolicy.COVER and self.cropping is CroppingPolicy.NONE:
            raise ValueError("Cover scaling requires an explicit cropping policy.")
        if self.scaling is ScalingPolicy.EXACT_NO_RESIZE and (
            self.cropping is not CroppingPolicy.NONE or self.padding is not PaddingPolicy.NONE
        ):
            raise ValueError("Exact-no-resize exports cannot crop or pad.")
        if self.background_rgb is not None and (
            len(self.background_rgb) != 3 or any(channel not in range(256) for channel in self.background_rgb)
        ):
            raise ValueError("Background colour must be an RGB tuple with values from 0 to 255.")


# Gelato specifies a 200 x 96 mm printable area for the White 11oz Ceramic
# Mug and recommends 300 DPI at final print size. The target pixels below
# are round(mm * 300 / 25.4): 2362 x 1134. PNG is accepted by Gelato, so this
# profile preserves the canonical master's transparent background.
GELATO_WHITE_11OZ_CERAMIC_MUG = ProviderExportSpec(
    provider="Gelato",
    product="White 11oz Ceramic Mug",
    target_width_px=2362,
    target_height_px=1134,
    dpi=300,
    required_mode="RGBA",
    alpha_handling=AlphaHandling.PRESERVE,
    background_rgb=None,
    scaling=ScalingPolicy.CONTAIN,
    cropping=CroppingPolicy.NONE,
    padding=PaddingPolicy.TRANSPARENT,
    output_format="PNG",
    specification_url="https://support.gelato.com/en/articles/8996273-what-is-the-printable-area-for-mugs",
)
