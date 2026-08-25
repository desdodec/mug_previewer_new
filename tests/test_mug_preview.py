from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from mug_previewer.preview.diagnostic import create_mockup_diagnostic_wrap
from mug_previewer.preview.mockup import (
    CANONICAL_WRAP_PREVIEW_GEOMETRY,
    DEFAULT_MUG_PREVIEW_LAYOUT,
    MugPreviewError,
    MugPreviewOptions,
    PreviewOrientation,
    project_canonical_wrap,
    render_mug_preview,
)


def _banded_wrap() -> Image.Image:
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    image = Image.new("RGBA", (geometry.width_px, geometry.height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 944, geometry.height_px - 1), fill=(220, 40, 40, 255))
    draw.rectangle((945, 0, 1416, geometry.height_px - 1), fill=(40, 170, 70, 255))
    draw.rectangle((1417, 0, 2361, geometry.height_px - 1), fill=(45, 80, 210, 255))
    return image


def test_preview_has_expected_output_and_does_not_mutate_wrap() -> None:
    wrap = _banded_wrap()
    before = wrap.tobytes()

    preview = render_mug_preview(wrap)

    assert preview.size == (1024, 1536)
    assert preview.mode == "RGBA"
    assert wrap.tobytes() == before


def test_front_is_central_while_seam_and_rear_are_limited_to_edges() -> None:
    projected = project_canonical_wrap(_banded_wrap(), target_size=(485, 623))

    assert projected.getpixel((242, 311))[:3] == (220, 40, 40)
    assert projected.getpixel((484, 311))[1] > 100  # seam at handle-side edge
    assert projected.getpixel((0, 311))[2] > 150  # rear tail only at far left wrap edge


def test_cylindrical_projection_compresses_edge_stripes_more_than_centre() -> None:
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    wrap = Image.new("RGBA", (geometry.width_px, geometry.height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(wrap)
    centre = round(geometry.front_centre_x)
    draw.rectangle((centre - 15, 0, centre + 14, geometry.height_px - 1), fill=(255, 0, 0, 255))
    draw.rectangle((centre + 360, 0, centre + 389, geometry.height_px - 1), fill=(0, 0, 255, 255))
    projected = project_canonical_wrap(wrap, target_size=(485, 623))
    row = [projected.getpixel((x, 311)) for x in range(projected.width)]
    centre_width = sum(red > 220 and blue < 20 for red, _, blue, alpha in row if alpha > 220)
    edge_width = sum(blue > 220 and red < 20 for red, _, blue, alpha in row if alpha > 220)

    assert centre_width > 0
    assert edge_width > 0
    assert edge_width < centre_width


def test_artwork_stays_inside_owned_body_mask() -> None:
    preview = render_mug_preview(_banded_wrap())
    assets = Path(__file__).parents[1] / "src" / "mug_previewer" / "preview" / "assets"
    with Image.open(assets / "white_mug.png") as opened:
        base = opened.convert("RGBA")
    with Image.open(assets / "white_mug_mask.png") as opened:
        mask = opened.convert("L")
    for point in ((0, 0), (900, 800), (160, 800)):
        assert mask.getpixel(point) == 0
        assert preview.getpixel(point) == base.getpixel(point)


def test_preview_is_deterministic_and_debug_guides_are_opt_in() -> None:
    wrap = _banded_wrap()
    assert render_mug_preview(wrap).tobytes() == render_mug_preview(wrap).tobytes()
    debug = render_mug_preview(wrap, options=MugPreviewOptions(show_debug_guides=True))
    assert debug.tobytes() != render_mug_preview(wrap).tobytes()


def test_diagnostic_wrap_and_invalid_size_are_explicit() -> None:
    diagnostic = create_mockup_diagnostic_wrap()
    assert diagnostic.size == (2362, 1063)
    assert diagnostic.getpixel((200, 500))[:3] == (217, 75, 75)
    assert diagnostic.getpixel((1100, 500))[:3] == (75, 154, 100)
    assert diagnostic.getpixel((1800, 500))[:3] == (77, 116, 199)
    with pytest.raises(MugPreviewError, match="requires canonical"):
        render_mug_preview(Image.new("RGBA", (1, 1)))


def test_default_front_orientation_matches_explicit_front() -> None:
    wrap = _banded_wrap()
    default = render_mug_preview(wrap)
    explicit = render_mug_preview(wrap, MugPreviewOptions(orientation=PreviewOrientation.FRONT_HANDLE_RIGHT))
    string_option = render_mug_preview(wrap, MugPreviewOptions(orientation="front-handle-right"))

    assert default.tobytes() == explicit.tobytes() == string_option.tobytes()


def test_rear_projection_centres_rear_with_seam_at_left_and_front_at_right() -> None:
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    projected = project_canonical_wrap(
        _banded_wrap(), target_size=(485, 623), source_centre_x=geometry.rear_centre_x,
    )

    assert projected.getpixel((242, 311))[:3] == (45, 80, 210)
    assert projected.getpixel((0, 311))[1] > 100  # seam beside the left handle edge
    assert projected.getpixel((484, 311))[0] > 150  # front tail only at the opposite edge


def test_rear_preview_is_deterministic_masked_and_debuggable() -> None:
    wrap = _banded_wrap()
    rear_options = MugPreviewOptions(orientation="rear-handle-left")
    rear = render_mug_preview(wrap, rear_options)

    assert rear.size == (1024, 1536)
    assert rear.mode == "RGBA"
    assert rear.tobytes() == render_mug_preview(wrap, rear_options).tobytes()
    assert rear.tobytes() != render_mug_preview(wrap, MugPreviewOptions(orientation="rear-handle-left", show_debug_guides=True)).tobytes()

    assets = Path(__file__).parents[1] / "src" / "mug_previewer" / "preview" / "assets"
    with Image.open(assets / "white_mug.png") as opened:
        mirrored_base = opened.convert("RGBA").transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    with Image.open(assets / "white_mug_mask.png") as opened:
        mirrored_mask = opened.convert("L").transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    for point in ((0, 0), (900, 800), (860, 800)):
        assert mirrored_mask.getpixel(point) == 0
        assert rear.getpixel(point) == mirrored_base.getpixel(point)
