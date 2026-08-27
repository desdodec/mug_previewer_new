from __future__ import annotations

import base64
import math
from pathlib import Path

import pytest
from PIL import Image

from mug_previewer.datasets.models import StreetRecord
from mug_previewer.rendering import face
from mug_previewer.diagnostics.front_candidates import (
    Candidate,
    CandidateGrid,
    DiagnosticMaskError,
    FaceAnatomyMasks,
    ProximityThresholds,
    generate_candidates,
    nearest_foreground_distance,
    overlap_pixels,
    score_candidate,
    transform_street_mask,
    audit_masks,
    _asset_mask,
    _render_mask,
    render_production_masks,
)


def _mask(points: set[tuple[int, int]], size: tuple[int, int] = (40, 40)) -> Image.Image:
    image = Image.new("L", size, 0)
    for point in points:
        image.putpixel(point, 255)
    return image


def _masks() -> FaceAnatomyMasks:
    return FaceAnatomyMasks(
        _mask({(19, 19), (20, 19), (19, 20), (20, 20)}),
        _mask({(4, 4)}), _mask({(35, 4)}), _mask({(4, 35)}), _mask({(35, 35)}), Image.new("RGBA", (40, 40)),
    )


def _blank_masks(street: Image.Image, *, left: Image.Image | None = None, nose: Image.Image | None = None) -> FaceAnatomyMasks:
    return FaceAnatomyMasks(
        street, left or _mask({(38, 38)}, street.size), _mask({(39, 39)}, street.size),
        nose or _mask({(1, 38)}, street.size), _mask({(1, 39)}, street.size), Image.new("RGBA", street.size),
    )


def test_candidate_grid_is_deterministic_and_has_only_allowed_orientations() -> None:
    grid = CandidateGrid(scales=(1.0, 0.9), x_offsets=(0,), y_offsets=(-20, 0, 20))
    first, second = generate_candidates(grid), generate_candidates(grid)
    assert first == second
    assert len(first) == grid.candidate_count == 12
    assert {item.orientation_deg for item in first} == {0, 180}


def test_overlap_pixels_handles_none_partial_and_full() -> None:
    base = _mask({(1, 1), (2, 1)})
    assert overlap_pixels(base, _mask({(3, 3)})) == 0
    assert overlap_pixels(base, _mask({(2, 1), (3, 1)})) == 1
    assert overlap_pixels(base, _mask({(1, 1), (2, 1)})) == 2


def test_protected_collisions_score_worse_than_clear_candidate() -> None:
    masks = _masks()
    clear = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    eye = score_candidate(masks, Candidate(0, 1.0, -15, -15))
    nose = score_candidate(masks, Candidate(0, 1.0, -15, 15))
    typography = score_candidate(masks, Candidate(0, 1.0, 15, 15))
    assert clear.score > eye.score
    assert clear.score > nose.score
    assert typography.collision_penalty > clear.collision_penalty


def test_larger_clear_candidate_and_original_orientation_are_preferred() -> None:
    masks = _masks()
    full = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    smaller = score_candidate(masks, Candidate(0, 0.8, 0, 0))
    rotated = score_candidate(masks, Candidate(180, 1.0, 0, 0))
    assert full.score > smaller.score
    assert full.score > rotated.score


def test_clipping_is_detected_and_severely_penalised() -> None:
    masks = _masks()
    clipped_mask, clipped = transform_street_mask(masks.street_mouth, Candidate(0, 1.0, -40, 0))
    assert clipped and clipped_mask.getbbox() is None
    with pytest.raises(DiagnosticMaskError, match="empty street mask"):
        score_candidate(masks, Candidate(0, 1.0, -40, 0))


@pytest.mark.parametrize(
    ("street", "protected", "expected"),
    [({(100, 100)}, {(110, 100)}, 10.0), ({(100, 100)}, {(100, 110)}, 10.0), ({(100, 100)}, {(103, 104)}, 5.0), ({(100, 100)}, {(100, 100)}, 0.0)],
)
def test_street_nose_distance_is_exact(street: set[tuple[int, int]], protected: set[tuple[int, int]], expected: float) -> None:
    distance, street_point, protected_point = nearest_foreground_distance(_mask(street, (120, 120)), _mask(protected, (120, 120)))
    assert distance == expected
    assert math.dist(street_point, protected_point) == expected


def test_foreground_distance_reports_nearest_pixels_and_rejects_empty_masks() -> None:
    distance, street_point, protected_point = nearest_foreground_distance(_mask({(1, 1), (10, 10)}), _mask({(14, 13)}))
    assert (distance, street_point, protected_point) == (5.0, (10, 10), (14, 13))
    with pytest.raises(DiagnosticMaskError, match="empty protected"):
        nearest_foreground_distance(_mask({(1, 1)}), Image.new("L", (40, 40)))


def test_nose_proximity_penalises_without_overlap_and_is_monotonic() -> None:
    street = _mask({(20, 16), (20, 17)})
    nose = _mask({(20, 25)})
    masks = _blank_masks(street, nose=nose)
    far = score_candidate(masks, Candidate(0, 1.0, 0, -10))
    near = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    closer = score_candidate(masks, Candidate(0, 1.0, 0, 4))
    assert near.nose_overlap_pixels == 0 and near.proximity_penalty > 0
    assert far.proximity_penalty < near.proximity_penalty < closer.proximity_penalty


