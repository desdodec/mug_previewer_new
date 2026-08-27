from __future__ import annotations

from PIL import Image

from mug_previewer.diagnostics.front_candidates import (
    Candidate,
    CandidateGrid,
    FaceMasks,
    generate_candidates,
    overlap_pixels,
    score_candidate,
    transform_street_mask,
)


def _mask(points: set[tuple[int, int]], size: tuple[int, int] = (20, 20)) -> Image.Image:
    image = Image.new("L", size, 0)
    for point in points:
        image.putpixel(point, 255)
    return image


def _masks() -> FaceMasks:
    return FaceMasks(
        _mask({(9, 9), (10, 9), (9, 10), (10, 10)}),
        _mask({(1, 1)}),
        _mask({(18, 1)}),
        _mask({(1, 18)}),
        _mask({(18, 18)}),
        Image.new("RGBA", (20, 20)),
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
    eye = score_candidate(masks, Candidate(0, 1.0, -8, -8))
    mouth = score_candidate(masks, Candidate(0, 1.0, -8, 8))
    typography = score_candidate(masks, Candidate(0, 1.0, 8, 8))
    assert clear.score > eye.score
    assert clear.score > mouth.score
    assert clear.score > typography.score
    assert typography.score < eye.score


def test_larger_clear_candidate_and_original_orientation_are_preferred() -> None:
    masks = _masks()
    full = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    smaller = score_candidate(masks, Candidate(0, 0.8, 0, 0))
    rotated = score_candidate(masks, Candidate(180, 1.0, 0, 0))
    assert full.score > smaller.score
    assert full.score > rotated.score


def test_clipping_is_detected_and_severely_penalised() -> None:
    masks = _masks()
    clipped_mask, clipped = transform_street_mask(masks.street, Candidate(0, 1.0, -20, 0))
    result = score_candidate(masks, Candidate(0, 1.0, -20, 0))
    assert clipped and clipped_mask.getbbox() is None
    assert result.clipped
    assert result.score < -50
