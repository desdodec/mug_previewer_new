"""
Street Face Generator v21.

V21 keeps the immutable-road geometry and rendering system from v20, but makes
street-feature assignment explicitly hierarchical:

    1. Try every useful rotation as a mouth.
    2. If none reads credibly as a mouth, try every rotation as a nose.
    3. If neither works, use the best rotation as a hairline.

The renderer and command-line interface remain compatible with v20.  This file
is deliberately a small version layer over v20 so the existing generator is
not copied, edited, or overwritten.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from . import core as v20


# These gates apply to the best rotation for each role.  The raw-score gate
# prevents a shape from becoming a mouth merely because it resembles a jaw or
# hairline; the blended gate preserves v20's playful supporting evidence.
MOUTH_RAW_MINIMUM = 0.65
MOUTH_BLENDED_MINIMUM = 1.00
# A road can be mouth-like even when a long side makes its overall bounding box
# close to square.  Keep this exception deliberately tight: it is for a clear
# U/V bowl with enough supporting mouth evidence, not for arbitrary compact
# angular paths.
U_SHAPE_MOUTH_MINIMUM = 0.75
U_SHAPE_MOUTH_RAW_MINIMUM = 0.28
U_SHAPE_MOUTH_BLENDED_MINIMUM = 0.60
# Near-square U mouths need a compact feature box.  The normal mouth box is
# deliberately generous for shallow, wide paths; applied to a tall U it makes
# the road descend into the lower edge of a single-face card.
U_SHAPE_MOUTH_TARGET_HEIGHT_SCALE = 0.45
U_SHAPE_MOUTH_TARGET_LIFT = 0.35
NOSE_RAW_MINIMUM = 0.54
NOSE_BLENDED_MINIMUM = 0.72
PLAYFUL_NOSE_RAW_MINIMUM = 0.42
PLAYFUL_NOSE_BLENDED_MINIMUM = 0.40
PLAYFUL_NOSE_MAX_ASPECT = 0.70
HAIRLINE_MINIMUM = 0.48
HAIRLINE_MIN_ASPECT = 1.50
STREET_STROKE_DEFAULT = 1.18
STREET_STROKE_REFERENCE_CLAMP = 1.10
STREET_STROKE_REFERENCE_FACTOR = 0.90


def _candidate_rotations(paths: Sequence[Sequence[v20.Point]]) -> List[float]:
    points = v20.flatten_paths(paths)
    base_angle = v20.principal_axis_angle(points)
    candidates = (
        0.0,
        90.0,
        180.0,
        270.0,
        -base_angle,
        90.0 - base_angle,
        180.0 - base_angle,
        270.0 - base_angle,
    )
    unique: List[float] = []
    for angle in candidates:
        normalised = ((angle + 180.0) % 360.0) - 180.0
        if all(abs(normalised - existing) > 3.0 for existing in unique):
            unique.append(normalised)
    return unique


def _rotation_evidence(
    paths: Sequence[Sequence[v20.Point]], angle: float
) -> Dict[str, object]:
    rotated_paths = v20.rotate_path_collection(paths, angle)
    rotated_points = v20.flatten_paths(rotated_paths)
    support = v20.role_scores(rotated_points, rotated_paths)
    score_map = dict(support)
    u_bonus = v20.u_shape_mouth_bonus(rotated_paths)

    mouth = (
        score_map.get("mouth", 0.0)
        + 0.24 * score_map.get("jaw_chin", 0.0)
        + 0.14 * score_map.get("hairline", 0.0)
        + 0.10 * score_map.get("eyebrow", 0.0)
        + 0.34 * u_bonus
    )
    nose = (
        score_map.get("nose", 0.0)
        + 0.32 * score_map.get("profile", 0.0)
        + 0.08 * score_map.get("eyebrow", 0.0)
        - 0.18 * u_bonus
    )
    hairline = (
        score_map.get("hairline", 0.0)
        + 0.20 * score_map.get("eyebrow", 0.0)
        + 0.12 * score_map.get("jaw_chin", 0.0)
        + 0.08 * max(0.0, 1.0 - u_bonus)
    )

    # Retain v19/v20's bias toward the street's existing orientation.
    rotation_penalty = 0.035 if abs(angle) > 1.0 else 0.0
    return {
        "angle": angle,
        "paths": rotated_paths,
        "metrics": v20.compute_path_metrics(rotated_paths),
        "support": support,
        "u_bonus": u_bonus,
        "raw_mouth": score_map.get("mouth", 0.0),
        "raw_nose": score_map.get("nose", 0.0),
        "mouth": mouth - rotation_penalty,
        "nose": nose - rotation_penalty,
        "hairline": hairline - rotation_penalty,
    }


def _is_credible_mouth(item: Dict[str, object]) -> bool:
    """Return whether a rotation has earned mouth placement."""
    raw_mouth = float(item["raw_mouth"])
    mouth = float(item["mouth"])
    if raw_mouth >= MOUTH_RAW_MINIMUM and mouth >= MOUTH_BLENDED_MINIMUM:
        return True
    return (
        float(item["u_bonus"]) >= U_SHAPE_MOUTH_MINIMUM
        and raw_mouth >= U_SHAPE_MOUTH_RAW_MINIMUM
        and mouth >= U_SHAPE_MOUTH_BLENDED_MINIMUM
    )


def _uses_compact_u_mouth_placement(spec: v20.FaceSpec) -> bool:
    """Identify the narrow U-mouth exception without changing its face role."""
    if spec.role_choice.role != "mouth" or spec.role_choice.metrics.aspect > 1.20:
        return False
    report = dict(spec.report)
    return float(report.get("u_shape_mouth_bonus", 0.0)) >= U_SHAPE_MOUTH_MINIMUM


def _compact_u_mouth_target(
    target: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Keep a tall U mouth clear of the lower edge of the face card."""
    x, y, width, height = target
    return (
        x,
        y - height * U_SHAPE_MOUTH_TARGET_LIFT,
        width,
        height * U_SHAPE_MOUTH_TARGET_HEIGHT_SCALE,
    )


