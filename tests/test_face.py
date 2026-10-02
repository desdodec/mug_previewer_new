from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from mug_previewer.cli import main
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.manual import ManualPlacementOverride
from mug_previewer.rendering.face import (
    AREA_Y_RATIO,
    FRONT_GROUP_SCALE,
    FRONT_GROUP_Y_OFFSET,
    FRONT_PANEL_PX,
    FRONT_TITLE_LOCALITY_GAP_DELTA_PX,
    FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX,
    LOCALITY_FONT_SIZE,
    STREET_STROKE_MULTIPLIER,
    TITLE_FONT_SIZE_TIERS,
    TITLE_SAFE_WIDTH_PX,
    TITLE_Y_RATIO,
    FaceRenderError,
    FaceRenderOptions,
    _front_group_transform,
    _render_face_standard,
    _front_text_y_positions,
    _spread_vertical_position,
    extract_street_feature_colour,
    render_face,
    render_face_svg,
    select_title_font,
    street_feature_colour,
)
from mug_previewer.rendering.native import face_policy as native

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"

def dataset_copy(tmp_path: Path) -> Path:
    path = tmp_path / "workflow_v6_valid"
    shutil.copytree(FIXTURE, path)
    return path


def test_canonical_svg_exposes_the_same_street_feature_colour_used_by_front_face(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")
    expected = street_feature_colour(street)
    markup = render_face_svg(data, street, FaceRenderOptions(area=data.display_name))

    assert expected.startswith("#") and len(expected) == 7
    assert extract_street_feature_colour(markup) == expected


def test_render_face_returns_v28_front_panel(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    image = render_face(data.get_street("0001"), FaceRenderOptions(area=data.display_name))
    assert image.size == FRONT_PANEL_PX
    assert image.mode == "RGBA"


def test_final_front_composition_scale_offset_and_text_gap_remain_shared_and_safe(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    assert FRONT_GROUP_SCALE == pytest.approx(1.18)
    assert FRONT_GROUP_Y_OFFSET == pytest.approx(60.0)
    assert FRONT_TITLE_LOCALITY_GAP_DELTA_PX == pytest.approx(6.0)
    assert FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX == pytest.approx(-14.0)
    title_y, locality_y = _front_text_y_positions(
        462, FRONT_TITLE_LOCALITY_GAP_DELTA_PX, FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX,
    )
    assert title_y == pytest.approx(462 * TITLE_Y_RATIO - 14.0)
    assert locality_y - title_y == pytest.approx((AREA_Y_RATIO - TITLE_Y_RATIO) * 462 + 6.0)
    transform = _front_group_transform(247.5, FRONT_PANEL_PX[1], FRONT_GROUP_SCALE, FRONT_GROUP_Y_OFFSET)
    assert "translate(247.50 231.00) scale(1.1800) translate(-247.50 -231.00)" in transform

    for street in data.streets:
        image = render_face(street, FaceRenderOptions(area=data.display_name))
        bounds = image.getchannel("A").getbbox()
        assert bounds is not None
        left, top, right, bottom = bounds
        assert 0 < left < right < FRONT_PANEL_PX[0]
        assert 0 < top < bottom < FRONT_PANEL_PX[1]
        assert (left + right) / 2 == pytest.approx(FRONT_PANEL_PX[0] / 2, abs=1.0)


def test_title_font_selection_uses_only_approved_bounded_tiers() -> None:
    font_stack = native.get_text_font_stack(native.DEFAULT_TEXT_FONT_KEY)
    short = select_title_font("Park Road", font_stack)
    medium = select_title_font("William Lucy Way", font_stack)
    long = select_title_font("Victoria Park Gardens North", font_stack)
    very_long = select_title_font("Stoke Newington Church Street", font_stack)

    assert short.size_px == medium.size_px == TITLE_FONT_SIZE_TIERS[0]
    assert long.size_px == TITLE_FONT_SIZE_TIERS[1]
    assert very_long.size_px == TITLE_FONT_SIZE_TIERS[2]
    choices = (short, medium, long, very_long)
    assert all(choice.size_px in TITLE_FONT_SIZE_TIERS for choice in choices)
    assert all(choice.size_px <= TITLE_FONT_SIZE_TIERS[0] for choice in choices)
    assert all(choice.rendered_width_px <= TITLE_SAFE_WIDTH_PX for choice in choices)
    assert LOCALITY_FONT_SIZE == pytest.approx(18.0)
    assert FaceRenderOptions().typography_block_y_offset == FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX


def test_print_calibration_defaults_preserve_existing_front_style() -> None:
    options = FaceRenderOptions()
    assert options.title_font_scale == pytest.approx(1.0)
    assert options.locality_font_scale == pytest.approx(1.0)
    assert options.supporting_stroke_multiplier == pytest.approx(1.0)
    assert options.street_feature_stroke_multiplier == pytest.approx(STREET_STROKE_MULTIPLIER)
    assert options.vertical_spread == pytest.approx(1.0)


def test_title_font_scale_changes_actual_selected_size_without_changing_safe_width() -> None:
    font_stack = native.get_text_font_stack(native.DEFAULT_TEXT_FONT_KEY)
    baseline = select_title_font("Park Road", font_stack)
    enlarged = select_title_font("Park Road", font_stack, font_scale=1.20)

    assert enlarged.size_px == pytest.approx(baseline.size_px * 1.20)
    assert enlarged.rendered_width_px > baseline.rendered_width_px
    assert enlarged.rendered_width_px <= TITLE_SAFE_WIDTH_PX


def test_vertical_spread_moves_rows_away_from_centre_and_identity_is_exact() -> None:
    height = 462.0
    above = 120.0
    below = 340.0
    assert _spread_vertical_position(above, height, 1.0) == pytest.approx(above)
    assert _spread_vertical_position(below, height, 1.0) == pytest.approx(below)
    assert _spread_vertical_position(above, height, 1.20) < above
    assert _spread_vertical_position(below, height, 1.20) > below


def test_editable_svg_exposes_independent_print_calibration_controls(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")
    markup = render_face_svg(
        data,
        street,
        FaceRenderOptions(
            area=data.display_name,
            title_font_scale=1.10,
            locality_font_scale=1.20,
            supporting_stroke_multiplier=1.50,
            vertical_spread=1.10,
        ),
    )

    assert f"stroke-width:{1.68 * 1.50:.2f}px" in markup
    assert f"font:500 {LOCALITY_FONT_SIZE * 1.20:.1f}px" in markup


def test_title_font_selection_refuses_text_that_cannot_fit_at_the_minimum_tier() -> None:
    font_stack = native.get_text_font_stack(native.DEFAULT_TEXT_FONT_KEY)
    with pytest.raises(FaceRenderError, match="cannot fit safely"):
        select_title_font("A Very Long Street Name " * 8, font_stack)


def test_render_face_missing_glyph_is_clear(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    missing = replace(data.get_street("0001"), glyph_path=tmp_path / "missing.svg")

    with pytest.raises(FaceRenderError, match=r'Cannot render street 0001 "St John\'s Road": glyph file does not exist:'):
        render_face(missing)
def test_render_face_uses_the_production_adapted_transform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An approved rescue uses ProductionPlacementDecision.transform, not diagnostic state."""
    from types import SimpleNamespace
    from mug_previewer.diagnostics import front_candidates
    from mug_previewer.diagnostics.front_candidates import Candidate

    decision = SimpleNamespace(adapted=True, transform=SimpleNamespace(candidate=Candidate(0, 1.0, 0, 0)))
    monkeypatch.setattr(front_candidates, "select_production_placement_from_masks", lambda masks: (decision, ()))
    data = load_dataset(dataset_copy(tmp_path))
    image = render_face(data.get_street("0001"), FaceRenderOptions(area=data.display_name))
    assert image.size == FRONT_PANEL_PX
    assert image.info["street_feature_colour"] == native.get_face_palette(native.DEFAULT_PALETTE_KEY).feature.upper()



def test_render_face_accepts_unicode_street_name(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    image = render_face(data.get_street("0002"), FaceRenderOptions(area=data.display_name))
    assert image.size == FRONT_PANEL_PX


def test_cli_renders_fixture_face(tmp_path: Path) -> None:
    path = dataset_copy(tmp_path)
    output = tmp_path / "face.png"
    assert main(["render", "face", "--dataset", str(path), "--street-id", "0001", "--output", str(output)]) == 0
    assert output.is_file()
    with Image.open(output) as image:
        assert image.size == FRONT_PANEL_PX


def test_healthy_fixture_render_remains_byte_identical_to_standard_rendering(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street('0001')
    options = FaceRenderOptions(area=data.display_name)
    assert render_face(street, options).tobytes() == _render_face_standard(street, options).tobytes()


def test_approved_manual_override_uses_exact_constrained_transform(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")
    standard = render_face(street, FaceRenderOptions(area=data.display_name))
    override = ManualPlacementOverride.approved_transform(
        data.id, street.id, street.display_name, orientation_deg=180, scale=0.95, y_offset=20,
    )
    edited = render_face(street, FaceRenderOptions(area=data.display_name, manual_override=override))
    assert edited.size == FRONT_PANEL_PX
    assert edited.tobytes() != standard.tobytes()