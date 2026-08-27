"""Experimental pixel-mask candidate scoring for frozen front-face artwork.

This module is diagnostic-only.  It reads production geometry but production
rendering, previews, and exports never import it.
"""

from __future__ import annotations

import base64
import csv
import io
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import cairosvg
from PIL import Image, ImageDraw

from ..datasets.models import StreetRecord
from ..rendering import face
from ..rendering.native import face_policy as native

MASK_ALPHA_THRESHOLD = 16
_DISTANCE_FIELD_CACHE: dict[tuple[bytes, tuple[int, int]], tuple[float, ...]] = {}

class DiagnosticMaskError(ValueError):
    """Raised when masks cannot support a mathematically valid diagnostic."""


@dataclass(frozen=True)
class MaskAudit:
    """Thresholded-mask evidence in final front-panel pixel coordinates."""

    name: str
    size: tuple[int, int]
    foreground_pixels: int
    bounds: tuple[int, int, int, int] | None
    alpha_threshold: int = MASK_ALPHA_THRESHOLD


@dataclass(frozen=True)
class ProximityThresholds:
    """Pixel spacing zones on the 495 x 462 front panel."""

    # The clean controls contain 8 px gaps, while Burnley's 14 px gap is also
    # healthy. Anything at least 8 px apart therefore satisfies clearance.
    nose_hard_min_px: float = 4.0
    nose_comfortable_px: float = 8.0
    eye_hard_min_px: float = 9.0
    eye_comfortable_px: float = 24.0
    typography_hard_min_px: float = 6.0
    typography_comfortable_px: float = 16.0
    # The lower two thirds of the fixed nose bounds is the lower-face anchor.
    mouth_target_nose_bounds_ratio: float = 0.67
    # A 70 px half-band accommodates the observed production street-mass span.
    mouth_tolerance_px: float = 70.0
    edge_soft_margin_px: float = 4.0


@dataclass(frozen=True)
class CandidateGrid:
    """Small bounded transform grid relative to the existing street placement."""

    orientations_deg: tuple[int, ...] = (0, 180)
    scales: tuple[float, ...] = (1.00, 0.95, 0.90, 0.85, 0.80)
    x_offsets: tuple[int, ...] = (0,)
    y_offsets: tuple[int, ...] = (-60, -40, -20, 0, 20, 40, 60)

    def __post_init__(self) -> None:
        if self.orientations_deg != tuple(sorted(set(self.orientations_deg))) or any(
            value not in (0, 180) for value in self.orientations_deg
        ):
            raise ValueError("Diagnostic orientations must be exactly from (0, 180).")
        if not self.scales or any(not 0 < value <= 1.05 for value in self.scales):
            raise ValueError("Scales must be positive and remain in the bounded diagnostic range.")

    @property
    def candidate_count(self) -> int:
        return len(self.orientations_deg) * len(self.scales) * len(self.x_offsets) * len(self.y_offsets)


@dataclass(frozen=True)
class ScoringWeights:
    """Inspectable penalty weights; collisions intentionally dominate aesthetics."""

    base_score: float = 100.0
    typography_overlap_pixel: float = 2.0
    eye_overlap_pixel: float = 1.0
    nose_overlap_pixel: float = 3.0
    clipping: float = 200.0
    nose_proximity: float = 16.0
    eye_proximity: float = 5.0
    typography_proximity: float = 6.0
    edge_proximity: float = 2.0
    scale_reduction: float = 30.0
    displacement_per_pixel: float = 0.05
    rotation_180: float = 3.0
    orientation_plausibility: float = 7.0
    mouth_role_placement: float = 12.0

@dataclass(frozen=True)
class ClassificationThresholds:
    max_eye_overlap_ratio: float = 0.015
    max_nose_overlap_ratio: float = 0.020
    max_typography_overlap_ratio: float = 0.005
    min_scale: float = 0.80
    minimum_score: float = 80.0
    modest_min_scale: float = 0.90
    modest_max_offset: int = 40
    orientation_change_threshold: float = 5.0
    effective_tie_score: float = 1.0
    # A small soft-band breach remains visually plausible, but candidates that
    # consume more than this are not eligible as a diagnostic rescue.  This
    # keeps a mouth-role failure from being masked by unrelated clearance.
    max_mouth_role_penalty: float = 2.0


@dataclass(frozen=True)
class Candidate:
    orientation_deg: int
    scale: float
    x_offset: int
    y_offset: int


