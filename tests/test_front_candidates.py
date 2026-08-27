from __future__ import annotations

from PIL import Image

from mug_previewer.diagnostics.front_candidates import (
    Candidate,
    CandidateGrid,
    FaceMasks,
    ProximityThresholds,
    generate_candidates,
    overlap_pixels,
    score_candidate,
    transform_street_mask,
)


def _mask(points: set[tuple[int, int]], size: tuple[int, int] = (40, 40)) -> Image.Image:
    image = Image.new("L", size, 0)
    for point in points:
        image.putpixel(point, 255)
    return image


def _masks() -> FaceMasks:
    return FaceMasks(
        _mask({(19, 19), (20, 19), (19, 20), (20, 20)}),
        _mask({(4, 4)}), _mask({(35, 4)}), _mask({(4, 35)}), _mask({(35, 35)}), Image.new("RGBA", (40, 40)),
    )


def _blank_masks(street: Image.Image, *, left: Image.Image | None = None, mouth: Image.Image | None = None) -> FaceMasks:
    empty = Image.new("L", street.size, 0)
    return FaceMasks(street, left or empty, empty, mouth or empty, empty, Image.new("RGBA", street.size))


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
    mouth = score_candidate(masks, Candidate(0, 1.0, -15, 15))
    typography = score_candidate(masks, Candidate(0, 1.0, 15, 15))
    assert clear.score > eye.score
    assert clear.score > mouth.score
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
    clipped_mask, clipped = transform_street_mask(masks.street, Candidate(0, 1.0, -40, 0))
    result = score_candidate(masks, Candidate(0, 1.0, -40, 0))
    assert clipped and clipped_mask.getbbox() is None
    assert result.clipped
    assert result.score < -50


def test_mouth_proximity_penalises_without_overlap_and_is_monotonic() -> None:
    street = _mask({(20, 16), (20, 17)})
    mouth = _mask({(20, 25)})
    masks = _blank_masks(street, mouth=mouth)
    far = score_candidate(masks, Candidate(0, 1.0, 0, -10))
    near = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    closer = score_candidate(masks, Candidate(0, 1.0, 0, 4))
    assert near.mouth_overlap == 0 and near.proximity_penalty > 0
    assert far.proximity_penalty < near.proximity_penalty < closer.proximity_penalty


def test_comfortable_mouth_spacing_has_no_penalty() -> None:
    street = _mask({(20, 1), (20, 2)})
    result = score_candidate(_blank_masks(street, mouth=_mask({(20, 35)})), Candidate(0, 1.0, 0, 0))
    assert result.mouth_min_distance_px >= ProximityThresholds().mouth_comfortable_px
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
    clipped = score_candidate(masks, Candidate(0, 1.0, 0, -25))
    assert safe.edge_penalty == 0
    assert 0 < small_margin.edge_penalty < clipped.collision_penalty
    assert clipped.clipped


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