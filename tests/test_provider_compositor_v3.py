from dataclasses import replace

from PIL import Image, ImageDraw
import pytest

from mug_previewer.exporting import (
    compose_provider_artwork,
    save_profile_set,
    supplier_output_filename,
)
from mug_previewer.providers import get_provider_profile


def artwork(size=(495, 462), box=(100, 80, 300, 300), colour=(0, 0, 0, 255)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(box, fill=colour)
    return image


def test_inkthreadable_v3_profile_uses_requested_canvas_and_quarter_wrap_anchors():
    profile = get_provider_profile("inkthreadable_11oz_white")
    result = compose_provider_artwork(artwork(), artwork(), profile)
    assert result.image.size == (2550, 1125)
    assert result.front_center_xy == (638, 563)
    assert result.rear_center_xy == (1913, 563)
    assert result.image.mode == "RGBA"


def test_visual_bounds_not_container_bounds_control_centring():
    profile = get_provider_profile("inkthreadable_11oz_white")
    padded = artwork(box=(140, 120, 240, 220))
    result = compose_provider_artwork(padded, padded, profile)
    front = result.front_placement
    rear = result.rear_placement
    assert front.x + front.width / 2 == pytest.approx(result.front_center_xy[0], abs=0.5)
    assert front.y + front.height / 2 == pytest.approx(result.front_center_xy[1], abs=0.5)
    assert rear.x + rear.width / 2 == pytest.approx(result.rear_center_xy[0], abs=0.5)
    assert rear.y + rear.height / 2 == pytest.approx(result.rear_center_xy[1], abs=0.5)


def test_scaling_is_uniform_and_profile_controlled():
    profile = replace(get_provider_profile("inkthreadable_11oz_white"), front_scale=0.5)
    result = compose_provider_artwork(artwork(box=(100, 100, 299, 199)), artwork(), profile)
    source_width = result.front_source_bounds[2] - result.front_source_bounds[0]
    source_height = result.front_source_bounds[3] - result.front_source_bounds[1]
    assert result.front_placement.width / source_width == pytest.approx(
        result.front_placement.height / source_height, rel=0.02
    )


def test_inward_offset_is_symmetric_and_stored_in_mm():
    profile = replace(get_provider_profile("inkthreadable_11oz_white"), inward_offset_mm=2.54)
    result = compose_provider_artwork(artwork(), artwork(), profile)
    assert result.inward_offset_px == pytest.approx(30.0)
    assert result.front_center_xy[0] == 668
    assert result.rear_center_xy[0] == 1883


def test_debug_guides_are_separate_from_production_pixels():
    profile = get_provider_profile("inkthreadable_11oz_white")
    result = compose_provider_artwork(artwork(), artwork(), profile, debug=True)
    assert result.debug_image is not None
    assert result.image.getpixel((profile.canvas_width_px // 2, 10))[3] == 0
    assert result.debug_image.getpixel((profile.canvas_width_px // 2, 10))[3] != 0


def test_prodigi_h_mug_w_profile_uses_product_specific_canvas():
    profile = get_provider_profile("prodigi_h_mug_w")
    result = compose_provider_artwork(artwork(), artwork(), profile)
    assert result.image.size == (2705, 1122)
    assert result.front_center_xy == (676, 561)
    assert result.rear_center_xy == (2029, 561)
    assert profile.front_centre_x == 0.25
    assert profile.rear_centre_x == 0.75


def test_supplier_filename_matches_v3_contract():
    ink = get_provider_profile("inkthreadable_11oz_white")
    prodigi = get_provider_profile("prodigi_h_mug_w")
    assert supplier_output_filename("Aspinall Street", ink) == "aspinall-street_inkthreadable.png"
    assert supplier_output_filename("Aspinall Street", prodigi, debug=True) == "aspinall-street_prodigi_debug.png"



def test_one_source_pair_can_write_both_supplier_files_and_debugs(tmp_path):
    outputs = save_profile_set(
        artwork(),
        artwork(),
        tmp_path,
        "Aspinall Street",
        debug=True,
    )
    assert set(outputs) == {"inkthreadable_11oz_white", "prodigi_h_mug_w"}
    ink, ink_debug = outputs["inkthreadable_11oz_white"]
    prodigi, prodigi_debug = outputs["prodigi_h_mug_w"]
    assert ink.name == "aspinall-street_inkthreadable.png"
    assert prodigi.name == "aspinall-street_prodigi.png"
    assert ink_debug is not None and ink_debug.name == "aspinall-street_inkthreadable_debug.png"
    assert prodigi_debug is not None and prodigi_debug.name == "aspinall-street_prodigi_debug.png"
    assert all(path.is_file() for path in (ink, ink_debug, prodigi, prodigi_debug))