def _orientation_preserving_mouth(
    best: Dict[str, object], evidence: Sequence[Dict[str, object]]
) -> Dict[str, object]:
    """Prefer the closest credible half-turn equivalent to the source position."""
    best_angle = float(best["angle"])
    opposite = []
    for item in evidence:
        angle = float(item["angle"])
        separation = abs(((angle - best_angle + 180.0) % 360.0) - 180.0)
        if separation < 177.0:
            continue
        if _is_credible_mouth(item):
            opposite.append(item)
    if not opposite:
        return best
    closest = min([best, *opposite], key=lambda item: abs(float(item["angle"])))
    return closest


def choose_role_and_rotation(
    paths: Sequence[Sequence[v20.Point]],
) -> Tuple[v20.RoleChoice, List[Tuple[str, float]]]:
    """Choose a role in strict mouth -> nose -> hairline order."""
    evidence = [_rotation_evidence(paths, angle) for angle in _candidate_rotations(paths)]
    score_best_mouth = max(evidence, key=lambda item: float(item["mouth"]))
    best_mouth = _orientation_preserving_mouth(score_best_mouth, evidence)
    best_nose = max(evidence, key=lambda item: float(item["nose"]))
    best_hair = max(evidence, key=lambda item: float(item["hairline"]))

    if _is_credible_mouth(best_mouth):
        role = "mouth"
        chosen = best_mouth
    elif (
        (
            float(best_nose["raw_nose"]) >= NOSE_RAW_MINIMUM
            and float(best_nose["nose"]) >= NOSE_BLENDED_MINIMUM
        )
        or (
            float(best_nose["raw_nose"]) >= PLAYFUL_NOSE_RAW_MINIMUM
            and float(best_nose["nose"]) >= PLAYFUL_NOSE_BLENDED_MINIMUM
            and best_nose["metrics"].aspect <= PLAYFUL_NOSE_MAX_ASPECT
        )
    ):
        role = "nose"
        chosen = best_nose
    elif (
        float(best_hair["hairline"]) >= HAIRLINE_MINIMUM
        and best_hair["metrics"].aspect >= HAIRLINE_MIN_ASPECT
    ):
        role = "hairline"
        chosen = best_hair
    else:
        # Compact, branched outliers read more naturally as eccentric noses
        # than as weak hairlines. Hair must be positively earned above.
        role = "nose"
        chosen = best_nose

    chosen_score = float(chosen[role])
    choice = v20.RoleChoice(
        role=role,
        rotation=float(chosen["angle"]),
        score=chosen_score,
        metrics=chosen["metrics"],
    )
    report = [
        ("hierarchy_mouth_best", float(best_mouth["mouth"])),
        ("hierarchy_mouth_raw", float(best_mouth["raw_mouth"])),
        ("hierarchy_nose_best", float(best_nose["nose"])),
        ("hierarchy_nose_raw", float(best_nose["raw_nose"])),
        ("hierarchy_hairline_best", float(best_hair["hairline"])),
        ("u_shape_mouth_bonus", float(chosen["u_bonus"])),
    ]
    report.extend(
        (f"support_{name}", value)
        for name, value in chosen["support"]
    )
    return choice, sorted(report, key=lambda item: item[1], reverse=True)