@dataclass(frozen=True)
class CandidateResult:
    orientation_deg: int
    scale: float
    x_offset: int
    y_offset: int
    street_width: int
    street_height: int
    street_pixels: int
    left_eye_overlap: int
    right_eye_overlap: int
    nose_overlap_pixels: int
    typography_overlap: int
    left_eye_overlap_ratio: float
    right_eye_overlap_ratio: float
    nose_overlap_ratio: float
    typography_overlap_ratio: float
    clipped: bool
    edge_proximity: bool
    nose_min_distance_px: float
    typography_min_distance_px: float
    left_eye_min_distance_px: float
    right_eye_min_distance_px: float
    nose_nearest_street_x: int
    nose_nearest_street_y: int
    nose_nearest_nose_x: int
    nose_nearest_nose_y: int
    left_eye_street_nearest_x: int
    left_eye_street_nearest_y: int
    left_eye_nearest_x: int
    left_eye_nearest_y: int
    right_eye_street_nearest_x: int
    right_eye_street_nearest_y: int
    right_eye_nearest_x: int
    right_eye_nearest_y: int
    nose_min_distance_ratio: float
    left_eye_min_distance_ratio: float
    right_eye_min_distance_ratio: float
    top_margin_px: int
    bottom_margin_px: int
    left_margin_px: int
    right_margin_px: int
    min_edge_margin_px: int
    mouth_role_centroid_y: float
    mouth_role_target_y: float
    mouth_role_offset_px: float
    vertical_centroid_ratio: float
    upper_half_ratio: float
    lower_half_ratio: float
    top_band_width: int
    bottom_band_width: int
    collision_penalty: float
    proximity_penalty: float
    nose_clearance_penalty: float
    typography_clearance_penalty: float
    mouth_role_penalty: float
    edge_penalty: float
    scale_penalty: float
    offset_penalty: float
    rotation_penalty: float
    orientation_penalty_or_bonus: float
    score: float
    rank: int = 0

    @property
    def candidate(self) -> Candidate:
        return Candidate(self.orientation_deg, self.scale, self.x_offset, self.y_offset)
    @property
    def street_nose_overlap_pixels(self) -> int:
        """Exact street-mouth to static-nose overlap in final pixels."""
        return self.nose_overlap_pixels

    @property
    def street_nose_overlap_ratio(self) -> float:
        return self.nose_overlap_ratio

    @property
    def street_nose_min_distance_px(self) -> float:
        return self.nose_min_distance_px

    @property
    def street_nose_nearest_pair(self) -> tuple[tuple[int, int], tuple[int, int]]:
        return (
            (self.nose_nearest_street_x, self.nose_nearest_street_y),
            (self.nose_nearest_nose_x, self.nose_nearest_nose_y),
        )

    @property
    def nose_clearance_contribution(self) -> float:
        return -self.nose_clearance_penalty


@dataclass(frozen=True)
class FaceAnatomyMasks:
    street_mouth: Image.Image
    left_eye: Image.Image
    right_eye: Image.Image
    static_nose: Image.Image
    typography: Image.Image
    base: Image.Image


@dataclass(frozen=True)
class CandidateAnalysis:
    street: StreetRecord
    area: str
    grid: CandidateGrid
    results: tuple[CandidateResult, ...]
    current: CandidateResult
    best: CandidateResult
    classification: str

    @property
    def improvement(self) -> float:
        return self.best.score - self.current.score


def generate_candidates(grid: CandidateGrid = CandidateGrid()) -> tuple[Candidate, ...]:
    """Generate reproducible candidates in lexical transform order."""
    return tuple(
        Candidate(orientation, scale, x_offset, y_offset)
        for orientation in grid.orientations_deg
        for scale in grid.scales
        for x_offset in grid.x_offsets
        for y_offset in grid.y_offsets
    )


def overlap_pixels(street_mask: Image.Image, protected_mask: Image.Image) -> int:
    """Return thresholded alpha-mask intersections, avoiding bbox false positives."""
    _require_same_size(street_mask, protected_mask)
    return sum(left > 0 and right > 0 for left, right in zip(_binary(street_mask).getdata(), _binary(protected_mask).getdata()))


def analyse_front_candidates(
    street: StreetRecord,
    *,
    area: str = "",
    grid: CandidateGrid = CandidateGrid(),
    weights: ScoringWeights = ScoringWeights(),
    thresholds: ClassificationThresholds = ClassificationThresholds(),
) -> CandidateAnalysis:
    """Score the grid, rank it deterministically, and classify diagnostic evidence."""
    masks = render_production_masks(street, area=area)
    results = [score_candidate(masks, item, weights=weights) for item in generate_candidates(grid)]
    ordered = sorted(
        results,
        key=lambda item: (-item.score, item.orientation_deg, -item.scale, abs(item.y_offset), abs(item.x_offset), item.y_offset, item.x_offset),
    )
    ranked = tuple(CandidateResult(**{**asdict(item), "rank": index}) for index, item in enumerate(ordered, 1))
    current = next(item for item in ranked if item.candidate == Candidate(0, 1.0, 0, 0))
    best = _conservative_best(ranked, thresholds)
    return CandidateAnalysis(street, area, grid, ranked, current, best, classify_candidate(current, best, thresholds))


