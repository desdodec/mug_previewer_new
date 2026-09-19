"""V3 profile-driven mug print compositor."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
import unicodedata

from PIL import Image, ImageDraw

from ..providers import ProviderProfile, get_provider_profile
from ..rendering.artwork import PixelBox, TEMPLATE_V2_WRAP_LAYOUT
from ..rendering.face import FRONT_PANEL_PX

REFERENCE_GROUP_SCALE = TEMPLATE_V2_WRAP_LAYOUT.front_box.width / FRONT_PANEL_PX[0]
DEFAULT_PROFILE_IDS = ("inkthreadable_11oz_white", "prodigi_h_mug_w")


class ProviderCompositionError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderCompositionResult:
    image: Image.Image
    debug_image: Image.Image | None
    front_source_bounds: tuple[int, int, int, int]
    rear_source_bounds: tuple[int, int, int, int]
    front_placement: PixelBox
    rear_placement: PixelBox
    front_center_xy: tuple[int, int]
    rear_center_xy: tuple[int, int]
    inward_offset_px: float


def compose_provider_artwork(
    front_artwork: Image.Image,
    rear_artwork: Image.Image,
    profile: ProviderProfile,
    *,
    debug: bool = False,
) -> ProviderCompositionResult:
    """Centre tight visual bounds on the profile's normalised anchors."""
    if not isinstance(front_artwork, Image.Image) or not isinstance(rear_artwork, Image.Image):
        raise ProviderCompositionError("Front and rear artwork must be PIL images.")
    front, front_bounds = _tight_crop(front_artwork, "front")
    rear, rear_bounds = _tight_crop(rear_artwork, "rear")
    front = _uniform_scale(front, REFERENCE_GROUP_SCALE * profile.front_scale)
    rear = _uniform_scale(rear, REFERENCE_GROUP_SCALE * profile.rear_scale)

    width, height = profile.canvas_width_px, profile.canvas_height_px
    inward = profile.inward_offset_mm * profile.dpi / 25.4
    front_center = (
        _round_half_up(width * profile.front_centre_x + inward),
        _round_half_up(height * profile.front_centre_y),
    )
    rear_center = (
        _round_half_up(width * profile.rear_centre_x - inward),
        _round_half_up(height * profile.rear_centre_y),
    )
    front_box = _placement_box(front.size, front_center)
    rear_box = _placement_box(rear.size, rear_center)
    safe_px = _round_half_up(width * profile.edge_safe_fraction)
    _validate_placement(front_box, width, height, safe_px, "Front")
    _validate_placement(rear_box, width, height, safe_px, "Rear")

    working = _background(profile)
    working.alpha_composite(front, (front_box.x, front_box.y))
    working.alpha_composite(rear, (rear_box.x, rear_box.y))
    image = working if profile.background_policy.startswith("transparent-") else working.convert("RGB")
    image.info["dpi"] = (profile.dpi, profile.dpi)
    debug_image = _debug(working.copy(), profile, front_box, rear_box, front_center, rear_center) if debug else None
    if debug_image is not None:
        debug_image.info["dpi"] = (profile.dpi, profile.dpi)
    return ProviderCompositionResult(
        image, debug_image, front_bounds, rear_bounds, front_box, rear_box,
        front_center, rear_center, inward,
    )