def apply_batch_relative_roles(specs: Sequence[v20.FaceSpec]) -> None:
    """Add batch-relative diagnostics without overriding hierarchical roles."""
    aspects = [spec.role_choice.metrics.aspect for spec in specs]
    inverse_aspects = [1.0 / max(value, 1e-9) for value in aspects]
    sinuosities = [spec.role_choice.metrics.sinuosity for spec in specs]
    lengths = [spec.role_choice.metrics.length for spec in specs]
    node_counts = [float(spec.role_choice.metrics.node_count) for spec in specs]
    deflections = [spec.role_choice.metrics.turn_angle_sum for spec in specs]

    for spec in specs:
        metrics = spec.role_choice.metrics
        aspect_p = v20.percentile_rank(metrics.aspect, aspects)
        inverse_p = v20.percentile_rank(
            1.0 / max(metrics.aspect, 1e-9), inverse_aspects
        )
        sinuosity_p = v20.percentile_rank(metrics.sinuosity, sinuosities)
        node_p = v20.percentile_rank(float(metrics.node_count), node_counts)
        deflection_p = v20.percentile_rank(metrics.turn_angle_sum, deflections)
        spec.length_percentile = v20.percentile_rank(metrics.length, lengths)
        spec.report.extend(
            [
                ("batch_aspect_percentile", aspect_p),
                ("batch_inverse_aspect_percentile", inverse_p),
                ("batch_sinuosity_percentile", sinuosity_p),
                ("batch_node_percentile", node_p),
                ("batch_deflection_percentile", deflection_p),
                ("complexity_score", metrics.complexity),
            ]
        )



def misery_score(spec: v20.FaceSpec) -> float:
    """Measure the strongest explicit unhappy reading on a zero-to-one scale."""
    return max(v20.sadness_score(spec), v20.unhappiness_score(spec))


def emotional_valence_score(spec: v20.FaceSpec) -> float:
    """Positive is happy; negative is miserable."""
    return v20.happiness_score(spec) - misery_score(spec)


def _sample_emotional_spectrum(
    ranked: Sequence[v20.FaceSpec], target_count: int
) -> List[v20.FaceSpec]:
    """Sample evenly across the numeric happy-to-miserable score range."""
    if target_count <= 0 or not ranked:
        return []
    if target_count >= len(ranked):
        return list(ranked)
    if target_count == 1:
        return [ranked[0]]

    values = [emotional_valence_score(spec) for spec in ranked]
    happiest = values[0]
    most_miserable = values[-1]
    if abs(happiest - most_miserable) <= 1e-12:
        indices = [
            round(step * (len(ranked) - 1) / (target_count - 1))
            for step in range(target_count)
        ]
        return [ranked[index] for index in indices]

    remaining = set(range(len(ranked)))
    selected: List[v20.FaceSpec] = []
    for step in range(target_count):
        wanted = happiest - (happiest - most_miserable) * step / (target_count - 1)
        index = min(remaining, key=lambda candidate: (abs(values[candidate] - wanted), candidate))
        remaining.remove(index)
        selected.append(ranked[index])
    return _strictly_sorted_specs(selected, "gallery")