def score_candidate(
    masks: FaceAnatomyMasks,
    candidate: Candidate,
    *,
    weights: ScoringWeights = ScoringWeights(),
    proximity: ProximityThresholds = ProximityThresholds(),
) -> CandidateResult:
    """Score a diagnostic candidate using collisions, spacing, margins, and shape."""
    _validate_face_masks(masks)
    street, clipped = transform_street_mask(masks.street_mouth, candidate)
    bounds = street.getbbox()
    pixels = _pixel_count(street)
    left = overlap_pixels(street, masks.left_eye)
    right = overlap_pixels(street, masks.right_eye)
    nose = overlap_pixels(street, masks.static_nose)
    typography = overlap_pixels(street, masks.typography)
    if street.getbbox() is None:
        if not clipped:
            raise DiagnosticMaskError("Transformed street mask is unexpectedly empty.")
        # A wholly clipped candidate is invalid, not a safely distant feature.
        # Keep it in the grid so one impossible transform cannot abort a real
        # validation run; its clipping penalty makes it noncompetitive and the
        # unavailable proximity evidence remains explicit in the CSV.
        nose_distance = left_distance = right_distance = math.inf
        typography_distance = math.inf
        nose_street = nose_point = left_street = left_point = right_street = right_point = (-1, -1)
        nose_proximity = left_proximity = right_proximity = typography_proximity = 0.0
        mouth_centroid = mouth_target = mouth_offset = 0.0
        mouth_penalty = 0.0
    else:
        nose_distance, nose_street, nose_point = nearest_foreground_distance(street, masks.static_nose)
        left_distance, left_street, left_point = nearest_foreground_distance(street, masks.left_eye)
        right_distance, right_street, right_point = nearest_foreground_distance(street, masks.right_eye)
        typography_distance, _typography_street, _typography_point = nearest_foreground_distance(street, masks.typography)
        nose_proximity = _spacing_penalty(nose_distance, proximity.nose_hard_min_px, proximity.nose_comfortable_px, weights.nose_proximity)
        left_proximity = _spacing_penalty(left_distance, proximity.eye_hard_min_px, proximity.eye_comfortable_px, weights.eye_proximity)
        right_proximity = _spacing_penalty(right_distance, proximity.eye_hard_min_px, proximity.eye_comfortable_px, weights.eye_proximity)
        typography_proximity = _spacing_penalty(typography_distance, proximity.typography_hard_min_px, proximity.typography_comfortable_px, weights.typography_proximity)
        mouth_centroid, mouth_target, mouth_offset = _mouth_role_descriptor(street, masks.static_nose, proximity)
        mouth_penalty = _mouth_role_penalty(mouth_offset, proximity.mouth_tolerance_px, weights.mouth_role_placement)
    proximity_penalty = left_proximity + right_proximity
    top_margin, bottom_margin, left_margin, right_margin = _edge_margins(street)
    min_margin = min(top_margin, bottom_margin, left_margin, right_margin)
    edge_penalty = _edge_penalty(min_margin, proximity.edge_soft_margin_px, weights.edge_proximity)
    edge_proximity = edge_penalty > 0
    centroid_ratio, upper_ratio, lower_ratio, top_width, bottom_width = _shape_descriptors(street)
    width_reference = max(top_width, bottom_width, 1)
    orientation_signal = 0.7 * ((top_width - bottom_width) / width_reference) + 0.3 * (upper_ratio - lower_ratio)
    orientation_bonus = weights.orientation_plausibility * orientation_signal
    collision_penalty = (
        typography * weights.typography_overlap_pixel
        + (left + right) * weights.eye_overlap_pixel
        + nose * weights.nose_overlap_pixel
        + (weights.clipping if clipped else 0)
    )
    scale_penalty = (1 - candidate.scale) * weights.scale_reduction
    offset_penalty = (abs(candidate.x_offset) + abs(candidate.y_offset)) * weights.displacement_per_pixel
    rotation_penalty = weights.rotation_180 if candidate.orientation_deg == 180 else 0.0
    score = weights.base_score - collision_penalty - proximity_penalty - nose_proximity - typography_proximity - mouth_penalty - edge_penalty - scale_penalty - offset_penalty - rotation_penalty + orientation_bonus
    return CandidateResult(
        orientation_deg=candidate.orientation_deg, scale=candidate.scale, x_offset=candidate.x_offset, y_offset=candidate.y_offset,
        street_width=0 if bounds is None else bounds[2] - bounds[0], street_height=0 if bounds is None else bounds[3] - bounds[1],
        street_pixels=pixels, left_eye_overlap=left, right_eye_overlap=right, nose_overlap_pixels=nose, typography_overlap=typography,
        left_eye_overlap_ratio=_ratio(left, pixels), right_eye_overlap_ratio=_ratio(right, pixels), nose_overlap_ratio=_ratio(nose, pixels), typography_overlap_ratio=_ratio(typography, pixels),
        clipped=clipped, edge_proximity=edge_proximity,
        nose_min_distance_px=round(nose_distance, 3), typography_min_distance_px=round(typography_distance, 3), left_eye_min_distance_px=round(left_distance, 3), right_eye_min_distance_px=round(right_distance, 3),
        nose_nearest_street_x=nose_street[0], nose_nearest_street_y=nose_street[1], nose_nearest_nose_x=nose_point[0], nose_nearest_nose_y=nose_point[1],
        left_eye_street_nearest_x=left_street[0], left_eye_street_nearest_y=left_street[1], left_eye_nearest_x=left_point[0], left_eye_nearest_y=left_point[1],
        right_eye_street_nearest_x=right_street[0], right_eye_street_nearest_y=right_street[1], right_eye_nearest_x=right_point[0], right_eye_nearest_y=right_point[1],
        nose_min_distance_ratio=_ratio_float(nose_distance, street.height), left_eye_min_distance_ratio=_ratio_float(left_distance, street.height), right_eye_min_distance_ratio=_ratio_float(right_distance, street.height),
        top_margin_px=top_margin, bottom_margin_px=bottom_margin, left_margin_px=left_margin, right_margin_px=right_margin, min_edge_margin_px=min_margin,
        mouth_role_centroid_y=round(mouth_centroid, 3), mouth_role_target_y=round(mouth_target, 3), mouth_role_offset_px=round(mouth_offset, 3),
        vertical_centroid_ratio=round(centroid_ratio, 6), upper_half_ratio=round(upper_ratio, 6), lower_half_ratio=round(lower_ratio, 6), top_band_width=top_width, bottom_band_width=bottom_width,
        collision_penalty=round(collision_penalty, 3), proximity_penalty=round(proximity_penalty, 3), nose_clearance_penalty=round(nose_proximity, 3), typography_clearance_penalty=round(typography_proximity, 3), mouth_role_penalty=round(mouth_penalty, 3), edge_penalty=round(edge_penalty, 3), scale_penalty=round(scale_penalty, 3), offset_penalty=round(offset_penalty, 3), rotation_penalty=round(rotation_penalty, 3), orientation_penalty_or_bonus=round(orientation_bonus, 3), score=round(score, 3),
    )