def save_provider_artwork(
    front_artwork: Image.Image,
    rear_artwork: Image.Image,
    profile: ProviderProfile,
    destination: Path | str,
    *,
    debug_destination: Path | str | None = None,
) -> ProviderCompositionResult:
    destination = Path(destination)
    if destination.suffix.casefold() != ".png":
        raise ProviderCompositionError("V3 supplier exports must use a .png destination.")
    result = compose_provider_artwork(
        front_artwork, rear_artwork, profile, debug=debug_destination is not None,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.image.save(destination, format="PNG", dpi=(profile.dpi, profile.dpi))
    if debug_destination is not None:
        debug_path = Path(debug_destination)
        if debug_path.suffix.casefold() != ".png":
            raise ProviderCompositionError("V3 debug exports must use a .png destination.")
        assert result.debug_image is not None
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        result.debug_image.save(debug_path, format="PNG", dpi=(profile.dpi, profile.dpi))
    return result


def save_profile_set(
    front_artwork: Image.Image,
    rear_artwork: Image.Image,
    destination_directory: Path | str,
    design_name: str,
    *,
    profile_ids: tuple[str, ...] = DEFAULT_PROFILE_IDS,
    debug: bool = False,
) -> dict[str, tuple[Path, Path | None]]:
    """Create multiple supplier files from the same two artwork groups."""
    directory = Path(destination_directory)
    outputs = {}
    for profile_id in profile_ids:
        profile = get_provider_profile(profile_id)
        production = directory / supplier_output_filename(design_name, profile)
        debug_path = directory / supplier_output_filename(design_name, profile, debug=True) if debug else None
        save_provider_artwork(front_artwork, rear_artwork, profile, production, debug_destination=debug_path)
        outputs[profile_id] = (production, debug_path)
    return outputs


def supplier_output_filename(design_name: str, profile: ProviderProfile, *, debug: bool = False) -> str:
    design, supplier = _slug(design_name), _slug(profile.provider_name)
    if not design:
        raise ProviderCompositionError("Design name must contain usable filename text.")
    return f"{design}_{supplier}{'_debug' if debug else ''}.png"


def _tight_crop(image: Image.Image, label: str):
    rgba = image.convert("RGBA")
    bounds = rgba.getchannel("A").getbbox()
    if bounds is None:
        raise ProviderCompositionError(f"{label.capitalize()} artwork has no visible pixels.")
    return rgba.crop(bounds), bounds


def _uniform_scale(image: Image.Image, scale: float) -> Image.Image:
    if not math.isfinite(scale) or scale <= 0:
        raise ProviderCompositionError("Artwork scale must be positive and finite.")
    size = (max(1, _round_half_up(image.width * scale)), max(1, _round_half_up(image.height * scale)))
    return image.copy() if size == image.size else image.resize(size, Image.Resampling.LANCZOS)


def _placement_box(size: tuple[int, int], center: tuple[int, int]) -> PixelBox:
    width, height = size
    return PixelBox(
        _round_half_up(center[0] - width / 2),
        _round_half_up(center[1] - height / 2),
        width, height,
    )


def _validate_placement(box: PixelBox, width: int, height: int, safe_px: int, label: str) -> None:
    if box.x < 0 or box.y < 0 or box.right > width or box.bottom > height:
        raise ProviderCompositionError(
            f"{label} artwork does not fit {width}x{height} provider canvas: "
            f"{box.width}x{box.height} at ({box.x}, {box.y})."
        )
    if box.x < safe_px or box.right > width - safe_px:
        raise ProviderCompositionError(
            f"{label} artwork crosses the provider edge safe zone ({safe_px}px at each wrap end)."
        )


def _background(profile: ProviderProfile) -> Image.Image:
    size = (profile.canvas_width_px, profile.canvas_height_px)
    if profile.background_policy.startswith("transparent-"):
        return Image.new("RGBA", size, (0, 0, 0, 0))
    if profile.background_policy == "white-required":
        return Image.new("RGBA", size, (255, 255, 255, 255))
    raise ProviderCompositionError(f"Unsupported background policy: {profile.background_policy!r}.")


def _debug(
    image: Image.Image,
    profile: ProviderProfile,
    front_box: PixelBox,
    rear_box: PixelBox,
    front_center: tuple[int, int],
    rear_center: tuple[int, int],
) -> Image.Image:
    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size
    safe = _round_half_up(width * profile.edge_safe_fraction)
    if safe:
        draw.rectangle((0, 0, safe - 1, height - 1), fill=(255, 80, 80, 45), outline=(255, 80, 80, 210), width=2)
        draw.rectangle((width - safe, 0, width - 1, height - 1), fill=(255, 80, 80, 45), outline=(255, 80, 80, 210), width=2)
    for fraction in (0.25, 0.50, 0.75):
        x = _round_half_up(width * fraction)
        draw.line((x, 0, x, height - 1), fill=(50, 120, 255, 210), width=2)
    y = _round_half_up(height * 0.5)
    draw.line((0, y, width - 1, y), fill=(50, 120, 255, 180), width=2)
    draw.rectangle((front_box.x, front_box.y, front_box.right - 1, front_box.bottom - 1), outline=(20, 190, 90, 230), width=3)
    draw.rectangle((rear_box.x, rear_box.y, rear_box.right - 1, rear_box.bottom - 1), outline=(245, 160, 30, 230), width=3)
    _center(draw, front_center, (20, 190, 90, 255))
    _center(draw, rear_center, (245, 160, 30, 255))
    return image


def _center(draw: ImageDraw.ImageDraw, center: tuple[int, int], colour) -> None:
    x, y = center
    draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=colour)


def _round_half_up(value: float) -> int:
    if not math.isfinite(value):
        raise ProviderCompositionError("Placement values must be finite.")
    return math.floor(value + 0.5)


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.casefold())).strip("-")
