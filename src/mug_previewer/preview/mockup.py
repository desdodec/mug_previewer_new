"""Deterministic, preview-only compositing for one photographed white mug."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import asin, ceil, floor, pi, radians, sin
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


class MugPreviewError(ValueError):
    """Raised when an image cannot be rendered with the fixed mug preview."""


class PreviewOrientation(StrEnum):
    """Supported physical viewpoints for the fixed preview mug."""

    FRONT_HANDLE_RIGHT = "front-handle-right"
    REAR_HANDLE_LEFT = "rear-handle-left"


@dataclass(frozen=True)
class CanonicalWrapPreviewGeometry:
    """Frozen canonical-master coordinates used only for preview projection."""

    width_px: int = 2362
    height_px: int = 1063
    front_left_px: int = 0
    front_width_px: int = 945
    seam_left_px: int = 945
    seam_width_px: int = 472
    rear_left_px: int = 1417
    rear_width_px: int = 945

    @property
    def front_centre_x(self) -> float:
        return self.front_left_px + self.front_width_px / 2

    @property
    def rear_centre_x(self) -> float:
        return self.rear_left_px + self.rear_width_px / 2


@dataclass(frozen=True)
class MugPreviewLayout:
    """Coordinates of the owned white-mug photograph and printable body mask."""

    canvas_size: tuple[int, int] = (1024, 1536)
    body_bounds_xyxy: tuple[int, int, int, int] = (198, 482, 683, 1105)
    visible_angle_degrees: float = 150.0
    mesh_segments: int = 64

    def __post_init__(self) -> None:
        left, top, right, bottom = self.body_bounds_xyxy
        if self.canvas_size[0] <= 0 or self.canvas_size[1] <= 0:
            raise ValueError("Preview canvas dimensions must be positive.")
        if not (0 <= left < right <= self.canvas_size[0] and 0 <= top < bottom <= self.canvas_size[1]):
            raise ValueError("Mug body bounds must sit inside the preview canvas.")
        if not 20.0 <= self.visible_angle_degrees < 180.0:
            raise ValueError("Visible cylinder angle must be from 20 degrees up to (but not including) 180.")
        if self.mesh_segments < 8:
            raise ValueError("Mug projection needs at least eight mesh segments.")


def scaled_mug_preview_layout(
    scale: float,
    layout: MugPreviewLayout | None = None,
) -> MugPreviewLayout:
    """Return a proportionally scaled owned-mug layout for screen previews."""
    if not 0 < scale <= 1:
        raise ValueError("Preview scale must be greater than zero and no greater than one.")
    source = layout or DEFAULT_MUG_PREVIEW_LAYOUT
    left, top, right, bottom = source.body_bounds_xyxy
    return MugPreviewLayout(
        canvas_size=tuple(max(1, round(value * scale)) for value in source.canvas_size),
        body_bounds_xyxy=tuple(round(value * scale) for value in (left, top, right, bottom)),
        visible_angle_degrees=source.visible_angle_degrees,
        mesh_segments=source.mesh_segments,
    )


@dataclass(frozen=True)
class MugPreviewOptions:
    """Preview-only options; none affect canonical or provider artwork."""

    layout: MugPreviewLayout = field(default_factory=lambda: DEFAULT_MUG_PREVIEW_LAYOUT)
    wrap_geometry: CanonicalWrapPreviewGeometry = field(
        default_factory=lambda: CANONICAL_WRAP_PREVIEW_GEOMETRY,
    )
    orientation: PreviewOrientation | str = PreviewOrientation.FRONT_HANDLE_RIGHT
    show_debug_guides: bool = False


CANONICAL_WRAP_PREVIEW_GEOMETRY = CanonicalWrapPreviewGeometry()
DEFAULT_MUG_PREVIEW_LAYOUT = MugPreviewLayout()


@dataclass(frozen=True)
class _OrientationGeometry:
    """Source-centre and owned-mug treatment for one physical viewpoint."""

    source_centre_x: float
    mirror_mug: bool


def _resolve_orientation(
    orientation: PreviewOrientation | str,
    geometry: CanonicalWrapPreviewGeometry = CANONICAL_WRAP_PREVIEW_GEOMETRY,
) -> _OrientationGeometry:
    """Return shared-projection settings without ever mirroring artwork."""
    try:
        resolved = PreviewOrientation(orientation)
    except ValueError as error:
        values = ", ".join(member.value for member in PreviewOrientation)
        raise MugPreviewError(f"Unsupported preview orientation: {orientation!r}. Expected one of: {values}.") from error
    if resolved is PreviewOrientation.FRONT_HANDLE_RIGHT:
        return _OrientationGeometry(source_centre_x=geometry.front_centre_x, mirror_mug=False)
    return _OrientationGeometry(source_centre_x=geometry.rear_centre_x, mirror_mug=True)


def render_mug_preview(wrap: Image.Image, options: MugPreviewOptions | None = None) -> Image.Image:
    """Render a visual-review mockup from a completed canonical master.

    This downstream preview must never be sent to a provider or replace the
    flat print master.
    """
    options = options or MugPreviewOptions()
    _validate_wrap(wrap, options.wrap_geometry)
    orientation = _resolve_orientation(options.orientation, options.wrap_geometry)
    base, body_mask = _load_owned_mug_assets(options.layout)
    layout = options.layout
    if orientation.mirror_mug:
        base = base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        body_mask = body_mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        layout = _mirrored_layout(layout)
    left, top, right, bottom = layout.body_bounds_xyxy
    projected = project_canonical_wrap(
        wrap, target_size=(right - left, bottom - top), visible_angle_degrees=options.layout.visible_angle_degrees,
        geometry=options.wrap_geometry, mesh_segments=options.layout.mesh_segments, source_centre_x=orientation.source_centre_x,
    )
    local_mask = body_mask.crop((left, top, right, bottom))
    projected.putalpha(ImageChops.multiply(projected.getchannel("A"), local_mask))
    artwork = Image.new("RGBA", base.size, (0, 0, 0, 0))
    artwork.alpha_composite(projected, (left, top))
    result = Image.alpha_composite(base, artwork)
    # The supplied studio asset already carries the mug's rim, body, handle,
    # and cast-shadow lighting. Do not overlay lighting over the projected
    # rectangle: even a feathered layer makes a blank wrap change ceramic.
    if options.show_debug_guides:
        _draw_debug_guides(result, layout, handle_on_left=orientation.mirror_mug)
    return result


def project_canonical_wrap(
    wrap: Image.Image,
    *,
    target_size: tuple[int, int],
    geometry: CanonicalWrapPreviewGeometry = CANONICAL_WRAP_PREVIEW_GEOMETRY,
    visible_angle_degrees: float = 150.0,
    mesh_segments: int = 64,
    source_centre_x: float | None = None,
) -> Image.Image:
    """Project a canonical-wrap interval into a cylindrical body region.

    Equal source widths increasingly compress toward both cylinder edges. The
    selected source centre samples most faithfully. The continuous source can
    cross the wrap boundary without reversing any artwork.
    """
    if target_size[0] <= 0 or target_size[1] <= 0:
        raise MugPreviewError("Projection target dimensions must be positive.")
    if not 20.0 <= visible_angle_degrees < 180.0 or mesh_segments < 8:
        raise MugPreviewError("Projection needs a 20â€“180 degree view and at least eight mesh segments.")
    _validate_wrap(wrap, geometry)
    source = wrap.convert("RGBA")
    target_width, target_height = target_size
    angle_limit = radians(visible_angle_degrees / 2)
    sine_limit = sin(angle_limit)
    centre = geometry.front_centre_x if source_centre_x is None else source_centre_x

    def source_x(output_x: float) -> float:
        """Invert x_screen = centre + radius * sin(theta) for Pillow."""
        midpoint = max((target_width - 1) / 2, 1.0)
        normalised = (output_x - midpoint) / midpoint
        return centre + geometry.width_px * asin(normalised * sine_limit) / (2 * pi)

    positions = (source_x(0), source_x(target_width))
    logical_left = floor(min(positions)) - 2
    logical_right = ceil(max(positions)) + 3
    strip = _continuous_wrap_strip(source, logical_left, logical_right - logical_left)
    mesh: list[tuple[tuple[int, int, int, int], tuple[float, float, float, float, float, float, float, float]]] = []
    for segment in range(mesh_segments):
        x0, x1 = round(segment * target_width / mesh_segments), round((segment + 1) * target_width / mesh_segments)
        if x1 > x0:
            # Adjacent mesh cells share the same cylindrical source boundary.
            sx0, sx1 = source_x(x0) - logical_left, source_x(x1) - logical_left
            mesh.append(((x0, 0, x1, target_height), (sx0, 0, sx0, source.height, sx1, source.height, sx1, 0)))
    return strip.transform(target_size, Image.Transform.MESH, mesh, resample=Image.Resampling.BICUBIC)



def _mirrored_layout(layout: MugPreviewLayout) -> MugPreviewLayout:
    """Mirror only the owned mug geometry for the handle-left viewpoint."""
    left, top, right, bottom = layout.body_bounds_xyxy
    width, _ = layout.canvas_size
    return MugPreviewLayout(
        canvas_size=layout.canvas_size,
        body_bounds_xyxy=(width - right, top, width - left, bottom),
        visible_angle_degrees=layout.visible_angle_degrees,
        mesh_segments=layout.mesh_segments,
    )


def _continuous_wrap_strip(source: Image.Image, logical_left: int, width: int) -> Image.Image:
    """Copy one continuous, seam-aware source range without altering artwork."""
    strip = Image.new("RGBA", (width, source.height), (0, 0, 0, 0))
    start = logical_left % source.width
    first_width = min(width, source.width - start)
    strip.paste(source.crop((start, 0, start + first_width, source.height)), (0, 0))
    remaining = width - first_width
    if remaining:
        strip.paste(source.crop((0, 0, remaining, source.height)), (first_width, 0))
    return strip




def _load_owned_mug_assets(layout: MugPreviewLayout) -> tuple[Image.Image, Image.Image]:
    assets = Path(__file__).with_name("assets")
    with Image.open(assets / "white_mug.png") as source:
        base = source.convert("RGBA").copy()
    with Image.open(assets / "white_mug_mask.png") as source:
        mask = source.convert("L").copy()
    if base.size != mask.size:
        raise MugPreviewError("White mug preview assets must share one canvas size.")
    if base.size != layout.canvas_size:
        base = base.resize(layout.canvas_size, Image.Resampling.LANCZOS)
        mask = mask.resize(layout.canvas_size, Image.Resampling.NEAREST)
    return base, mask


def _validate_wrap(wrap: Image.Image, geometry: CanonicalWrapPreviewGeometry = CANONICAL_WRAP_PREVIEW_GEOMETRY) -> None:
    if wrap.size != (geometry.width_px, geometry.height_px):
        raise MugPreviewError(f"Mug preview requires canonical {geometry.width_px}x{geometry.height_px} artwork, got {wrap.width}x{wrap.height}.")


def _draw_debug_guides(image: Image.Image, layout: MugPreviewLayout, *, handle_on_left: bool = False) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    left, top, right, bottom = layout.body_bounds_xyxy
    draw.rectangle((left, top, right - 1, bottom - 1), outline=(255, 132, 36, 220), width=3)
    draw.line(((left + right) // 2, top, (left + right) // 2, bottom), fill=(44, 145, 255, 220), width=2)
    handle_edge = left if handle_on_left else right - 1
    draw.line((handle_edge, top, handle_edge, bottom), fill=(80, 220, 125, 220), width=3)