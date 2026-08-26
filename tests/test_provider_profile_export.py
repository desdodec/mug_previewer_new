from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from mug_previewer.exporting import (
    ExportOptions,
    ProviderExportError,
    calculate_contain_geometry,
    prepare_provider_image,
    save_provider_export,
)
from mug_previewer.providers import get_provider_profile


CANONICAL_SIZE = (2362, 1063)


def canonical_image() -> Image.Image:
    image = Image.new('RGBA', CANONICAL_SIZE, (20, 40, 60, 255))
    image.putpixel((100, 100), (30, 70, 200, 255))
    return image


def test_inkthreadable_fast_path_preserves_pixels() -> None:
    source = canonical_image()
    exported = prepare_provider_image(source, get_provider_profile('inkthreadable_11oz_white'))
    assert exported.size == CANONICAL_SIZE
    assert exported.mode == 'RGBA'
    assert exported.tobytes() == source.tobytes()


def test_printify_geometry_is_uniform_and_floor_centred() -> None:
    geometry = calculate_contain_geometry(CANONICAL_SIZE, (2475, 1155))
    assert geometry.scaled_size == (2475, 1114)
    assert geometry.left == 0
    assert geometry.top == 20
    assert geometry.padding_ltrb == (0, 20, 0, 21)
    assert geometry.scale_factor == pytest.approx(2475 / 2362)


def test_printify_preserves_marked_edges_and_transparent_padding() -> None:
    source = Image.new('RGBA', CANONICAL_SIZE, (10, 10, 10, 255))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 19, 19), fill=(255, 0, 0, 255))
    draw.rectangle((2342, 0, 2361, 19), fill=(0, 255, 0, 255))
    draw.rectangle((0, 1043, 19, 1062), fill=(0, 0, 255, 255))
    draw.rectangle((2342, 1043, 2361, 1062), fill=(255, 255, 0, 255))
    exported = prepare_provider_image(source, get_provider_profile('printify_generic_11oz_ceramic'))
    assert exported.size == (2475, 1155)
    assert exported.getpixel((100, 0))[3] == 0
    assert exported.getpixel((100, 20))[3] == 255
    assert exported.getpixel((10, 30))[:3] == (255, 0, 0)
    assert exported.getpixel((2464, 30))[:3] == (0, 255, 0)
    assert exported.getpixel((10, 1123))[:3] == (0, 0, 255)
    assert exported.getpixel((2464, 1123))[:3] == (255, 255, 0)


def test_profile_formats_and_jpeg_flattening_are_explicit() -> None:
    profile = get_provider_profile('printify_generic_11oz_ceramic')
    with pytest.raises(ProviderExportError, match='jpeg_background'):
        prepare_provider_image(canonical_image(), profile, ExportOptions(format='JPEG'))
    exported = prepare_provider_image(canonical_image(), profile, ExportOptions(format='JPEG', jpeg_background=(255, 255, 255)))
    assert exported.mode == 'RGB'
    with pytest.raises(ProviderExportError, match='not accepted'):
        prepare_provider_image(canonical_image(), get_provider_profile('inkthreadable_11oz_white'), ExportOptions(format='JPEG'))


def test_png_save_writes_dimensions_and_dpi(tmp_path) -> None:
    profile = get_provider_profile('printify_generic_11oz_ceramic')
    path = save_provider_export(canonical_image(), profile, tmp_path / 'printify.png')
    with Image.open(path) as reopened:
        assert reopened.size == (2475, 1155)
        assert reopened.mode == 'RGBA'
        assert reopened.info['dpi'] == pytest.approx((300, 300), abs=0.1)


def test_canonical_input_and_destination_extension_are_validated(tmp_path) -> None:
    profile = get_provider_profile('inkthreadable_11oz_white')
    with pytest.raises(ProviderExportError, match='canonical'):
        prepare_provider_image(Image.new('RGBA', (1, 1)), profile)
    with pytest.raises(ProviderExportError, match='extension'):
        save_provider_export(canonical_image(), profile, tmp_path / 'wrong.jpg')
