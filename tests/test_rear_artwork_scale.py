from __future__ import annotations

import json

import pytest
from PIL import Image, ImageDraw

from mug_previewer.design import (
    DesignOptions,
    build_render_options,
    design_profile_fingerprint,
    load_design_profile,
)
from mug_previewer.exporting import compose_provider_artwork
from mug_previewer.providers import get_provider_profile
from mug_previewer.rendering.context_map import ContextRenderOptions
from mug_previewer.rendering.rear_artwork_scale import REAR_ARTWORK_SCALE_INFO_KEY


def _artwork(size=(495, 462), box=(100, 80, 300, 300)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(box, fill=(0, 0, 0, 255))
    return image


def test_context_options_accept_rear_artwork_scale() -> None:
    options = ContextRenderOptions(artwork_scale=1.15)
    assert options.artwork_scale == pytest.approx(1.15)


def test_design_profile_threads_rear_artwork_scale_to_context_options() -> None:
    design = DesignOptions(rear_artwork_scale=1.15)
    options = build_render_options(design, area="Stoke Newington")
    assert options.context_options is not None
    assert options.context_options.artwork_scale == pytest.approx(1.15)


def test_profile_load_defaults_old_profiles_to_one(tmp_path) -> None:
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"face_render_options": {}, "rear_render_options": {}}))
    assert load_design_profile(path).rear_artwork_scale == pytest.approx(1.0)


def test_profile_load_reads_rear_artwork_scale(tmp_path) -> None:
    path = tmp_path / "scaled.json"
    path.write_text(json.dumps({
        "face_render_options": {},
        "rear_render_options": {"artwork_scale": 1.15},
    }))
    loaded = load_design_profile(path)
    assert loaded.rear_artwork_scale == pytest.approx(1.15)
    assert design_profile_fingerprint(loaded) != design_profile_fingerprint(DesignOptions())


def test_provider_compositor_enlarges_completed_rear_group_only() -> None:
    profile = get_provider_profile("prodigi_h_mug_w")
    front = _artwork()
    rear = _artwork()
    baseline = compose_provider_artwork(front, rear, profile)

    scaled_rear = rear.copy()
    scaled_rear.info[REAR_ARTWORK_SCALE_INFO_KEY] = 1.15
    scaled = compose_provider_artwork(front, scaled_rear, profile)

    assert scaled.front_placement == baseline.front_placement
    assert scaled.rear_placement.width == pytest.approx(
        baseline.rear_placement.width * 1.15,
        abs=2,
    )
    assert scaled.rear_placement.height == pytest.approx(
        baseline.rear_placement.height * 1.15,
        abs=2,
    )
    assert scaled.rear_center_xy == baseline.rear_center_xy


def test_rear_artwork_scale_validation() -> None:
    with pytest.raises(ValueError, match="Rear artwork scale"):
        DesignOptions(rear_artwork_scale=1.51)
    with pytest.raises(ValueError, match="Rear artwork scale"):
        ContextRenderOptions(artwork_scale=0.49)