def transform_street_mask(mask: Image.Image, candidate: Candidate) -> tuple[Image.Image, bool]:
    """Transform only the real street mask: no mirroring or arbitrary rotation."""
    if candidate.orientation_deg not in (0, 180):
        raise ValueError("Only 0 and 180 degree diagnostic orientations are allowed.")
    bounds = _binary(mask).getbbox()
    output = Image.new("L", mask.size, 0)
    if bounds is None:
        return output, False
    feature = _binary(mask).crop(bounds)
    if candidate.orientation_deg == 180:
        feature = feature.transpose(Image.Transpose.ROTATE_180)
    scaled = (max(1, round(feature.width * candidate.scale)), max(1, round(feature.height * candidate.scale)))
    if scaled != feature.size:
        feature = feature.resize(scaled, Image.Resampling.NEAREST)
    centre_x = (bounds[0] + bounds[2]) / 2 + candidate.x_offset
    centre_y = (bounds[1] + bounds[3]) / 2 + candidate.y_offset
    left, top = round(centre_x - feature.width / 2), round(centre_y - feature.height / 2)
    clipped = left < 0 or top < 0 or left + feature.width > output.width or top + feature.height > output.height
    output.paste(feature, (left, top))
    return output, clipped


def classify_candidate(
    current: CandidateResult,
    best: CandidateResult,
    thresholds: ClassificationThresholds = ClassificationThresholds(),
) -> str:
    if _acceptable(current, thresholds):
        return "STANDARD"
    if not _acceptable(best, thresholds):
        return "UNRESOLVED"
    modest = best.scale >= thresholds.modest_min_scale and max(abs(best.x_offset), abs(best.y_offset)) <= thresholds.modest_max_offset
    return "ADAPTED" if modest else "UNRESOLVED"


def write_diagnostic_report(analysis: CandidateAnalysis, output_dir: Path, *, top_count: int = 3) -> Path:
    """Write CSV and debug-only current/best/top candidate PNGs outside normal outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report = output_dir / "candidates.csv"
    names = tuple(CandidateResult.__dataclass_fields__)
    with report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("street_id", "street_name", *names))
        writer.writeheader()
        for result in analysis.results:
            writer.writerow({"street_id": analysis.street.id, "street_name": analysis.street.display_name, **asdict(result)})
    masks = render_production_masks(analysis.street, area=analysis.area)
    _write_mask_audit(masks, output_dir / "mask_integrity.csv")
    _write_image(masks, analysis.current, output_dir / "current.png")
    _write_image(masks, analysis.best, output_dir / "best.png")
    _write_alignment_image(masks, analysis.current, output_dir / "alignment.png")
    for index, result in enumerate(analysis.results[:top_count], 1):
        _write_image(masks, result, output_dir / f"top_{index:02d}.png")
    return report


def write_anatomy_debug_output(
    street: StreetRecord,
    output_dir: Path,
    *,
    area: str = "",
) -> tuple[MaskAudit, ...]:
    """Write proof images for the corrected production anatomy outside normal outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    masks = render_production_masks(street, area=area)
    for filename, mask in (
        ("final_face.png", masks.base),
        ("street_mouth.png", masks.street_mouth),
        ("static_nose.png", masks.static_nose),
        ("left_eye.png", masks.left_eye),
        ("right_eye.png", masks.right_eye),
        ("typography.png", masks.typography),
    ):
        mask.save(output_dir / filename, format="PNG")
    combined = Image.new("L", masks.street_mouth.size, 0)
    for mask in (masks.static_nose, masks.left_eye, masks.right_eye, masks.typography):
        combined = Image.frombytes("L", combined.size, bytes(max(left, right) for left, right in zip(combined.getdata(), _binary(mask).getdata())))
    combined.save(output_dir / "protected_combined.png", format="PNG")
    _write_alignment_image(masks, score_candidate(masks, Candidate(0, 1.0, 0, 0)), output_dir / "anatomy_overlay.png")
    return audit_masks(masks)

