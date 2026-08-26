from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from mug_previewer.design import (
    DESIGN_WEIGHT_MAX,
    DESIGN_WEIGHT_MIN,
    DesignOptions,
    build_render_options,
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


@pytest.mark.parametrize("value", [0.74, 1.51, float("inf"), float("nan")])
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