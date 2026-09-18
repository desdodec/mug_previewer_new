"""Physical-cylinder projection for Mug Previewer V2."""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from ..preview.mockup import (
    CANONICAL_WRAP_PREVIEW_GEOMETRY,
    CanonicalWrapPreviewGeometry,
    project_canonical_wrap,
)
from .models import CameraPose, MugCalibration, PreviewV2Error, PreviewV2View


@dataclass(frozen=True)
class ProjectionDiagnostics:
    canonical_width_px: int
    circumference_width_px: int
    unprinted_gap_px: int
    source_centre_x: float
    camera_centre_x: float
    handle_delta_degrees: float


def circumference_width_px(
    calibration: MugCalibration,
    geometry: CanonicalWrapPreviewGeometry = CANONICAL_WRAP_PREVIEW_GEOMETRY,
) -> int:
    """Return the virtual full-cylinder width implied by the calibrated print arc."""
    width = round(geometry.width_px * 360.0 / calibration.wrap_span_degrees)
    if width <= geometry.width_px:
        raise PreviewV2Error("V2 calibration must leave a non-zero unprinted handle gap.")
    return width


def build_circumference_strip(
    wrap: Image.Image,
    calibration: MugCalibration,
    geometry: CanonicalWrapPreviewGeometry = CANONICAL_WRAP_PREVIEW_GEOMETRY,
) -> Image.Image:
    """Place the canonical print strip onto a transparent 360-degree cylinder strip."""
    if wrap.size != (geometry.width_px, geometry.height_px):
        raise PreviewV2Error(
            f"V2 preview requires canonical {geometry.width_px}x{geometry.height_px} artwork, "
            f"got {wrap.width}x{wrap.height}."
        )
    full_width = circumference_width_px(calibration, geometry)
    strip = Image.new("RGBA", (full_width, geometry.height_px), (0, 0, 0, 0))
    strip.alpha_composite(wrap.convert("RGBA"), (0, 0))
    return strip


def source_centre_for_view(
    view: PreviewV2View | str,
    geometry: CanonicalWrapPreviewGeometry = CANONICAL_WRAP_PREVIEW_GEOMETRY,
) -> float:
    resolved = PreviewV2View(view)
    return geometry.front_centre_x if resolved is PreviewV2View.FRONT else geometry.rear_centre_x


def projection_diagnostics(
    calibration: MugCalibration,
    camera: CameraPose,
    view: PreviewV2View | str,
    geometry: CanonicalWrapPreviewGeometry = CANONICAL_WRAP_PREVIEW_GEOMETRY,
) -> ProjectionDiagnostics:
    full_width = circumference_width_px(calibration, geometry)
    source_centre = source_centre_for_view(view, geometry)
    camera_centre = source_centre + full_width * camera.yaw_degrees / 360.0
    handle_centre = geometry.width_px + (full_width - geometry.width_px) / 2.0
    handle_angle = handle_centre / full_width * 360.0
    camera_angle = camera_centre / full_width * 360.0
    delta = _normalise_angle(handle_angle - camera_angle)
    return ProjectionDiagnostics(
        canonical_width_px=geometry.width_px,
        circumference_width_px=full_width,
        unprinted_gap_px=full_width - geometry.width_px,
        source_centre_x=source_centre,
        camera_centre_x=camera_centre,
        handle_delta_degrees=delta,
    )


def project_wrap_v2(
    wrap: Image.Image,
    *,
    target_size: tuple[int, int],
    calibration: MugCalibration,
    camera: CameraPose,
    view: PreviewV2View | str,
    mesh_segments: int = 96,
    geometry: CanonicalWrapPreviewGeometry = CANONICAL_WRAP_PREVIEW_GEOMETRY,
) -> tuple[Image.Image, ProjectionDiagnostics]:
    """Project artwork while preserving the physical unprinted handle gap."""
    strip = build_circumference_strip(wrap, calibration, geometry)
    diagnostics = projection_diagnostics(calibration, camera, view, geometry)
    physical_geometry = CanonicalWrapPreviewGeometry(
        width_px=strip.width,
        height_px=strip.height,
        front_left_px=0,
        front_width_px=1,
        seam_left_px=1,
        seam_width_px=1,
        rear_left_px=2,
        rear_width_px=1,
    )
    projected = project_canonical_wrap(
        strip,
        target_size=target_size,
        geometry=physical_geometry,
        visible_angle_degrees=calibration.visible_angle_degrees,
        mesh_segments=mesh_segments,
        source_centre_x=diagnostics.camera_centre_x,
    )
    return projected, diagnostics


def _normalise_angle(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0