def subtitle_for_sort_mode(subtitle: str, sort_by: str | None) -> str:
    mode = v20.normalize_gallery_sort_mode(sort_by)
    if mode == "happiness":
        suffix = "ordered by smile-like happiness"
    elif mode == "sinuosity":
        suffix = "ordered by street sinuosity"
    else:
        suffix = "ordered from happiness to total misery"
    return f"{subtitle} - {suffix}" if subtitle else suffix.capitalize()


def _strictly_sorted_specs(
    specs: Sequence[v20.FaceSpec], sort_by: str | None
) -> List[v20.FaceSpec]:
    """Sort by the requested primary metric without role buckets or showcases."""
    mode = v20.normalize_gallery_sort_mode(sort_by)
    if mode == "happiness":
        return sorted(
            specs,
            key=lambda spec: (
                -v20.happiness_score(spec),
                -v20.smile_depth_score(spec),
                -v20.calm_curve_score(spec),
                -v20.role_confidence(spec),
                spec.name.lower(),
            ),
        )
    if mode == "sinuosity":
        return sorted(
            specs,
            key=lambda spec: (
                -spec.role_choice.metrics.sinuosity,
                -spec.role_choice.metrics.turn_angle_sum,
                -spec.role_choice.metrics.length,
                spec.name.lower(),
            ),
        )
    return sorted(
        specs,
        key=lambda spec: (
            -emotional_valence_score(spec),
            -v20.happiness_score(spec),
            misery_score(spec),
            spec.name.lower(),
        ),
    )


def curate_specs(
    specs: Sequence[v20.FaceSpec],
    target_count: Optional[int],
    sort_by: str | None = "happiness",
) -> List[v20.FaceSpec]:
    """Select the leading streets in the explicitly requested metric order."""
    ranked = _strictly_sorted_specs(specs, sort_by)
    if target_count is None:
        return ranked
    if target_count <= 0:
        return []
    mode = v20.normalize_gallery_sort_mode(sort_by)
    if mode == "gallery":
        return _sample_emotional_spectrum(ranked, target_count)
    return ranked[:target_count]


def curate_specs_with_forced_street(
    specs: Sequence[v20.FaceSpec],
    target_count: Optional[int],
    forced_street: Optional[str] = None,
    forced_search_specs: Optional[Sequence[v20.FaceSpec]] = None,
    sort_by: str | None = "happiness",
) -> Tuple[List[v20.FaceSpec], Optional[str], Optional[str]]:
    """Preserve strict metric ordering even when a street is forced in."""
    curated = curate_specs(specs, target_count, sort_by=sort_by)
    if not forced_street or not forced_street.strip():
        return curated, None, None

    search_specs = forced_search_specs if forced_search_specs is not None else specs
    forced_spec = v20.find_spec_by_name(search_specs, forced_street)
    if forced_spec is None:
        raise ValueError(f"Street not found in the available collection: {forced_street}")

    forced_key = v20.normalize_street_name(forced_spec.name)
    if any(v20.normalize_street_name(spec.name) == forced_key for spec in curated):
        return curated, f"{forced_spec.name} is already in the curated collection.", forced_spec.name

    if not curated:
        return [forced_spec], f"Added {forced_spec.name}.", forced_spec.name

    replaced = curated[-1]
    curated = _strictly_sorted_specs([*curated[:-1], forced_spec], sort_by)
    return (
        curated,
        f"Added {forced_spec.name}; replaced {replaced.name}.",
        forced_spec.name,
    )

def provenance_payload(area_name: str, generated_on: Optional[str] = None) -> str:
    """Build a v21 provenance payload while retaining the v20 checksum scheme."""
    area = re.sub(r"\s+", " ", str(area_name or "unknown area").strip())
    generated = generated_on or date.today().isoformat()
    base = f"SFV21|AREA={area}|DATE={generated}"
    checksum = hashlib.sha256(
        f"{v20.signature_secret_id()}|{base}".encode("utf-8", errors="replace")
    ).hexdigest()[:10].upper()
    return f"{base}|CHK={checksum}"