def render_production_masks(street: StreetRecord, *, area: str = "") -> FaceAnatomyMasks:
    """Derive face masks from the exact native asset and production text geometry."""
    centre = face.SOURCE_CANVAS_PX[0] * face.FRONT_CENTER_RATIO
    markup = face._render_native_face(street.glyph_path, centre, *face.SOURCE_CANVAS_PX, face.STREET_STROKE_MULTIPLIER)
    street_mask = _asset_mask(markup, {"street"}, role="street_mouth")
    eyes = _asset_mask(markup, {"eye"}, role="eyes")
    components = _components(eyes)
    if len(components) != 2:
        raise ValueError(f"Expected two native eye masks, found {len(components)}.")
    typography = _typography_mask(street, area)
    masks = FaceAnatomyMasks(
        street_mask, _box_mask(eyes, components[0]), _box_mask(eyes, components[1]),
        _asset_mask(markup, {"v28-nose"}, role="static_nose"), typography,
        face.render_face(street, face.FaceRenderOptions(area=area)),
    )
    _validate_face_masks(masks, expected_size=face.FRONT_PANEL_PX)
    return masks


def _asset_mask(markup: str, classes: set[str], *, role: str) -> Image.Image:
    """Render one production role while retaining every inherited SVG transform.

    The native face is embedded as an asset.  Its selected ``face-content``
    group may be nested under translated, scaled, rotated, or matrix-transformed
    ancestors, so serialising the group alone changes its rendered location.
    This rebuilds its complete ancestor chain rather than composing transform
    strings, preserving SVG's native transform order generically.
    """
    asset, placement = _decode_asset(markup)
    root = ET.fromstring(asset)
    namespace = "{http://www.w3.org/2000/svg}"
    parent_by_child = {child: parent for parent in root.iter() for child in parent}
    content = next((node for node in root.iter(f"{namespace}g") if "face-content" in _classes(node)), None)
    if content is None:
        raise DiagnosticMaskError("Native face asset did not provide face content.")
    _retain(content, classes)
    selected = content
    ancestor = parent_by_child.get(content)
    while ancestor is not None and ancestor is not root:
        wrapper = ET.Element(ancestor.tag, ancestor.attrib)
        wrapper.append(selected)
        selected = wrapper
        ancestor = parent_by_child.get(ancestor)
    defs = root.find(f"{namespace}defs")
    defs_markup = "" if defs is None else ET.tostring(defs, encoding="unicode")
    filtered = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{face.FACE_ASSET_SIZE[0]}" height="{face.FACE_ASSET_SIZE[1]}" '
        f'viewBox="0 0 {face.FACE_ASSET_SIZE[0]} {face.FACE_ASSET_SIZE[1]}">{defs_markup}{ET.tostring(selected, encoding="unicode")}</svg>'
    )
    href = "data:image/svg+xml;base64," + base64.b64encode(filtered.encode("utf-8")).decode("ascii")
    width, height = face.SOURCE_CANVAS_PX
    transform = face._front_group_transform(width * face.FRONT_CENTER_RATIO, height, face.FRONT_GROUP_SCALE, face.FRONT_GROUP_Y_OFFSET)
    mask = _render_mask(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><g transform="{transform}"><image href="{href}" {placement}/></g></svg>')
    if mask.getbbox() is None:
        raise DiagnosticMaskError(f'Expected production anatomy role "{role}" was not rendered.')
    return mask

def _typography_mask(street: StreetRecord, area: str) -> Image.Image:
    width, height = face.SOURCE_CANVAS_PX
    palette, font_stack = native.get_face_palette(native.DEFAULT_PALETTE_KEY), native.get_text_font_stack(native.DEFAULT_TEXT_FONT_KEY)
    text = street.display_name.strip() or street.street_name.strip() or street.id
    title = face.select_title_font(text, font_stack)
    title_y, area_y = face._front_text_y_positions(height, face.FRONT_TITLE_LOCALITY_GAP_DELTA_PX, face.FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX)
    centre = width * face.FRONT_CENTER_RATIO
    transform = face._front_group_transform(centre, height, face.FRONT_GROUP_SCALE, face.FRONT_GROUP_Y_OFFSET)
    markup = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><style>.t{{font-family:{font_stack};font-size:{title.size_px}px;font-weight:{face.TITLE_WEIGHT};fill:{palette.feature};text-anchor:middle}}.a{{font:500 {face.LOCALITY_FONT_SIZE}px {font_stack};fill:{palette.feature};text-anchor:middle;letter-spacing:0.6px}}</style><g transform="{transform}"><text class="t" x="{centre}" y="{title_y}">{face._escape(text)}</text><text class="a" x="{centre}" y="{area_y}">{face._escape(area.strip())}</text></g></svg>'''
    return _render_mask(markup)


def _decode_asset(markup: str) -> tuple[str, str]:
    match = re.search(r'href="data:image/svg\+xml;base64,([^"]+)"\s+([^>]+)>', markup)
    if match is None:
        raise ValueError("Could not decode production face asset.")
    return base64.b64decode(match.group(1)).decode("utf-8"), match.group(2).rstrip("/").strip()


def _retain(node: ET.Element, classes: set[str]) -> bool:
    keep = bool(_classes(node) & classes)
    for child in list(node):
        if _retain(child, classes):
            keep = True
        else:
            node.remove(child)
    return keep


def _classes(node: ET.Element) -> set[str]:
    return set(node.get("class", "").split())


def _render_mask(markup: str) -> Image.Image:
    png = cairosvg.svg2png(bytestring=markup.encode("utf-8"), output_width=face.SOURCE_CANVAS_PX[0], output_height=face.SOURCE_CANVAS_PX[1])
    with Image.open(io.BytesIO(png)) as image:
        return _binary(image.getchannel("A").crop((0, 0, *face.FRONT_PANEL_PX)))


def _binary(mask: Image.Image) -> Image.Image:
    return mask.convert("L").point(lambda value: 255 if value >= MASK_ALPHA_THRESHOLD else 0)


def _pixel_count(mask: Image.Image) -> int:
    return sum(value > 0 for value in _binary(mask).getdata())


def audit_masks(masks: FaceAnatomyMasks) -> tuple[MaskAudit, ...]:
    """Return reproducible integrity evidence for final-coordinate masks."""
    return tuple(
        MaskAudit(name, _binary(mask).size, _pixel_count(mask), _binary(mask).getbbox())
        for name, mask in (
            ("street_mouth", masks.street_mouth), ("left_eye", masks.left_eye), ("right_eye", masks.right_eye),
            ("static_nose", masks.static_nose), ("typography", masks.typography),
        )
    )


def _validate_face_masks(masks: FaceAnatomyMasks, *, expected_size: tuple[int, int] | None = None) -> None:
    """Reject mismatched or empty masks instead of disguising bad geometry as spacing."""
    audits = audit_masks(masks)
    sizes = {item.size for item in audits}
    if len(sizes) != 1 or masks.base.size not in sizes:
        raise DiagnosticMaskError(f"Diagnostic masks must share one coordinate space; got {[item.size for item in audits]} and base {masks.base.size}.")
    size = audits[0].size
    if expected_size is not None and size != expected_size:
        raise DiagnosticMaskError(f"Diagnostic masks must use {expected_size}, got {size}.")
    empty = [item.name for item in audits if item.foreground_pixels == 0]
    if empty:
        raise DiagnosticMaskError(f"Required diagnostic mask(s) are empty: {', '.join(empty)}.")


def _require_same_size(left: Image.Image, right: Image.Image) -> None:
    if left.size != right.size:
        raise DiagnosticMaskError(f"Masks must share a coordinate space, got {left.size} and {right.size}.")


def _ratio(value: int, pixels: int) -> float:
    return round(value / pixels, 6) if pixels else 0.0


def _components(mask: Image.Image) -> list[tuple[int, int, int, int]]:
    pixels, seen, boxes = _binary(mask).load(), set(), []
    for y in range(mask.height):
        for x in range(mask.width):
            if not pixels[x, y] or (x, y) in seen:
                continue
            stack, points = [(x, y)], []
            seen.add((x, y))
            while stack:
                px, py = stack.pop()
                points.append((px, py))
                for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                    if 0 <= nx < mask.width and 0 <= ny < mask.height and pixels[nx, ny] and (nx, ny) not in seen:
                        seen.add((nx, ny)); stack.append((nx, ny))
            xs, ys = zip(*points)
            boxes.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1))
    return sorted(boxes, key=lambda item: item[0])


def _box_mask(mask: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    output = Image.new("L", mask.size, 0)
    output.paste(mask.crop(box), box[:2])
    return output


def _distance_field(mask: Image.Image) -> tuple[float, ...]:
    """Exact Euclidean distance transform of a thresholded protected mask."""
    binary = _binary(mask)
    if binary.getbbox() is None:
        raise DiagnosticMaskError("Cannot calculate proximity to an empty protected mask.")
    cache_key = (binary.tobytes(), binary.size)
    cached = _DISTANCE_FIELD_CACHE.get(cache_key)
    if cached is not None:
        return cached
    width, height = binary.size
    source = binary.load()
    infinity = width * width + height * height
    columns = [[0 if source[x, y] else infinity for y in range(height)] for x in range(width)]
    vertical = [_edt_1d(column) for column in columns]
    result = [0.0] * (width * height)
    for y in range(height):
        row = _edt_1d([vertical[x][y] for x in range(width)])
        for x, squared_distance in enumerate(row):
            result[y * width + x] = math.sqrt(squared_distance)
    field = tuple(result)
    _DISTANCE_FIELD_CACHE[cache_key] = field
    return field


def _edt_1d(values: list[int]) -> list[int]:
    """Felzenszwalb-Huttenlocher one-dimensional squared distance transform."""
    count = len(values)
    sites, boundaries, output = [0] * count, [0.0] * (count + 1), [0] * count
    low, high = 0, 0
    sites[0], boundaries[0], boundaries[1] = 0, float('-inf'), float('inf')
    for query in range(1, count):
        intersection = ((values[query] + query * query) - (values[sites[high]] + sites[high] * sites[high])) / (2 * query - 2 * sites[high])
        while intersection <= boundaries[high]:
            high -= 1
            intersection = ((values[query] + query * query) - (values[sites[high]] + sites[high] * sites[high])) / (2 * query - 2 * sites[high])
        high += 1
        sites[high], boundaries[high], boundaries[high + 1] = query, intersection, float('inf')
    for query in range(count):
        while boundaries[low + 1] < query:
            low += 1
        nearest = sites[low]
        output[query] = (query - nearest) * (query - nearest) + values[nearest]
    return output


def nearest_foreground_distance(
    street: Image.Image, protected: Image.Image,
) -> tuple[float, tuple[int, int], tuple[int, int]]:
    """Return exact foreground minimum and its deterministic ``(x, y)`` pair.

    Overlap is distance zero; ties choose street then protected pixels in
    row-major order. Both masks must be non-empty and share final coordinates.
    """
    _require_same_size(street, protected)
    street_binary, protected_binary = _binary(street), _binary(protected)
    if street_binary.getbbox() is None:
        raise DiagnosticMaskError("Cannot calculate proximity from an empty street mask.")
    if protected_binary.getbbox() is None:
        raise DiagnosticMaskError("Cannot calculate proximity to an empty protected mask.")
    field, width = _distance_field(protected_binary), street_binary.width
    distance, street_x, street_y = min(
        (field[index], index % width, index // width)
        for index, value in enumerate(street_binary.getdata()) if value
    )
    radius, pixels = math.ceil(distance), protected_binary.load()
    nearest = min(
        (math.hypot(protected_x - street_x, protected_y - street_y), protected_x, protected_y)
        for protected_y in range(max(0, street_y - radius), min(protected_binary.height, street_y + radius + 1))
        for protected_x in range(max(0, street_x - radius), min(protected_binary.width, street_x + radius + 1))
        if pixels[protected_x, protected_y]
    )
    maximum = math.hypot(street_binary.width - 1, street_binary.height - 1)
    if not (0 <= nearest[0] <= maximum) or not math.isclose(nearest[0], distance, abs_tol=1e-9):
        raise DiagnosticMaskError("Proximity distance is outside panel bounds or lacks a matching foreground pixel.")
    return nearest[0], (street_x, street_y), (nearest[1], nearest[2])


def _spacing_penalty(distance: float, hard_minimum: float, comfortable: float, weight: float) -> float:
    if distance >= comfortable:
        return 0.0
    if distance >= hard_minimum:
        return weight * (comfortable - distance) / (comfortable - hard_minimum)
    return weight * (1 + (hard_minimum - distance) / hard_minimum)


def _mouth_role_penalty(offset: float, tolerance: float, weight: float) -> float:
    excess = max(0.0, abs(offset) - tolerance)
    return weight * min(1.0, excess / tolerance)


def _mouth_role_descriptor(
    street: Image.Image, nose: Image.Image, thresholds: ProximityThresholds,
) -> tuple[float, float, float]:
    street_pixels = _binary(street)
    nose_bounds = _binary(nose).getbbox()
    ys = [
        y for y in range(street_pixels.height) for x in range(street_pixels.width)
        if street_pixels.getpixel((x, y))
    ]
    if not ys or nose_bounds is None:
        raise DiagnosticMaskError('Cannot evaluate mouth-role placement from an empty mask.')
    centroid = sum(ys) / len(ys)
    target = nose_bounds[1] + (nose_bounds[3] - nose_bounds[1]) * thresholds.mouth_target_nose_bounds_ratio
    return centroid, target, centroid - target


def _edge_margins(mask: Image.Image) -> tuple[int, int, int, int]:
    bounds = _binary(mask).getbbox()
    if bounds is None:
        return mask.height, mask.height, mask.width, mask.width
    return bounds[1], mask.height - bounds[3], bounds[0], mask.width - bounds[2]


def _edge_penalty(margin: int, soft_margin: float, weight: float) -> float:
    return 0.0 if margin >= soft_margin else weight * (soft_margin - margin) / soft_margin


def _shape_descriptors(mask: Image.Image) -> tuple[float, float, float, int, int]:
    bounds = _binary(mask).getbbox()
    if bounds is None:
        return 0.5, 0.5, 0.5, 0, 0
    pixels = _binary(mask).load()
    left, top, right, bottom = bounds
    height = bottom - top
    points = [(x, y) for y in range(top, bottom) for x in range(left, right) if pixels[x, y]]
    midpoint = top + height / 2
    upper = sum(y < midpoint for _x, y in points)
    lower = len(points) - upper
    band = max(1, math.ceil(height * 0.2))
    top_width = len({x for x, y in points if y < top + band})
    bottom_width = len({x for x, y in points if y >= bottom - band})
    centroid = sum(y for _x, y in points) / len(points)
    return (centroid - top) / max(1, height - 1), upper / len(points), lower / len(points), top_width, bottom_width


def _ratio_float(value: float, denominator: int) -> float:
    return round(value / denominator, 6) if denominator else 0.0

def _acceptable(item: CandidateResult, thresholds: ClassificationThresholds) -> bool:
    return (
        not item.clipped and item.scale >= thresholds.min_scale and item.score >= thresholds.minimum_score
        and item.left_eye_overlap_ratio <= thresholds.max_eye_overlap_ratio
        and item.right_eye_overlap_ratio <= thresholds.max_eye_overlap_ratio
        and item.nose_overlap_ratio <= thresholds.max_nose_overlap_ratio
        and item.typography_overlap_ratio <= thresholds.max_typography_overlap_ratio
        and item.mouth_role_penalty <= thresholds.max_mouth_role_penalty
    )


def _conservative_best(results: Sequence[CandidateResult], thresholds: ClassificationThresholds) -> CandidateResult:
    eligible = [item for item in results if _acceptable(item, thresholds)]
    pool = eligible or list(results)
    best_score = max(item.score for item in pool)
    effective_ties = [item for item in pool if best_score - item.score <= thresholds.effective_tie_score]
    return min(
        effective_ties,
        key=lambda item: (item.orientation_deg != 0, abs(1.0 - item.scale), abs(item.y_offset), abs(item.x_offset), item.y_offset, item.x_offset),
    )


def _write_mask_audit(masks: FaceAnatomyMasks, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("name", "size", "foreground_pixels", "bounds", "alpha_threshold"))
        writer.writeheader()
        for item in audit_masks(masks):
            writer.writerow({**asdict(item), "size": f"{item.size[0]}x{item.size[1]}", "bounds": item.bounds or ""})


def _write_image(masks: FaceAnatomyMasks, result: CandidateResult, path: Path) -> None:
    street, _clipped = transform_street_mask(masks.street_mouth, result.candidate)
    image = Image.new("RGBA", masks.base.size, "white")
    image.alpha_composite(masks.base)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for mask, colour in ((masks.left_eye, (50, 120, 255, 100)), (masks.right_eye, (50, 120, 255, 100)), (masks.static_nose, (255, 70, 70, 100)), (masks.typography, (255, 190, 30, 80)), (street, (30, 180, 80, 210))):
        layer = Image.new("RGBA", image.size, colour)
        overlay.alpha_composite(Image.composite(layer, Image.new("RGBA", image.size), mask))
    image.alpha_composite(overlay)
    ImageDraw.Draw(image).text((8, image.height - 18), f"{result.orientation_deg}deg scale={result.scale:.2f} dx={result.x_offset} dy={result.y_offset} score={result.score:.1f}", fill="black")
    image.save(path, format="PNG")


def _write_alignment_image(masks: FaceAnatomyMasks, result: CandidateResult, path: Path) -> None:
    """Write a production-face overlay with street-to-nose geometry evidence."""
    street, _clipped = transform_street_mask(masks.street_mouth, result.candidate)
    image = Image.new("RGBA", masks.base.size, "white")
    image.alpha_composite(masks.base)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for mask, colour in ((masks.left_eye, (50, 120, 255, 100)), (masks.right_eye, (50, 120, 255, 100)), (masks.static_nose, (255, 70, 70, 130)), (masks.typography, (255, 190, 30, 80)), (street, (30, 180, 80, 210))):
        overlay.alpha_composite(Image.composite(Image.new("RGBA", image.size, colour), Image.new("RGBA", image.size), mask))
    image.alpha_composite(overlay)
    draw = ImageDraw.Draw(image)
    for mask, colour in ((street, "green"), (masks.static_nose, "red")):
        bounds = _binary(mask).getbbox()
        if bounds is not None:
            draw.rectangle((bounds[0], bounds[1], bounds[2] - 1, bounds[3] - 1), outline=colour, width=1)
    street_point = (result.nose_nearest_street_x, result.nose_nearest_street_y)
    nose_point = (result.nose_nearest_nose_x, result.nose_nearest_nose_y)
    draw.line((street_point, nose_point), fill="magenta", width=2)
    draw.ellipse((street_point[0] - 2, street_point[1] - 2, street_point[0] + 2, street_point[1] + 2), fill="green")
    draw.ellipse((nose_point[0] - 2, nose_point[1] - 2, nose_point[0] + 2, nose_point[1] + 2), fill="red")
    draw.text((8, image.height - 18), f"nose {result.nose_min_distance_px:.3f}px: street={street_point} nose={nose_point}", fill="black")
    image.save(path, format="PNG")