def test_comfortable_nose_spacing_has_no_penalty() -> None:
    street = _mask({(20, 1), (20, 2)})
    result = score_candidate(_blank_masks(street, nose=_mask({(20, 35)})), Candidate(0, 1.0, 0, 0))
    assert result.nose_min_distance_px >= ProximityThresholds().nose_comfortable_px
    assert result.proximity_penalty == 0


def test_eye_proximity_without_overlap_is_measured() -> None:
    street = _mask({(20, 16), (20, 17)})
    result = score_candidate(_blank_masks(street, left=_mask({(20, 23)})), Candidate(0, 1.0, 0, 0))
    assert result.left_eye_overlap == 0
    assert result.left_eye_min_distance_px < ProximityThresholds().eye_comfortable_px
    assert result.proximity_penalty > 0


def test_edge_penalty_is_graded_and_clipping_is_severe() -> None:
    street = _mask({(20, 20), (20, 21)})
    masks = _blank_masks(street)
    safe = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    small_margin = score_candidate(masks, Candidate(0, 1.0, 0, -17))
    assert safe.edge_penalty == 0
    assert 0 < small_margin.edge_penalty
    with pytest.raises(DiagnosticMaskError, match="empty street mask"):
        score_candidate(masks, Candidate(0, 1.0, 0, -25))


def test_large_safe_feature_is_not_rewarded_for_recentering() -> None:
    street = _mask({(x, y) for x in range(10, 31) for y in range(6, 22)})
    masks = _blank_masks(street)
    current = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    recentered = score_candidate(masks, Candidate(0, 1.0, 0, 10))
    assert current.edge_penalty == recentered.edge_penalty == 0
    assert current.score > recentered.score


def test_orientation_descriptors_invert_and_asymmetric_shape_can_win_rotated() -> None:
    points = {(20, y) for y in range(8, 29)} | {(x, y) for x in range(14, 27) for y in range(8, 13)}
    masks = _blank_masks(_mask(points))
    original = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    rotated = score_candidate(masks, Candidate(180, 1.0, 0, 0))
    assert original.upper_half_ratio > original.lower_half_ratio
    assert rotated.lower_half_ratio > rotated.upper_half_ratio
    assert original.top_band_width > original.bottom_band_width
    assert original.orientation_penalty_or_bonus > rotated.orientation_penalty_or_bonus


def test_symmetric_shape_keeps_original_orientation_and_results_are_deterministic() -> None:
    street = _mask({(x, y) for x in range(18, 23) for y in range(12, 28)})
    masks = _blank_masks(street)
    first = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    second = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    rotated = score_candidate(masks, Candidate(180, 1.0, 0, 0))
    assert first == second
    assert first.orientation_penalty_or_bonus == 0
    assert first.score > rotated.score


def test_fixture_anatomy_uses_real_street_mouth_and_static_nose() -> None:
    glyph = Path("tests/fixtures/workflow_v6_valid/glyphs/0001_St John's Road.svg")
    street = StreetRecord("0001", None, "St John's Road", "St John's Road", glyph)
    masks = render_production_masks(street, area="Fixture")
    audits = {item.name: item for item in audit_masks(masks)}
    assert set(audits) == {"street_mouth", "static_nose", "left_eye", "right_eye", "typography"}
    assert all(item.size == (495, 462) and item.foreground_pixels > 0 for item in audits.values())
    assert masks.street_mouth.getbbox() is not None
    assert masks.static_nose.getbbox() is not None

def test_asset_extraction_retains_nested_inherited_svg_transforms() -> None:
    asset = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="337" height="315" viewBox="0 0 337 315">'
        '<g transform="translate(20 10) scale(2)"><g class="face-content" transform="translate(3 4)">'
        '<rect class="street" x="1" y="2" width="4" height="5"/></g></g></svg>'
    )
    href = "data:image/svg+xml;base64," + base64.b64encode(asset.encode("utf-8")).decode("ascii")
    placement = 'x="100" y="50" width="337" height="315" preserveAspectRatio="xMidYMid meet"'
    transform = face._front_group_transform(247.5, 462, face.FRONT_GROUP_SCALE, face.FRONT_GROUP_Y_OFFSET)
    production = f'<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462"><g transform="{transform}"><image href="{href}" {placement}/></g></svg>'
    assert _asset_mask(production, {"street"}, role="street_mouth").tobytes() == _render_mask(production).tobytes()


def test_missing_production_anatomy_role_raises_clearly() -> None:
    asset = '<svg xmlns="http://www.w3.org/2000/svg" width="337" height="315"><g><g class="face-content"><rect class="street" x="1" y="1" width="2" height="2"/></g></g></svg>'
    href = "data:image/svg+xml;base64," + base64.b64encode(asset.encode("utf-8")).decode("ascii")
    production = f'<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462"><image href="{href}" x="0" y="0" width="337" height="315"/></svg>'
    with pytest.raises(DiagnosticMaskError, match='static_nose'):
        _asset_mask(production, {"v28-nose"}, role="static_nose")