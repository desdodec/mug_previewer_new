"""Experimental pixel-mask candidate scoring for frozen front-face artwork.

This module is diagnostic-only.  It reads production geometry but production
rendering, previews, and exports never import it.
"""

from __future__ import annotations

import base64
import csv
import io
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
    edge_proximity: float = 8.0
    scale_reduction: float = 30.0
    displacement_per_pixel: float = 0.05
    rotation_180: float = 3.0


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
) -> CandidateResult:
    """Calculate raw overlap, normalised overlap, bounds, clipping, and score."""
    street, clipped = transform_street_mask(masks.street, candidate)
    bounds = street.getbbox()
    pixels = _pixel_count(street)
    left = overlap_pixels(street, masks.left_eye)
    right = overlap_pixels(street, masks.right_eye)
    mouth = overlap_pixels(street, masks.mouth)
    typography = overlap_pixels(street, masks.typography)
    edge_proximity = _edge_proximity(street)
    score = (
        weights.base_score
        - typography * weights.typography_overlap_pixel
        - (left + right) * weights.eye_overlap_pixel
        - mouth * weights.mouth_overlap_pixel
        - (weights.clipping if clipped else 0)
        - (weights.edge_proximity if edge_proximity else 0)
        - (1 - candidate.scale) * weights.scale_reduction
        - (abs(candidate.x_offset) + abs(candidate.y_offset)) * weights.displacement_per_pixel
        - (weights.rotation_180 if candidate.orientation_deg == 180 else 0)
    )
    return CandidateResult(
        candidate.orientation_deg, candidate.scale, candidate.x_offset, candidate.y_offset,
        0 if bounds is None else bounds[2] - bounds[0], 0 if bounds is None else bounds[3] - bounds[1],
        pixels, left, right, mouth, typography, _ratio(left, pixels), _ratio(right, pixels),
        _ratio(mouth, pixels), _ratio(typography, pixels), clipped, edge_proximity, round(score, 3),
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
    _write_image(masks, analysis.current, output_dir / "current.png")
    _write_image(masks, analysis.best, output_dir / "best.png")
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
    return FaceMasks(
        street_mask, _box_mask(eyes, components[0]), _box_mask(eyes, components[1]),
        _asset_mask(markup, {"mouth"}), typography,
        face.render_face(street, face.FaceRenderOptions(area=area)),
    )


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


def _edge_proximity(mask: Image.Image, margin: int = 8) -> bool:
    bounds = _binary(mask).getbbox()
    return bool(bounds and (bounds[0] < margin or bounds[1] < margin or bounds[2] > mask.width - margin or bounds[3] > mask.height - margin))


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
