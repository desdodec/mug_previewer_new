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

    mouth_hard_min_px: float = 12.0
    mouth_comfortable_px: float = 32.0
    eye_hard_min_px: float = 9.0
    eye_comfortable_px: float = 24.0
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
    mouth_overlap_pixel: float = 0.8
    clipping: float = 200.0
    mouth_proximity: float = 9.0
    eye_proximity: float = 5.0
    edge_proximity: float = 2.0
    scale_reduction: float = 30.0
    displacement_per_pixel: float = 0.05
    rotation_180: float = 3.0
    orientation_plausibility: float = 7.0

@dataclass(frozen=True)
class ClassificationThresholds:
    max_eye_overlap_ratio: float = 0.015
    max_mouth_overlap_ratio: float = 0.020
    max_typography_overlap_ratio: float = 0.005
    min_scale: float = 0.80
    minimum_score: float = 80.0
    modest_min_scale: float = 0.90
    modest_max_offset: int = 40
    orientation_change_threshold: float = 5.0


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
    mouth_overlap: int
    typography_overlap: int
    left_eye_overlap_ratio: float
    right_eye_overlap_ratio: float
    mouth_overlap_ratio: float
    typography_overlap_ratio: float
    clipped: bool
    edge_proximity: bool
    mouth_min_distance_px: float
    left_eye_min_distance_px: float
    right_eye_min_distance_px: float
    mouth_street_nearest_x: int
    mouth_street_nearest_y: int
    mouth_nearest_x: int
    mouth_nearest_y: int
    left_eye_street_nearest_x: int
    left_eye_street_nearest_y: int
    left_eye_nearest_x: int
    left_eye_nearest_y: int
    right_eye_street_nearest_x: int
    right_eye_street_nearest_y: int
    right_eye_nearest_x: int
    right_eye_nearest_y: int
    mouth_min_distance_ratio: float
    left_eye_min_distance_ratio: float
    right_eye_min_distance_ratio: float
    top_margin_px: int
    bottom_margin_px: int
    left_margin_px: int
    right_margin_px: int
    min_edge_margin_px: int
    vertical_centroid_ratio: float
    upper_half_ratio: float
    lower_half_ratio: float
    top_band_width: int
    bottom_band_width: int
    collision_penalty: float
    proximity_penalty: float
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


