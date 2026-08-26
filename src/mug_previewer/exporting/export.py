"""Transform a completed canonical master into one provider print file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
from typing import Optional

from PIL import Image

from ..providers import ProviderProfile
from ..rendering.artwork import CANONICAL_WRAP_SIZE
from .providers import AlphaHandling, CroppingPolicy, PaddingPolicy, ProviderExportSpec, ScalingPolicy


class ProviderExportError(ValueError):
    """Raised when an image cannot satisfy a provider export specification."""


@dataclass(frozen=True)
class ProviderExportOptions:
    """Export behaviour that is independent of a provider requirement."""

    resampling: Image.Resampling = Image.Resampling.LANCZOS


@dataclass(frozen=True)
class ExportOptions:
    # Options for one provider export.
    format: Optional[str] = None
    jpeg_background: Optional[tuple[int, int, int]] = None


@dataclass(frozen=True)
class ContainGeometry:
    source_size: tuple[int, int]
    target_size: tuple[int, int]
    scale_factor: float
    scaled_size: tuple[int, int]
    left: int
    top: int

    @property
    def padding_ltrb(self) -> tuple[int, int, int, int]:
        return (
            self.left,
            self.top,
            self.target_size[0] - self.scaled_size[0] - self.left,
            self.target_size[1] - self.scaled_size[1] - self.top,
        )


@dataclass(frozen=True)
class ProviderExportResult:
    """Provider image plus deterministic transformation diagnostics."""

    image: Image.Image
    source_size: tuple[int, int]
    scaled_size: tuple[int, int]
    scale_factor: float
    placement_xywh: tuple[int, int, int, int]
    padding_ltrb: tuple[int, int, int, int]
    crop_xywh: tuple[int, int, int, int] | None


def export_wrap(
    image: Image.Image,
    spec: ProviderExportSpec,
    options: ProviderExportOptions | None = None,
) -> Image.Image:
    """Return a provider-ready image from a completed canonical master.

    The source image is never changed. The whole master is transformed once;
    front, seam, and rear regions are not handled independently.
    """
    return export_wrap_result(image, spec, options).image


def export_wrap_result(
    image: Image.Image,
    spec: ProviderExportSpec,
    options: ProviderExportOptions | None = None,
) -> ProviderExportResult:
    """Export one master and return both the image and its placement details."""
    if image.width <= 0 or image.height <= 0:
        raise ProviderExportError("Source image dimensions must be positive.")
    options = options or ProviderExportOptions()
    source_size = image.size
    source = image.convert("RGBA")
    target_size = (spec.target_width_px, spec.target_height_px)
    scaled, scale_factor, placement, padding, crop = _transform(source, target_size, spec, options)
    output = _compose_output(scaled, target_size, placement, spec)
    output.info["dpi"] = (spec.dpi, spec.dpi)
    return ProviderExportResult(
        image=output,
        source_size=source_size,
        scaled_size=scaled.size,
        scale_factor=scale_factor,
        placement_xywh=(placement[0], placement[1], scaled.width, scaled.height),
        padding_ltrb=padding,
        crop_xywh=crop,
    )


def save_provider_png(
    image: Image.Image,
    path: Path,
    spec: ProviderExportSpec,
    options: ProviderExportOptions | None = None,
) -> ProviderExportResult:
    """Transform and save a PNG with the provider DPI metadata explicitly set."""
    result = export_wrap_result(image, spec, options)
    path.parent.mkdir(parents=True, exist_ok=True)
    result.image.save(path, format="PNG", dpi=(spec.dpi, spec.dpi))
    return result


def prepare_provider_image(
    wrap: Image.Image,
    profile: ProviderProfile,
    options: Optional[ExportOptions] = None,
) -> Image.Image:
    _validate_profile_wrap(wrap, profile)
    options = options or ExportOptions()
    export_format = _resolve_profile_format(profile, options.format)
    image = _prepare_profile_rgba(wrap, profile)
    if export_format == 'JPEG':
        return _flatten_jpeg(image, options.jpeg_background)
    return image


def save_provider_export(
    wrap: Image.Image,
    profile: ProviderProfile,
    destination: Path,
    options: Optional[ExportOptions] = None,
) -> Path:
    options = options or ExportOptions()
    export_format = _resolve_profile_format(profile, options.format)
    destination = Path(destination)
    _validate_destination(destination, export_format)
    image = prepare_provider_image(wrap, profile, options)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format=export_format, dpi=(profile.dpi, profile.dpi))
    return destination


def calculate_contain_geometry(
    source_size: tuple[int, int], target_size: tuple[int, int],
) -> ContainGeometry:
    source_width, source_height = source_size
    target_width, target_height = target_size
    if not source_width:
        raise ProviderExportError('Source image dimensions must be positive.')
    if not source_height or not target_width or not target_height:
        raise ProviderExportError('Image and target dimensions must be positive.')
    scale_factor = min(target_width / source_width, target_height / source_height)
    scaled_size = (round(source_width * scale_factor), round(source_height * scale_factor))
    return ContainGeometry(
        source_size, target_size, scale_factor, scaled_size,
        (target_width - scaled_size[0]) // 2, (target_height - scaled_size[1]) // 2,
    )


def provider_export_filename(street_id: str, street_name: str, spec: ProviderExportSpec) -> str:
    """Return a predictable Windows-safe provider export filename."""
    identifier = _slug(street_id)
    street = _slug(street_name)
    provider = _slug(spec.provider)
    product = _slug(spec.product)
    if not all((identifier, street, provider, product)):
        raise ProviderExportError("Street ID, street name, provider, and product must contain usable text.")
    return f"{identifier}_{street}_{provider}_{product}.png"


def _transform(
    source: Image.Image,
    target_size: tuple[int, int],
    spec: ProviderExportSpec,
    options: ProviderExportOptions,
) -> tuple[Image.Image, float, tuple[int, int], tuple[int, int, int, int], tuple[int, int, int, int] | None]:
    source_width, source_height = source.size
    target_width, target_height = target_size
    if spec.scaling is ScalingPolicy.EXACT_NO_RESIZE:
        if source.size != target_size:
            raise ProviderExportError(
                f"Exact-no-resize export requires {target_width}x{target_height}, got {source_width}x{source_height}."
            )
        return source.copy(), 1.0, (0, 0), (0, 0, 0, 0), None

    if spec.scaling is ScalingPolicy.CONTAIN:
        factor = min(target_width / source_width, target_height / source_height)
    elif spec.scaling is ScalingPolicy.COVER:
        factor = max(target_width / source_width, target_height / source_height)
    else:
        raise ProviderExportError(f"Unsupported scaling policy: {spec.scaling}.")

    scaled_size = (max(1, round(source_width * factor)), max(1, round(source_height * factor)))
    scaled = source if scaled_size == source.size else source.resize(scaled_size, options.resampling)

    if spec.scaling is ScalingPolicy.CONTAIN:
        left = (target_width - scaled.width) // 2
        top = (target_height - scaled.height) // 2
        padding = (left, top, target_width - scaled.width - left, target_height - scaled.height - top)
        return scaled, factor, (left, top), padding, None

    if spec.cropping is not CroppingPolicy.CENTRE:
        raise ProviderExportError(f"Unsupported cropping policy: {spec.cropping}.")
    left = (scaled.width - target_width) // 2
    top = (scaled.height - target_height) // 2
    crop = (left, top, target_width, target_height)
    return scaled.crop((left, top, left + target_width, top + target_height)), factor, (0, 0), (0, 0, 0, 0), crop


def _compose_output(
    artwork: Image.Image,
    target_size: tuple[int, int],
    placement: tuple[int, int],
    spec: ProviderExportSpec,
) -> Image.Image:
    if spec.alpha_handling is AlphaHandling.PRESERVE:
        if spec.padding is PaddingPolicy.SOLID:
            if spec.background_rgb is None:
                raise ProviderExportError("Solid padding requires an explicit background colour.")
            background = (*spec.background_rgb, 255)
        else:
            background = (0, 0, 0, 0)
        output = Image.new("RGBA", target_size, background)
        output.alpha_composite(artwork, placement)
        return output

    if spec.alpha_handling is not AlphaHandling.FLATTEN or spec.background_rgb is None:
        raise ProviderExportError("A flattened export requires an explicit background colour.")
    flattened = Image.new("RGBA", target_size, (*spec.background_rgb, 255))
    flattened.alpha_composite(artwork, placement)
    return flattened.convert("RGB")


def _validate_profile_wrap(wrap: Image.Image, profile: ProviderProfile) -> None:
    if not isinstance(wrap, Image.Image):
        raise ProviderExportError('Provider export requires a PIL Image canonical wrap.')
    if not wrap.width or not wrap.height:
        raise ProviderExportError('Source image dimensions must be positive.')
    if wrap.size != CANONICAL_WRAP_SIZE:
        raise ProviderExportError(
            f'Provider export requires canonical {CANONICAL_WRAP_SIZE[0]}x{CANONICAL_WRAP_SIZE[1]} artwork, '
            f'got {wrap.width}x{wrap.height}.'
        )
    if not isinstance(profile, ProviderProfile):
        raise ProviderExportError('Provider export requires a ProviderProfile.')
    if not profile.canvas_width_px or not profile.canvas_height_px:
        raise ProviderExportError('Provider target canvas dimensions must be positive.')


def _resolve_profile_format(profile: ProviderProfile, requested: Optional[str]) -> str:
    if requested is None:
        return profile.preferred_format
    if not isinstance(requested, str) or not requested.strip():
        raise ProviderExportError('Requested export format must be a non-empty string.')
    export_format = requested.strip().upper()
    if export_format not in profile.accepted_formats:
        accepted = ', '.join(profile.accepted_formats)
        raise ProviderExportError(
            f'Format {export_format!r} is not accepted by provider profile {profile.id!r}; accepted formats: {accepted}.'
        )
    return export_format


def _prepare_profile_rgba(wrap: Image.Image, profile: ProviderProfile) -> Image.Image:
    target_size = (profile.canvas_width_px, profile.canvas_height_px)
    if wrap.size == target_size:
        output = wrap.copy()
        output.info['dpi'] = (profile.dpi, profile.dpi)
        return output
    if not profile.background_policy.startswith('transparent-'):
        raise ProviderExportError(f'Provider profile {profile.id!r} does not define transparent padding.')
    geometry = calculate_contain_geometry(wrap.size, target_size)
    artwork = wrap.convert('RGBA')
    if artwork.size != geometry.scaled_size:
        artwork = artwork.resize(geometry.scaled_size, Image.Resampling.LANCZOS)
    output = Image.new('RGBA', target_size, (0, 0, 0, 0))
    output.alpha_composite(artwork, (geometry.left, geometry.top))
    output.info['dpi'] = (profile.dpi, profile.dpi)
    return output


def _flatten_jpeg(image: Image.Image, background: Optional[tuple[int, int, int]]) -> Image.Image:
    rgba = image.convert('RGBA')
    alpha_min, _ = rgba.getchannel('A').getextrema()
    if alpha_min != 255:
        if background is None:
            raise ProviderExportError('JPEG export with transparency requires an explicit jpeg_background.')
        if len(background) != 3 or any(channel not in range(256) for channel in background):
            raise ProviderExportError('jpeg_background must be an RGB tuple with values from 0 to 255.')
        flattened = Image.new('RGBA', rgba.size, (*background, 255))
        flattened.alpha_composite(rgba)
        return flattened.convert('RGB')
    return rgba.convert('RGB')


def _validate_destination(destination: Path, export_format: str) -> None:
    extensions = {'PNG': {'.png'}, 'JPEG': {'.jpg', '.jpeg'}}
    if destination.suffix.lower() not in extensions[export_format]:
        expected = '/'.join(sorted(extensions[export_format]))
        raise ProviderExportError(
            f'Destination extension {destination.suffix!r} conflicts with {export_format}; expected {expected}.'
        )


def _slug(value: str) -> str:
    normalised = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", normalised.lower())).strip("-")
