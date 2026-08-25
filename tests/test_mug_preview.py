from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from mug_previewer.preview.diagnostic import create_cylindrical_stripe_wrap, create_mockup_diagnostic_wrap
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


def _body_difference_stats(
    expected: Image.Image, actual: Image.Image, mask: Image.Image,
) -> tuple[float, int, float]:
    deltas = [
        max(abs(left - right) for left, right in zip(expected_pixel[:3], actual_pixel[:3]))
        for expected_pixel, actual_pixel, coverage in zip(expected.getdata(), actual.getdata(), mask.getdata())
        if coverage
    ]
    return sum(deltas) / len(deltas), max(deltas), sum(delta > 0 for delta in deltas) * 100 / len(deltas)


def test_transparent_wrap_is_identical_to_blank_mug_over_the_full_body() -> None:
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    transparent = Image.new("RGBA", (geometry.width_px, geometry.height_px), (0, 0, 0, 0))
    assets = Path(__file__).parents[1] / "src" / "mug_previewer" / "preview" / "assets"
    with Image.open(assets / "white_mug.png") as opened:
        base = opened.convert("RGBA")
    with Image.open(assets / "white_mug_mask.png") as opened:
        body_mask = opened.convert("L")

    for orientation in ("front-handle-right", "rear-handle-left"):
        expected = base if orientation == "front-handle-right" else base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        mask = body_mask if orientation == "front-handle-right" else body_mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        actual = render_mug_preview(transparent, MugPreviewOptions(orientation=orientation))
        mean, maximum, changed_percent = _body_difference_stats(expected, actual, mask)

        assert (mean, maximum, changed_percent) == (0.0, 0, 0.0)


def test_small_opaque_mark_changes_only_its_projected_pixels() -> None:
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    target_size = (
        DEFAULT_MUG_PREVIEW_LAYOUT.body_bounds_xyxy[2] - DEFAULT_MUG_PREVIEW_LAYOUT.body_bounds_xyxy[0],
        DEFAULT_MUG_PREVIEW_LAYOUT.body_bounds_xyxy[3] - DEFAULT_MUG_PREVIEW_LAYOUT.body_bounds_xyxy[1],
    )
    for orientation, source_centre_x, left in (
        ("front-handle-right", geometry.front_centre_x, 198),
        ("rear-handle-left", geometry.rear_centre_x, 341),
    ):
        marked = Image.new("RGBA", (geometry.width_px, geometry.height_px), (0, 0, 0, 0))
        centre = round(source_centre_x)
        ImageDraw.Draw(marked).rectangle((centre - 10, 500, centre + 10, 520), fill=(220, 30, 30, 255))
        blank = render_mug_preview(Image.new("RGBA", marked.size, (0, 0, 0, 0)), MugPreviewOptions(orientation=orientation))
        actual = render_mug_preview(marked, MugPreviewOptions(orientation=orientation))
        projected_alpha = project_canonical_wrap(
            marked, target_size=target_size, source_centre_x=source_centre_x,
        ).getchannel("A")
        changed = [
            (x, y)
            for y in range(482, 1105)
            for x in range(left, left + target_size[0])
            if actual.getpixel((x, y))[:3] != blank.getpixel((x, y))[:3]
        ]

        assert changed
        assert all(projected_alpha.getpixel((x - left, y - 482)) > 0 for x, y in changed)


def test_stripe_spacing_is_widest_at_cylinder_centre_for_both_orientations() -> None:
    for source_centre_x in (
        CANONICAL_WRAP_PREVIEW_GEOMETRY.front_centre_x,
        CANONICAL_WRAP_PREVIEW_GEOMETRY.rear_centre_x,
    ):
        stripe_wrap = Image.new("RGBA", (2362, 1063), (0, 0, 0, 0))
        stripe_draw = ImageDraw.Draw(stripe_wrap)
        for index in range(-6, 7):
            x = round(source_centre_x) + index * 75
            stripe_draw.rectangle((x - 5, 0, x + 5, 1062), fill=(24, 91, 191, 255))
        projected = project_canonical_wrap(stripe_wrap, target_size=(485, 623), source_centre_x=source_centre_x)
        row = [projected.getpixel((x, 311))[3] > 200 for x in range(projected.width)]
        regions: list[tuple[int, int]] = []
        start = None
        for x, occupied in enumerate(row + [False]):
            if occupied and start is None:
                start = x
            elif not occupied and start is not None:
                regions.append((start, x - 1))
                start = None
        centres = [(start + end) / 2 for start, end in regions]
        spacings = [right - left for left, right in zip(centres, centres[1:])]

        assert len(centres) == 13
        assert spacings[0] < spacings[1] < spacings[2] < spacings[3] < spacings[4] < spacings[5]
        assert spacings[6] > spacings[7] > spacings[8] > spacings[9] > spacings[10] > spacings[11]



def _luminance(pixel: tuple[int, int, int, int]) -> float:
    return sum(pixel[:3]) / 3


def test_transparent_wrap_preserves_blank_mug_without_print_area_bands() -> None:
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    transparent = Image.new("RGBA", (geometry.width_px, geometry.height_px), (0, 0, 0, 0))
    assets = Path(__file__).parents[1] / "src" / "mug_previewer" / "preview" / "assets"
    with Image.open(assets / "white_mug.png") as opened:
        base = opened.convert("RGBA")

    for orientation, boundaries in (
        ("front-handle-right", (198, 683)),
        ("rear-handle-left", (341, 826)),
    ):
        preview = render_mug_preview(transparent, MugPreviewOptions(orientation=orientation))
        expected_base = base if orientation == "front-handle-right" else base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        for boundary in boundaries:
            before = _luminance(preview.getpixel((boundary - 1, 800)))
            after = _luminance(preview.getpixel((boundary, 800)))
            assert abs(after - before) < 8
            assert preview.getpixel((boundary, 800)) == expected_base.getpixel((boundary, 800))


def test_transparent_pixels_do_not_change_unprinted_ceramic() -> None:
    geometry = CANONICAL_WRAP_PREVIEW_GEOMETRY
    transparent = Image.new("RGBA", (geometry.width_px, geometry.height_px), (0, 0, 0, 0))
    marked = transparent.copy()
    ImageDraw.Draw(marked).rectangle((450, 500, 494, 550), fill=(220, 30, 30, 255))

    for orientation in ("front-handle-right", "rear-handle-left"):
        blank = render_mug_preview(transparent, MugPreviewOptions(orientation=orientation))
        preview = render_mug_preview(marked, MugPreviewOptions(orientation=orientation))
        # The source mark is central only in the front view; distant transparent
        # pixels must always reveal exactly the same underlying ceramic.
        assert preview.getpixel((250, 800)) == blank.getpixel((250, 800))