@dataclass(frozen=True)
class FaceMasks:
    street: Image.Image
    left_eye: Image.Image
    right_eye: Image.Image
    mouth: Image.Image
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
    masks: FaceMasks,
    candidate: Candidate,
    *,
    weights: ScoringWeights = ScoringWeights(),
    proximity: ProximityThresholds = ProximityThresholds(),
) -> CandidateResult:
    """Score a diagnostic candidate using collisions, spacing, margins, and shape."""
    _validate_face_masks(masks)
    street, clipped = transform_street_mask(masks.street, candidate)
    bounds = street.getbbox()
    pixels = _pixel_count(street)
    left = overlap_pixels(street, masks.left_eye)
    right = overlap_pixels(street, masks.right_eye)
    mouth = overlap_pixels(street, masks.mouth)
    typography = overlap_pixels(street, masks.typography)
    mouth_distance, mouth_street, mouth_point = nearest_foreground_distance(street, masks.mouth)
    left_distance, left_street, left_point = nearest_foreground_distance(street, masks.left_eye)
    right_distance, right_street, right_point = nearest_foreground_distance(street, masks.right_eye)
    mouth_proximity = _spacing_penalty(mouth_distance, proximity.mouth_hard_min_px, proximity.mouth_comfortable_px, weights.mouth_proximity)
    left_proximity = _spacing_penalty(left_distance, proximity.eye_hard_min_px, proximity.eye_comfortable_px, weights.eye_proximity)
    right_proximity = _spacing_penalty(right_distance, proximity.eye_hard_min_px, proximity.eye_comfortable_px, weights.eye_proximity)
    proximity_penalty = mouth_proximity + left_proximity + right_proximity
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
        + mouth * weights.mouth_overlap_pixel
        + (weights.clipping if clipped else 0)
    )
    scale_penalty = (1 - candidate.scale) * weights.scale_reduction
    offset_penalty = (abs(candidate.x_offset) + abs(candidate.y_offset)) * weights.displacement_per_pixel
    rotation_penalty = weights.rotation_180 if candidate.orientation_deg == 180 else 0.0
    score = weights.base_score - collision_penalty - proximity_penalty - edge_penalty - scale_penalty - offset_penalty - rotation_penalty + orientation_bonus
    return CandidateResult(
        orientation_deg=candidate.orientation_deg, scale=candidate.scale, x_offset=candidate.x_offset, y_offset=candidate.y_offset,
        street_width=0 if bounds is None else bounds[2] - bounds[0], street_height=0 if bounds is None else bounds[3] - bounds[1],
        street_pixels=pixels, left_eye_overlap=left, right_eye_overlap=right, mouth_overlap=mouth, typography_overlap=typography,
        left_eye_overlap_ratio=_ratio(left, pixels), right_eye_overlap_ratio=_ratio(right, pixels), mouth_overlap_ratio=_ratio(mouth, pixels), typography_overlap_ratio=_ratio(typography, pixels),
        clipped=clipped, edge_proximity=edge_proximity,
        mouth_min_distance_px=round(mouth_distance, 3), left_eye_min_distance_px=round(left_distance, 3), right_eye_min_distance_px=round(right_distance, 3),
        mouth_street_nearest_x=mouth_street[0], mouth_street_nearest_y=mouth_street[1], mouth_nearest_x=mouth_point[0], mouth_nearest_y=mouth_point[1],
        left_eye_street_nearest_x=left_street[0], left_eye_street_nearest_y=left_street[1], left_eye_nearest_x=left_point[0], left_eye_nearest_y=left_point[1],
        right_eye_street_nearest_x=right_street[0], right_eye_street_nearest_y=right_street[1], right_eye_nearest_x=right_point[0], right_eye_nearest_y=right_point[1],
        mouth_min_distance_ratio=_ratio_float(mouth_distance, street.height), left_eye_min_distance_ratio=_ratio_float(left_distance, street.height), right_eye_min_distance_ratio=_ratio_float(right_distance, street.height),
        top_margin_px=top_margin, bottom_margin_px=bottom_margin, left_margin_px=left_margin, right_margin_px=right_margin, min_edge_margin_px=min_margin,
        vertical_centroid_ratio=round(centroid_ratio, 6), upper_half_ratio=round(upper_ratio, 6), lower_half_ratio=round(lower_ratio, 6), top_band_width=top_width, bottom_band_width=bottom_width,
        collision_penalty=round(collision_penalty, 3), proximity_penalty=round(proximity_penalty, 3), edge_penalty=round(edge_penalty, 3), scale_penalty=round(scale_penalty, 3), offset_penalty=round(offset_penalty, 3), rotation_penalty=round(rotation_penalty, 3), orientation_penalty_or_bonus=round(orientation_bonus, 3), score=round(score, 3),
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
        return "UNSUITABLE"
    modest = best.scale >= thresholds.modest_min_scale and max(abs(best.x_offset), abs(best.y_offset)) <= thresholds.modest_max_offset
    return "ADAPTED" if modest else "EXTREME"


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


def render_production_masks(street: StreetRecord, *, area: str = "") -> FaceMasks:
    """Derive face masks from the exact native asset and production text geometry."""
    centre = face.SOURCE_CANVAS_PX[0] * face.FRONT_CENTER_RATIO
    markup = face._render_native_face(street.glyph_path, centre, *face.SOURCE_CANVAS_PX, face.STREET_STROKE_MULTIPLIER)
    street_mask = _asset_mask(markup, {"street"})
    eyes = _asset_mask(markup, {"eye"})
    components = _components(eyes)
    if len(components) != 2:
        raise ValueError(f"Expected two native eye masks, found {len(components)}.")
    typography = _typography_mask(street, area)
    masks = FaceMasks(
        street_mask, _box_mask(eyes, components[0]), _box_mask(eyes, components[1]),
        _asset_mask(markup, {"mouth"}), typography,
        face.render_face(street, face.FaceRenderOptions(area=area)),
    )
    _validate_face_masks(masks, expected_size=face.FRONT_PANEL_PX)
    return masks


def _asset_mask(markup: str, classes: set[str]) -> Image.Image:
    asset, placement = _decode_asset(markup)
    root = ET.fromstring(asset)
    namespace = "{http://www.w3.org/2000/svg}"
    content = next((node for node in root.iter(f"{namespace}g") if "face-content" in _classes(node)), None)
    if content is None:
        raise ValueError("Native face asset did not provide face content.")
    _retain(content, classes)
    defs = root.find(f"{namespace}defs")
    defs_markup = "" if defs is None else ET.tostring(defs, encoding="unicode")
    filtered = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{face.FACE_ASSET_SIZE[0]}" height="{face.FACE_ASSET_SIZE[1]}" '
        f'viewBox="0 0 {face.FACE_ASSET_SIZE[0]} {face.FACE_ASSET_SIZE[1]}">{defs_markup}{ET.tostring(content, encoding="unicode")}</svg>'
    )
    href = "data:image/svg+xml;base64," + base64.b64encode(filtered.encode("utf-8")).decode("ascii")
    width, height = face.SOURCE_CANVAS_PX
    transform = face._front_group_transform(width * face.FRONT_CENTER_RATIO, height, face.FRONT_GROUP_SCALE, face.FRONT_GROUP_Y_OFFSET)
    return _render_mask(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><g transform="{transform}"><image href="{href}" {placement}/></g></svg>')


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


def audit_masks(masks: FaceMasks) -> tuple[MaskAudit, ...]:
    """Return reproducible integrity evidence for final-coordinate masks."""
    return tuple(
        MaskAudit(name, _binary(mask).size, _pixel_count(mask), _binary(mask).getbbox())
        for name, mask in (
            ("street", masks.street), ("left_eye", masks.left_eye), ("right_eye", masks.right_eye),
            ("mouth", masks.mouth), ("typography", masks.typography),
        )
    )


def _validate_face_masks(masks: FaceMasks, *, expected_size: tuple[int, int] | None = None) -> None:
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
        and item.mouth_overlap_ratio <= thresholds.max_mouth_overlap_ratio
        and item.typography_overlap_ratio <= thresholds.max_typography_overlap_ratio
    )


