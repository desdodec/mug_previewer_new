from __future__ import annotations

from pathlib import Path
import json
import shutil

import pytest

from mug_previewer.design import (
    DESIGN_WEIGHT_MAX,
    DESIGN_WEIGHT_MIN,
    DesignOptions,
    build_render_options,
    design_profile_fingerprint,
    load_design_profile,
)
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.rendering.artwork import render_wrap_result
from mug_previewer.rendering.context_map import REAR_STREET_HIGHLIGHT_SCALE
from mug_previewer.rendering.face import (
    FRONT_GROUP_SCALE,
    FRONT_GROUP_Y_OFFSET,
    STREET_STROKE_MULTIPLIER,
)
from mug_previewer.ui.state import AppState

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def dataset_copy(tmp_path: Path) -> Path:
    path = tmp_path / "workflow_v6_valid"
    shutil.copytree(FIXTURE, path)
    return path


def test_design_options_default_to_the_validated_production_style() -> None:
    assert DesignOptions().front_feature_weight == 1.0
    assert DesignOptions().rear_highlight_weight == 1.0


@pytest.mark.parametrize("value", [DESIGN_WEIGHT_MIN, DESIGN_WEIGHT_MAX])
def test_design_options_accept_global_weight_boundaries(value: float) -> None:
    options = DesignOptions(value, value)
    assert options.front_feature_weight == value
    assert options.rear_highlight_weight == value


@pytest.mark.parametrize("value", [DESIGN_WEIGHT_MIN - 0.01, DESIGN_WEIGHT_MAX + 0.01, float("inf"), float("nan")])
def test_design_options_reject_invalid_weight_values(value: float) -> None:
    with pytest.raises(ValueError):
        DesignOptions(front_feature_weight=value)
    with pytest.raises(ValueError):
        DesignOptions(rear_highlight_weight=value)


def test_design_mapping_changes_only_the_exposed_renderer_strokes() -> None:
    options = build_render_options(DesignOptions(1.25, 0.75), area="Oxford")

    assert options.face_options is not None
    assert options.context_options is not None
    assert options.face_options.street_feature_stroke_multiplier == pytest.approx(STREET_STROKE_MULTIPLIER * 1.25)
    assert options.context_options.highlight_stroke_scale == pytest.approx(REAR_STREET_HIGHLIGHT_SCALE * 0.75)
    assert options.face_options.group_scale == FRONT_GROUP_SCALE
    assert options.face_options.group_y_offset == FRONT_GROUP_Y_OFFSET


def test_calibration_profile_maps_to_complete_production_design(tmp_path: Path) -> None:
    profile = tmp_path / "style.json"
    profile.write_text(
        json.dumps({
            "profile_version": 2,
            "face_render_options": {
                "group_scale": 1.12,
                "group_y_offset": 58.0,
                "title_locality_gap_delta": 11.0,
                "typography_block_y_offset": -18.0,
                "title_font_scale": 1.15,
                "locality_font_scale": 1.10,
                "facial_linework_multiplier": 1.45,
                "supporting_stroke_multiplier": 1.05,
                "street_feature_stroke_multiplier": STREET_STROKE_MULTIPLIER * 1.30,
                "vertical_spread": 1.08,
            },
            "rear_render_options": {
                "attribution_font_scale": 1.25,
                "attribution_line_spacing_scale": 1.15,
            },
        }),
        encoding="utf-8",
    )

    design = load_design_profile(profile)
    options = build_render_options(design, area="Hebden Bridge")

    assert design.front_feature_weight == pytest.approx(1.30)
    assert design.front_facial_linework_multiplier == pytest.approx(1.45)
    assert design.rear_attribution_font_scale == pytest.approx(1.25)
    assert options.face_options.title_font_scale == pytest.approx(1.15)
    assert options.face_options.vertical_spread == pytest.approx(1.08)
    assert options.context_options.attribution_font_scale == pytest.approx(1.25)
    assert options.context_options.attribution_line_spacing_scale == pytest.approx(1.15)
    assert design_profile_fingerprint(design) != design_profile_fingerprint(DesignOptions())


def test_quick_weight_change_does_not_discard_loaded_profile_values() -> None:
    state = AppState(
        design_options=DesignOptions(
            front_title_font_scale=1.20,
            rear_attribution_font_scale=1.30,
        )
    )
    state.set_design_options(1.4, 0.8)

    assert state.design_options.front_feature_weight == pytest.approx(1.4)
    assert state.design_options.rear_highlight_weight == pytest.approx(0.8)
    assert state.design_options.front_title_font_scale == pytest.approx(1.20)
    assert state.design_options.rear_attribution_font_scale == pytest.approx(1.30)


def test_default_design_render_options_preserve_the_existing_renderer_baseline(tmp_path: Path) -> None:
    data = load_dataset(dataset_copy(tmp_path))
    street = data.get_street("0001")

    baseline = render_wrap_result(data, street)
    styled = render_wrap_result(data, street, build_render_options(DesignOptions(), area=data.display_name))

    assert styled.image.tobytes() == baseline.image.tobytes()
    assert styled.front_panel.tobytes() == baseline.front_panel.tobytes()
    assert styled.rear_panel.tobytes() == baseline.rear_panel.tobytes()
    assert styled.context.framing_mode == baseline.context.framing_mode
    assert styled.context.final_context_width_m == baseline.context.final_context_width_m


def test_reset_design_options_preserves_other_ui_state() -> None:
    state = AppState(street_filter="park")
    state.set_design_options(1.25, 0.75)
    state.reset_design_options()

    assert state.design_options == DesignOptions()
    assert state.street_filter == "park"
