"""Transform a completed canonical master into one provider print file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

from PIL import Image

from .providers import AlphaHandling, CroppingPolicy, PaddingPolicy, ProviderExportSpec, ScalingPolicy


class ProviderExportError(ValueError):
    """Raised when an image cannot satisfy a provider export specification."""


@dataclass(frozen=True)
class ProviderExportOptions:
    """Export behaviour that is independent of a provider requirement."""

    resampling: Image.Resampling = Image.Resampling.LANCZOS


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


def _slug(value: str) -> str:
    normalised = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", normalised.lower())).strip("-")
