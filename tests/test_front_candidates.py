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
    ClassificationThresholds,
    DiagnosticMaskError,
    FaceAnatomyMasks,
    ProximityThresholds,
    ProductionTriageStatus,
    generate_candidates,
    nearest_foreground_distance,
    overlap_pixels,
    score_candidate,
    transform_street_mask,
    audit_masks,
    classify_candidate,
    select_front_placement_from_masks,
    select_production_placement_from_masks,
    triage_production_placement,
    select_production_placement,
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
    result = score_candidate(masks, Candidate(0, 1.0, -40, 0))
    assert result.clipped
    assert math.isinf(result.nose_min_distance_px)
    assert result.street_nose_nearest_pair == ((-1, -1), (-1, -1))
    assert result.score <= -100


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
    nose = _mask({(20, 23)})
    masks = _blank_masks(street, nose=nose)
    far = score_candidate(masks, Candidate(0, 1.0, 0, -10))
    near = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    closer = score_candidate(masks, Candidate(0, 1.0, 0, 4))
    assert near.nose_overlap_pixels == 0 and near.nose_clearance_penalty > 0
    assert far.nose_clearance_penalty < near.nose_clearance_penalty < closer.nose_clearance_penalty


def test_comfortable_nose_spacing_has_no_penalty() -> None:
    street = _mask({(20, 1), (20, 2)})
    result = score_candidate(_blank_masks(street, nose=_mask({(20, 35)})), Candidate(0, 1.0, 0, 0))
    assert result.nose_min_distance_px >= ProximityThresholds().nose_comfortable_px
    assert result.proximity_penalty == 0


def test_nose_clearance_saturates_after_healthy_gap() -> None:
    street = _mask({(20, 10), (20, 11)})
    masks = _blank_masks(street, nose=_mask({(20, 30)}))
    healthy = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    distant = score_candidate(masks, Candidate(0, 1.0, 0, -8))
    assert healthy.nose_min_distance_px >= ProximityThresholds().nose_comfortable_px
    assert healthy.nose_clearance_penalty == distant.nose_clearance_penalty == 0


def test_nose_overlap_remains_a_severe_penalty() -> None:
    street = _mask({(20, 20), (20, 21)})
    result = score_candidate(_blank_masks(street, nose=_mask({(20, 20)})), Candidate(0, 1.0, 0, 0))
    assert result.nose_overlap_pixels == 1
    assert result.collision_penalty >= 3.0


def test_eye_proximity_without_overlap_is_measured() -> None:
    street = _mask({(20, 16), (20, 17)})
    result = score_candidate(_blank_masks(street, left=_mask({(20, 23)})), Candidate(0, 1.0, 0, 0))
    assert result.left_eye_overlap == 0
    assert result.left_eye_min_distance_px < ProximityThresholds().eye_comfortable_px
    assert result.proximity_penalty > 0


def test_mouth_role_soft_band_distinguishes_high_and_low_placement() -> None:
    street = _mask({(20, 20), (21, 20)}, (60, 60))
    nose = _mask({(20, 18), (20, 19)}, (60, 60))
    masks = _blank_masks(street, nose=nose)
    thresholds = ProximityThresholds(mouth_tolerance_px=3.0)
    centred = score_candidate(masks, Candidate(0, 1.0, 0, 0), proximity=thresholds)
    high = score_candidate(masks, Candidate(0, 1.0, 0, -10), proximity=thresholds)
    low = score_candidate(masks, Candidate(0, 1.0, 0, 10), proximity=thresholds)
    assert centred.mouth_role_penalty == 0
    assert high.mouth_role_penalty > 0
    assert low.mouth_role_penalty > 0


def test_mouth_role_tolerance_does_not_punish_small_movement() -> None:
    street = _mask({(20, 20), (21, 20)}, (60, 60))
    nose = _mask({(20, 18), (20, 19)}, (60, 60))
    masks = _blank_masks(street, nose=nose)
    thresholds = ProximityThresholds(mouth_tolerance_px=8.0)
    current = score_candidate(masks, Candidate(0, 1.0, 0, 0), proximity=thresholds)
    nearby = score_candidate(masks, Candidate(0, 1.0, 0, 4), proximity=thresholds)
    assert current.mouth_role_penalty == nearby.mouth_role_penalty == 0


