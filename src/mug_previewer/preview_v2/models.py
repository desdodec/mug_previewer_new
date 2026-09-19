"""Domain models for the provider-neutral Mug Previewer V2 renderer."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class PreviewV2Error(ValueError):
    """Raised when V2 preview geometry is invalid."""


class PreviewV2Mode(StrEnum):
    CUSTOMER = "customer"
    ENGINEERING = "engineering"


class PreviewV2View(StrEnum):
    FRONT = "front"
    REAR = "rear"


@dataclass(frozen=True)
class MugCalibration:
    """Calibrated visual geometry, deliberately separate from production export specs.

    wrap_span_degrees says how much of the physical cylinder is covered by
    the canonical flat print canvas. The canonical canvas already contains its
    measured central handle/seam exclusion zone; the remaining arc here is the
    opposite-side area outside that canvas. V2 therefore models both blank
    regions explicitly instead of pretending the flat canvas covers 360 degrees.
    """

    id: str
    label: str
    body_width_to_height: float = 0.82
    printable_height_fraction: float = 0.947
    wrap_span_degrees: float = 300.0
    visible_angle_degrees: float = 150.0
    body_corner_fraction: float = 0.055
    handle_width_fraction: float = 0.30
    handle_height_fraction: float = 0.54
    handle_stroke_fraction: float = 0.075

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.label.strip():
            raise PreviewV2Error("Mug calibration id and label must be non-empty.")
        if not 0.4 <= self.body_width_to_height <= 1.4:
            raise PreviewV2Error("Body width/height calibration must be from 0.4 to 1.4.")
        if not 0.2 <= self.printable_height_fraction <= 1.0:
            raise PreviewV2Error("Printable height fraction must be from 0.2 to 1.0.")
        if not 180.0 <= self.wrap_span_degrees < 360.0:
            raise PreviewV2Error("Wrap span must cover at least 180 degrees and less than 360 degrees.")
        if not 20.0 <= self.visible_angle_degrees < 180.0:
            raise PreviewV2Error("Visible cylinder angle must be from 20 degrees up to 180 degrees.")
        for name, value in (
            ("body_corner_fraction", self.body_corner_fraction),
            ("handle_width_fraction", self.handle_width_fraction),
            ("handle_height_fraction", self.handle_height_fraction),
            ("handle_stroke_fraction", self.handle_stroke_fraction),
        ):
            if not 0.0 < value < 1.0:
                raise PreviewV2Error(f"{name} must be greater than zero and less than one.")

    @property
    def unprinted_span_degrees(self) -> float:
        return 360.0 - self.wrap_span_degrees


@dataclass(frozen=True)
class CameraPose:
    """Camera rotation relative to the selected front/rear artwork centre."""

    yaw_degrees: float = 0.0

    def __post_init__(self) -> None:
        if not -180.0 <= self.yaw_degrees <= 180.0:
            raise PreviewV2Error("Camera yaw must be between -180 and 180 degrees.")


@dataclass(frozen=True)
class PreviewScene:
    """Pixel-space presentation settings; never used for provider export."""

    canvas_size: tuple[int, int] = (1000, 800)
    body_height_px: int = 590
    centre_x_fraction: float = 0.50
    centre_y_fraction: float = 0.53
    background_rgb: tuple[int, int, int] = (246, 244, 241)

    def __post_init__(self) -> None:
        width, height = self.canvas_size
        if width <= 0 or height <= 0 or self.body_height_px <= 0:
            raise PreviewV2Error("Preview scene dimensions must be positive.")
        if self.body_height_px >= height:
            raise PreviewV2Error("Mug body must fit inside the scene canvas.")
        if not 0.15 <= self.centre_x_fraction <= 0.85:
            raise PreviewV2Error("Scene centre_x_fraction must be from 0.15 to 0.85.")
        if not 0.2 <= self.centre_y_fraction <= 0.8:
            raise PreviewV2Error("Scene centre_y_fraction must be from 0.2 to 0.8.")
        if len(self.background_rgb) != 3 or any(not 0 <= channel <= 255 for channel in self.background_rgb):
            raise PreviewV2Error("Scene background_rgb must be a three-channel RGB value.")


@dataclass(frozen=True)
class MugPreviewV2Options:
    calibration: MugCalibration = field(default_factory=lambda: GENERIC_11OZ_CALIBRATION)
    camera: CameraPose = field(default_factory=CameraPose)
    scene: PreviewScene = field(default_factory=PreviewScene)
    view: PreviewV2View | str = PreviewV2View.FRONT
    mode: PreviewV2Mode | str = PreviewV2Mode.CUSTOMER
    show_guides: bool = False
    mesh_segments: int = 96

    def __post_init__(self) -> None:
        try:
            PreviewV2View(self.view)
        except ValueError as error:
            raise PreviewV2Error(f"Unsupported V2 view: {self.view!r}.") from error
        try:
            PreviewV2Mode(self.mode)
        except ValueError as error:
            raise PreviewV2Error(f"Unsupported V2 mode: {self.mode!r}.") from error
        if self.mesh_segments < 16:
            raise PreviewV2Error("V2 cylindrical projection requires at least 16 mesh segments.")


# The starting calibration is intentionally provider-neutral. The 300-degree
# span makes the current canonical front/rear centres exactly opposite on the
# virtual cylinder while leaving a 60-degree opposite-side arc outside the
# canonical print canvas. The existing central seam zone remains the handle gap.
# Provider- or
# SKU-specific calibrations can replace this without changing the renderer.
GENERIC_11OZ_CALIBRATION = MugCalibration(
    id="generic-11oz-v2",
    label="Generic 11 oz ceramic — V2 starting calibration",
)