def _conservative_best(results: Sequence[CandidateResult], thresholds: ClassificationThresholds) -> CandidateResult:
    best = results[0]
    if best.orientation_deg == 0:
        return best
    original = next(item for item in results if item.orientation_deg == 0)
    return best if best.score - original.score >= thresholds.orientation_change_threshold else original


def _write_mask_audit(masks: FaceMasks, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("name", "size", "foreground_pixels", "bounds", "alpha_threshold"))
        writer.writeheader()
        for item in audit_masks(masks):
            writer.writerow({**asdict(item), "size": f"{item.size[0]}x{item.size[1]}", "bounds": item.bounds or ""})


def _write_image(masks: FaceMasks, result: CandidateResult, path: Path) -> None:
    street, _clipped = transform_street_mask(masks.street, result.candidate)
    image = Image.new("RGBA", masks.base.size, "white")
    image.alpha_composite(masks.base)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for mask, colour in ((masks.left_eye, (50, 120, 255, 100)), (masks.right_eye, (50, 120, 255, 100)), (masks.mouth, (255, 70, 70, 100)), (masks.typography, (255, 190, 30, 80)), (street, (30, 180, 80, 210))):
        layer = Image.new("RGBA", image.size, colour)
        overlay.alpha_composite(Image.composite(layer, Image.new("RGBA", image.size), mask))
    image.alpha_composite(overlay)
    ImageDraw.Draw(image).text((8, image.height - 18), f"{result.orientation_deg}deg scale={result.scale:.2f} dx={result.x_offset} dy={result.y_offset} score={result.score:.1f}", fill="black")
    image.save(path, format="PNG")


def _write_alignment_image(masks: FaceMasks, result: CandidateResult, path: Path) -> None:
    """Write a production-face overlay with mouth-distance geometry evidence."""
    street, _clipped = transform_street_mask(masks.street, result.candidate)
    image = Image.new("RGBA", masks.base.size, "white")
    image.alpha_composite(masks.base)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for mask, colour in ((masks.left_eye, (50, 120, 255, 100)), (masks.right_eye, (50, 120, 255, 100)), (masks.mouth, (255, 70, 70, 130)), (masks.typography, (255, 190, 30, 80)), (street, (30, 180, 80, 210))):
        overlay.alpha_composite(Image.composite(Image.new("RGBA", image.size, colour), Image.new("RGBA", image.size), mask))
    image.alpha_composite(overlay)
    draw = ImageDraw.Draw(image)
    for mask, colour in ((street, "green"), (masks.mouth, "red")):
        bounds = _binary(mask).getbbox()
        if bounds is not None:
            draw.rectangle((bounds[0], bounds[1], bounds[2] - 1, bounds[3] - 1), outline=colour, width=1)
    street_point = (result.mouth_street_nearest_x, result.mouth_street_nearest_y)
    mouth_point = (result.mouth_nearest_x, result.mouth_nearest_y)
    draw.line((street_point, mouth_point), fill="magenta", width=2)
    draw.ellipse((street_point[0] - 2, street_point[1] - 2, street_point[0] + 2, street_point[1] + 2), fill="green")
    draw.ellipse((mouth_point[0] - 2, mouth_point[1] - 2, mouth_point[0] + 2, mouth_point[1] + 2), fill="red")
    draw.text((8, image.height - 18), f"mouth {result.mouth_min_distance_px:.3f}px: street={street_point} mouth={mouth_point}", fill="black")
    image.save(path, format="PNG")
