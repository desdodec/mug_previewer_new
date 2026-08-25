from __future__ import annotations

from dataclasses import replace

import pytest
from PIL import Image, ImageDraw

from mug_previewer.exporting import (
    GELATO_WHITE_11OZ_CERAMIC_MUG,
    AlphaHandling,
    CroppingPolicy,
    PaddingPolicy,
    ProviderExportError,
    ProviderExportSpec,
    ScalingPolicy,
    export_wrap,
    export_wrap_result,
    provider_export_filename,
    save_provider_png,
)


def test_matching_exact_spec_preserves_pixels_and_source() -> None:
    source = Image.new("RGBA", (2362, 1063), (0, 0, 0, 0))
    source.putpixel((100, 100), (30, 70, 200, 255))
    source_before = source.tobytes()
    spec = ProviderExportSpec(
        provider="Test Provider", product="Exact profile", target_width_px=2362, target_height_px=1063,
        dpi=300, required_mode="RGBA", alpha_handling=AlphaHandling.PRESERVE, background_rgb=None,
        scaling=ScalingPolicy.EXACT_NO_RESIZE, cropping=CroppingPolicy.NONE, padding=PaddingPolicy.NONE,
        output_format="PNG", specification_url="https://example.invalid/exact",
    )
    exported = export_wrap(source, spec)
    assert exported is not source
    assert exported.tobytes() == source_before
    assert source.tobytes() == source_before
    assert exported.info["dpi"] == (300, 300)


def test_gelato_profile_has_exact_dimensions_and_centered_transparent_padding() -> None:
    result = export_wrap_result(Image.new("RGBA", (2362, 1063), (20, 40, 60, 255)), GELATO_WHITE_11OZ_CERAMIC_MUG)
    assert result.image.size == (2362, 1134)
    assert result.image.mode == "RGBA"
    assert result.scale_factor == 1
    assert result.placement_xywh == (0, 35, 2362, 1063)
    assert result.padding_ltrb == (0, 35, 0, 36)
    assert result.image.getpixel((400, 34)) == (0, 0, 0, 0)
    assert result.image.getpixel((400, 35)) == (20, 40, 60, 255)
    assert result.image.getpixel((400, 1097)) == (20, 40, 60, 255)
    assert result.image.getpixel((400, 1098)) == (0, 0, 0, 0)


def test_contain_preserves_non_square_feature_aspect_ratio() -> None:
    source = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    ImageDraw.Draw(source).rectangle((20, 10, 59, 29), fill=(255, 0, 0, 255))
    result = export_wrap_result(source, replace(GELATO_WHITE_11OZ_CERAMIC_MUG, target_width_px=200, target_height_px=200))
    alpha = result.image.getchannel("A")
    opaque_bounds = alpha.point(lambda value: 255 if value > 250 else 0).getbbox()
    assert opaque_bounds == (41, 71, 119, 109)
    assert (opaque_bounds[2] - opaque_bounds[0]) / (opaque_bounds[3] - opaque_bounds[1]) == pytest.approx(2, abs=0.1)


def test_cover_uses_deterministic_center_crop() -> None:
    source = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 99, 99), fill=(255, 0, 0, 255))
    draw.rectangle((100, 0, 199, 99), fill=(0, 255, 0, 255))
    draw.rectangle((200, 0, 299, 99), fill=(0, 0, 255, 255))
    spec = replace(GELATO_WHITE_11OZ_CERAMIC_MUG, target_width_px=100, target_height_px=100,
                   scaling=ScalingPolicy.COVER, cropping=CroppingPolicy.CENTRE, padding=PaddingPolicy.NONE)
    result = export_wrap_result(source, spec)
    assert result.scale_factor == 1
    assert result.crop_xywh == (100, 0, 100, 100)
    assert result.image.getpixel((50, 50)) == (0, 255, 0, 255)


def test_rgb_flattening_uses_explicit_background() -> None:
    spec = ProviderExportSpec(
        provider="Test Provider", product="Opaque profile", target_width_px=10, target_height_px=10,
        dpi=300, required_mode="RGB", alpha_handling=AlphaHandling.FLATTEN, background_rgb=(255, 255, 255),
        scaling=ScalingPolicy.EXACT_NO_RESIZE, cropping=CroppingPolicy.NONE, padding=PaddingPolicy.NONE,
        output_format="PNG", specification_url="https://example.invalid/opaque",
    )
    exported = export_wrap(Image.new("RGBA", (10, 10), (255, 0, 0, 128)), spec)
    assert exported.mode == "RGB"
    assert exported.getpixel((0, 0)) == (255, 127, 127)


def test_png_save_preserves_dpi_metadata(tmp_path) -> None:
    path = tmp_path / "provider.png"
    result = save_provider_png(Image.new("RGBA", (2362, 1063)), path, GELATO_WHITE_11OZ_CERAMIC_MUG)
    with Image.open(path) as reopened:
        assert reopened.size == result.image.size
        assert reopened.mode == "RGBA"
        assert reopened.info["dpi"] == pytest.approx((300, 300), abs=0.1)


def test_filename_is_ascii_safe_and_predictable() -> None:
    assert provider_export_filename("0246", "Stoke Newington Church Street", GELATO_WHITE_11OZ_CERAMIC_MUG) == "0246_stoke-newington-church-street_gelato_white-11oz-ceramic-mug.png"
    assert provider_export_filename("0002", "Café / Road?", GELATO_WHITE_11OZ_CERAMIC_MUG).startswith("0002_cafe-road_")


def test_exact_no_resize_rejects_wrong_source_size() -> None:
    spec = replace(GELATO_WHITE_11OZ_CERAMIC_MUG, scaling=ScalingPolicy.EXACT_NO_RESIZE, padding=PaddingPolicy.NONE)
    with pytest.raises(ProviderExportError, match="Exact-no-resize"):
        export_wrap(Image.new("RGBA", (1, 1)), spec)
