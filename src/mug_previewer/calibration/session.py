"""Calibration-session geometry and overlay rendering."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path

from PIL import Image

from ..preview.mockup import (
    CANONICAL_WRAP_PREVIEW_GEOMETRY,
    CanonicalWrapPreviewGeometry,
    project_canonical_wrap,
)
from ..preview_v2 import MugCalibration, MugCalibrationProfile, PreviewV2View
from ..preview_v2.projection import build_circumference_strip


@dataclass(frozen=True)
class ImageBounds:
    x: int
    y: int
    width: int
    height: int

    def validate_for(self, image: Image.Image) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Mug bounds must have positive width and height.")
        if self.x < 0 or self.y < 0:
            raise ValueError("Mug bounds cannot start outside the image.")
        if self.x + self.width > image.width or self.y + self.height > image.height:
            raise ValueError("Mug bounds must fit inside the loaded mockup.")


@dataclass(frozen=True)
class ViewFit:
    bounds: ImageBounds | None = None
    camera_yaw_degrees: float = 0.0
    vertical_offset_fraction: float = 0.0
    vertical_scale: float = 1.0


@dataclass(frozen=True)
class CalibrationFit:
    profile_id: str
    artwork_offset_degrees: float = 0.0
    visible_angle_degrees: float | None = None
    print_arc_degrees: float | None = None
    front: ViewFit = ViewFit()
    rear: ViewFit = ViewFit()

    def view_fit(self, view: PreviewV2View | str) -> ViewFit:
        return self.front if PreviewV2View(view) is PreviewV2View.FRONT else self.rear

    def effective_calibration(self, base: MugCalibration) -> MugCalibration:
        return replace(
            base,
            wrap_span_degrees=self.print_arc_degrees or base.wrap_span_degrees,
            visible_angle_degrees=self.visible_angle_degrees or base.visible_angle_degrees,
        )


def render_target_overlay(
    mockup: Image.Image,
    target: Image.Image,
    *,
    profile: MugCalibrationProfile,
    fit: CalibrationFit,
    view: PreviewV2View | str,
    opacity: float = 0.55,
) -> Image.Image:
    """Overlay the expected calibration target onto a provider mockup."""
    resolved_view = PreviewV2View(view)
    view_fit = fit.view_fit(resolved_view)
    if view_fit.bounds is None:
        return mockup.convert("RGBA").copy()
    view_fit.bounds.validate_for(mockup)
    if not 0.0 <= opacity <= 1.0:
        raise ValueError("Overlay opacity must be from 0 to 1.")

    calibration = fit.effective_calibration(profile.calibration)
    strip = build_circumference_strip(target, calibration)
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    full_width = strip.width
    source_centre = (
        geometry.front_centre_x
        if resolved_view is PreviewV2View.FRONT
        else geometry.rear_centre_x
    )
    source_centre += full_width * (
        view_fit.camera_yaw_degrees + fit.artwork_offset_degrees
    ) / 360.0

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

    target_height = max(1, round(view_fit.bounds.height * view_fit.vertical_scale))
    projected = project_canonical_wrap(
        strip,
        target_size=(view_fit.bounds.width, target_height),
        geometry=physical_geometry,
        visible_angle_degrees=calibration.visible_angle_degrees,
        mesh_segments=128,
        source_centre_x=source_centre,
    )
    alpha = projected.getchannel("A").point(lambda value: round(value * opacity))
    projected.putalpha(alpha)

    result = mockup.convert("RGBA").copy()
    top = view_fit.bounds.y + round(
        (view_fit.bounds.height - target_height) / 2
        + view_fit.vertical_offset_fraction * view_fit.bounds.height
    )
    result.alpha_composite(projected, (view_fit.bounds.x, top))
    return result


def save_fit_session(path: Path | str, fit: CalibrationFit) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(asdict(fit), indent=2) + "\n", encoding="utf-8")
    return destination


def load_fit_session(path: Path | str) -> CalibrationFit:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Calibration session must be a JSON object.")

    def parse_view(name: str) -> ViewFit:
        raw = payload.get(name) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"Calibration session field {name!r} must be an object.")
        raw_bounds = raw.get("bounds")
        bounds = None
        if raw_bounds is not None:
            if not isinstance(raw_bounds, dict):
                raise ValueError(f"Calibration session {name}.bounds must be an object or null.")
            bounds = ImageBounds(
                x=int(raw_bounds["x"]),
                y=int(raw_bounds["y"]),
                width=int(raw_bounds["width"]),
                height=int(raw_bounds["height"]),
            )
        return ViewFit(
            bounds=bounds,
            camera_yaw_degrees=float(raw.get("camera_yaw_degrees", 0.0)),
            vertical_offset_fraction=float(raw.get("vertical_offset_fraction", 0.0)),
            vertical_scale=float(raw.get("vertical_scale", 1.0)),
        )

    return CalibrationFit(
        profile_id=str(payload["profile_id"]),
        artwork_offset_degrees=float(payload.get("artwork_offset_degrees", 0.0)),
        visible_angle_degrees=(
            None if payload.get("visible_angle_degrees") is None
            else float(payload["visible_angle_degrees"])
        ),
        print_arc_degrees=(
            None if payload.get("print_arc_degrees") is None
            else float(payload["print_arc_degrees"])
        ),
        front=parse_view("front"),
        rear=parse_view("rear"),
    )


def candidate_profile_mapping(
    profile: MugCalibrationProfile,
    fit: CalibrationFit,
    *,
    calibration_id: str,
    source_description: str,
) -> dict[str, object]:
    """Create an explicit candidate profile mapping without modifying built-ins."""
    calibration = fit.effective_calibration(profile.calibration)
    return {
        "id": calibration_id,
        "provider_name": profile.provider_name,
        "product_name": profile.product_name,
        "provider_profile_id": profile.provider_profile_id,
        "sku": profile.sku,
        "status": "provisional",
        "body_width_to_height": calibration.body_width_to_height,
        "printable_height_fraction": calibration.printable_height_fraction,
        "wrap_span_degrees": calibration.wrap_span_degrees,
        "visible_angle_degrees": calibration.visible_angle_degrees,
        "body_corner_fraction": calibration.body_corner_fraction,
        "handle_width_fraction": calibration.handle_width_fraction,
        "handle_height_fraction": calibration.handle_height_fraction,
        "handle_stroke_fraction": calibration.handle_stroke_fraction,
        "default_front_yaw_degrees": fit.front.camera_yaw_degrees,
        "default_rear_yaw_degrees": fit.rear.camera_yaw_degrees,
        "artwork_registration_offset_degrees": fit.artwork_offset_degrees,
        "source_description": source_description,
        "source_url": profile.source_url,
        "verified_date": None,
        "fit_notes": {
            "front_bounds": None if fit.front.bounds is None else asdict(fit.front.bounds),
            "rear_bounds": None if fit.rear.bounds is None else asdict(fit.rear.bounds),
            "front_vertical_scale": fit.front.vertical_scale,
            "rear_vertical_scale": fit.rear.vertical_scale,
            "front_vertical_offset_fraction": fit.front.vertical_offset_fraction,
            "rear_vertical_offset_fraction": fit.rear.vertical_offset_fraction,
        },
    }


def save_candidate_profile(
    path: Path | str,
    mapping: dict[str, object],
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    return destination