def test_severe_mouth_role_failure_is_unresolved_not_an_adaptation() -> None:
    street = _mask({(20, 10), (21, 10)}, (200, 200))
    nose = _mask({(20, 150), (20, 151)}, (200, 200))
    result = score_candidate(_blank_masks(street, nose=nose), Candidate(0, 1.0, 0, 0))
    assert result.score >= ClassificationThresholds().minimum_score
    assert result.mouth_role_penalty > ClassificationThresholds().max_mouth_role_penalty
    assert classify_candidate(result, result) == "UNRESOLVED"


def test_typography_clearance_saturates_after_healthy_gap() -> None:
    street = _mask({(20, 10), (20, 11)})
    typography = _mask({(20, 30)})
    masks = FaceAnatomyMasks(street, _mask({(1, 1)}), _mask({(2, 1)}), _mask({(1, 35)}), typography, Image.new('RGBA', (40, 40)))
    healthy = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    distant = score_candidate(masks, Candidate(0, 1.0, 0, -8))
    assert healthy.typography_min_distance_px >= ProximityThresholds().typography_comfortable_px
    assert healthy.typography_clearance_penalty == distant.typography_clearance_penalty == 0


def test_safe_transform_beats_nose_collision_despite_movement_cost() -> None:
    street = _mask({(20, 20), (20, 21)}, (80, 80))
    masks = _blank_masks(street, nose=_mask({(20, 20)}, (80, 80)))
    collided = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    safe = score_candidate(masks, Candidate(0, 1.0, 0, -20))
    assert safe.nose_overlap_pixels == 0
    assert safe.score > collided.score


def test_conservative_y_zero_beats_unnecessary_safe_y_twenty() -> None:
    street = _mask({(50, 30), (51, 30)}, (100, 100))
    masks = FaceAnatomyMasks(street, _mask({(1, 1)}, (100, 100)), _mask({(2, 1)}, (100, 100)), _mask({(1, 30)}, (100, 100)), _mask({(98, 98)}, (100, 100)), Image.new('RGBA', (100, 100)))
    current = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    moved = score_candidate(masks, Candidate(0, 1.0, 0, 20))
    assert current.nose_clearance_penalty == moved.nose_clearance_penalty == 0
    assert current.score > moved.score


def test_edge_penalty_is_graded_and_clipping_is_severe() -> None:
    street = _mask({(20, 20), (20, 21)})
    masks = _blank_masks(street)
    safe = score_candidate(masks, Candidate(0, 1.0, 0, 0))
    small_margin = score_candidate(masks, Candidate(0, 1.0, 0, -17))
    assert safe.edge_penalty == 0
    assert 0 < small_margin.edge_penalty
    clipped = score_candidate(masks, Candidate(0, 1.0, 0, -25))
    assert clipped.clipped and clipped.score <= -100


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


def test_shared_decision_keeps_a_healthy_standard_candidate() -> None:
    street = _mask({(100, 120)}, (200, 200))
    decision, _results = select_front_placement_from_masks(_blank_masks(street, nose=_mask({(100, 100)}, (200, 200))))
    assert decision.classification == "STANDARD"
    assert decision.rendered.candidate == Candidate(0, 1.0, 0, 0)


def test_shared_decision_adapts_a_real_nose_collision_to_an_eligible_candidate() -> None:
    street = _mask({(100, 100)}, (200, 200))
    decision, _results = select_front_placement_from_masks(
        _blank_masks(street, nose=_mask({(100, 100)}, (200, 200))),
        grid=CandidateGrid(orientations_deg=(0,), scales=(1.0,), x_offsets=(0,), y_offsets=(0, 20)),
    )
    assert decision.classification == "ADAPTED"
    assert decision.rendered.candidate == Candidate(0, 1.0, 0, 20)
    assert decision.standard.nose_overlap_pixels == 1
    assert decision.rendered.nose_overlap_pixels == 0