_v20_render_provenance = v20.render_attribution_provenance_mark
_v20_render_face_svg = v20.render_face_svg
_v20_render_grid = v20.render_grid


def render_attribution_provenance_mark(*args: object, **kwargs: object) -> str:
    markup = _v20_render_provenance(*args, **kwargs)
    return markup.replace("street-face-v20", "street-face-v21")


def render_face_svg(spec: v20.FaceSpec, *args: object, **kwargs: object) -> str:
    """Render every sacred street with Forman Place's single-face stroke width."""
    previous_percentile = spec.length_percentile
    try:
        # Single-face Forman Place uses v20's 0.90 multiplier at percentile 0.50.
        # Pin only for rendering so batch-relative length cannot change width.
        spec.length_percentile = 0.50
        return _v20_render_face_svg(spec, *args, **kwargs)
    finally:
        spec.length_percentile = previous_percentile



def render_grid(
    specs: object,
    out_svg: object,
    *args: object,
    street_stroke_multiplier: float = STREET_STROKE_DEFAULT,
    **kwargs: object,
) -> None:
    """Make the full GUI street-stroke range affect the final SVG width."""
    requested = max(0.05, float(street_stroke_multiplier))
    previous_renderer = v20.render_face_svg
    previous_fitter = v20.fit_paths_to_feature_box
    compact_u_mouth_paths = {
        id(spec.original_paths)
        for spec in specs
        if _uses_compact_u_mouth_placement(spec)
    }

    def scaled_renderer(
        spec: v20.FaceSpec, *face_args: object, **face_kwargs: object
    ) -> str:
        inherited_width = float(face_kwargs.get("street_stroke_multiplier", 1.0))
        inherited_scale = max(1.0, v20.clamp(requested, 0.90, 1.10))
        hierarchy_base = inherited_width / inherited_scale
        reference_width = max(
            2.0,
            hierarchy_base
            * STREET_STROKE_REFERENCE_CLAMP
            * STREET_STROKE_REFERENCE_FACTOR,
        )
        exact_width = reference_width * requested / STREET_STROKE_DEFAULT
        markup = render_face_svg(spec, *face_args, **face_kwargs)
        return re.sub(
            r'(<polyline\b[^>]*\bclass="street"[^>]*style="stroke-width:)[0-9.]+',
            lambda match: f'{match.group(1)}{exact_width:.3f}',
            markup,
        )

    def fit_with_compact_u_mouth(
        paths: Sequence[Sequence[v20.Point]],
        rotation: float,
        target: Tuple[float, float, float, float],
    ) -> Tuple[List[List[v20.Point]], str]:
        if id(paths) in compact_u_mouth_paths:
            target = _compact_u_mouth_target(target)
        return previous_fitter(paths, rotation, target)

    v20.render_face_svg = scaled_renderer
    v20.fit_paths_to_feature_box = fit_with_compact_u_mouth
    try:
        _v20_render_grid(
            specs, out_svg, *args,
            street_stroke_multiplier=requested, **kwargs
        )
    finally:
        v20.render_face_svg = previous_renderer
        v20.fit_paths_to_feature_box = previous_fitter


def _install_v21_policy() -> None:
    v20.__doc__ = __doc__
    v20.DEFAULT_SIGNATURE_ID = "street-face-generator-v21-private-mark"
    v20.choose_role_and_rotation = choose_role_and_rotation
    v20.render_grid = render_grid
    v20.apply_batch_relative_roles = apply_batch_relative_roles
    v20.provenance_payload = provenance_payload
    v20.subtitle_for_sort_mode = subtitle_for_sort_mode
    v20.render_attribution_provenance_mark = render_attribution_provenance_mark
    v20.render_face_svg = render_face_svg
    v20.curate_specs = curate_specs
    v20.curate_specs_with_forced_street = curate_specs_with_forced_street


_install_v21_policy()


def __getattr__(name: str) -> object:
    """Expose the rest of v20's public API through the v21 module."""
    return getattr(v20, name)


def main() -> None:
    v20.main()


if __name__ == "__main__":
    main()
