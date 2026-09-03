from __future__ import annotations

from PIL import Image, ImageDraw

from mug_previewer.rendering.face import assess_nose_street_clearance


def _masks(*, street_top: int, street_width: int = 121, street_height: int = 2) -> tuple[Image.Image, Image.Image]:
    nose = Image.new('L', (220, 100), 0)
    street = Image.new('L', nose.size, 0)
    ImageDraw.Draw(nose).line((40, 30, 160, 30), fill=255, width=1)
    ImageDraw.Draw(street).rectangle((40, street_top, 40 + street_width - 1, street_top + street_height - 1), fill=255)
    return nose, street


def test_tiny_overlap_gets_corrected() -> None:
    nose, street = _masks(street_top=28)
    correction = assess_nose_street_clearance(nose, street)
    assert correction == (5, 5, 'auto-corrected')


def test_correction_uses_the_minimum_required_downward_shift() -> None:
    nose, street = _masks(street_top=30)
    correction = assess_nose_street_clearance(nose, street)
    assert correction.required_shift_px == 3
    assert correction.applied_shift_px == 3


def test_correction_cap_allows_exactly_twelve_pixels() -> None:
    nose, street = _masks(street_top=21)
    correction = assess_nose_street_clearance(nose, street)
    assert correction == (12, 12, 'auto-corrected')


def test_large_overlap_is_not_auto_corrected() -> None:
    nose, street = _masks(street_top=20)
    correction = assess_nose_street_clearance(nose, street)
    assert correction == (13, 0, 'manual-review')


def test_atypical_lower_feature_remains_manual() -> None:
    nose, street = _masks(street_top=25, street_width=90, street_height=20)
    correction = assess_nose_street_clearance(nose, street)
    assert correction == (8, 0, 'manual-review')


def test_clearance_decision_is_deterministic() -> None:
    nose, street = _masks(street_top=28)
    assert assess_nose_street_clearance(nose, street) == assess_nose_street_clearance(nose, street)