def test_shared_decision_retains_standard_when_no_candidate_is_eligible() -> None:
    street = _mask({(100, 10)}, (200, 200))
    decision, _results = select_front_placement_from_masks(
        _blank_masks(street, nose=_mask({(100, 150)}, (200, 200))),
        grid=CandidateGrid(orientations_deg=(0,), scales=(1.0,), x_offsets=(0,), y_offsets=(0,)),
    )
    assert decision.classification == "UNRESOLVED"
    assert decision.diagnostic_selected.candidate == Candidate(0, 1.0, 0, 0)
    assert decision.rendered.candidate == Candidate(0, 1.0, 0, 0)


def test_production_triage_auto_approves_a_healthy_standard() -> None:
    street = _mask({(100, 120)}, (200, 200))
    decision, _results = select_production_placement_from_masks(
        _blank_masks(street, nose=_mask({(100, 100)}, (200, 200))),
    )
    assert decision.triage_status is ProductionTriageStatus.AUTO_APPROVED
    assert decision.placement_class == 'STANDARD'
    assert decision.reason_codes == ('standard_healthy',)


def test_production_triage_requires_material_improvement_before_adapting() -> None:
    street = _mask({(x, 100) for x in range(100, 105)}, (200, 200))
    decision, _results = select_production_placement_from_masks(
        _blank_masks(street, nose=_mask({(x, y) for x in range(100, 105) for y in (100, 160)}, (200, 200))),
        grid=CandidateGrid(orientations_deg=(0,), scales=(1.0,), x_offsets=(0,), y_offsets=(0, 40)),
    )
    assert decision.triage_status is ProductionTriageStatus.AUTO_APPROVED
    assert decision.placement_class == 'ADAPTED'
    assert decision.transform is not None and decision.transform.y_offset == 40
    assert 'standard_nose_overlap' in decision.reason_codes
    assert 'adaptation_materially_improved' in decision.reason_codes


def test_production_triage_sends_marginal_rescue_to_manual_review() -> None:
    street = _mask({(100, 100)}, (200, 200))
    decision, _results = select_production_placement_from_masks(
        _blank_masks(street, nose=_mask({(100, 100)}, (200, 200))),
        grid=CandidateGrid(orientations_deg=(0,), scales=(1.0,), x_offsets=(0,), y_offsets=(0, 20)),
    )
    assert decision.triage_status is ProductionTriageStatus.MANUAL_REVIEW
    assert decision.placement_class is None
    assert decision.reason_codes == ('standard_nose_overlap', 'adaptation_low_confidence')


def test_production_triage_surfaces_diagnostic_unresolved_geometry() -> None:
    street = _mask({(100, 10)}, (200, 200))
    diagnostic, ranked = select_front_placement_from_masks(
        _blank_masks(street, nose=_mask({(100, 150)}, (200, 200))),
        grid=CandidateGrid(orientations_deg=(0,), scales=(1.0,), x_offsets=(0,), y_offsets=(0,)),
    )
    decision = triage_production_placement(diagnostic, ranked)
    assert decision.triage_status is ProductionTriageStatus.MANUAL_REVIEW
    assert 'unresolved_geometry' in decision.reason_codes


def test_production_triage_classifies_missing_glyph_as_unrenderable_input(tmp_path: Path) -> None:
    street = StreetRecord('missing', None, 'Missing Road', 'Missing Road', tmp_path / 'missing.svg')
    decision = select_production_placement(street)
    assert decision.triage_status is ProductionTriageStatus.UNRENDERABLE_INPUT
    assert decision.reason_codes == ('missing_glyph',)


def test_shared_decision_is_deterministic_and_preserves_effective_tie_standard() -> None:
    street = _mask({(100, 120)}, (200, 200))
    masks = _blank_masks(street, nose=_mask({(100, 100)}, (200, 200)))
    grid = CandidateGrid(orientations_deg=(0,), scales=(1.0,), x_offsets=(0,), y_offsets=(0, 20))
    first, _ = select_front_placement_from_masks(masks, grid=grid)
    second, _ = select_front_placement_from_masks(masks, grid=grid)
    assert first == second
    assert first.classification == "STANDARD"
    assert first.rendered.candidate == Candidate(0, 1.0, 0, 0)
