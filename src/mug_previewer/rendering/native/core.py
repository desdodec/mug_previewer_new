#!/usr/bin/env python3
"""
street_face_generator_v20.py

Geometry-locked street-to-face generator with curated print editions.
V16 can also create the robust step-09 overlay SVG first and use that overlay
as the face source, so the generated faces come from the filtered street set.
When one SVG file is selected directly, V16 offers A4/A5/A6 output and uses
stronger face and street strokes suited to a single large face.
V17 adds a deterministic provenance microdot seal after the OpenStreetMap
attribution note. It encodes the area/district and generation date in a
print-friendly finder circle plus dot strip.
V18 adds happiness-led gallery ordering. Smile-like crescent mouths rise to
the top while the older sinuosity ordering remains available.
V20 keeps street-name labels at their intended font size and wraps names that
exceed the available frame width onto two balanced, centred lines without
changing the configured font size.

Core rule:
    The street path is sacred.
    It may be uniformly scaled, translated, and rotated.
    It must not be smoothed, bent, stretched, mirrored, or redrawn into a nicer facial feature.

Usage:
    python street_face_generator_v20.py input1.svg input2.svg input3.svg -o faces.svg --png faces.png --pdf faces.pdf
    python street_face_generator_v20.py N16 -o output
    python street_face_generator_v20.py --overlay-area Stoke_Newington -o output
    python street_face_generator_v20.py --sort-by happiness --batch-single-folder glyphs -o single_faces --paper a6 --batch-pdf
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import importlib.util
import json
import math
import os
import re
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from argparse import Namespace
from typing import List, Optional, Sequence, Tuple



Point = Tuple[float, float]
ROOT_DIR = Path(__file__).resolve().parent
STEP09_V2_PATH = ROOT_DIR / "09_create_street_overlay_parks_water_streets_updated_v2.py"
DEFAULT_OVERLAY_OUTPUT_ROOT = "workflow_outputs_from_csv_classified_widths_frame_center_parks_water_boundary_clip_color_streets_v2"
DEFAULT_OVERLAY_ROYAL_MAIL_SOURCE_DIR = str(ROOT_DIR / "os_maps_royal_mail_addresses")
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"

WARM_PAPER = "#f4eadb"
CARD_PAPER = "#fff8ec"
CARD_BORDER = "#e3d5c2"
CHARCOAL = "#242321"
META_CHARCOAL = "#5c5750"
STREET_PALETTE = ["#137f83", "#b65a38", "#5d6872"]
BLUSH_GREY_BEIGE = "#cbc1b4"


@dataclass(frozen=True)
class FacePalette:
    key: str
    label: str
    background: str
    card: str
    border: str
    feature: str
    meta: str
    streets: Tuple[str, ...]
    blush: str


@dataclass(frozen=True)
class GalleryPlace:
    label: str
    town_or_city: str
    district: str
    is_postcode: bool


FACE_PALETTES = {
    "warm-paper": FacePalette(
        key="warm-paper",
        label="Warm Paper",
        background=WARM_PAPER,
        card=CARD_PAPER,
        border=CARD_BORDER,
        feature=CHARCOAL,
        meta=META_CHARCOAL,
        streets=tuple(STREET_PALETTE),
        blush=BLUSH_GREY_BEIGE,
    ),
    "gallery-mono": FacePalette(
        key="gallery-mono",
        label="Gallery Mono",
        background="#f7f9fb",
        card="#ffffff",
        border="#d7dde4",
        feature="#111827",
        meta="#4b5563",
        streets=("#1455d9", "#e23d28", "#374151"),
        blush="#d7dce5",
    ),
    "night-print": FacePalette(
        key="night-print",
        label="Night Print",
        background="#171717",
        card="#22211f",
        border="#3a3935",
        feature="#f2efe7",
        meta="#c8c2b8",
        streets=("#25d0d8", "#e85aa0", "#a6c96a"),
        blush="#6f6478",
    ),
    "graphite-print": FacePalette(
        key="graphite-print",
        label="Graphite Print",
        background="#2a2824",
        card="#34312c",
        border="#504b43",
        feature="#eee7dc",
        meta="#c7bdae",
        streets=("#33b8bc", "#d85d9a", "#9dbd64"),
        blush="#6f6478",
    ),
}
DEFAULT_PALETTE_KEY = "warm-paper"
TEXT_FONT_PRESETS = {
    "default": (
        "Default system",
        'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    ),
    "playfair-display": (
        "Playfair Display",
        '"Playfair Display", Georgia, "Times New Roman", serif',
    ),
    "futura": (
        "Futura geometric sans",
        'Futura, "Futura PT", "Avenir Next", Avenir, Montserrat, '
        '"Century Gothic", system-ui, sans-serif',
    ),
}
DEFAULT_TEXT_FONT_KEY = "default"
PRINT_TITLE = "THE ROADS GALLERY"
PRINT_SUBTITLE = "Selected street faces"
EAR_MODES = ("none", "varied", "one-stroke", "two-stroke")
PRESENTATION_MODES = ("varied", "neutral", "feminine", "masculine")
GALLERY_SORT_MODES = ("happiness", "sinuosity", "gallery")
ROAD_TYPES = ("major", "medium", "minor")
CURATORIAL_NOTE = (
    "A curated collection of street forms selected for their facial character. "
    "Each road geometry is preserved, then scaled, rotated and placed as a feature. "
    "Street data (c) OpenStreetMap contributors."
)
SIGNATURE_ENV_VAR = "STREET_FACE_SIGNATURE_ID"
DEFAULT_SIGNATURE_ID = "street-face-generator-v20-private-mark"
PAPER_PRESETS = {
    "a1": {
        "width": 2245,
        "height": 3179,
        "width_mm": 594,
        "height_mm": 841,
        "png_width": 7016,
        "png_height": 9933,
        "margin": 64.0,
        "gap": 20.0,
    },
    "a2": {
        "width": 1587,
        "height": 2245,
        "width_mm": 420,
        "height_mm": 594,
        "png_width": 4961,
        "png_height": 7016,
        "margin": 44.0,
        "gap": 16.0,
    },
    "a3": {
        "width": 1122,
        "height": 1587,
        "width_mm": 297,
        "height_mm": 420,
        "png_width": 3508,
        "png_height": 4961,
        "margin": 32.0,
        "gap": 12.0,
    },
    "a4": {
        "width": 794, "height": 1123, "width_mm": 210, "height_mm": 297,
        "png_width": 2480, "png_height": 3508, "margin": 23.0, "gap": 9.0,
    },
    "a5": {
        "width": 559, "height": 794, "width_mm": 148, "height_mm": 210,
        "png_width": 1748, "png_height": 2480, "margin": 16.0, "gap": 7.0,
    },
    "a6": {
        "width": 397, "height": 559, "width_mm": 105, "height_mm": 148,
        "png_width": 1240, "png_height": 1748, "margin": 12.0, "gap": 5.0,
    },}
SINGLE_SVG_STROKE_SCALE = 1.65
SINGLE_FACE_GALLERY_SCALE = 2.40
SINGLE_FACE_SCALE_REFERENCE_PAPER = "a6"
SINGLE_FACE_VERTICAL_OFFSET_FRACTION = 0.10
EDITION_PRESETS = {
    "roads": {
        "target_count": 36,
        "cols": 9,
        "paper": "a2",
        "note": True,
        "title": True,
        "ear_mode": "varied",
        "show_blush": False,
    },
    "index": {
        "target_count": None,
        "cols": 13,
        "paper": "a2",
        "note": True,
        "title": False,
        "ear_mode": "varied",
        "show_blush": False,
    },
    "twelve": {
        "target_count": 12,
        "cols": 4,
        "paper": "a3",
        "note": True,
        "title": True,
        "ear_mode": "none",
        "show_blush": True,
    },
}
EDITION_ALIASES = {
    "curated": "roads",
    "archive": "index",
    "mini": "twelve",
}


def get_face_palette(key: str = DEFAULT_PALETTE_KEY) -> FacePalette:
    return FACE_PALETTES.get(key, FACE_PALETTES[DEFAULT_PALETTE_KEY])


def get_text_font_stack(key: str = DEFAULT_TEXT_FONT_KEY) -> str:
    return TEXT_FONT_PRESETS.get(key, TEXT_FONT_PRESETS[DEFAULT_TEXT_FONT_KEY])[1]


def add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def cubic(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    u = 1.0 - t
    return (
        u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
        u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
    )


def quad(p0: Point, p1: Point, p2: Point, t: float) -> Point:
    u = 1.0 - t
    return (
        u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
        u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
    )


def bbox(points: Sequence[Point]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def centroid(points: Sequence[Point]) -> Point:
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def path_length(points: Sequence[Point]) -> float:
    return sum(dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def collection_length(paths: Sequence[Sequence[Point]]) -> float:
    return sum(path_length(path) for path in paths)


def longest_path(paths: Sequence[Sequence[Point]]) -> Sequence[Point]:
    return max(paths, key=path_length)


def rotate_points(
    points: Sequence[Point], angle_degrees: float, origin: Optional[Point] = None
) -> List[Point]:
    if origin is None:
        origin = centroid(points)
    a = math.radians(angle_degrees)
    ca, sa = math.cos(a), math.sin(a)
    ox, oy = origin
    out = []
    for x, y in points:
        dx, dy = x - ox, y - oy
        out.append((ox + dx * ca - dy * sa, oy + dx * sa + dy * ca))
    return out


def translate_points(points: Sequence[Point], dx: float, dy: float) -> List[Point]:
    return [(x + dx, y + dy) for x, y in points]


def polyline_to_svg_points(points: Sequence[Point]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def flatten_paths(paths: Sequence[Sequence[Point]]) -> List[Point]:
    return [pt for path in paths for pt in path]


def principal_axis_angle(points: Sequence[Point]) -> float:
    c = centroid(points)
    xs = [p[0] - c[0] for p in points]
    ys = [p[1] - c[1] for p in points]
    sxx = sum(x * x for x in xs) / max(1, len(points))
    syy = sum(y * y for y in ys) / max(1, len(points))
    sxy = sum(x * y for x, y in zip(xs, ys)) / max(1, len(points))
    return math.degrees(0.5 * math.atan2(2 * sxy, sxx - syy))


def turn_angles(points: Sequence[Point]) -> List[float]:
    vals = []
    for i in range(1, len(points) - 1):
        a = sub(points[i], points[i - 1])
        b = sub(points[i + 1], points[i])
        la, lb = math.hypot(*a), math.hypot(*b)
        if la < 1e-9 or lb < 1e-9:
            continue
        aa = math.atan2(a[1], a[0])
        bb = math.atan2(b[1], b[0])
        vals.append(math.degrees((bb - aa + math.pi) % (2 * math.pi) - math.pi))
    return vals


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def norm(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return clamp((v - lo) / (hi - lo), 0.0, 1.0)


_TOKEN_RE = re.compile(
    r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
)


def _is_cmd(tok: str) -> bool:
    return len(tok) == 1 and tok.isalpha()


def _num(tok: str) -> float:
    return float(tok)


def normalize_road_types(
    road_types: Optional[Sequence[str]],
) -> Optional[Tuple[str, ...]]:
    if not road_types:
        return None

    normalized: List[str] = []
    for road_type in road_types:
        key = str(road_type).strip().lower()
        if key == "all":
            return None
        if key not in ROAD_TYPES:
            raise ValueError(
                f"Unknown road type {road_type!r}. Choose from: "
                f"{', '.join(ROAD_TYPES)}."
            )
        if key not in normalized:
            normalized.append(key)
    return tuple(normalized) if normalized else None


def road_class_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = re.search(
        r"\b(?:road class|svg layer):\s*(major|medium|minor)\b", text, re.I
    )
    return match.group(1).lower() if match else None


def road_class_from_element(elem: ET.Element) -> Optional[str]:
    values = [
        elem.get("id"),
        elem.get("class"),
        elem.get("{http://www.inkscape.org/namespaces/inkscape}label"),
    ]
    for value in values:
        if not value:
            continue
        lowered = value.lower()
        for road_type in ROAD_TYPES:
            if re.search(rf"\b{road_type}\b", lowered) or f"_{road_type}" in lowered:
                return road_type

    for child in elem:
        if child.tag.split("}")[-1] == "desc":
            road_class = road_class_from_text(child.text)
            if road_class:
                return road_class
    return None


def parse_svg_path_d(d: str, curve_steps: int = 18) -> List[Point]:
    return flatten_paths(parse_svg_path_d_subpaths(d, curve_steps=curve_steps))


def parse_svg_path_d_subpaths(d: str, curve_steps: int = 18) -> List[List[Point]]:
    """
    Lightweight SVG path sampler.
    Supports M/L/H/V/C/S/Q/T/Z. Arcs are conservatively represented by their endpoint.
    For Inkscape-exported OSM street shapes this is usually exact because paths are polylines.
    """
    tokens = _TOKEN_RE.findall(d)
    i = 0
    cmd = None
    points: List[Point] = []
    subpaths: List[List[Point]] = []
    current: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    last_cubic_ctrl: Optional[Point] = None
    last_quad_ctrl: Optional[Point] = None

    def finish_subpath() -> None:
        nonlocal points
        clean = []
        for p in points:
            if not clean or dist(clean[-1], p) > 1e-6:
                clean.append(p)
        if len(clean) >= 2:
            subpaths.append(clean)
        points = []

    def read_pair(relative: bool) -> Point:
        nonlocal i, current
        x = _num(tokens[i])
        y = _num(tokens[i + 1])
        i += 2
        p = (x, y)
        return add(current, p) if relative else p

    while i < len(tokens):
        if _is_cmd(tokens[i]):
            cmd = tokens[i]
            i += 1
        if cmd is None:
            raise ValueError("SVG path missing command")

        relative = cmd.islower()
        c = cmd.upper()

        if c == "M":
            if points:
                finish_subpath()
            p = read_pair(relative)
            current = p
            start = p
            points.append(p)
            cmd = "l" if relative else "L"
            last_cubic_ctrl = last_quad_ctrl = None

        elif c == "L":
            while i < len(tokens) and not _is_cmd(tokens[i]):
                current = read_pair(relative)
                points.append(current)
            last_cubic_ctrl = last_quad_ctrl = None

        elif c == "H":
            while i < len(tokens) and not _is_cmd(tokens[i]):
                x = _num(tokens[i])
                i += 1
                if relative:
                    x = current[0] + x
                current = (x, current[1])
                points.append(current)
            last_cubic_ctrl = last_quad_ctrl = None

        elif c == "V":
            while i < len(tokens) and not _is_cmd(tokens[i]):
                y = _num(tokens[i])
                i += 1
                if relative:
                    y = current[1] + y
                current = (current[0], y)
                points.append(current)
            last_cubic_ctrl = last_quad_ctrl = None

        elif c == "C":
            while i < len(tokens) and not _is_cmd(tokens[i]):
                p1 = read_pair(relative)
                p2 = read_pair(relative)
                p3 = read_pair(relative)
                for step in range(1, curve_steps + 1):
                    points.append(cubic(current, p1, p2, p3, step / curve_steps))
                current = p3
                last_cubic_ctrl = p2
                last_quad_ctrl = None

        elif c == "S":
            while i < len(tokens) and not _is_cmd(tokens[i]):
                p1 = (
                    current
                    if last_cubic_ctrl is None
                    else (
                        2 * current[0] - last_cubic_ctrl[0],
                        2 * current[1] - last_cubic_ctrl[1],
                    )
                )
                p2 = read_pair(relative)
                p3 = read_pair(relative)
                for step in range(1, curve_steps + 1):
                    points.append(cubic(current, p1, p2, p3, step / curve_steps))
                current = p3
                last_cubic_ctrl = p2
                last_quad_ctrl = None

        elif c == "Q":
            while i < len(tokens) and not _is_cmd(tokens[i]):
                p1 = read_pair(relative)
                p2 = read_pair(relative)
                for step in range(1, curve_steps + 1):
                    points.append(quad(current, p1, p2, step / curve_steps))
                current = p2
                last_quad_ctrl = p1
                last_cubic_ctrl = None

        elif c == "T":
            while i < len(tokens) and not _is_cmd(tokens[i]):
                p1 = (
                    current
                    if last_quad_ctrl is None
                    else (
                        2 * current[0] - last_quad_ctrl[0],
                        2 * current[1] - last_quad_ctrl[1],
                    )
                )
                p2 = read_pair(relative)
                for step in range(1, curve_steps + 1):
                    points.append(quad(current, p1, p2, step / curve_steps))
                current = p2
                last_quad_ctrl = p1
                last_cubic_ctrl = None

        elif c == "A":
            while i < len(tokens) and not _is_cmd(tokens[i]):
                # rx ry xrot large sweep x y
                x = _num(tokens[i + 5])
                y = _num(tokens[i + 6])
                i += 7
                p = (x, y)
                if relative:
                    p = add(current, p)
                current = p
                points.append(p)
            last_cubic_ctrl = last_quad_ctrl = None

        elif c == "Z":
            current = start
            points.append(start)
            last_cubic_ctrl = last_quad_ctrl = None

        else:
            raise ValueError(f"Unsupported SVG path command: {cmd}")

    if points:
        finish_subpath()
    return subpaths


def load_svg_paths(
    svg_path: Path,
    road_types: Optional[Sequence[str]] = None,
) -> Tuple[str, List[List[Point]], str, List[str], bool]:
    root = ET.parse(svg_path).getroot()
    selected_road_types = normalize_road_types(road_types)
    title = svg_path.stem
    for elem in root.iter():
        if elem.tag.split("}")[-1] == "title" and elem.text and elem.text.strip():
            title = elem.text.strip()
            break

    all_paths: List[List[Point]] = []
    path_ds: List[str] = []
    uses_fill = False
    stroke = "#b56a5c"
    saw_road_class = False

    def visit(elem: ET.Element, inherited_road_class: Optional[str] = None) -> None:
        nonlocal stroke, uses_fill, saw_road_class
        tag = elem.tag.split("}")[-1]
        elem_road_class = road_class_from_element(elem) or inherited_road_class
        if elem_road_class:
            saw_road_class = True

        if tag == "path" and elem.get("d"):
            if selected_road_types and elem_road_class not in selected_road_types:
                return
            if elem.get("stroke"):
                stroke = elem.get("stroke") or stroke
            fill = elem.get("fill")
            if fill and fill.lower() != "none":
                uses_fill = True
            d = elem.get("d") or ""
            path_ds.append(d)
            all_paths.extend(parse_svg_path_d_subpaths(d))
            return

        for child in elem:
            visit(child, elem_road_class)

    visit(root)

    if not all_paths:
        if selected_road_types and saw_road_class:
            raise ValueError(
                f"No path data found in {svg_path} for road type"
                f"{'s' if len(selected_road_types) != 1 else ''}: "
                f"{', '.join(selected_road_types)}"
            )
        raise ValueError(f"No path data found in {svg_path}")
    return title, all_paths, stroke, path_ds, uses_fill


@dataclass
class ShapeMetrics:
    width: float
    height: float
    aspect: float
    length: float
    chord: float
    sinuosity: float
    angularity: float
    curvature_softness: float
    principal_angle: float
    symmetry_lr: float
    endpoint_angle: float
    turn_angle_sum: float
    complexity: float
    node_count: int
    bbox: Tuple[float, float, float, float]


@dataclass
class RoleChoice:
    role: str
    rotation: float
    score: float
    metrics: ShapeMetrics


@dataclass
class FaceSpec:
    name: str
    group_color: str
    role_choice: RoleChoice
    original_paths: List[List[Point]]
    transformed_paths: List[List[Point]]
    card_paths: List[List[Point]]
    source_path_ds: List[str]
    source_uses_fill: bool
    street_transform: str
    report: List[Tuple[str, float]]
    transform_matrix: Tuple[float, float, float, float, float, float] = (1, 0, 0, 1, 0, 0)
    scaling_factor_applied: float = 1.0
    length_percentile: float = 0.5
    collision_triggered: bool = False

    @property
    def original_points(self) -> List[Point]:
        return flatten_paths(self.original_paths)

    @property
    def transformed_points(self) -> List[Point]:
        return flatten_paths(self.transformed_paths)

    @property
    def card_points(self) -> List[Point]:
        return flatten_paths(self.card_paths)


def compute_metrics(points: Sequence[Point]) -> ShapeMetrics:
    return compute_path_metrics([points])


def compute_path_metrics(paths: Sequence[Sequence[Point]]) -> ShapeMetrics:
    points = flatten_paths(paths)
    x0, y0, x1, y1 = bbox(points)
    w = max(1e-9, x1 - x0)
    h = max(1e-9, y1 - y0)
    length = max(1e-9, collection_length(paths))
    primary = longest_path(paths)
    endpoint_chord = max(dist(path[0], path[-1]) for path in paths if len(path) >= 2)
    bbox_chord = math.hypot(w, h)
    chord = max(1e-9, endpoint_chord if endpoint_chord > 1e-6 else bbox_chord)
    sinuosity = length / chord
    angles = [angle for path in paths for angle in turn_angles(path)]
    turn_angle_sum = sum(abs(a) for a in angles)
    angularity = statistics.mean(abs(a) for a in angles) if angles else 0.0
    softness = 1.0 - norm(angularity, 10, 90)
    pangle = principal_axis_angle(points)
    if dist(primary[0], primary[-1]) > 1e-6:
        endpoint_angle = math.degrees(
            math.atan2(primary[-1][1] - primary[0][1], primary[-1][0] - primary[0][0])
        )
    else:
        endpoint_angle = pangle

    cx = (x0 + x1) / 2
    left = [abs(p[0] - cx) for p in points if p[0] < cx]
    right = [abs(p[0] - cx) for p in points if p[0] >= cx]
    lm = statistics.mean(left) if left else 0.0
    rm = statistics.mean(right) if right else 0.0
    symmetry_lr = 1.0 - clamp(abs(lm - rm) / max(w, 1e-9), 0.0, 1.0)

    return ShapeMetrics(
        width=w,
        height=h,
        aspect=w / h,
        length=length,
        chord=chord,
        sinuosity=sinuosity,
        angularity=angularity,
        curvature_softness=softness,
        principal_angle=pangle,
        symmetry_lr=symmetry_lr,
        endpoint_angle=endpoint_angle,
        turn_angle_sum=turn_angle_sum,
        complexity=sinuosity * (turn_angle_sum / length),
        node_count=len(points),
        bbox=(x0, y0, x1, y1),
    )


def role_scores(
    points: Sequence[Point], paths: Optional[Sequence[Sequence[Point]]] = None
) -> List[Tuple[str, float]]:
    """
    Face-prior-aware affordance scoring.

    This version is intentionally more opinionated than pure geometry.

    The question is not merely:
        "Is this wide or tall?"

    It is:
        "Could this exact immutable path convincingly become one facial feature?"

    Important bias:
        - Tall, narrow, kinked forms should become noses/profiles.
        - Wide shallow forms should become mouths.
        - Jaw/chin should only win when the shape is genuinely broad and bottom-framing.
    """
    if paths is None:
        paths = [points]
    m = compute_path_metrics(paths)
    primary = longest_path(paths)
    w, h, a = m.width, m.height, m.aspect
    inv_a = 1.0 / max(a, 1e-9)

    x0, y0, x1, y1 = m.bbox
    dx = primary[-1][0] - primary[0][0]
    dy = primary[-1][1] - primary[0][1]
    if dist(primary[0], primary[-1]) <= 1e-6:
        angle = math.radians(m.principal_angle)
        if m.width >= m.height:
            dx = math.cos(angle) * w
            dy = math.sin(angle) * w
        else:
            dx = -math.sin(angle) * h
            dy = math.cos(angle) * h

    horizontal = norm(abs(dx), 0, max(w, 1e-9))
    vertical = norm(abs(dy), 0, max(h, 1e-9))

    ys = [p[1] for p in points]
    xs = [p[0] for p in points]
    mid_y = statistics.median(ys)
    end_y = (primary[0][1] + primary[-1][1]) / 2
    if dist(primary[0], primary[-1]) <= 1e-6:
        end_y = (y0 + y1) / 2

    # In SVG coordinates, higher y is lower on the page.
    # bowl_down = median lies below endpoints: smile/jaw affordance.
    # bowl_up = median lies above endpoints: frown/hairline affordance.
    bowl_down = norm(mid_y - end_y, 0, max(h, 1e-9) * 0.45)
    bowl_up = norm(end_y - mid_y, 0, max(h, 1e-9) * 0.45)

    angles = [angle for path in paths for angle in turn_angles(path)]
    abs_angles = [abs(v) for v in angles]
    max_turn = max(abs_angles or [0])
    mean_turn = statistics.mean(abs_angles) if abs_angles else 0

    hook = norm(max_turn, 25, 105)
    angular = norm(mean_turn, 10, 80)

    # Endpoint balance and bottom heaviness help distinguish mouth/jaw from nose.
    endpoint_same_height = 1.0 - norm(
        abs(primary[0][1] - primary[-1][1]), 0, max(h, 1e-9) * 0.55
    )
    if dist(primary[0], primary[-1]) <= 1e-6:
        endpoint_same_height = 0.5
    endpoint_spread = norm(abs(dx), 0, max(w, 1e-9))
    bottom_heavy = norm(max(ys) - mid_y, 0, max(h, 1e-9) * 0.55)

    # Count direction changes. A nose can have a spine + hook.
    # A mouth tends to be horizontally flowing.
    x_range = max(w, 1e-9)
    y_range = max(h, 1e-9)
    tall_narrow = norm(inv_a, 1.25, 5.0)
    very_tall_narrow = norm(inv_a, 2.0, 8.0)
    wide_shallow = norm(a, 1.5, 7.0)
    very_wide = norm(a, 2.2, 9.0)

    # "Nose/profile" prior:
    # - vertical or near-vertical dominance
    # - a hook/kink is useful
    # - narrowness is useful
    # - asymmetry helps profile
    nose = (
        0.34 * tall_narrow
        + 0.22 * vertical
        + 0.20 * hook
        + 0.10 * angular
        + 0.08 * (1.0 - m.symmetry_lr)
        + 0.06 * (1.0 - norm(m.sinuosity, 1.8, 3.5))
    )

    profile = (
        0.30 * tall_narrow
        + 0.22 * hook
        + 0.18 * (1.0 - m.symmetry_lr)
        + 0.16 * vertical
        + 0.10 * angular
        + 0.04 * norm(m.sinuosity, 1.1, 2.4)
    )

    # "Mouth" prior:
    # - horizontal, shallow, endpoint spread, similar endpoint heights
    # - curve/sinuosity helps, but too-tall vertical paths should be penalised
    mouth = (
        0.34 * wide_shallow
        + 0.22 * horizontal
        + 0.16 * endpoint_spread
        + 0.12 * endpoint_same_height
        + 0.10 * max(bowl_down, 0.35 * bowl_up)
        + 0.06 * norm(m.sinuosity, 1.03, 2.4)
    )

    # "Jaw/chin" prior:
    # - must be broad
    # - must feel like bottom support
    # - U/V/bowl helps
    # - symmetry helps
    # - narrow vertical things get heavily penalised
    jaw = (
        0.34 * very_wide
        + 0.22 * max(bowl_down, bowl_up)
        + 0.16 * bottom_heavy
        + 0.12 * endpoint_spread
        + 0.10 * m.symmetry_lr
        + 0.06 * norm(m.sinuosity, 1.15, 2.8)
    )

    # "Eyebrow" prior:
    # - wide-ish, short-ish, angled/arched
    # - not huge/tall
    eyebrow = (
        0.34 * wide_shallow
        + 0.22 * angular
        + 0.16 * hook
        + 0.12 * endpoint_same_height
        + 0.10 * (1.0 - bottom_heavy)
        + 0.06 * (1.0 - norm(h, w * 0.35, w * 1.2))
    )

    hairline = (
        0.34 * very_wide
        + 0.22 * bowl_up
        + 0.16 * endpoint_spread
        + 0.12 * endpoint_same_height
        + 0.10 * m.symmetry_lr
        + 0.06 * (1.0 - angular)
    )

    # Hard face-prior corrections.
    # These are the crucial fixes for examples like Woodlea/Abney.
    if a < 1.15:
        jaw *= 0.28
        mouth *= 0.62
        eyebrow *= 0.55

    if inv_a > 1.35 and hook > 0.20:
        nose += 0.22
        profile += 0.14

    if inv_a > 2.2:
        jaw *= 0.12
        mouth *= 0.45

    if a > 1.65 and h < w * 0.65:
        mouth += 0.10
        eyebrow += 0.04

    if a > 1.8 and max(bowl_down, bowl_up) > 0.35:
        jaw += 0.10

    return [
        ("mouth", mouth),
        ("nose", nose),
        ("eyebrow", eyebrow),
        ("jaw_chin", jaw),
        ("profile", profile),
        ("hairline", hairline),
    ]


def u_shape_mouth_bonus(paths: Sequence[Sequence[Point]]) -> float:
    primary = longest_path(paths)
    if len(primary) < 3:
        return 0.0

    x0, y0, x1, y1 = bbox(flatten_paths(paths))
    w = max(1e-9, x1 - x0)
    h = max(1e-9, y1 - y0)
    aspect = w / h

    start = primary[0]
    end = primary[-1]
    mid = primary[len(primary) // 2]
    endpoint_width = abs(end[0] - start[0])
    endpoint_height_delta = abs(end[1] - start[1])
    endpoints_level = 1.0 - norm(endpoint_height_delta, 0, h * 0.70)
    endpoint_spread = norm(endpoint_width, w * 0.20, w * 0.95)

    # SVG y increases downward: a smile-like U has its middle below the endpoints;
    # an inverted U can still read as a frown mouth.
    endpoint_y = (start[1] + end[1]) / 2
    smile_bowl = norm(mid[1] - endpoint_y, h * 0.10, h * 0.55)
    frown_bowl = norm(endpoint_y - mid[1], h * 0.10, h * 0.55)
    bowl = max(smile_bowl, frown_bowl)

    angles = [abs(angle) for path in paths for angle in turn_angles(path)]
    bend = norm(max(angles or [0.0]), 25, 105)
    mouth_aspect = 1.0 - norm(abs(aspect - 1.75), 0.0, 2.25)

    return clamp(
        0.34 * bowl
        + 0.24 * endpoints_level
        + 0.18 * endpoint_spread
        + 0.14 * bend
        + 0.10 * mouth_aspect,
        0.0,
        1.0,
    )


def choose_role_and_rotation(
    paths: Sequence[Sequence[Point]],
) -> Tuple[RoleChoice, List[Tuple[str, float]]]:
    points = flatten_paths(paths)
    base_angle = principal_axis_angle(points)
    candidates = [
        0.0,
        90.0,
        180.0,
        270.0,
        -base_angle,
        90.0 - base_angle,
        180.0 - base_angle,
        270.0 - base_angle,
    ]

    unique = []
    for a in candidates:
        aa = ((a + 180) % 360) - 180
        if all(abs(aa - b) > 3 for b in unique):
            unique.append(aa)

    best: Optional[RoleChoice] = None
    best_report: List[Tuple[str, float]] = []

    for rot in unique:
        rp_paths = rotate_path_collection(paths, rot)
        rp = flatten_paths(rp_paths)
        scores = role_scores(rp, rp_paths)
        score_map = dict(scores)
        u_bonus = u_shape_mouth_bonus(rp_paths)
        mouth_score = (
            score_map.get("mouth", 0.0)
            + 0.24 * score_map.get("jaw_chin", 0.0)
            + 0.14 * score_map.get("hairline", 0.0)
            + 0.10 * score_map.get("eyebrow", 0.0)
            + 0.34 * u_bonus
        )
        nose_score = (
            score_map.get("nose", 0.0)
            + 0.32 * score_map.get("profile", 0.0)
            + 0.08 * score_map.get("eyebrow", 0.0)
            - 0.18 * u_bonus
        )
        role_scores_only = [("mouth", mouth_score), ("nose", nose_score)]
        role, score = max(role_scores_only, key=lambda x: x[1])
        if abs(rot) > 1:
            score -= 0.035
        score += 0.04

        choice = RoleChoice(
            role=role, rotation=rot, score=score, metrics=compute_path_metrics(rp_paths)
        )
        if best is None or choice.score > best.score:
            best = choice
            best_report = (
                role_scores_only
                + [("u_shape_mouth_bonus", u_bonus)]
                + [(f"support_{name}", value) for name, value in scores]
            )

    assert best is not None
    return best, sorted(best_report, key=lambda x: x[1], reverse=True)


def normalise_path_to_feature_box(
    points: Sequence[Point], target: Tuple[float, float, float, float]
) -> List[Point]:
    x0, y0, x1, y1 = bbox(points)
    w = max(1e-9, x1 - x0)
    h = max(1e-9, y1 - y0)
    tx, ty, tw, th = target
    s = min(tw / w, th / h)
    scaled = [(x * s, y * s) for x, y in points]
    sx0, sy0, sx1, sy1 = bbox(scaled)
    dx = tx + (tw - (sx1 - sx0)) / 2 - sx0
    dy = ty + (th - (sy1 - sy0)) / 2 - sy0
    return translate_points(scaled, dx, dy)


def rotate_path_collection(
    paths: Sequence[Sequence[Point]], angle_degrees: float
) -> List[List[Point]]:
    all_points = flatten_paths(paths)
    origin = centroid(all_points)
    return [rotate_points(path, angle_degrees, origin=origin) for path in paths]


def normalise_paths_to_feature_box(
    paths: Sequence[Sequence[Point]], target: Tuple[float, float, float, float]
) -> List[List[Point]]:
    all_points = flatten_paths(paths)
    x0, y0, x1, y1 = bbox(all_points)
    w = max(1e-9, x1 - x0)
    h = max(1e-9, y1 - y0)
    tx, ty, tw, th = target
    s = min(tw / w, th / h)
    scaled = [[(x * s, y * s) for x, y in path] for path in paths]
    scaled_points = flatten_paths(scaled)
    sx0, sy0, sx1, sy1 = bbox(scaled_points)
    dx = tx + (tw - (sx1 - sx0)) / 2 - sx0
    dy = ty + (th - (sy1 - sy0)) / 2 - sy0
    return [[(x + dx, y + dy) for x, y in path] for path in scaled]


def validate_affine_matrix(
    matrix: Tuple[float, float, float, float, float, float]
) -> float:
    a, b, c, d, _e, _f = matrix
    determinant = a * d - b * c
    if determinant <= 0:
        raise ValueError(
            "Artistic Violation: Reflection detected in transformation matrix."
        )
    return determinant


def parse_affine_transform(
    transform: str,
) -> Tuple[float, float, float, float, float, float]:
    values = tuple(float(value) for value in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", transform))
    if len(values) != 6:
        raise ValueError(f"Invalid SVG affine transform: {transform}")
    matrix = values
    validate_affine_matrix(matrix)
    return matrix


def fit_paths_to_feature_box(
    paths: Sequence[Sequence[Point]],
    rotation: float,
    target: Tuple[float, float, float, float],
) -> Tuple[List[List[Point]], str]:
    all_points = flatten_paths(paths)
    origin = centroid(all_points)
    rotated = [rotate_points(path, rotation, origin=origin) for path in paths]
    rx0, ry0, rx1, ry1 = bbox(flatten_paths(rotated))
    rw = max(1e-9, rx1 - rx0)
    rh = max(1e-9, ry1 - ry0)
    tx, ty, tw, th = target
    s = min(tw / rw, th / rh)
    dx = tx + (tw - rw * s) / 2 - rx0 * s
    dy = ty + (th - rh * s) / 2 - ry0 * s
    fitted = [[(x * s + dx, y * s + dy) for x, y in path] for path in rotated]

    angle = math.radians(rotation)
    ca, sa = math.cos(angle), math.sin(angle)
    ox, oy = origin
    a = s * ca
    b = s * sa
    c = -s * sa
    d = s * ca
    e = s * (ox - ca * ox + sa * oy) + dx
    f = s * (oy - sa * ox - ca * oy) + dy
    matrix = (a, b, c, d, e, f)
    validate_affine_matrix(matrix)
    transform = f"matrix({a:.8f} {b:.8f} {c:.8f} {d:.8f} {e:.8f} {f:.8f})"
    return fitted, transform


def base_feature_target_for_role(
    role: str, card_x: float, card_y: float, card_w: float, card_h: float
) -> Tuple[float, float, float, float]:
    if role == "mouth":
        return (
            card_x + card_w * 0.30,
            card_y + card_h * 0.455,
            card_w * 0.42,
            card_h * 0.16,
        )
    if role == "jaw_chin":
        return (
            card_x + card_w * 0.25,
            card_y + card_h * 0.49,
            card_w * 0.50,
            card_h * 0.23,
        )
    if role == "nose":
        return (
            card_x + card_w * 0.45,
            card_y + card_h * 0.30,
            card_w * 0.16,
            card_h * 0.36,
        )
    if role == "profile":
        return (
            card_x + card_w * 0.43,
            card_y + card_h * 0.25,
            card_w * 0.22,
            card_h * 0.48,
        )
    if role == "eyebrow":
        return (
            card_x + card_w * 0.36,
            card_y + card_h * 0.25,
            card_w * 0.30,
            card_h * 0.11,
        )
    if role == "hairline":
        return (
            card_x + card_w * 0.28,
            card_y + card_h * 0.20,
            card_w * 0.45,
            card_h * 0.13,
        )
    return (
        card_x + card_w * 0.34,
        card_y + card_h * 0.36,
        card_w * 0.34,
        card_h * 0.24,
    )

V14_ROLE_GEOMETRY_SCALE = {
    "mouth": 1.00,
    "jaw_chin": 1.10,
    "nose": 1.40,
    "profile": 1.25,
    "eyebrow": 1.35,
    "hairline": 1.15,
}


def feature_target_for_role(
    role: str, card_x: float, card_y: float, card_w: float, card_h: float
) -> Tuple[float, float, float, float]:
    """Blend v12's geographic presence with v13's expanded role variation."""
    tx, ty, tw, th = base_feature_target_for_role(
        role, card_x, card_y, card_w, card_h
    )
    requested_scale = V14_ROLE_GEOMETRY_SCALE.get(role, 1.0)
    safe_left = card_x + card_w * 0.08
    safe_right = card_x + card_w * 0.92
    safe_top = card_y + card_h * 0.13
    safe_bottom = card_y + card_h * 0.88
    scale = min(
        requested_scale,
        (safe_right - safe_left) / max(tw, 1e-9),
        (safe_bottom - safe_top) / max(th, 1e-9),
    )
    cx = tx + tw / 2
    cy = ty + th / 2
    expanded_w = tw * scale
    expanded_h = th * scale
    expanded_x = clamp(cx - expanded_w / 2, safe_left, safe_right - expanded_w)
    expanded_y = clamp(cy - expanded_h / 2, safe_top, safe_bottom - expanded_h)
    return expanded_x, expanded_y, expanded_w, expanded_h

def make_face_spec(
    name: str,
    paths: List[List[Point]],
    path_ds: List[str],
    uses_fill: bool,
    color: str,
    card_x: float,
    card_y: float,
    card_w: float,
    card_h: float,
) -> FaceSpec:
    role, report = choose_role_and_rotation(paths)
    rotated_paths = rotate_path_collection(paths, role.rotation)
    target = feature_target_for_role(role.role, card_x, card_y, card_w, card_h)
    card_paths, transform = fit_paths_to_feature_box(paths, role.rotation, target)
    return FaceSpec(
        name,
        color,
        role,
        paths,
        rotated_paths,
        card_paths,
        path_ds,
        uses_fill,
        transform,
        report,
        transform_matrix=parse_affine_transform(transform),
        scaling_factor_applied=math.sqrt(validate_affine_matrix(parse_affine_transform(transform))),
    )


def percentile_rank(value: float, population: Sequence[float]) -> float:
    if len(population) <= 1:
        return 0.5
    below = sum(candidate < value for candidate in population)
    equal = sum(candidate == value for candidate in population)
    return (below + 0.5 * equal) / len(population)


def apply_batch_relative_roles(specs: Sequence[FaceSpec]) -> None:
    """Assign roles using batch-relative geometry instead of regional constants."""
    aspects = [spec.role_choice.metrics.aspect for spec in specs]
    inverse_aspects = [1.0 / max(value, 1e-9) for value in aspects]
    sinuosities = [spec.role_choice.metrics.sinuosity for spec in specs]
    lengths = [spec.role_choice.metrics.length for spec in specs]
    node_counts = [float(spec.role_choice.metrics.node_count) for spec in specs]
    deflections = [spec.role_choice.metrics.turn_angle_sum for spec in specs]

    for spec in specs:
        metrics = spec.role_choice.metrics
        aspect_p = percentile_rank(metrics.aspect, aspects)
        inverse_p = percentile_rank(1.0 / max(metrics.aspect, 1e-9), inverse_aspects)
        sinuosity_p = percentile_rank(metrics.sinuosity, sinuosities)
        node_p = percentile_rank(float(metrics.node_count), node_counts)
        deflection_p = percentile_rank(metrics.turn_angle_sum, deflections)
        spec.length_percentile = percentile_rank(metrics.length, lengths)

        if inverse_p >= 0.75:
            role = "nose"
        elif aspect_p >= 0.75 and sinuosity_p >= 0.50:
            role = "mouth"
        elif node_p <= 0.25 and deflection_p >= 0.60:
            role = "eyebrow"
        else:
            role = spec.role_choice.role
        spec.role_choice.role = role
        spec.report.extend(
            [
                ("batch_aspect_percentile", aspect_p),
                ("batch_inverse_aspect_percentile", inverse_p),
                ("batch_sinuosity_percentile", sinuosity_p),
                ("complexity_score", metrics.complexity),
            ]
        )


def role_confidence(spec: FaceSpec) -> float:
    scores = sorted(spec.report, key=lambda x: x[1], reverse=True)
    top = scores[0][1] if scores else 0.0
    second = scores[1][1] if len(scores) > 1 else 0.0
    return top - second


def sinuosity_score(spec: FaceSpec) -> float:
    return norm(spec.role_choice.metrics.sinuosity, 1.05, 2.6)


def gallery_score(spec: FaceSpec) -> float:
    confidence = role_confidence(spec)
    m = spec.role_choice.metrics

    sinuosity_score_value = sinuosity_score(spec)
    angularity_score = norm(m.angularity, 8, 65)
    confidence_score = norm(confidence, 0.05, 0.28)
    length_score = norm(m.length, 20, 300)
    asymmetry_score = norm(1.0 - m.symmetry_lr, 0.08, 0.55)

    too_straight_penalty = 0.35 if m.sinuosity < 1.04 and m.angularity < 6 else 0.0
    too_ambiguous_penalty = 0.30 if confidence < 0.06 else 0.0
    too_generic_penalty = 0.12 if m.angularity < 10 and m.symmetry_lr > 0.88 else 0.0

    return clamp(
        0.32 * confidence_score
        + 0.24 * sinuosity_score_value
        + 0.17 * angularity_score
        + 0.10 * length_score
        + 0.07 * asymmetry_score
        + 0.10 * spec.role_choice.score
        - too_straight_penalty
        - too_ambiguous_penalty
        - too_generic_penalty,
        0,
        1,
    )


def _primary_curve_reading(paths: Sequence[Sequence[Point]]) -> Tuple[float, float, float, float]:
    primary = longest_path(paths)
    if len(primary) < 3:
        return 0.0, 0.0, 0.0, 0.0

    x0, y0, x1, y1 = bbox(flatten_paths(paths))
    w = max(1e-9, x1 - x0)
    h = max(1e-9, y1 - y0)
    start = primary[0]
    end = primary[-1]
    mid = primary[len(primary) // 2]
    endpoint_y = (start[1] + end[1]) / 2

    endpoints_level = 1.0 - norm(abs(start[1] - end[1]), 0, h * 0.58)
    endpoint_spread = norm(abs(end[0] - start[0]), w * 0.18, w * 0.96)
    wide_shallow = 1.0 - norm(abs((w / h) - 2.05), 0.0, 2.65)
    smile_bowl = norm(mid[1] - endpoint_y, h * 0.08, h * 0.52)
    frown_bowl = norm(endpoint_y - mid[1], h * 0.08, h * 0.52)
    angles = [abs(angle) for path in paths for angle in turn_angles(path)]
    crescent = norm(max(angles or [0.0]), 18, 95)

    smile = clamp(
        0.36 * smile_bowl
        + 0.22 * endpoints_level
        + 0.18 * endpoint_spread
        + 0.14 * crescent
        + 0.10 * wide_shallow,
        0.0,
        1.0,
    )
    frown = clamp(
        0.40 * frown_bowl
        + 0.22 * endpoints_level
        + 0.18 * endpoint_spread
        + 0.12 * crescent
        + 0.08 * wide_shallow,
        0.0,
        1.0,
    )
    mouth_read = clamp(
        0.32 * endpoints_level
        + 0.28 * endpoint_spread
        + 0.20 * crescent
        + 0.20 * wide_shallow,
        0.0,
        1.0,
    )
    return smile, frown, mouth_read, crescent


def smile_curve_score(spec: FaceSpec) -> float:
    return _primary_curve_reading(spec.transformed_paths)[0]


def frown_curve_score(spec: FaceSpec) -> float:
    return _primary_curve_reading(spec.transformed_paths)[1]


def _primary_curve_depth(paths: Sequence[Sequence[Point]]) -> Tuple[float, float]:
    primary = list(longest_path(paths))
    if len(primary) < 3:
        return 0.0, 0.0

    start = primary[0]
    end = primary[-1]
    if start[0] > end[0]:
        start, end = end, start

    vx = end[0] - start[0]
    vy = end[1] - start[1]
    chord = max(1e-9, math.hypot(vx, vy))
    smile_depth = 0.0
    frown_depth = 0.0
    for px, py in primary[1:-1]:
        signed_depth = (vx * (py - start[1]) - vy * (px - start[0])) / chord
        smile_depth = max(smile_depth, signed_depth)
        frown_depth = max(frown_depth, -signed_depth)

    return smile_depth / chord, frown_depth / chord


def smile_depth_score(spec: FaceSpec) -> float:
    smile_depth, _frown_depth = _primary_curve_depth(spec.transformed_paths)
    return norm(smile_depth, 0.025, 0.130)


def frown_depth_score(spec: FaceSpec) -> float:
    _smile_depth, frown_depth = _primary_curve_depth(spec.transformed_paths)
    return norm(frown_depth, 0.025, 0.130)


def calm_curve_score(spec: FaceSpec) -> float:
    m = spec.role_choice.metrics
    smile = smile_curve_score(spec)
    depth = smile_depth_score(spec)
    calm_arc = 1.0 - norm(m.angularity, 7.0, 19.0)
    simple_arc = 1.0 - norm(m.turn_angle_sum, 180.0, 520.0)
    return clamp(
        smile * depth * (0.68 * calm_arc + 0.32 * simple_arc),
        0.0,
        1.0,
    )


def unhappiness_score(spec: FaceSpec) -> float:
    smile = smile_curve_score(spec)
    frown = frown_curve_score(spec)
    smile_depth = smile_depth_score(spec)
    frown_depth = frown_depth_score(spec)
    role_factor = 1.0 if spec.role_choice.role == "mouth" else 0.45
    straight_mouth = (1.0 - smile_depth) * (1.0 - frown_depth)
    inverted_mouth = frown_depth * max(frown, 0.35)
    return clamp(
        role_factor * (0.72 * inverted_mouth + 0.28 * straight_mouth),
        0.0,
        1.0,
    )


def neutral_score(spec: FaceSpec) -> float:
    smile_depth = smile_depth_score(spec)
    frown_depth = frown_depth_score(spec)
    role_factor = 1.0 if spec.role_choice.role == "mouth" else 0.35
    straightness = 1.0 - max(smile_depth, frown_depth)
    calmness = 1.0 - norm(spec.role_choice.metrics.angularity, 3.0, 24.0)
    return clamp(role_factor * (0.72 * straightness + 0.28 * calmness), 0.0, 1.0)


def sadness_score(spec: FaceSpec) -> float:
    frown = frown_curve_score(spec)
    frown_depth = frown_depth_score(spec)
    role_factor = 1.0 if spec.role_choice.role == "mouth" else 0.35
    return clamp(role_factor * (0.70 * frown_depth + 0.30 * frown), 0.0, 1.0)


def happiness_score(spec: FaceSpec) -> float:
    m = spec.role_choice.metrics
    smile, frown, mouth_read, crescent = _primary_curve_reading(spec.transformed_paths)
    smile_depth = smile_depth_score(spec)
    frown_depth = frown_depth_score(spec)
    calm_smile = calm_curve_score(spec)
    visible_smile = smile * smile_depth
    expressive_smile = visible_smile * norm(m.turn_angle_sum, 24.0, 76.0)
    role = spec.role_choice.role
    role_mouthness = 1.0 if role == "mouth" else 0.58 if role == "jaw_chin" else 0.10
    role_penalty = 0.0 if role == "mouth" else 0.08 if role == "jaw_chin" else 0.22
    confidence_score = norm(role_confidence(spec), 0.04, 0.24)
    softness = 1.0 - norm(m.angularity, 8, 28)
    openness = norm(m.aspect, 1.15, 3.85)
    symmetry = norm(m.symmetry_lr, 0.52, 0.94)
    straight_penalty = (1.0 - smile_depth) * 0.22

    return clamp(
        0.26 * smile_depth
        + 0.22 * visible_smile
        + 0.13 * calm_smile
        + 0.10 * expressive_smile
        + 0.07 * mouth_read
        + 0.12 * role_mouthness
        + 0.04 * softness
        + 0.03 * openness
        + 0.03 * symmetry
        + 0.02 * confidence_score
        + 0.01 * crescent
        - 0.15 * frown
        - 0.15 * frown_depth
        - straight_penalty
        - role_penalty,
        0.0,
        1.0,
    )


def normalize_gallery_sort_mode(sort_by: str | None) -> str:
    if not sort_by:
        return "happiness"
    normalized = str(sort_by).strip().lower().replace("_", "-")
    if normalized not in GALLERY_SORT_MODES:
        raise ValueError(f"Sort mode must be one of: {', '.join(GALLERY_SORT_MODES)}.")
    return normalized


def happiness_role_priority(spec: FaceSpec) -> float:
    role = spec.role_choice.role
    if role == "mouth":
        return 2.0
    if role == "jaw_chin":
        return 1.0
    return 0.0


def gallery_sort_key(spec: FaceSpec, sort_by: str | None = None) -> Tuple[float, float, float, float, float, str]:
    mode = normalize_gallery_sort_mode(sort_by)
    if mode == "sinuosity":
        return (
            sinuosity_score(spec),
            spec.role_choice.metrics.sinuosity,
            gallery_score(spec),
            role_confidence(spec),
            happiness_role_priority(spec),
            spec.name.lower(),
        )
    if mode == "gallery":
        return (
            gallery_score(spec),
            role_confidence(spec),
            sinuosity_score(spec),
            happiness_score(spec),
            happiness_role_priority(spec),
            spec.name.lower(),
        )
    return (
        happiness_role_priority(spec),
        happiness_score(spec),
        smile_depth_score(spec),
        calm_curve_score(spec),
        role_confidence(spec),
        spec.name.lower(),
    )


def normalize_street_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def find_spec_by_name(
    specs: Sequence[FaceSpec], street_name: Optional[str]
) -> Optional[FaceSpec]:
    if not street_name or not street_name.strip():
        return None

    wanted = normalize_street_name(street_name)
    if not wanted:
        return None

    for spec in specs:
        if normalize_street_name(spec.name) == wanted:
            return spec

    for spec in specs:
        normalized = normalize_street_name(spec.name)
        if wanted in normalized or normalized in wanted:
            return spec
    return None


def _append_unique_spec(selected: List[FaceSpec], candidate: FaceSpec) -> None:
    candidate_key = normalize_street_name(candidate.name)
    if not any(normalize_street_name(spec.name) == candidate_key for spec in selected):
        selected.append(candidate)


def sinuosity_showcase_count(target_count: int) -> int:
    if target_count >= 24:
        return 2
    if target_count >= 8:
        return 1
    return 0


def sample_ranked_distribution(
    ranked: Sequence[FaceSpec], slot_count: int
) -> List[FaceSpec]:
    if slot_count <= 0 or not ranked:
        return []
    if slot_count >= len(ranked):
        return list(ranked)

    selected: List[FaceSpec] = []
    unhappy_count = min(max(1, math.ceil(slot_count * 0.18)), max(0, slot_count - 2))
    head_count = min(slot_count - unhappy_count, max(1, math.ceil(slot_count * 0.55)))
    for candidate in ranked[:head_count]:
        _append_unique_spec(selected, candidate)

    middle_slots = slot_count - unhappy_count - len(selected)
    middle = ranked[head_count:]
    if middle_slots > 0 and middle:
        last_index = len(middle) - 1
        for slot in range(middle_slots):
            t = 0.0 if middle_slots == 1 else slot / (middle_slots - 1)
            index = round(last_index * (t ** 1.25))
            _append_unique_spec(selected, middle[index])

    unhappy_ranked = sorted(
        ranked,
        key=lambda spec: (
            unhappiness_score(spec),
            frown_depth_score(spec),
            1.0 - smile_depth_score(spec),
            -happiness_score(spec),
        ),
        reverse=True,
    )
    for candidate in unhappy_ranked:
        if len(selected) >= slot_count:
            break
        _append_unique_spec(selected, candidate)

    for candidate in ranked:
        if len(selected) >= slot_count:
            break
        _append_unique_spec(selected, candidate)
    return sorted(
        selected,
        key=lambda spec: gallery_sort_key(spec, "happiness"),
        reverse=True,
    )


def _interleave_showcase_specs(
    gradient_specs: Sequence[FaceSpec], showcase_specs: Sequence[FaceSpec], target_count: int
) -> List[FaceSpec]:
    arranged = list(gradient_specs)
    if not showcase_specs:
        return arranged[:target_count]

    for offset, showcase in enumerate(showcase_specs, start=1):
        position = round(offset * (len(arranged) + 1) / (len(showcase_specs) + 1))
        position = max(0, min(len(arranged), position))
        arranged.insert(position, showcase)
    return arranged[:target_count]


def _take_ranked_specs(
    pool: Sequence[FaceSpec], count: int, selected: List[FaceSpec]
) -> None:
    for candidate in pool:
        if count <= 0:
            break
        before = len(selected)
        _append_unique_spec(selected, candidate)
        if len(selected) > before:
            count -= 1


def select_sinuosity_showcases(
    specs: Sequence[FaceSpec], count: int, already_selected: Sequence[FaceSpec]
) -> List[FaceSpec]:
    if count <= 0:
        return []
    already_keys = {normalize_street_name(spec.name) for spec in already_selected}
    sinuous_ranked = [
        spec
        for spec in sorted(
            specs,
            key=lambda spec: (
                spec.role_choice.metrics.sinuosity,
                gallery_score(spec),
                stable_unit(f"{spec.name}:sinuosity-showcase"),
            ),
            reverse=True,
        )
        if normalize_street_name(spec.name) not in already_keys
    ]
    if not sinuous_ranked:
        return []

    candidate_pool = sinuous_ranked[: max(count * 5, 8)]
    selected: List[FaceSpec] = []
    if count == 1:
        _append_unique_spec(selected, candidate_pool[0])
        return selected

    # Use a small spread through the high-sinuosity pool: one extreme outlier,
    # then a still-high but more legible shape rather than always the top two.
    for slot in range(count):
        t = 0.0 if count == 1 else slot / (count - 1)
        index = round((len(candidate_pool) - 1) * (t * 0.24))
        index = max(0, min(len(candidate_pool) - 1, index))
        _append_unique_spec(selected, candidate_pool[index])

    _take_ranked_specs(sinuous_ranked, count - len(selected), selected)
    return selected[:count]


def curate_happiness_gradient_specs(
    specs: Sequence[FaceSpec], target_count: Optional[int]
) -> List[FaceSpec]:
    ranked = sorted(
        specs,
        key=lambda spec: gallery_sort_key(spec, "happiness"),
        reverse=True,
    )
    if target_count is None:
        return ranked
    if target_count <= 0:
        return []

    sinuous_count = min(sinuosity_showcase_count(target_count), target_count)
    emotional_count = max(0, target_count - sinuous_count)
    happy_count = min(emotional_count, max(1, math.ceil(emotional_count * 0.44)))
    neutral_count = min(
        max(0, emotional_count - happy_count),
        max(1, math.ceil(emotional_count * 0.26)) if emotional_count - happy_count > 0 else 0,
    )
    sad_count = max(0, emotional_count - happy_count - neutral_count)

    mouths = [spec for spec in specs if happiness_role_priority(spec) >= 2.0]
    non_mouth_ranked = [spec for spec in ranked if happiness_role_priority(spec) < 2.0]
    happy_pool = sorted(
        mouths,
        key=lambda spec: (
            happiness_score(spec),
            smile_depth_score(spec),
            calm_curve_score(spec),
            -sadness_score(spec),
        ),
        reverse=True,
    )
    neutral_pool = sorted(
        mouths,
        key=lambda spec: (
            neutral_score(spec),
            1.0 - abs(happiness_score(spec) - unhappiness_score(spec)),
            -sadness_score(spec),
        ),
        reverse=True,
    )
    sad_pool = sorted(
        mouths,
        key=lambda spec: (
            sadness_score(spec),
            unhappiness_score(spec),
            frown_depth_score(spec),
            -happiness_score(spec),
        ),
        reverse=True,
    )

    happy_specs: List[FaceSpec] = []
    _take_ranked_specs(happy_pool, happy_count, happy_specs)

    neutral_specs: List[FaceSpec] = []
    _take_ranked_specs(
        [spec for spec in neutral_pool if normalize_street_name(spec.name) not in {normalize_street_name(item.name) for item in happy_specs}],
        neutral_count,
        neutral_specs,
    )

    sad_specs: List[FaceSpec] = []
    used_before_sad = {
        normalize_street_name(spec.name) for spec in [*happy_specs, *neutral_specs]
    }
    _take_ranked_specs(
        [spec for spec in sad_pool if normalize_street_name(spec.name) not in used_before_sad],
        sad_count,
        sad_specs,
    )

    selected_so_far = [*happy_specs, *neutral_specs, *sad_specs]
    showcase_specs = select_sinuosity_showcases(specs, sinuous_count, selected_so_far)

    first_showcase = showcase_specs[:1]
    second_showcase = showcase_specs[1:]
    arranged = [
        *happy_specs,
        *first_showcase,
        *neutral_specs,
        *second_showcase,
        *sad_specs,
    ]

    for candidate in [*happy_pool, *neutral_pool, *sad_pool, *non_mouth_ranked, *ranked]:
        if len(arranged) >= target_count:
            break
        _append_unique_spec(arranged, candidate)
    return arranged[:target_count]


def curate_specs(
    specs: Sequence[FaceSpec], target_count: Optional[int], sort_by: str | None = "happiness"
) -> List[FaceSpec]:
    mode = normalize_gallery_sort_mode(sort_by)
    if mode == "happiness":
        return curate_happiness_gradient_specs(specs, target_count)

    ranked = sorted(
        specs,
        key=lambda spec: gallery_sort_key(spec, mode),
        reverse=True,
    )
    if target_count is None:
        return ranked
    return ranked[:target_count]


def curate_specs_with_forced_street(
    specs: Sequence[FaceSpec],
    target_count: Optional[int],
    forced_street: Optional[str] = None,
    forced_search_specs: Optional[Sequence[FaceSpec]] = None,
    sort_by: str | None = "happiness",
) -> Tuple[List[FaceSpec], Optional[str], Optional[str]]:
    curated = curate_specs(specs, target_count, sort_by=sort_by)
    if not forced_street or not forced_street.strip():
        return curated, None, None

    search_specs = forced_search_specs if forced_search_specs is not None else specs
    forced_spec = find_spec_by_name(search_specs, forced_street)
    if forced_spec is None:
        raise ValueError(
            f"Street not found in the available collection: {forced_street}"
        )

    forced_key = normalize_street_name(forced_spec.name)
    if any(normalize_street_name(spec.name) == forced_key for spec in curated):
        return (
            curated,
            f"{forced_spec.name} is already in the curated collection.",
            forced_spec.name,
        )

    if not curated:
        return [forced_spec], f"Added {forced_spec.name}.", forced_spec.name

    replaced = curated[-1]
    curated = [*curated[:-1], forced_spec]
    return (
        curated,
        f"Added {forced_spec.name}; replaced {replaced.name}.",
        forced_spec.name,
    )


def print_forced_street_note(note: Optional[str]) -> None:
    if note:
        print(f"Forced street: {note}")


def expression_for_spec(spec: FaceSpec) -> str:
    m = spec.role_choice.metrics
    if happiness_score(spec) >= 0.68:
        return "happy"
    if m.angularity > 45:
        return "suspicious"
    if m.sinuosity > 2.0:
        return "dreamy"
    if spec.role_choice.role == "profile":
        return "side_eye"
    if spec.role_choice.role == "jaw_chin":
        return "deadpan"
    if m.symmetry_lr < 0.55:
        return "wonky"
    return "neutral"


def stable_unit(text: str) -> float:
    return (sum((idx + 1) * ord(ch) for idx, ch in enumerate(text)) % 997) / 997.0


def normalize_ear_mode(ear_mode: str | None) -> str:
    if not ear_mode:
        return "none"
    normalized = str(ear_mode).strip().lower().replace("_", "-")
    if normalized not in EAR_MODES:
        raise ValueError(f"Ear mode must be one of: {', '.join(EAR_MODES)}.")
    return normalized


def ear_stroke_count_for_spec(spec: "FaceSpec", ear_mode: str) -> int:
    mode = normalize_ear_mode(ear_mode)
    if mode == "none":
        return 0
    if mode == "one-stroke":
        return 1
    if mode == "two-stroke":
        return 2
    return 1 if stable_unit(f"{spec.name}:ears") < 0.5 else 2


def normalize_presentation_mode(presentation_mode: str | None) -> str:
    if not presentation_mode:
        return "varied"
    normalized = str(presentation_mode).strip().lower().replace("_", "-")
    if normalized not in PRESENTATION_MODES:
        raise ValueError(
            f"Presentation mode must be one of: {', '.join(PRESENTATION_MODES)}."
        )
    return normalized


def presentation_for_spec(spec: "FaceSpec", presentation_mode: str) -> str:
    mode = normalize_presentation_mode(presentation_mode)
    if mode != "varied":
        return mode
    value = stable_unit(f"{spec.name}:presentation")
    if value < 0.38:
        return "feminine"
    if value < 0.76:
        return "neutral"
    return "masculine"


def path_bbox_centres(
    points: Sequence[Point],
) -> Tuple[float, float, float, float, float, float]:
    x0, y0, x1, y1 = bbox(points)
    return x0, y0, x1, y1, (x0 + x1) / 2, (y0 + y1) / 2


def draw_arc(
    cx: float, cy: float, rx: float, ry: float, start_deg: float, end_deg: float
) -> str:
    a0 = math.radians(start_deg)
    a1 = math.radians(end_deg)
    x0, y0 = cx + rx * math.cos(a0), cy + ry * math.sin(a0)
    x1, y1 = cx + rx * math.cos(a1), cy + ry * math.sin(a1)
    large = 1 if abs(end_deg - start_deg) > 180 else 0
    sweep = 1 if end_deg > start_deg else 0
    return (
        f"M {x0:.2f},{y0:.2f} A {rx:.2f},{ry:.2f} 0 {large} {sweep} {x1:.2f},{y1:.2f}"
    )


def escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )



def signature_secret_id() -> str:
    """Return the private key used to add a short provenance checksum."""
    return os.environ.get(SIGNATURE_ENV_VAR, DEFAULT_SIGNATURE_ID).strip() or DEFAULT_SIGNATURE_ID


def provenance_payload(area_name: str, generated_on: Optional[str] = None) -> str:
    """Build the small text payload encoded in the v20 microdot mark."""
    area = re.sub(r"\s+", " ", str(area_name or "unknown area").strip())
    generated = generated_on or date.today().isoformat()
    base = f"SFV20|AREA={area}|DATE={generated}"
    checksum = hashlib.sha256(
        f"{signature_secret_id()}|{base}".encode("utf-8", errors="replace")
    ).hexdigest()[:10].upper()
    return f"{base}|CHK={checksum}"


def payload_to_bits(payload: str) -> List[int]:
    """Encode text as 8-bit bytes, least-significant bit first per byte."""
    bits: List[int] = []
    for byte in payload.encode("utf-8", errors="replace"):
        bits.extend((byte >> shift) & 1 for shift in range(8))
    return bits


def render_attribution_provenance_mark(
    area_name: str,
    x: float,
    y: float,
    text_height: float,
    max_right: float,
    palette: FacePalette,
) -> str:
    """Draw a printable finder circle plus rectangular microdot data strip.

    The circle is roughly the same diameter as the attribution text height, so
    it reads as a tiny seal. The rectangle carries the actual area/date payload
    as dots that can be photographed and decoded later.
    """
    payload = provenance_payload(area_name)
    bits = payload_to_bits(payload)
    rows = 8
    cols = max(48, math.ceil(len(bits) / rows))
    dot_r = clamp(text_height * 0.070, 0.42, 0.85)
    spacing = clamp(text_height * 0.32, dot_r * 3.6, dot_r * 5.2)
    circle_r = text_height * 0.52
    strip_w = (cols - 1) * spacing + dot_r * 2
    strip_h = (rows - 1) * spacing + dot_r * 2
    gap = text_height * 0.38
    total_w = circle_r * 2 + gap + strip_w

    left = min(x, max_right - total_w)
    circle_cx = left + circle_r
    circle_cy = y - text_height * 0.34
    strip_x = circle_cx + circle_r + gap
    strip_y = circle_cy - strip_h / 2

    finder = f"""
    <circle cx=\"{circle_cx:.2f}\" cy=\"{circle_cy:.2f}\" r=\"{circle_r:.2f}\" class=\"provenance-finder\"/>
    <circle cx=\"{circle_cx:.2f}\" cy=\"{circle_cy:.2f}\" r=\"{circle_r * 0.45:.2f}\" class=\"provenance-finder-inner\"/>
"""
    dots = []
    for idx in range(cols * rows):
        bit = bits[idx] if idx < len(bits) else 0
        col = idx // rows
        row = idx % rows
        cls = "provenance-bit-one" if bit else "provenance-bit-zero"
        radius = dot_r if bit else dot_r * 0.48
        dots.append(
            f'<circle cx=\"{strip_x + col * spacing:.2f}\" '
            f'cy=\"{strip_y + row * spacing:.2f}\" r=\"{radius:.2f}\" class=\"{cls}\"/>'
        )

    return (
        f'<g class=\"provenance-signature\" data-provenance=\"street-face-v20\" '
        f'data-payload=\"{escape_xml(payload)}\" data-encoding=\"utf8-lsb-rows-8\" '
        f'data-area=\"{escape_xml(area_name)}\" data-rows=\"{rows}\" data-cols=\"{cols}\">'
        + finder
        + f'<g class=\"provenance-dots\">{"".join(dots)}</g></g>'
    )

def render_face_svg(
    spec: FaceSpec,
    card_x: float,
    card_y: float,
    card_w: float,
    card_h: float,
    show_blush: bool = True,
    ear_mode: str = "two-stroke",
    presentation_mode: str = "varied",
    street_stroke_multiplier: float = 1.18,
    palette: Optional[FacePalette] = None,
    frame_x: Optional[float] = None,
    frame_y: Optional[float] = None,
    frame_w: Optional[float] = None,
    frame_h: Optional[float] = None,
    single_line_label: bool = False,
    center_face: bool = False,
    auto_center_content: bool = False,
    label_font_size: Optional[float] = None,
) -> str:
    palette = palette or get_face_palette()
    pts = spec.card_points
    m = spec.role_choice.metrics
    x0, y0, x1, y1, pcx, pcy = path_bbox_centres(pts)

    sinu = clamp((m.sinuosity - 1.0) / 2.5, 0.0, 1.0)
    angular = clamp(m.angularity / 75.0, 0.0, 1.0)
    wide = clamp((x1 - x0) / card_w, 0.0, 1.0)
    tall = clamp((y1 - y0) / card_h, 0.0, 1.0)
    face_unit = min(card_h, card_w * 1.55)
    y_unit = min(card_h, card_w * 1.85)
    expression = expression_for_spec(spec)
    presentation = presentation_for_spec(spec, presentation_mode)
    eye_variant = stable_unit(f"{spec.name}:eyes")
    brow_variant = stable_unit(f"{spec.name}:brows")
    nose_variant = stable_unit(f"{spec.name}:nose")
    crown_variant = stable_unit(f"{spec.name}:crown")
    balance_variant = stable_unit(f"{spec.name}:balance")
    geom_wide = norm(m.aspect, 0.75, 3.20)
    geom_tall = norm(1.0 / max(m.aspect, 1e-9), 0.75, 3.20)
    geom_curvy = clamp((m.sinuosity - 1.0) / 1.80, 0.0, 1.0)
    geom_angular = clamp(m.angularity / 55.0, 0.0, 1.0)
    geom_asym = clamp(1.0 - m.symmetry_lr, 0.0, 1.0)
    feature_mass = clamp((x1 - x0 + y1 - y0) / max(card_w + card_h, 1e-9), 0.0, 1.0)

    face_cx = pcx
    if center_face:
        face_cx = card_x + card_w / 2
    elif spec.role_choice.role == "profile":
        face_cx = pcx + 0.03 * card_w
    else:
        face_cx += (balance_variant - 0.5) * card_w * 0.025

    face_w = max(card_w * 0.44, (x1 - x0) * 1.75, (y1 - y0) * 0.72)
    face_w *= 1.0 + 0.14 * sinu + 0.08 * wide

    eye_y = card_y + card_h * 0.36
    if spec.role_choice.role in {"mouth", "jaw_chin"}:
        # A compact U-shaped street mouth is appreciably taller than a normal
        # shallow mouth.  Keep the eyes close enough to it that it reads as a
        # mouth rather than a detached shape at the foot of the card.
        report = dict(spec.report)
        compact_u_mouth = (
            spec.role_choice.role == "mouth"
            and m.aspect <= 1.20
            and float(report.get("u_shape_mouth_bonus", 0.0)) >= 0.75
        )
        eye_gap = 0.08 + 0.02 * sinu if compact_u_mouth else 0.26 + 0.06 * sinu
        eye_y = min(y0 - y_unit * eye_gap, card_y + card_h * 0.43)
    elif spec.role_choice.role in {"nose", "profile"}:
        eye_y = y0 + (y1 - y0) * 0.20
    elif spec.role_choice.role in {"eyebrow", "hairline"}:
        eye_y = y1 + card_h * 0.10
    eye_y += (stable_unit(f"{spec.name}:eye-y") - 0.5) * y_unit * 0.040

    eye_spacing = max(card_w * 0.20, face_w * (0.30 + 0.13 * sinu) + (x1 - x0) * 0.15)
    eye_spacing = min(eye_spacing, card_w * 0.48)
    eye_spacing *= 0.90 + geom_wide * 0.14 - geom_tall * 0.06 + eye_variant * 0.08
    asym_y = (sinu - 0.2) * y_unit * 0.035
    asym_x = (1 - m.symmetry_lr) * card_w * 0.025
    if expression == "wonky":
        asym_y += y_unit * 0.018
        asym_x += card_w * 0.012
    elif expression == "side_eye":
        asym_x += card_w * 0.018

    min_face_y = card_y + card_h * 0.15
    max_face_y = card_y + card_h * 0.86

    left_eye = (face_cx - eye_spacing / 2 - asym_x, eye_y - asym_y)
    right_eye = (face_cx + eye_spacing / 2 + asym_x * 0.5, eye_y + asym_y * 0.65)

    eye_margin = card_w * 0.12
    left_eye = (
        clamp(left_eye[0], card_x + eye_margin, card_x + card_w * 0.46),
        left_eye[1],
    )
    right_eye = (
        clamp(right_eye[0], card_x + card_w * 0.54, card_x + card_w - eye_margin),
        right_eye[1],
    )

    # Repel procedural eye anchors from the sacred street's 15%-width buffer.
    buffer_size = max((x1 - x0) * 0.15, card_w * 0.012)
    buffer_box = (x0 - buffer_size, y0 - buffer_size, x1 + buffer_size, y1 + buffer_size)
    direction = -1.0 if eye_y <= pcy else 1.0
    repulsion_step = max(card_h * 0.018, buffer_size * 0.20)
    for _attempt in range(24):
        intersects = any(
            buffer_box[0] <= point[0] <= buffer_box[2]
            and buffer_box[1] <= point[1] <= buffer_box[3]
            for point in (left_eye, right_eye)
        )
        if not intersects:
            break
        spec.collision_triggered = True
        eye_y = clamp(eye_y + direction * repulsion_step, min_face_y, max_face_y)
        left_eye = (left_eye[0], left_eye[1] + direction * repulsion_step)
        right_eye = (right_eye[0], right_eye[1] + direction * repulsion_step)

    head_half_w = max(
        face_w * (0.34 + geom_wide * 0.08),
        eye_spacing * (0.52 + geom_asym * 0.10),
        card_w * 0.17,
    )
    head_half_w = min(head_half_w, card_w * (0.32 + geom_wide * 0.06))
    ear_gap = head_half_w + card_w * (0.014 + 0.020 * geom_wide + 0.010 * geom_asym)
    ear_size = face_unit * (
        0.060 + 0.036 * geom_tall + 0.024 * geom_curvy + 0.018 * feature_mass
    )
    ear_y = eye_y + y_unit * (
        0.020 + 0.026 * geom_tall + 0.010 * geom_asym - 0.008 * geom_curvy
    )

    # Keep ears inside the face/card box.
    ear_margin = card_w * 0.055 + ear_size * 0.5
    min_ear_x = card_x + ear_margin
    max_ear_x = card_x + card_w - ear_margin

    left_ear_cx = max(face_cx - ear_gap, min_ear_x)
    right_ear_cx = min(face_cx + ear_gap, max_ear_x)

    # If clamping makes the ears too close, shrink them slightly rather than letting them escape.
    available_half_width = min(face_cx - min_ear_x, max_ear_x - face_cx)
    if available_half_width < ear_gap:
        shrink = clamp(available_half_width / max(ear_gap, 1e-9), 0.80, 1.0)
        ear_size *= shrink

    blush_opacity = (
        0.08
        + 0.28 * clamp((m.length - m.chord) / max(m.chord, 1e-9), 0, 1)
        + 0.20 * sinu
    )
    blush_r = face_unit * (0.035 + 0.025 * sinu)
    blush_y = eye_y + y_unit * 0.10

    eye_r = (
        face_unit
        * 0.018
        * (0.86 + geom_curvy * 0.20 + geom_angular * 0.12 + eye_variant * 0.10)
    )
    left_eye_r = eye_r
    right_eye_r = eye_r * (0.92 + stable_unit(f"{spec.name}:right-eye") * 0.18)
    if expression == "dreamy":
        left_eye_r *= 0.82
        right_eye_r *= 0.82
    elif expression == "suspicious":
        left_eye_r *= 0.78
        right_eye_r *= 0.92
    elif expression == "side_eye":
        left_eye_r *= 0.82
        right_eye_r *= 1.06
    elif expression == "deadpan":
        left_eye_r *= 0.70
        right_eye_r *= 0.70
    elif expression == "wonky":
        left_eye_r *= 0.86
        right_eye_r *= 1.08

    brow_y_left = left_eye[1] - y_unit * 0.082
    brow_y_right = right_eye[1] - y_unit * 0.082
    brow_len = card_w * (0.055 + 0.035 * angular) * (0.86 + brow_variant * 0.30)
    brow_tilt = (
        y_unit
        * (0.018 + 0.040 * angular)
        * (0.78 + stable_unit(f"{spec.name}:brow-tilt") * 0.42)
    )
    brow_y_left += (stable_unit(f"{spec.name}:left-brow-y") - 0.5) * y_unit * 0.018
    brow_y_right += (stable_unit(f"{spec.name}:right-brow-y") - 0.5) * y_unit * 0.018
    if presentation == "feminine":
        brow_len *= 0.78
        brow_tilt *= 0.58
        brow_y_left += y_unit * 0.012
        brow_y_right += y_unit * 0.012
    elif presentation == "masculine":
        brow_len *= 1.14
        brow_tilt *= 1.12
        brow_y_left -= y_unit * 0.005
        brow_y_right -= y_unit * 0.005
    if expression == "suspicious":
        brow_tilt *= 1.45
        brow_y_left -= y_unit * 0.012
        brow_y_right += y_unit * 0.004
    elif expression == "dreamy":
        brow_tilt *= 0.55
        brow_y_left += y_unit * 0.006
        brow_y_right += y_unit * 0.006
    elif expression == "deadpan":
        brow_tilt *= 0.18
    elif expression == "side_eye":
        brow_tilt *= 0.95
        brow_y_right -= y_unit * 0.012

    l_brow = f"M {left_eye[0]-brow_len:.2f},{brow_y_left+brow_tilt*0.35:.2f} Q {left_eye[0]:.2f},{brow_y_left-brow_tilt:.2f} {left_eye[0]+brow_len:.2f},{brow_y_left-brow_tilt*0.10:.2f}"
    r_brow = f"M {right_eye[0]-brow_len:.2f},{brow_y_right-brow_tilt*0.10:.2f} Q {right_eye[0]:.2f},{brow_y_right-brow_tilt:.2f} {right_eye[0]+brow_len:.2f},{brow_y_right+brow_tilt*0.35:.2f}"

    if angular > 0.45:
        l_brow = f"M {left_eye[0]-brow_len:.2f},{brow_y_left-brow_tilt:.2f} L {left_eye[0]+brow_len:.2f},{brow_y_left+brow_tilt*0.25:.2f}"
        r_brow = f"M {right_eye[0]-brow_len:.2f},{brow_y_right+brow_tilt*0.25:.2f} L {right_eye[0]+brow_len:.2f},{brow_y_right-brow_tilt:.2f}"
    if presentation == "feminine":
        l_brow = f"M {left_eye[0]-brow_len:.2f},{brow_y_left:.2f} Q {left_eye[0]:.2f},{brow_y_left-brow_tilt*0.65:.2f} {left_eye[0]+brow_len:.2f},{brow_y_left:.2f}"
        r_brow = f"M {right_eye[0]-brow_len:.2f},{brow_y_right:.2f} Q {right_eye[0]:.2f},{brow_y_right-brow_tilt*0.65:.2f} {right_eye[0]+brow_len:.2f},{brow_y_right:.2f}"
    elif brow_variant > 0.72:
        l_brow = f"M {left_eye[0]-brow_len:.2f},{brow_y_left+brow_tilt*0.15:.2f} Q {left_eye[0]:.2f},{brow_y_left-brow_tilt*0.45:.2f} {left_eye[0]+brow_len:.2f},{brow_y_left+brow_tilt*0.15:.2f}"
        r_brow = f"M {right_eye[0]-brow_len:.2f},{brow_y_right+brow_tilt*0.15:.2f} Q {right_eye[0]:.2f},{brow_y_right-brow_tilt*0.45:.2f} {right_eye[0]+brow_len:.2f},{brow_y_right+brow_tilt*0.15:.2f}"
    elif brow_variant < 0.18:
        l_brow = f"M {left_eye[0]-brow_len:.2f},{brow_y_left:.2f} L {left_eye[0]+brow_len:.2f},{brow_y_left-brow_tilt*0.10:.2f}"
        r_brow = f"M {right_eye[0]-brow_len:.2f},{brow_y_right-brow_tilt*0.10:.2f} L {right_eye[0]+brow_len:.2f},{brow_y_right:.2f}"

    small_mouth = ""
    if spec.role_choice.role not in {"mouth", "jaw_chin"}:
        feature_bottom = y1 if spec.role_choice.role not in {"nose", "profile"} else pcy
        mouth_y = clamp(
            max(feature_bottom + y_unit * 0.050, eye_y + y_unit * 0.215),
            eye_y + y_unit * 0.18,
            max_face_y - y_unit * 0.10,
        )
        mouth_w = card_w * (0.07 + 0.025 * (1 - sinu))
        mouth_start, mouth_end = 20, 160
        if expression == "deadpan":
            small_mouth = f'<path d="M {face_cx-mouth_w:.2f},{mouth_y:.2f} L {face_cx+mouth_w:.2f},{mouth_y:.2f}" class="ink feature thin"/>'
        else:
            if expression == "suspicious":
                mouth_start, mouth_end = 205, 335
            elif expression == "dreamy":
                mouth_start, mouth_end = 35, 145
            small_mouth = f'<path d="{draw_arc(face_cx, mouth_y, mouth_w, y_unit*0.025, mouth_start, mouth_end)}" class="ink feature"/>'

    tiny_feature = ""
    if spec.role_choice.role in {"mouth", "jaw_chin"}:
        nx = (
            face_cx
            if center_face
            else face_cx + card_w * (0.010 + (nose_variant - 0.5) * 0.030)
        )
        ny = eye_y + y_unit * (0.135 + stable_unit(f"{spec.name}:nose-y") * 0.040)
        if nose_variant < 0.34:
            tiny_feature = f'<path d="M {nx:.2f},{ny-y_unit*0.036:.2f} Q {nx-card_w*0.024:.2f},{ny-y_unit*0.004:.2f} {nx:.2f},{ny+y_unit*0.032:.2f}" class="ink feature"/>'
        elif nose_variant < 0.68:
            tiny_feature = f'<path d="M {nx-card_w*0.006:.2f},{ny-y_unit*0.040:.2f} Q {nx+card_w*0.018:.2f},{ny-y_unit*0.006:.2f} {nx-card_w*0.002:.2f},{ny+y_unit*0.036:.2f}" class="ink feature"/>'
        else:
            tiny_feature = f'<path d="M {nx:.2f},{ny-y_unit*0.035:.2f} L {nx-card_w*0.006:.2f},{ny+y_unit*0.006:.2f} Q {nx-card_w*0.004:.2f},{ny+y_unit*0.030:.2f} {nx+card_w*0.012:.2f},{ny+y_unit*0.036:.2f}" class="ink feature"/>'
    elif spec.role_choice.role in {"nose", "profile"}:
        ny = y1 + y_unit * (0.020 + nose_variant * 0.016)
        tiny_feature = f'<path d="M {face_cx-card_w*0.018:.2f},{ny:.2f} Q {face_cx:.2f},{ny+y_unit*0.014:.2f} {face_cx+card_w*0.018:.2f},{ny:.2f}" class="ink feature thin"/>'

    ear_rx = ear_size * (0.24 + 0.16 * geom_wide + 0.06 * geom_asym)
    ear_ry = ear_size * (0.38 + 0.24 * geom_tall + 0.10 * geom_curvy)
    inner_ear_rx = ear_rx * (0.42 + 0.14 * geom_curvy)
    inner_ear_ry = ear_ry * (0.50 + 0.12 * geom_tall)
    ear_start = 104 + geom_angular * 8 - geom_wide * 6
    ear_end = 252 - geom_curvy * 10 + geom_tall * 6
    right_ear_start = -72 - geom_tall * 4
    right_ear_end = 72 + geom_angular * 6 - geom_curvy * 5
    left_ear_rx = ear_rx * (1.0 + geom_asym * 0.28)
    right_ear_rx = ear_rx * (1.0 - geom_asym * 0.16)
    left_ear_ry = ear_ry * (0.94 + geom_tall * 0.14)
    right_ear_ry = ear_ry * (1.02 - geom_wide * 0.08)
    if geom_angular > 0.58:
        left_ear = (
            f"M {left_ear_cx+left_ear_rx*0.45:.2f},{ear_y-left_ear_ry*0.78:.2f} "
            f"L {left_ear_cx-left_ear_rx*0.45:.2f},{ear_y:.2f} "
            f"L {left_ear_cx+left_ear_rx*0.42:.2f},{ear_y+left_ear_ry*0.76:.2f}"
        )
        right_ear = (
            f"M {right_ear_cx-right_ear_rx*0.45:.2f},{ear_y-right_ear_ry*0.78:.2f} "
            f"L {right_ear_cx+right_ear_rx*0.45:.2f},{ear_y:.2f} "
            f"L {right_ear_cx-right_ear_rx*0.42:.2f},{ear_y+right_ear_ry*0.76:.2f}"
        )
        left_inner = (
            f"M {left_ear_cx+inner_ear_rx*0.30:.2f},{ear_y-inner_ear_ry*0.52:.2f} "
            f"L {left_ear_cx-inner_ear_rx*0.22:.2f},{ear_y:.2f} "
            f"L {left_ear_cx+inner_ear_rx*0.28:.2f},{ear_y+inner_ear_ry*0.48:.2f}"
        )
        right_inner = (
            f"M {right_ear_cx-inner_ear_rx*0.30:.2f},{ear_y-inner_ear_ry*0.52:.2f} "
            f"L {right_ear_cx+inner_ear_rx*0.22:.2f},{ear_y:.2f} "
            f"L {right_ear_cx-inner_ear_rx*0.28:.2f},{ear_y+inner_ear_ry*0.48:.2f}"
        )
    else:
        left_ear = draw_arc(
            left_ear_cx, ear_y, left_ear_rx, left_ear_ry, ear_start, ear_end
        )
        left_inner = draw_arc(
            left_ear_cx + left_ear_rx * 0.10,
            ear_y,
            inner_ear_rx,
            inner_ear_ry,
            ear_start + 5,
            ear_end - 6,
        )
        right_ear = draw_arc(
            right_ear_cx,
            ear_y,
            right_ear_rx,
            right_ear_ry,
            right_ear_start,
            right_ear_end,
        )
        right_inner = draw_arc(
            right_ear_cx - right_ear_rx * 0.10,
            ear_y,
            inner_ear_rx,
            inner_ear_ry,
            right_ear_start + 6,
            right_ear_end - 5,
        )

    frame_x = card_x if frame_x is None else frame_x
    frame_y = card_y if frame_y is None else frame_y
    frame_w = card_w if frame_w is None else frame_w
    frame_h = card_h if frame_h is None else frame_h
    label_x = frame_x + frame_w / 2
    if single_line_label and label_font_size is not None:
        label_y = frame_y + min(card_h * 0.12, label_font_size * 1.80)
    else:
        label_y = frame_y + clamp(card_h * 0.055, 10, 22)
    # Keep the configured title size. Wrap only when the estimated rendered
    # width exceeds the frame, choosing the word break with the narrowest
    # longest line. Gallery labels retain v19's effective 1em (16px) size;
    # single-face labels retain their calculated size. Neither is reduced.
    resolved_label_font_size = (
        label_font_size
        if single_line_label and label_font_size is not None
        else 16.0
    )
    available_label_width = frame_w * 0.88

    def estimated_line_width(line: str) -> float:
        return len(line) * resolved_label_font_size * 0.56

    label_words = spec.name.split()
    label_lines = [spec.name]
    if estimated_line_width(spec.name) > available_label_width and len(label_words) > 1:
        split_at = min(
            range(1, len(label_words)),
            key=lambda idx: max(
                estimated_line_width(" ".join(label_words[:idx])),
                estimated_line_width(" ".join(label_words[idx:])),
            ),
        )
        label_lines = [" ".join(label_words[:split_at]), " ".join(label_words[split_at:])]

    label_font_value = f"{resolved_label_font_size:.2f}px"
    label_tracking = 0.0
    label_line_height = resolved_label_font_size * 1.12
    label_markup = "".join(
        f'<tspan x="{label_x:.1f}" dy="{0 if idx == 0 else label_line_height:.1f}">{escape_xml(line)}</tspan>'
        for idx, line in enumerate(label_lines)
    )

    blush_markup = ""
    blush_allowed = (
        expression not in {"deadpan", "suspicious"} and stable_unit(spec.name) > 0.22
    )
    if show_blush and blush_allowed:
        blush_markup = f"""
      <circle cx="{left_eye[0]-card_w*0.020:.2f}" cy="{blush_y:.2f}" r="{blush_r:.2f}" class="blush" opacity="{blush_opacity:.2f}"/>
      <circle cx="{right_eye[0]+card_w*0.020:.2f}" cy="{blush_y:.2f}" r="{blush_r:.2f}" class="blush" opacity="{blush_opacity:.2f}"/>
"""


    ear_markup = ""
    ear_stroke_count = ear_stroke_count_for_spec(spec, ear_mode)
    if ear_stroke_count:
        inner_ear_markup = ""
        if ear_stroke_count == 2:
            inner_ear_markup = f"""
      <path d="{left_inner}" class="ink feature thin"/>
      <path d="{right_inner}" class="ink feature thin"/>"""
        ear_markup = f"""
      <path d="{left_ear}" class="ink feature"/>
      <path d="{right_ear}" class="ink feature"/>{inner_ear_markup}
"""


    eyelid_markup = ""
    if expression in {"suspicious", "deadpan", "side_eye"}:
        lid_drop = y_unit * (0.010 if expression == "side_eye" else 0.014)
        eyelid_markup = f"""
      <path d="M {left_eye[0]-left_eye_r*1.8:.2f},{left_eye[1]-lid_drop:.2f} L {left_eye[0]+left_eye_r*1.8:.2f},{left_eye[1]-lid_drop*0.25:.2f}" class="ink thin"/>
      <path d="M {right_eye[0]-right_eye_r*1.8:.2f},{right_eye[1]-lid_drop*0.25:.2f} L {right_eye[0]+right_eye_r*1.8:.2f},{right_eye[1]-lid_drop:.2f}" class="ink thin"/>
"""

    lashes_markup = ""
    hair_markup = ""
    lip_markup = ""
    if presentation == "feminine":
        lash = y_unit * (0.014 + stable_unit(f"{spec.name}:lash") * 0.012)
        lashes_markup = f"""
      <path d="M {left_eye[0]-left_eye_r*1.25:.2f},{left_eye[1]-left_eye_r*0.45:.2f} L {left_eye[0]-left_eye_r*2.15:.2f},{left_eye[1]-lash:.2f}" class="ink thin"/>
      <path d="M {left_eye[0]+left_eye_r*1.05:.2f},{left_eye[1]-left_eye_r*0.75:.2f} L {left_eye[0]+left_eye_r*1.65:.2f},{left_eye[1]-lash*1.05:.2f}" class="ink thin"/>
      <path d="M {right_eye[0]-right_eye_r*1.05:.2f},{right_eye[1]-right_eye_r*0.75:.2f} L {right_eye[0]-right_eye_r*1.65:.2f},{right_eye[1]-lash*1.05:.2f}" class="ink thin"/>
      <path d="M {right_eye[0]+right_eye_r*1.25:.2f},{right_eye[1]-right_eye_r*0.45:.2f} L {right_eye[0]+right_eye_r*2.15:.2f},{right_eye[1]-lash:.2f}" class="ink thin"/>
"""
        if stable_unit(f"{spec.name}:lash-count") < 0.28:
            lashes_markup = f"""
      <path d="M {left_eye[0]+left_eye_r*1.05:.2f},{left_eye[1]-left_eye_r*0.75:.2f} L {left_eye[0]+left_eye_r*1.65:.2f},{left_eye[1]-lash*1.05:.2f}" class="ink thin"/>
      <path d="M {right_eye[0]-right_eye_r*1.05:.2f},{right_eye[1]-right_eye_r*0.75:.2f} L {right_eye[0]-right_eye_r*1.65:.2f},{right_eye[1]-lash*1.05:.2f}" class="ink thin"/>
"""
        if stable_unit(f"{spec.name}:hair") > 0.22:
            crown_w = face_w * (
                0.16 + geom_wide * 0.12 + geom_curvy * 0.07 + crown_variant * 0.035
            )
            crown_h = y_unit * (
                0.026
                + geom_tall * 0.036
                + geom_angular * 0.016
                + stable_unit(f"{spec.name}:crown-h") * 0.010
            )
            crown_shift = (geom_asym - 0.35) * card_w * 0.040 + (
                stable_unit(f"{spec.name}:crown-shift") - 0.5
            ) * card_w * 0.018
            crown_start = 194 + geom_angular * 12 - geom_wide * 6
            crown_end = 346 - geom_curvy * 16 + geom_tall * 8
            hair_y = clamp(
                min(left_eye[1], right_eye[1])
                - y_unit * (0.040 + geom_tall * 0.030 + geom_curvy * 0.010),
                min_face_y + y_unit * 0.075,
                min(left_eye[1], right_eye[1]) - y_unit * 0.030,
            )
            hair_markup = f"""
      <path d="{draw_arc(face_cx+crown_shift, hair_y, crown_w, crown_h, crown_start, crown_end)}" class="ink thin"/>
      <path d="{draw_arc(face_cx-card_w*0.018+crown_shift*0.45, hair_y+y_unit*(0.006+geom_curvy*0.006), crown_w*(0.58+geom_wide*0.16), crown_h*(0.46+geom_tall*0.20), crown_start+8, crown_end-20)}" class="soft-detail"/>
"""
        lip_y = clamp(
            max(pcy + y_unit * 0.075, eye_y + y_unit * 0.220),
            eye_y + y_unit * 0.18,
            max_face_y - y_unit * 0.10,
        )
        lip_w = card_w * 0.055
        lip_markup = f'<path d="{draw_arc(face_cx, lip_y, lip_w, y_unit*0.014, 18, 162)}" class="soft-detail"/>'

    # CairoSVG does not reliably honour vector-effect on transformed paths, which
    # makes PNG exports render the sacred street stroke far heavier than the SVG.
    # Use the fitted coordinates directly so both formats share the same stroke.
    # Short streets need extra contrast; long paths already carry visual mass.
    length_stroke_factor = 1.25 if spec.length_percentile < 0.50 else 0.90
    rendered_street_width = max(2.0, street_stroke_multiplier * length_stroke_factor)
    street_markup = "".join(
        f'<polyline points="{polyline_to_svg_points(path)}" class="street" '
        f'stroke="{spec.group_color}" style="stroke-width:{rendered_street_width:.3f}"/>'
        for path in spec.card_paths
    )

    content_dx = 0.0
    content_dy = 0.0
    if auto_center_content:
        content_x = [point[0] for path in spec.card_paths for point in path]
        content_y = [point[1] for path in spec.card_paths for point in path]
        content_x.extend(
            [
                left_eye[0] - left_eye_r,
                left_eye[0] + left_eye_r,
                right_eye[0] - right_eye_r,
                right_eye[0] + right_eye_r,
                left_ear_cx - left_ear_rx,
                left_ear_cx + left_ear_rx,
                right_ear_cx - right_ear_rx,
                right_ear_cx + right_ear_rx,
                left_eye[0] - brow_len,
                left_eye[0] + brow_len,
                right_eye[0] - brow_len,
                right_eye[0] + brow_len,
            ]
        )
        content_y.extend(
            [
                left_eye[1] - left_eye_r,
                left_eye[1] + left_eye_r,
                right_eye[1] - right_eye_r,
                right_eye[1] + right_eye_r,
                ear_y - max(left_ear_ry, right_ear_ry),
                ear_y + max(left_ear_ry, right_ear_ry),
                brow_y_left - abs(brow_tilt),
                brow_y_left + abs(brow_tilt),
                brow_y_right - abs(brow_tilt),
                brow_y_right + abs(brow_tilt),
            ]
        )
        if spec.role_choice.role in {"mouth", "jaw_chin"}:
            content_x.extend([nx - card_w * 0.03, nx + card_w * 0.03])
            content_y.extend([ny - y_unit * 0.04, ny + y_unit * 0.04])
        else:
            content_x.extend([face_cx - mouth_w, face_cx + mouth_w])
            content_y.extend([mouth_y - y_unit * 0.03, mouth_y + y_unit * 0.03])
        if hair_markup:
            content_x.extend([face_cx + crown_shift - crown_w, face_cx + crown_shift + crown_w])
            content_y.extend([hair_y - crown_h, hair_y + crown_h])
        if lip_markup:
            content_x.extend([face_cx - lip_w, face_cx + lip_w])
            content_y.extend([lip_y - y_unit * 0.02, lip_y + y_unit * 0.02])

        content_min_x, content_max_x = min(content_x), max(content_x)
        content_min_y, content_max_y = min(content_y), max(content_y)
        desired_x = frame_x + frame_w / 2
        desired_y = frame_y + frame_h / 2
        content_dx = desired_x - (content_min_x + content_max_x) / 2
        content_dy = desired_y - (content_min_y + content_max_y) / 2

        title_clearance = resolved_label_font_size * 1.15
        title_clearance += (len(label_lines) - 1) * label_line_height
        safe_top = label_y + title_clearance
        safe_bottom = frame_y + frame_h * 0.95
        safe_left = frame_x + frame_w * 0.05
        safe_right = frame_x + frame_w * 0.95
        content_dx = clamp(
            content_dx,
            safe_left - content_min_x,
            safe_right - content_max_x,
        )
        content_dy = clamp(
            content_dy,
            safe_top - content_min_y,
            safe_bottom - content_max_y,
        )

    return f"""
    <g>
      <rect x="{frame_x:.1f}" y="{frame_y:.1f}" width="{frame_w:.1f}" height="{frame_h:.1f}" class="card-frame" fill="{palette.card}" stroke="{palette.border}"/>
      <text x="{label_x:.1f}" y="{label_y:.1f}" class="title" style="font-size:{label_font_value};letter-spacing:{label_tracking:.2f}px">{label_markup}</text>

      <g class="face-content" transform="translate({content_dx:.2f} {content_dy:.2f})">
      {blush_markup}

      {hair_markup}

      {ear_markup}

      <path d="{l_brow}" class="ink brow"/>
      <path d="{r_brow}" class="ink brow"/>

      <circle cx="{left_eye[0]:.2f}" cy="{left_eye[1]:.2f}" r="{left_eye_r:.2f}" class="eye"/>
      <circle cx="{right_eye[0]:.2f}" cy="{right_eye[1]:.2f}" r="{right_eye_r:.2f}" class="eye"/>
      {eyelid_markup}
      {lashes_markup}

      {tiny_feature}
      {small_mouth}
      {lip_markup}

      {street_markup}
      </g>
    </g>
    """


def choose_grid(
    count: int,
    paper: dict,
    requested_cols: Optional[int] = None,
    reserve_note: bool = True,
    reserve_title: bool = False,
    note_height_fraction: float = 0.035,
) -> Tuple[int, int, float, float, float]:
    if count <= 0:
        return 1, 1, float(paper["width"]), float(paper["height"]), 0.0

    margin = float(paper["margin"])
    gap = float(paper["gap"])
    note_h = (
        float(paper["height"]) * note_height_fraction if reserve_note else 0.0
    )
    title_h = float(paper["height"]) * 0.060 if reserve_title else 0.0
    usable_w = float(paper["width"]) - margin * 2
    usable_h = float(paper["height"]) - margin * 2 - note_h - title_h

    if requested_cols and requested_cols > 0:
        cols = min(requested_cols, count)
    else:
        best = (0.0, 1, count, 0.0, 0.0)
        for candidate_cols in range(1, count + 1):
            rows = math.ceil(count / candidate_cols)
            cell_w = (usable_w - gap * (candidate_cols - 1)) / candidate_cols
            cell_h = (usable_h - gap * (rows - 1)) / rows
            card_w = cell_w
            card_h = cell_h
            score = card_w * card_h
            if score > best[0]:
                best = (score, candidate_cols, rows, card_w, card_h)
        return best[1], best[2], best[3], best[4], gap

    rows = math.ceil(count / cols)
    cell_w = (usable_w - gap * (cols - 1)) / cols
    cell_h = (usable_h - gap * (rows - 1)) / rows
    card_w = cell_w
    card_h = cell_h
    return cols, rows, card_w, card_h, gap


def wrap_text_lines(text: str, max_chars: int) -> List[str]:
    """Wrap text at word boundaries without altering its wording."""
    words = text.split()
    if not words:
        return [""]
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def default_print_title_font_size(paper_height: float) -> float:
    return max(18.0, min(42.0, paper_height * 0.013))


def default_print_subtitle_font_size(paper_height: float) -> float:
    return max(10.0, min(19.0, paper_height * 0.0065))


def default_curatorial_note_font_size(paper_height: float) -> float:
    return max(8.0, min(13.0, paper_height * 0.006))


def render_grid(
    specs: List[FaceSpec],
    out_svg: Path,
    out_png: Optional[Path] = None,
    out_pdf: Optional[Path] = None,
    cols: Optional[int] = None,
    show_blush: bool = True,
    ear_mode: str = "two-stroke",
    presentation_mode: str = "varied",
    street_stroke_multiplier: float = 1.18,
    palette: Optional[FacePalette] = None,
    paper_key: str = "a1",
    show_note: bool = True,
    show_title: bool = False,
    print_title: str = PRINT_TITLE,
    print_subtitle: str = PRINT_SUBTITLE,
    curatorial_note: str = CURATORIAL_NOTE,
    provenance_area: Optional[str] = None,
    print_title_font_size: Optional[float] = None,
    print_subtitle_font_size: Optional[float] = None,
    curatorial_note_font_size: Optional[float] = None,
    text_font_key: str = DEFAULT_TEXT_FONT_KEY,
    single_svg_mode: bool = False,
) -> None:
    palette = palette or get_face_palette()
    text_font_stack = get_text_font_stack(text_font_key)
    paper = PAPER_PRESETS.get(paper_key, PAPER_PRESETS["a1"])
    width = int(paper["width"])
    height = int(paper["height"])
    margin = float(paper["margin"])
    reference_paper = PAPER_PRESETS[SINGLE_FACE_SCALE_REFERENCE_PAPER]
    single_face_paper_scale = (
        float(paper["width_mm"]) / float(reference_paper["width_mm"])
    )
    compact_single = single_svg_mode and paper_key in {"a4", "a5", "a6"}
    note_height_fraction = 0.05 if compact_single else 0.035
    cols, rows, card_w, card_h, gap = choose_grid(
        len(specs),
        paper,
        cols,
        reserve_note=show_note,
        reserve_title=show_title,
        note_height_fraction=note_height_fraction,
    )
    grid_w = cols * card_w + (cols - 1) * gap
    grid_h = rows * card_h + (rows - 1) * gap
    start_x = (width - grid_w) / 2
    note_h = height * note_height_fraction if show_note else 0.0
    title_h = height * 0.060 if show_title else 0.0
    available_grid_h = height - margin * 2 - note_h - title_h
    if grid_h < available_grid_h * 0.55:
        start_y = margin + title_h + gap
    else:
        start_y = margin + title_h + (available_grid_h - grid_h) / 2

    # Procedural face marks whisper; the geographic path remains the hero.
    stroke_scale = SINGLE_SVG_STROKE_SCALE if single_svg_mode else 1.0
    ink_stroke = max(0.72, min(1.0, card_h * 0.0030)) * stroke_scale
    feature_stroke = max(0.64, ink_stroke * 0.86)
    thin_stroke = max(0.42, ink_stroke * 0.52)
    brow_stroke = ink_stroke
    hierarchy_base = max(1.8 * stroke_scale, ink_stroke * 2.0)
    street_stroke = max(
        hierarchy_base,
        hierarchy_base * clamp(street_stroke_multiplier, 0.90, 1.10),
    )
    card_border_stroke = 1.20 if compact_single else 1.0

    title_size = (
        max(26.0, min(78.0, card_w * 0.068))
        if single_svg_mode
        else max(7.5, min(15.0, card_h * 0.036))
    )

    _, _, gallery_card_w, gallery_card_h, _ = choose_grid(
        36,
        PAPER_PRESETS["a2"],
        9,
        reserve_note=True,
        reserve_title=True,
    )
    body = []
    # Refit each spec into its real card coordinates.
    fitted_specs = []
    for idx, spec in enumerate(specs):
        col = idx % cols
        row = idx // cols
        x = start_x + col * (card_w + gap)
        y = start_y + row * (card_h + gap)
        if single_svg_mode:
            effective_single_face_scale = (
                SINGLE_FACE_GALLERY_SCALE * single_face_paper_scale
            )
            face_layout_w = gallery_card_w * effective_single_face_scale
            face_layout_h = gallery_card_h * effective_single_face_scale
            face_layout_x = x + (card_w - face_layout_w) / 2
            face_y = (
                y
                + (card_h - face_layout_h) / 2
                + card_h * SINGLE_FACE_VERTICAL_OFFSET_FRACTION
            )
        else:
            face_layout_w = card_w
            face_layout_h = card_h
            face_layout_x = x
            face_y = y
        target = feature_target_for_role(
            spec.role_choice.role,
            face_layout_x,
            face_y,
            face_layout_w,
            face_layout_h,
        )
        if single_svg_mode:
            target = (
                face_layout_x + face_layout_w / 2 - target[2] / 2,
                target[1],
                target[2],
                target[3],
            )
        card_paths, transform = fit_paths_to_feature_box(
            spec.original_paths, spec.role_choice.rotation, target
        )
        fitted_specs.append(
            FaceSpec(
                spec.name,
                spec.group_color,
                spec.role_choice,
                spec.original_paths,
                spec.transformed_paths,
                card_paths,
                spec.source_path_ds,
                spec.source_uses_fill,
                transform,
                spec.report,
                transform_matrix=parse_affine_transform(transform),
                scaling_factor_applied=math.sqrt(validate_affine_matrix(parse_affine_transform(transform))),
                length_percentile=spec.length_percentile,
                collision_triggered=spec.collision_triggered,
            )
        )
        body.append(
            render_face_svg(
                fitted_specs[-1],
                face_layout_x,
                face_y,
                face_layout_w,
                face_layout_h,
                show_blush=show_blush,
                ear_mode=ear_mode,
                presentation_mode=presentation_mode,
                street_stroke_multiplier=street_stroke,
                palette=palette,
                frame_x=x,
                frame_y=y,
                frame_w=card_w,
                frame_h=card_h,
                single_line_label=single_svg_mode,
                center_face=single_svg_mode,
                auto_center_content=single_svg_mode,
                label_font_size=title_size,
            )
        )
        spec.street_transform = transform
        spec.transform_matrix = fitted_specs[-1].transform_matrix
        spec.scaling_factor_applied = fitted_specs[-1].scaling_factor_applied
        spec.collision_triggered = fitted_specs[-1].collision_triggered

    print_title_size = (
        max(1.0, print_title_font_size)
        if print_title_font_size is not None
        else default_print_title_font_size(height)
    )
    print_subtitle_size = (
        max(1.0, print_subtitle_font_size)
        if print_subtitle_font_size is not None
        else default_print_subtitle_font_size(height)
    )
    curatorial_note_size = (
        max(1.0, curatorial_note_font_size)
        if curatorial_note_font_size is not None
        else default_curatorial_note_font_size(height)
    )
    if compact_single:
        curatorial_note_size = max(6.0, curatorial_note_size - 1.0)

    if compact_single:
        title_width_cap = (width - margin * 2.4) / max(len(print_title) * 0.58, 1.0)
        subtitle_width_cap = (width - margin * 2.4) / max(
            len(print_subtitle) * 0.54, 1.0
        )
        print_title_size = min(print_title_size, title_width_cap)
        print_subtitle_size = min(
            print_subtitle_size,
            subtitle_width_cap,
            print_title_size * 0.42,
        )

    note_markup = ""
    if show_note:
        note_lines = [curatorial_note]
        if compact_single:
            note_max_width = width - margin * 2.4
            note_max_chars = max(
                24, int(note_max_width / max(curatorial_note_size * 0.56, 1.0))
            )
            note_lines = wrap_text_lines(curatorial_note, note_max_chars)
        note_line_height = curatorial_note_size * 1.35
        note_bottom_y = height - margin * 0.65
        note_first_y = note_bottom_y - note_line_height * (len(note_lines) - 1)
        note_tspans = "".join(
            f'<tspan x="{width / 2:.1f}" y="{note_first_y + idx * note_line_height:.1f}">{escape_xml(line)}</tspan>'
            for idx, line in enumerate(note_lines)
        )
        note_markup = (
            f'<text x="{width / 2:.1f}" class="curatorial-note">{note_tspans}</text>'
        )
        provenance_label = provenance_area or print_subtitle or "unknown area"
        final_note_line = note_lines[-1] if note_lines else curatorial_note
        approx_text_half_width = min(
            width * 0.42,
            len(final_note_line) * curatorial_note_size * 0.24,
        )
        provenance_x = width / 2 + approx_text_half_width + curatorial_note_size * 0.75
        provenance_y = note_first_y + (len(note_lines) - 1) * note_line_height
        provenance_markup = render_attribution_provenance_mark(
            provenance_label,
            provenance_x,
            provenance_y,
            curatorial_note_size,
            width - margin * 0.55,
            palette,
        )
        note_markup += provenance_markup

    title_markup = ""
    if show_title:
        print_title_y = margin + height * 0.018
        print_subtitle_y = margin + height * 0.034
        if compact_single:
            print_subtitle_y = (
                print_title_y
                + print_title_size * 0.75
                + print_subtitle_size * 0.45
            )
        title_markup = f"""
  <text x="{width / 2:.1f}" y="{print_title_y:.1f}" class="print-title">{escape_xml(print_title)}</text>
  <text x="{width / 2:.1f}" y="{print_subtitle_y:.1f}" class="print-subtitle">{escape_xml(print_subtitle)}</text>
"""

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{paper["width_mm"]}mm" height="{paper["height_mm"]}mm" viewBox="0 0 {width} {height}">
  <defs>
    <filter id="softBlur" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="7"/>
    </filter>
    <style>
      .card-frame {{ stroke-width: {card_border_stroke:.2f}; }}
      .title {{ font: 700 {title_size:.1f}px {text_font_stack}; fill: {palette.feature}; text-anchor: middle; }}
      .print-title {{ font: 800 {print_title_size:.1f}px {text_font_stack}; fill: {palette.feature}; text-anchor: middle; letter-spacing: 0; }}
      .print-subtitle {{ font: 400 {print_subtitle_size:.1f}px {text_font_stack}; fill: {palette.feature}; text-anchor: middle; }}
      .curatorial-note {{ font: 400 {curatorial_note_size:.1f}px {text_font_stack}; fill: {palette.feature}; text-anchor: middle; }}
      .provenance-signature {{ fill: {palette.meta}; opacity: 0.62; }}
      .provenance-finder {{ fill: none; stroke: {palette.meta}; stroke-width: {max(0.35, curatorial_note_size * 0.055):.2f}; opacity: 0.70; }}
      .provenance-finder-inner {{ fill: {palette.meta}; opacity: 0.50; }}
      .provenance-dots {{ fill: {palette.meta}; }}
      .provenance-bit-one {{ opacity: 0.82; }}
      .provenance-bit-zero {{ opacity: 0.30; }}
      .street {{ fill: none; stroke-width: {street_stroke:.2f}; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }}
      .ink {{ fill: none; stroke: {palette.meta}; stroke-width: {ink_stroke:.2f}; stroke-linecap: round; stroke-linejoin: round; }}
      .feature {{ stroke-width: {feature_stroke:.2f}; }}
      .thin {{ stroke-width: {thin_stroke:.2f}; }}
      .brow {{ stroke-width: {brow_stroke:.2f}; }}
      .eye {{ fill: {palette.meta}; }}
      .blush {{ fill: {palette.blush}; filter: url(#softBlur); }}
      .soft-detail {{ fill: none; stroke: {palette.blush}; stroke-width: {thin_stroke:.2f}; stroke-linecap: round; stroke-linejoin: round; }}
    </style>
  </defs>
  <rect id="rect1" x="0" y="0" width="{width}" height="{height}" fill="{palette.background}"/>
  {title_markup}
  {''.join(body)}
  {note_markup}
</svg>
"""
    out_svg.write_text(svg, encoding="utf-8")

    if out_png:
        try:
            import cairosvg

            preview_width = max(2048, width)
            preview_height = round(height * preview_width / width)
            cairosvg.svg2png(
                url=str(out_svg),
                write_to=str(out_png),
                output_width=preview_width,
                output_height=preview_height,
            )
        except Exception as exc:
            print(
                f"Could not write PNG preview. Install cairosvg or omit --png. Error: {exc}"
            )
    if out_pdf:
        try:
            import cairosvg

            out_pdf.parent.mkdir(parents=True, exist_ok=True)
            cairosvg.svg2pdf(url=str(out_svg), write_to=str(out_pdf))
        except Exception as exc:
            print(
                f"Could not write PDF. Install cairosvg or omit PDF export. Error: {exc}"
            )


def build_specs(
    input_files: Sequence[Path],
    palette: Optional[FacePalette] = None,
    road_types: Optional[Sequence[str]] = None,
) -> List[FaceSpec]:
    palette = palette or get_face_palette()
    selected_road_types = normalize_road_types(road_types)
    specs: List[FaceSpec] = []
    card_w, card_h = 560, 390
    for idx, p in enumerate(input_files):
        try:
            name, paths, stroke, path_ds, uses_fill = load_svg_paths(
                p, road_types=selected_road_types
            )
        except ValueError:
            if selected_road_types:
                continue
            raise
        color = palette.streets[idx % len(palette.streets)]
        specs.append(
            make_face_spec(name, paths, path_ds, uses_fill, color, 0, 0, card_w, card_h)
        )
    if specs:
        apply_batch_relative_roles(specs)
    if not specs:
        if selected_road_types:
            raise ValueError(
                "No SVG paths matched the selected road type"
                f"{'s' if len(selected_road_types) != 1 else ''}: "
                f"{', '.join(selected_road_types)}"
            )
        raise ValueError("No SVG paths could be loaded.")
    return specs


def apply_gallery_context_to_single_spec(
    specs: Sequence[FaceSpec],
    input_files: Sequence[Path],
    palette: FacePalette,
    road_types: Optional[Sequence[str]],
    gallery_specs: Optional[Sequence[FaceSpec]] = None,
) -> bool:
    """Reuse the gallery colour and batch-relative role for the same glyph."""
    if len(specs) != 1 or len(input_files) != 1:
        return False
    source = input_files[0]
    if gallery_specs is None:
        if source.parent.name.lower() != "glyphs":
            return False
        sibling_files = sorted(
            path
            for path in source.parent.iterdir()
            if path.is_file() and path.suffix.lower() == ".svg"
        )
        if len(sibling_files) <= 1:
            return False
        try:
            gallery_specs = build_specs(
                sibling_files, palette=palette, road_types=road_types
            )
        except ValueError:
            return False
    selected_key = _overlay_street_key(specs[0].name)
    gallery_match = next(
        (
            candidate
            for candidate in gallery_specs
            if _overlay_street_key(candidate.name) == selected_key
        ),
        None,
    )
    if gallery_match is None:
        return False
    specs[0].group_color = gallery_match.group_color
    specs[0].role_choice.role = gallery_match.role_choice.role
    specs[0].length_percentile = gallery_match.length_percentile
    specs[0].report = list(gallery_match.report)
    return True


def collect_svg_inputs(inputs: Sequence[Path]) -> List[Path]:
    svg_files: List[Path] = []
    for source in inputs:
        if source.is_dir():
            streets_svg = source / "streets.svg"
            if streets_svg.is_file():
                svg_files.append(streets_svg)
                continue
            svg_files.extend(
                sorted(
                    p
                    for p in source.iterdir()
                    if p.is_file() and p.suffix.lower() == ".svg"
                )
            )
        elif source.is_file() and source.suffix.lower() == ".svg":
            svg_files.append(source)
        else:
            raise ValueError(f"Input is not an SVG file or folder: {source}")
    if not svg_files:
        raise ValueError("No SVG files found.")
    return svg_files


def _overlay_street_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _element_label(elem: ET.Element) -> str:
    label = elem.get(f"{{{INKSCAPE_NS}}}label") or elem.get("label") or ""
    if label:
        return label.strip()
    for child in elem:
        if child.tag.split("}")[-1] == "title" and child.text:
            return child.text.strip()
    return ""


def _input_glyph_parent(input_files: Sequence[Path]) -> Optional[Path]:
    if not input_files:
        return None
    parent = input_files[0].parent
    if parent.name.lower() != "glyphs":
        return None
    if all(path.parent == parent for path in input_files):
        return parent
    return None


def write_faces_overlay_from_selection(
    input_files: Sequence[Path],
    specs: Sequence[FaceSpec],
) -> Optional[Path]:
    glyph_parent = _input_glyph_parent(input_files)
    if glyph_parent is None:
        return None

    overlay_path = glyph_parent.parent / "overlay.svg"
    if not overlay_path.is_file():
        return None

    selected_keys = {_overlay_street_key(spec.name) for spec in specs}
    selected_keys.discard("")
    if not selected_keys:
        return None

    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.register_namespace("inkscape", INKSCAPE_NS)
    tree = ET.parse(overlay_path)
    root = tree.getroot()
    kept_count = 0

    for layer in list(root):
        layer_id = layer.get("id") or ""
        if not layer_id.startswith("street_class_"):
            continue

        for child in list(layer):
            if child.tag.split("}")[-1] != "g":
                continue
            label_key = _overlay_street_key(_element_label(child))
            if label_key in selected_keys:
                kept_count += 1
            else:
                layer.remove(child)

    if not kept_count:
        return None

    output_path = glyph_parent.parent / "faces_overlay.svg"
    tree.write(output_path, encoding="utf-8", xml_declaration=False)
    return output_path


def _load_step09_v2_module():
    spec = importlib.util.spec_from_file_location("step09_overlay_v2", STEP09_V2_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load overlay builder from {STEP09_V2_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _overlay_area_from_inputs(inputs: Sequence[Path]) -> Optional[str]:
    if len(inputs) != 1:
        return None
    source = inputs[0]
    if source.exists() or source.suffix.lower() == ".svg":
        return None
    return str(source)


def build_overlay_namespace(args: argparse.Namespace, area_selector: str) -> Namespace:
    step09_v2 = _load_step09_v2_module()
    return Namespace(
        area_selector=area_selector,
        street_csv=str(args.overlay_street_csv) if args.overlay_street_csv else None,
        street_column=args.overlay_street_column,
        output_root=args.overlay_output_root,
        place_name=args.overlay_place_name,
        boundary_geojson_path=(
            str(args.overlay_boundary_geojson_path)
            if args.overlay_boundary_geojson_path
            else None
        ),
        no_boundary_geojson=args.overlay_no_boundary_geojson,
        osm_snapshot_date=args.overlay_osm_snapshot_date,
        overlay_scale_mode=args.overlay_scale_mode,
        zoom=args.overlay_zoom,
        clip_to_boundary=args.overlay_clip_to_boundary,
        refine_discontinuous=args.overlay_refine_discontinuous,
        frame_margin_km=args.overlay_frame_margin_km,
        frame_center_radius_px=args.overlay_frame_center_radius_px,
        frame_center_fill=args.overlay_frame_center_fill,
        same_street_gap_fill_max_m=args.overlay_same_street_gap_fill_max_m,
        royal_mail_source_dir=args.overlay_royal_mail_source_dir,
        partial_match_min_address_spread_m=(
            args.overlay_partial_match_min_address_spread_m
        ),
        partial_match_short_ratio=args.overlay_partial_match_short_ratio,
        partial_match_absolute_gap_m=args.overlay_partial_match_absolute_gap_m,
        no_auto_supplement=not args.overlay_auto_supplement,
        max_auto_supplements=args.overlay_max_auto_supplements,
        supplement_street=args.overlay_supplement_street,
        _step09_v2_module=step09_v2,
    )


def create_overlay_svg_from_area(
    args: argparse.Namespace, area_selector: str
) -> tuple[Path, dict]:
    overlay_args = build_overlay_namespace(args, area_selector)
    step09_v2 = overlay_args._step09_v2_module
    delattr(overlay_args, "_step09_v2_module")
    summary = step09_v2.build_robust_overlay(overlay_args)
    overlay_svg = Path(str(summary.get("final_overlay_svg") or ""))
    if not overlay_svg.is_file():
        raise FileNotFoundError(f"Overlay SVG was not created: {overlay_svg}")
    return overlay_svg, summary


RUN_FOLDER_SUFFIX_TOKENS = ("streets", "parks", "water", "boundary", "clip")


def format_place_label(value: str) -> str:
    words = re.sub(r"[_-]+", " ", value).strip().split()
    return " ".join(word if word.isupper() else word.capitalize() for word in words)


def extract_place_from_run_folder(folder_name: str) -> Optional[GalleryPlace]:
    parts = folder_name.split("_")
    if len(parts) < 3:
        return None

    street_idx = next(
        (idx for idx, part in enumerate(parts[2:], start=2) if part == "streets"),
        len(parts),
    )
    place_token = "_".join(parts[2:street_idx]).strip("_")
    if not place_token:
        return None

    try:
        postcode = normalise_postcode_prefix(place_token)
        town_or_city, district = place_parts_for_postcode(postcode)
        return GalleryPlace(postcode, town_or_city, district, True)
    except ValueError:
        place_name = format_place_label(place_token)
        return GalleryPlace(place_name, place_name, "", False)


def derive_gallery_place_from_path(path: Path) -> Optional[GalleryPlace]:
    candidates = [path if path.is_dir() else path.parent, *path.parents]
    for candidate in candidates:
        place = extract_place_from_run_folder(candidate.name)
        if place:
            return place
    return None


def derive_gallery_place_from_inputs(
    input_sources: Sequence[Path],
    input_files: Sequence[Path],
) -> GalleryPlace:
    if len(input_sources) == 1:
        source = input_sources[0]
        place = derive_gallery_place_from_path(source)
        if place:
            return place

    if input_files:
        common_parent = input_files[0].parent
        if all(path.parent == common_parent for path in input_files):
            place = derive_gallery_place_from_path(common_parent)
            if place:
                return place

    raise ValueError(
        "Could not derive a place name from the input path. One of the input path's "
        "parent folders must be named like "
        "20260520_154315_BS8_streets_parks_water_boundary_clip or "
        "20260603_160614_Homerton_streets_parks_water_boundary_clip."
    )


def place_parts_for_postcode(postcode: str) -> tuple[str, str]:
    place_name = get_main_postcode_district_name(postcode)
    parts = [part.strip() for part in place_name.split(",") if part.strip()]
    district = parts[0] if len(parts) > 1 else ""
    town_or_city = parts[-1] if parts else ""
    return town_or_city, district


def make_gallery_heading(postcode: str) -> tuple[str, str]:
    town_or_city, district = place_parts_for_postcode(postcode)

    if town_or_city:
        title = f"The {town_or_city}, {postcode} Roads Gallery"
    else:
        title = f"The {postcode} Roads Gallery"

    subtitle = (
        f"Selected street faces from {district}"
        if district
        else "Selected street faces"
    )
    return title.upper(), subtitle


def make_gallery_heading_for_place(place: GalleryPlace) -> tuple[str, str]:
    if place.is_postcode and place.town_or_city:
        title = f"The {place.town_or_city}, {place.label} Roads Gallery"
    else:
        title = f"The {place.label} Roads Gallery"

    subtitle = (
        f"Selected street faces from {place.district}"
        if place.district
        else "Selected street faces"
    )
    return title.upper(), subtitle


def make_curatorial_note(place_name: str) -> str:
    return (
        f"A curated collection of {place_name} street forms selected for their facial character. "
        "Each road geometry is preserved, then scaled, rotated and placed as a feature. "
        "Street data (c) OpenStreetMap contributors."
    )


def subtitle_for_sort_mode(subtitle: str, sort_by: str | None) -> str:
    mode = normalize_gallery_sort_mode(sort_by)
    if mode == "happiness":
        suffix = "ordered by smile-like happiness"
    elif mode == "sinuosity":
        suffix = "ordered by street sinuosity"
    else:
        suffix = "ordered by facial character"
    return f"{subtitle} - {suffix}" if subtitle else suffix.capitalize()


def filename_slug(value: object) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip().lower())
    return slug.strip("_") or "untitled"


def gallery_output_stem(
    postcode: str,
    town_or_city: str,
    district: str,
    edition: str,
    paper_key: str,
    face_count: int,
    cols: int,
    palette_key: str,
    show_blush: bool,
    ear_mode: str,
    presentation_mode: str,
    show_note: bool,
    street_stroke_multiplier: float,
    road_types: Optional[Sequence[str]] = None,
    forced_street_name: Optional[str] = None,
    single_street_name: Optional[str] = None,
    sort_by: str | None = "happiness",
) -> str:
    selected_road_types = normalize_road_types(road_types)
    road_type_part = (
        "all_road_types"
        if selected_road_types is None
        else "road_types_"
        + "_".join(filename_slug(road_type) for road_type in selected_road_types)
    )
    parts = [
        filename_slug(town_or_city) if town_or_city else None,
        filename_slug(postcode),
        filename_slug(district) if district else None,
        "roads_gallery",
        f"street_{filename_slug(single_street_name)}" if single_street_name else None,
        road_type_part,
        f"sort_{filename_slug(normalize_gallery_sort_mode(sort_by))}",
        f"added_{filename_slug(forced_street_name)}" if forced_street_name else None,
        filename_slug(normalize_edition_key(edition)),
        filename_slug(paper_key),
        f"{face_count}_face" if face_count == 1 else f"{face_count}_faces",
        f"{cols}_cols",
        filename_slug(palette_key),
        f"{filename_slug(normalize_presentation_mode(presentation_mode))}_presentation",
        "blush" if show_blush else "no_blush",
        f"{filename_slug(normalize_ear_mode(ear_mode))}_ears",
        "note" if show_note else "no_note",
        f"stroke_{filename_slug(f'{street_stroke_multiplier:.2f}')}",
    ]
    return "_".join(part for part in parts if part)


def output_svg_path(output: Path, default_stem: str) -> Path:
    if output.suffix.lower() == ".svg":
        output.parent.mkdir(parents=True, exist_ok=True)
        return output
    output.mkdir(parents=True, exist_ok=True)
    return output / f"{default_stem}.svg"


def default_png_path(svg_path: Path, write_png: bool) -> Optional[Path]:
    if not write_png:
        return None
    return svg_path.with_suffix(".png")

def default_pdf_path(svg_path: Path, write_pdf: bool) -> Optional[Path]:
    if not write_pdf:
        return None
    return svg_path.with_suffix(".pdf")


def curation_report_path(svg_path: Path) -> Path:
    return svg_path.with_name(f"{svg_path.stem}_curation_report.csv")


def write_curation_report(specs: Sequence[FaceSpec], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "street_name",
        "assigned_role",
        "happiness_score",
        "smile_curve_score",
        "frown_curve_score",
        "smile_depth_score",
        "frown_depth_score",
        "unhappiness_score",
        "neutral_score",
        "sadness_score",
        "calm_curve_score",
        "gallery_score",
        "confidence",
        "calculated_sinuosity",
        "complexity_score",
        "turn_angle_sum",
        "node_count",
        "angularity",
        "symmetry",
        "transformation_matrix",
        "collision_triggered",
        "scaling_factor_applied",
        "length_percentile",
    ]
    records = []
    for spec in specs:
        metrics = spec.role_choice.metrics
        records.append(
            {
                "street_name": spec.name,
                "assigned_role": spec.role_choice.role,
                "happiness_score": round(happiness_score(spec), 6),
                "smile_curve_score": round(smile_curve_score(spec), 6),
                "frown_curve_score": round(frown_curve_score(spec), 6),
                "smile_depth_score": round(smile_depth_score(spec), 6),
                "frown_depth_score": round(frown_depth_score(spec), 6),
                "unhappiness_score": round(unhappiness_score(spec), 6),
                "neutral_score": round(neutral_score(spec), 6),
                "sadness_score": round(sadness_score(spec), 6),
                "calm_curve_score": round(calm_curve_score(spec), 6),
                "gallery_score": round(gallery_score(spec), 6),
                "confidence": round(role_confidence(spec), 6),
                "calculated_sinuosity": round(metrics.sinuosity, 6),
                "complexity_score": round(metrics.complexity, 6),
                "turn_angle_sum": round(metrics.turn_angle_sum, 6),
                "node_count": metrics.node_count,
                "angularity": round(metrics.angularity, 6),
                "symmetry": round(metrics.symmetry_lr, 6),
                "transformation_matrix": [round(value, 8) for value in spec.transform_matrix],
                "collision_triggered": spec.collision_triggered,
                "scaling_factor_applied": round(spec.scaling_factor_applied, 8),
                "length_percentile": round(spec.length_percentile, 6),
            }
        )

    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            csv_record = dict(record)
            csv_record["transformation_matrix"] = json.dumps(
                csv_record["transformation_matrix"], separators=(",", ":")
            )
            writer.writerow(csv_record)

    out_json = out_csv.with_suffix(".json")
    out_json.write_text(json.dumps(records, indent=2), encoding="utf-8")


def normalize_edition_key(edition: str) -> str:
    return EDITION_ALIASES.get(edition, edition)


def edition_defaults(edition: str) -> dict:
    return EDITION_PRESETS.get(
        normalize_edition_key(edition), EDITION_PRESETS["roads"]
    )


def generate_faces(
    input_sources: Sequence[Path],
    output: Path,
    cols: Optional[int] = None,
    write_png: bool = False,
    write_pdf: bool = False,
    show_blush: Optional[bool] = None,
    ear_mode: Optional[str] = None,
    presentation_mode: Optional[str] = None,
    street_stroke_multiplier: float = 1.18,
    palette_key: str = DEFAULT_PALETTE_KEY,
    edition: str = "roads",
    paper_key: Optional[str] = None,
    target_count: Optional[int] = None,
    show_note: Optional[bool] = None,
    print_title_font_size: Optional[float] = None,
    print_subtitle_font_size: Optional[float] = None,
    curatorial_note_font_size: Optional[float] = None,
    text_font_key: str = DEFAULT_TEXT_FONT_KEY,
    road_types: Optional[Sequence[str]] = None,
    force_street: Optional[str] = None,
    sort_by: str | None = "happiness",
    gallery_context_specs: Optional[Sequence[FaceSpec]] = None,
    write_faces_overlay: bool = True,
) -> Tuple[Path, Optional[Path], Path, List[FaceSpec]]:
    defaults = edition_defaults(edition)
    palette = get_face_palette(palette_key)
    selected_road_types = normalize_road_types(road_types)
    input_files = collect_svg_inputs(input_sources)
    single_svg_mode = (
        len(input_sources) == 1
        and Path(input_sources[0]).is_file()
        and Path(input_sources[0]).suffix.lower() == ".svg"
    )
    place = derive_gallery_place_from_inputs(input_sources, input_files)
    print_title, print_subtitle = make_gallery_heading_for_place(place)
    resolved_sort_by = normalize_gallery_sort_mode(sort_by)
    print_subtitle = subtitle_for_sort_mode(print_subtitle, resolved_sort_by)
    curatorial_note = make_curatorial_note(place.label)
    specs = build_specs(input_files, palette=palette, road_types=selected_road_types)
    if single_svg_mode:
        apply_gallery_context_to_single_spec(
            specs,
            input_files,
            palette,
            selected_road_types,
            gallery_specs=gallery_context_specs,
        )
    forced_search_specs = (
        build_specs(input_files, palette=palette, road_types=None)
        if force_street and force_street.strip() and selected_road_types
        else None
    )
    target = (
        None
        if target_count == 0
        else (defaults["target_count"] if target_count is None else target_count)
    )
    specs, forced_street_note, forced_street_name = curate_specs_with_forced_street(
        specs, target, force_street, forced_search_specs, sort_by=resolved_sort_by
    )
    paper = paper_key or str(defaults["paper"])
    chosen_cols = cols if cols is not None else int(defaults["cols"])
    output_cols = min(chosen_cols, len(specs)) if specs else chosen_cols
    note = bool(defaults["note"]) if show_note is None else show_note
    title = bool(defaults["title"])
    blush = bool(defaults["show_blush"]) if show_blush is None else show_blush
    resolved_ear_mode = normalize_ear_mode(
        str(defaults["ear_mode"]) if ear_mode is None else ear_mode
    )
    resolved_presentation_mode = normalize_presentation_mode(presentation_mode)
    output_stem = gallery_output_stem(
        place.label,
        place.town_or_city,
        place.district,
        edition,
        paper,
        len(specs),
        output_cols,
        palette_key,
        blush,
        resolved_ear_mode,
        resolved_presentation_mode,
        note,
        street_stroke_multiplier,
        selected_road_types,
        forced_street_name,
        specs[0].name if single_svg_mode and len(specs) == 1 else None,
        resolved_sort_by,
    )
    svg_path = output_svg_path(output, output_stem)
    png_path = default_png_path(svg_path, write_png)
    pdf_path = default_pdf_path(svg_path, write_pdf)
    report_path = curation_report_path(svg_path)
    render_grid(
        specs,
        svg_path,
        png_path,
        out_pdf=pdf_path,
        cols=chosen_cols,
        show_blush=blush,
        ear_mode=resolved_ear_mode,
        presentation_mode=resolved_presentation_mode,
        street_stroke_multiplier=street_stroke_multiplier,
        palette=palette,
        paper_key=paper,
        show_note=note,
        show_title=title,
        print_title=print_title,
        print_subtitle=print_subtitle,
        curatorial_note=curatorial_note,
        provenance_area=place.label,
        print_title_font_size=print_title_font_size,
        print_subtitle_font_size=print_subtitle_font_size,
        curatorial_note_font_size=curatorial_note_font_size,
        text_font_key=text_font_key,
        single_svg_mode=single_svg_mode,
    )
    write_curation_report(specs, report_path)
    faces_overlay_path = (
        write_faces_overlay_from_selection(input_files, specs)
        if write_faces_overlay
        else None
    )
    if faces_overlay_path:
        print(f"Wrote faces overlay: {faces_overlay_path}")
    print_forced_street_note(forced_street_note)
    return svg_path, png_path, report_path, specs


def generate_single_faces_batch(
    input_folder: Path,
    output: Path,
    write_png: bool = False,
    write_pdf: bool = False,
    show_blush: Optional[bool] = None,
    ear_mode: Optional[str] = None,
    presentation_mode: Optional[str] = None,
    street_stroke_multiplier: float = 1.18,
    palette_key: str = DEFAULT_PALETTE_KEY,
    edition: str = "roads",
    paper_key: Optional[str] = None,
    show_note: Optional[bool] = None,
    print_title_font_size: Optional[float] = None,
    print_subtitle_font_size: Optional[float] = None,
    curatorial_note_font_size: Optional[float] = None,
    text_font_key: str = DEFAULT_TEXT_FONT_KEY,
    road_types: Optional[Sequence[str]] = None,
    sort_by: str | None = "happiness",
) -> Tuple[List[Path], List[Tuple[str, str]]]:
    """Export every glyph SVG in a folder as an independent single-face print."""
    if not input_folder.is_dir():
        raise ValueError(f"Batch input is not a folder: {input_folder}")
    excluded_names = {"streets.svg", "overlay.svg", "faces_overlay.svg"}
    all_svg_files = sorted(
        path
        for path in input_folder.iterdir()
        if path.is_file() and path.suffix.lower() == ".svg"
    )
    input_files = [path for path in all_svg_files if path.name.lower() not in excluded_names]
    skipped: List[Tuple[str, str]] = [
        (path.name, "aggregate overlay SVG, not an individual street glyph")
        for path in all_svg_files
        if path.name.lower() in excluded_names
    ]
    if not input_files:
        raise ValueError(f"No individual street SVG files found in: {input_folder}")

    palette = get_face_palette(palette_key)
    selected_road_types = normalize_road_types(road_types)
    gallery_specs = build_specs(
        input_files, palette=palette, road_types=selected_road_types
    )
    written: List[Path] = []
    batch_records: List[dict] = []
    output.mkdir(parents=True, exist_ok=True)
    for source in input_files:
        try:
            svg_path, _png_path, _report_path, _specs = generate_faces(
                [source],
                output,
                cols=1,
                write_png=write_png,
                write_pdf=write_pdf,
                show_blush=show_blush,
                ear_mode=ear_mode,
                presentation_mode=presentation_mode,
                street_stroke_multiplier=street_stroke_multiplier,
                palette_key=palette_key,
                edition=edition,
                paper_key=paper_key,
                target_count=1,
                show_note=show_note,
                print_title_font_size=print_title_font_size,
                print_subtitle_font_size=print_subtitle_font_size,
                curatorial_note_font_size=curatorial_note_font_size,
                text_font_key=text_font_key,
                road_types=selected_road_types,
                sort_by=sort_by,
                gallery_context_specs=gallery_specs,
                write_faces_overlay=False,
            )
            written.append(svg_path)
            batch_records.append(
                {"item": source.name, "status": "written", "reason": "", "svg": str(svg_path)}
            )
        except Exception as exc:
            reason = str(exc)
            skipped.append((source.name, reason))
            batch_records.append(
                {"item": source.name, "status": "skipped", "reason": reason, "svg": ""}
            )
    for item, reason in skipped:
        if not any(record["item"] == item for record in batch_records):
            batch_records.append(
                {"item": item, "status": "skipped", "reason": reason, "svg": ""}
            )
    report_path = output / "batch_single_export_report.csv"
    with report_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["item", "status", "reason", "svg"])
        writer.writeheader()
        writer.writerows(batch_records)
    return written, skipped


def launch_ui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Street Face Generator v20")
    root.geometry("900x800")
    root.minsize(780, 720)

    source_mode = tk.StringVar(value="folder")
    source_path = tk.StringVar()
    output_folder = tk.StringVar(value=str(Path.cwd() / "output"))
    cols_var = tk.IntVar(value=0)
    target_count_var = tk.IntVar(value=0)
    force_street_var = tk.StringVar()
    street_stroke_var = tk.DoubleVar(value=1.18)
    print_title_font_size_var = tk.DoubleVar(value=0.0)
    print_subtitle_font_size_var = tk.DoubleVar(value=0.0)
    curatorial_note_font_size_var = tk.DoubleVar(value=0.0)
    png_var = tk.BooleanVar(value=False)
    pdf_var = tk.BooleanVar(value=False)
    blush_var = tk.BooleanVar(value=False)
    road_type_vars = {
        road_type: tk.BooleanVar(value=road_type == "minor") for road_type in ROAD_TYPES
    }
    ear_mode_var = tk.StringVar(value=str(EDITION_PRESETS["roads"]["ear_mode"]))
    presentation_mode_var = tk.StringVar(value="varied")
    sort_by_var = tk.StringVar(value="happiness")
    note_var = tk.BooleanVar(value=True)
    edition_var = tk.StringVar(value="roads")
    paper_var = tk.StringVar(value=str(EDITION_PRESETS["roads"]["paper"]))
    palette_labels = {palette.label: key for key, palette in FACE_PALETTES.items()}
    palette_var = tk.StringVar(value=FACE_PALETTES[DEFAULT_PALETTE_KEY].label)
    font_labels = {label: key for key, (label, _stack) in TEXT_FONT_PRESETS.items()}
    text_font_var = tk.StringVar(value=TEXT_FONT_PRESETS[DEFAULT_TEXT_FONT_KEY][0])
    status_var = tk.StringVar(value="Choose a glyph folder or SVG.")

    def apply_font_size_defaults(*_args: object) -> None:
        paper = PAPER_PRESETS.get(paper_var.get(), PAPER_PRESETS["a1"])
        height = float(paper["height"])
        print_title_font_size_var.set(round(default_print_title_font_size(height), 1))
        print_subtitle_font_size_var.set(
            round(default_print_subtitle_font_size(height), 1)
        )
        curatorial_note_font_size_var.set(
            round(default_curatorial_note_font_size(height), 1)
        )

    def apply_edition_defaults(*_args: object) -> None:
        defaults = edition_defaults(edition_var.get())
        paper_var.set(str(defaults["paper"]))
        cols_var.set(int(defaults["cols"]))
        target_count_var.set(int(defaults["target_count"] or 0))
        note_var.set(bool(defaults["note"]))
        blush_var.set(bool(defaults["show_blush"]))
        ear_mode_var.set(str(defaults["ear_mode"]))
        for road_type, var in road_type_vars.items():
            var.set(road_type == "minor")

    def choose_source() -> None:
        if source_mode.get() == "file":
            selected = filedialog.askopenfilename(
                title="Choose street SVG",
                filetypes=[("SVG files", "*.svg"), ("All files", "*.*")],
            )
        elif source_mode.get() in {"folder", "batch"}:
            title = (
                "Choose folder of individual street SVGs for batch export"
                if source_mode.get() == "batch"
                else "Choose folder of street SVGs"
            )
            selected = filedialog.askdirectory(title=title)
        if selected:
            source_path.set(selected)

    def choose_output() -> None:
        selected = filedialog.askdirectory(title="Choose output folder")
        if selected:
            output_folder.set(selected)

    def generate() -> None:
        try:
            if not source_path.get():
                raise ValueError("Choose an input SVG or folder first.")
            if not output_folder.get():
                raise ValueError("Choose an output folder first.")
            status_var.set("Generating...")
            root.update_idletasks()
            selected_road_types = [
                road_type for road_type, var in road_type_vars.items() if var.get()
            ]
            if not selected_road_types:
                raise ValueError("Choose at least one road type.")
            if source_mode.get() == "batch":
                written, skipped = generate_single_faces_batch(
                    Path(source_path.get()),
                    Path(output_folder.get()),
                    write_png=png_var.get(),
                    write_pdf=pdf_var.get(),
                    show_blush=blush_var.get(),
                    ear_mode=ear_mode_var.get(),
                    presentation_mode=presentation_mode_var.get(),
                    street_stroke_multiplier=float(street_stroke_var.get()),
                    palette_key=palette_labels.get(
                        palette_var.get(), DEFAULT_PALETTE_KEY
                    ),
                    edition=edition_var.get(),
                    paper_key=paper_var.get(),
                    show_note=note_var.get(),
                    print_title_font_size=float(print_title_font_size_var.get()),
                    print_subtitle_font_size=float(print_subtitle_font_size_var.get()),
                    curatorial_note_font_size=float(
                        curatorial_note_font_size_var.get()
                    ),
                    text_font_key=font_labels.get(
                        text_font_var.get(), DEFAULT_TEXT_FONT_KEY
                    ),
                    road_types=selected_road_types,
                    sort_by=sort_by_var.get(),
                )
                skipped_text = "\n".join(
                    f"- {item}: {reason}" for item, reason in skipped
                )
                message = (
                    f"Wrote {len(written)} single-face artwork(s).\n"
                    f"Skipped: {len(skipped)}.\n"
                    f"Output: {output_folder.get()}\n"
                    f"Report: {Path(output_folder.get()) / 'batch_single_export_report.csv'}"
                )
                if skipped_text:
                    message += f"\n\nSkipped items:\n{skipped_text}"
                status_var.set(
                    f"Batch done: {len(written)} written, {len(skipped)} skipped"
                )
                messagebox.showinfo("Batch single-face export", message)
                return

            input_sources = [Path(source_path.get())]
            svg_path, png_path, report_path, specs = generate_faces(
                input_sources,
                Path(output_folder.get()),
                cols=int(cols_var.get()) or None,
                write_png=png_var.get(),
                write_pdf=pdf_var.get(),
                show_blush=blush_var.get(),
                ear_mode=ear_mode_var.get(),
                presentation_mode=presentation_mode_var.get(),
                street_stroke_multiplier=float(street_stroke_var.get()),
                palette_key=palette_labels.get(palette_var.get(), DEFAULT_PALETTE_KEY),
                edition=edition_var.get(),
                paper_key=paper_var.get(),
                target_count=int(target_count_var.get()) or None,
                show_note=note_var.get(),
                print_title_font_size=float(print_title_font_size_var.get()),
                print_subtitle_font_size=float(print_subtitle_font_size_var.get()),
                curatorial_note_font_size=float(curatorial_note_font_size_var.get()),
                text_font_key=font_labels.get(
                    text_font_var.get(), DEFAULT_TEXT_FONT_KEY
                ),
                road_types=selected_road_types,
                force_street=force_street_var.get(),
                sort_by=sort_by_var.get(),
            )
            png_note = f"\nPNG: {png_path}" if png_path else ""
            pdf_path = svg_path.with_suffix(".pdf") if pdf_var.get() else None
            pdf_note = f"\nPDF: {pdf_path}" if pdf_path else ""
            report_note = f"\nReport: {report_path}"
            faces_overlay_note = ""
            source = Path(source_path.get())
            glyph_parent = (
                source
                if source.is_dir() and source.name.lower() == "glyphs"
                else source.parent if source.parent.name.lower() == "glyphs" else None
            )
            if glyph_parent is not None:
                faces_overlay_path = glyph_parent.parent / "faces_overlay.svg"
                if faces_overlay_path.is_file():
                    faces_overlay_note = f"\nFaces overlay: {faces_overlay_path}"
            status_var.set(f"Done: {svg_path}")
            messagebox.showinfo(
                "Street faces generated",
                f"Wrote {len(specs)} face{'s' if len(specs) != 1 else ''}.\nSVG: {svg_path}{png_note}{pdf_note}{report_note}{faces_overlay_note}",
            )
        except Exception as exc:
            status_var.set("Generation failed.")
            messagebox.showerror("Could not generate street faces", str(exc))

    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    frame = ttk.Frame(root, padding=20)
    frame.grid(row=0, column=0, sticky="nsew")
    frame.columnconfigure(1, weight=1)

    ttk.Label(frame, text="Street Face Generator", font=("Segoe UI", 18, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 18)
    )

    ttk.Label(frame, text="Input").grid(row=1, column=0, sticky="w", pady=7)
    mode_frame = ttk.Frame(frame)
    mode_frame.grid(row=1, column=1, sticky="w", pady=7)
    ttk.Radiobutton(
        mode_frame, text="Existing SVG", value="file", variable=source_mode
    ).grid(row=0, column=0, padx=(0, 16))
    ttk.Radiobutton(
        mode_frame, text="Glyph Folder", value="folder", variable=source_mode
    ).grid(row=0, column=1, padx=(0, 16))
    ttk.Radiobutton(
        mode_frame,
        text="Batch Single SVGs",
        value="batch",
        variable=source_mode,
    ).grid(row=0, column=2)

    ttk.Entry(frame, textvariable=source_path).grid(
        row=2, column=1, sticky="ew", pady=7
    )
    ttk.Button(frame, text="Choose Input", command=choose_source).grid(
        row=2, column=2, sticky="ew", padx=(10, 0), pady=7
    )

    ttk.Label(frame, text="Output Folder").grid(row=3, column=0, sticky="w", pady=7)
    ttk.Entry(frame, textvariable=output_folder).grid(
        row=3, column=1, sticky="ew", pady=7
    )
    ttk.Button(frame, text="Choose Output", command=choose_output).grid(
        row=3, column=2, sticky="ew", padx=(10, 0), pady=7
    )

    ttk.Label(frame, text="Edition").grid(row=4, column=0, sticky="w", pady=7)
    edition_combo = ttk.Combobox(
        frame,
        textvariable=edition_var,
        values=sorted(EDITION_PRESETS),
        state="readonly",
        width=24,
    )
    edition_combo.grid(row=4, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Paper").grid(row=5, column=0, sticky="w", pady=7)
    paper_combo = ttk.Combobox(
        frame,
        textvariable=paper_var,
        values=sorted(PAPER_PRESETS),
        state="readonly",
        width=24,
    )
    paper_combo.grid(row=5, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Columns").grid(row=6, column=0, sticky="w", pady=7)
    ttk.Spinbox(frame, from_=0, to=18, textvariable=cols_var, width=8).grid(
        row=6, column=1, sticky="w", pady=7
    )

    ttk.Label(frame, text="Target Count").grid(row=7, column=0, sticky="w", pady=7)
    ttk.Spinbox(frame, from_=0, to=300, textvariable=target_count_var, width=8).grid(
        row=7, column=1, sticky="w", pady=7
    )

    ttk.Label(frame, text="Add Street").grid(row=8, column=0, sticky="w", pady=7)
    ttk.Entry(frame, textvariable=force_street_var).grid(
        row=8, column=1, sticky="ew", pady=7
    )

    ttk.Label(frame, text="Street Stroke").grid(row=9, column=0, sticky="w", pady=7)
    ttk.Spinbox(
        frame,
        from_=0.8,
        to=2.0,
        increment=0.05,
        textvariable=street_stroke_var,
        width=8,
    ).grid(row=9, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Title Font Size").grid(row=10, column=0, sticky="w", pady=7)
    ttk.Spinbox(
        frame,
        from_=1,
        to=96,
        increment=0.5,
        textvariable=print_title_font_size_var,
        width=8,
    ).grid(row=10, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Subtitle Font Size").grid(
        row=11, column=0, sticky="w", pady=7
    )
    ttk.Spinbox(
        frame,
        from_=1,
        to=96,
        increment=0.5,
        textvariable=print_subtitle_font_size_var,
        width=8,
    ).grid(row=11, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Note Font Size").grid(row=12, column=0, sticky="w", pady=7)
    ttk.Spinbox(
        frame,
        from_=1,
        to=96,
        increment=0.5,
        textvariable=curatorial_note_font_size_var,
        width=8,
    ).grid(row=12, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Text Font").grid(row=13, column=0, sticky="w", pady=7)
    font_combo = ttk.Combobox(
        frame,
        textvariable=text_font_var,
        values=[label for label, _stack in TEXT_FONT_PRESETS.values()],
        state="readonly",
        width=24,
    )
    font_combo.grid(row=13, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Palette").grid(row=14, column=0, sticky="w", pady=7)
    palette_combo = ttk.Combobox(
        frame,
        textvariable=palette_var,
        values=[palette.label for palette in FACE_PALETTES.values()],
        state="readonly",
        width=24,
    )
    palette_combo.grid(row=14, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Ears").grid(row=15, column=0, sticky="w", pady=7)
    ear_combo = ttk.Combobox(
        frame,
        textvariable=ear_mode_var,
        values=list(EAR_MODES),
        state="readonly",
        width=24,
    )
    ear_combo.grid(row=15, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Presentation").grid(row=16, column=0, sticky="w", pady=7)
    presentation_combo = ttk.Combobox(
        frame,
        textvariable=presentation_mode_var,
        values=list(PRESENTATION_MODES),
        state="readonly",
        width=24,
    )
    presentation_combo.grid(row=16, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Order By").grid(row=17, column=0, sticky="w", pady=7)
    sort_combo = ttk.Combobox(
        frame,
        textvariable=sort_by_var,
        values=list(GALLERY_SORT_MODES),
        state="readonly",
        width=24,
    )
    sort_combo.grid(row=17, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="Road Types").grid(row=18, column=0, sticky="w", pady=7)
    road_type_frame = ttk.Frame(frame)
    road_type_frame.grid(row=18, column=1, sticky="w", pady=7)
    for idx, road_type in enumerate(ROAD_TYPES):
        ttk.Checkbutton(
            road_type_frame,
            text=road_type.capitalize(),
            variable=road_type_vars[road_type],
        ).grid(row=0, column=idx, padx=(0, 18))

    options = ttk.Frame(frame)
    options.grid(row=19, column=1, sticky="w", pady=12)
    ttk.Checkbutton(options, text="Write PNG preview", variable=png_var).grid(
        row=0, column=0, padx=(0, 18)
    )
    ttk.Checkbutton(options, text="Write print PDF", variable=pdf_var).grid(
        row=0, column=1, padx=(0, 18)
    )
    ttk.Checkbutton(options, text="Blush", variable=blush_var).grid(
        row=0, column=2, padx=(0, 18)
    )
    ttk.Checkbutton(options, text="Curatorial note", variable=note_var).grid(
        row=0, column=3
    )
    swatch_frame = ttk.Frame(frame)
    swatch_frame.grid(row=20, column=0, columnspan=3, sticky="ew", pady=(8, 18))
    swatches = []

    def refresh_swatches(*_args: object) -> None:
        current = get_face_palette(
            palette_labels.get(palette_var.get(), DEFAULT_PALETTE_KEY)
        )
        values = [
            ("background", current.background),
            ("features", current.meta),
            ("street 1", current.streets[0]),
            ("street 2", current.streets[1]),
            ("street 3", current.streets[2]),
            ("blush", current.blush),
        ]
        for idx, (label, color) in enumerate(values):
            if idx >= len(swatches):
                swatch = tk.Label(swatch_frame, padx=8, pady=6)
                swatch.grid(row=0, column=idx, padx=(0, 8), sticky="w")
                swatches.append(swatch)
            foreground = (
                "#ffffff"
                if label == "features" or idx in {2, 3, 4}
                else current.feature
            )
            swatches[idx].configure(text=f"  {label}  ", bg=color, fg=foreground)

    edition_var.trace_add("write", apply_edition_defaults)
    paper_var.trace_add("write", apply_font_size_defaults)
    palette_var.trace_add("write", refresh_swatches)
    apply_edition_defaults()
    refresh_swatches()

    ttk.Button(frame, text="Generate Faces", command=generate).grid(
        row=21, column=1, sticky="w", pady=(8, 14)
    )
    ttk.Label(
        frame, textvariable=status_var, foreground=META_CHARCOAL, wraplength=700
    ).grid(row=22, column=0, columnspan=3, sticky="ew")

    root.mainloop()


def print_report(specs: Sequence[FaceSpec]) -> None:
    for spec in specs:
        m = spec.role_choice.metrics
        print(
            f"{spec.name}: role={spec.role_choice.role}, rotation={spec.role_choice.rotation:+.1f} deg, "
            f"happiness={happiness_score(spec):.3f}, smile={smile_curve_score(spec):.3f}, "
            f"depth={smile_depth_score(spec):.3f}, calm={calm_curve_score(spec):.3f}, "
            f"frown={frown_curve_score(spec):.3f}, "
            f"gallery={gallery_score(spec):.3f}, "
            f"confidence={role_confidence(spec):.3f}, sinuosity={m.sinuosity:.3f}, "
            f"angularity={m.angularity:.1f}, top={spec.report[:3]}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate curated geometry-locked street-face print editions."
    )
    parser.add_argument(
        "inputs", nargs="*", type=Path, help="Input SVG street files or folders."
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("street_faces.svg"),
        help="Output SVG, or an output folder.",
    )
    parser.add_argument(
        "--png", type=Path, default=None, help="Optional PNG preview output."
    )
    parser.add_argument(
        "--pdf", type=Path, default=None, help="Optional print-ready PDF output."
    )
    parser.add_argument(
        "--edition",
        choices=sorted(set(EDITION_PRESETS) | set(EDITION_ALIASES)),
        default="roads",
        help="Print edition preset.",
    )
    parser.add_argument(
        "--paper",
        choices=sorted(PAPER_PRESETS),
        default=None,
        help="Override the edition paper size.",
    )
    parser.add_argument(
        "--target-count",
        type=int,
        default=None,
        help="Override the edition face count. Use 0 for all faces.",
    )
    parser.add_argument(
        "--cols", type=int, default=None, help="Override the edition grid columns."
    )
    parser.add_argument(
        "--street-stroke",
        type=float,
        default=1.18,
        help="Street stroke multiplier relative to face ink. Default: 1.18.",
    )
    parser.add_argument(
        "--title-font-size",
        type=float,
        default=None,
        help="Override the main print title font size in SVG units.",
    )
    parser.add_argument(
        "--subtitle-font-size",
        type=float,
        default=None,
        help="Override the print subtitle font size in SVG units.",
    )
    parser.add_argument(
        "--note-font-size",
        type=float,
        default=None,
        help="Override the curatorial note font size in SVG units.",
    )
    parser.add_argument(
        "--text-font",
        choices=sorted(TEXT_FONT_PRESETS),
        default=DEFAULT_TEXT_FONT_KEY,
        help="Text font preset for titles, labels and notes.",
    )
    parser.add_argument(
        "--palette",
        choices=sorted(FACE_PALETTES),
        default=DEFAULT_PALETTE_KEY,
        help="Face palette.",
    )
    parser.add_argument(
        "--blush",
        dest="show_blush",
        action="store_true",
        default=None,
        help="Force blush on.",
    )
    parser.add_argument(
        "--no-blush", dest="show_blush", action="store_false", help="Force blush off."
    )
    parser.add_argument(
        "--ear-mode",
        choices=EAR_MODES,
        default=None,
        help="Ear drawing mode: none, varied, one-stroke, or two-stroke.",
    )
    parser.add_argument(
        "--presentation",
        choices=PRESENTATION_MODES,
        default="varied",
        help="Face presentation mode: varied, neutral, feminine, or masculine.",
    )
    parser.add_argument(
        "--sort-by",
        choices=GALLERY_SORT_MODES,
        default="happiness",
        help="Gallery ordering: happiness, sinuosity, or gallery. Default: happiness.",
    )
    parser.add_argument(
        "--road-types",
        nargs="+",
        choices=("all", *ROAD_TYPES),
        default=["minor"],
        help=(
            "Road classes to include from classified streets.svg files. "
            "Use all, or one or more of: major medium minor. Default: minor."
        ),
    )
    overlay_group = parser.add_argument_group(
        "overlay creation",
        (
            "Create the step-09 robust overlay first, then generate street faces "
            "from that overlay.svg."
        ),
    )
    overlay_group.add_argument(
        "--overlay-area",
        help=(
            "Postcode prefix or location stem to pass to "
            "09_create_street_overlay_parks_water_streets_updated_v2.py. "
            "If the only positional input is not an existing file/folder, v20 "
            "treats it as this area selector."
        ),
    )
    overlay_group.add_argument("--overlay-street-csv", type=Path)
    overlay_group.add_argument("--overlay-street-column", default="street")
    overlay_group.add_argument(
        "--overlay-output-root", default=DEFAULT_OVERLAY_OUTPUT_ROOT
    )
    overlay_group.add_argument("--overlay-place-name")
    overlay_group.add_argument("--overlay-boundary-geojson-path", type=Path)
    overlay_group.add_argument("--overlay-no-boundary-geojson", action="store_true")
    overlay_group.add_argument("--overlay-osm-snapshot-date")
    overlay_group.add_argument(
        "--overlay-scale-mode", choices=("zoom", "fit"), default="zoom"
    )
    overlay_group.add_argument("--overlay-zoom", type=float, default=17.0)
    overlay_group.set_defaults(overlay_clip_to_boundary=None)
    overlay_group.add_argument(
        "--overlay-clip-to-boundary",
        dest="overlay_clip_to_boundary",
        action="store_true",
    )
    overlay_group.add_argument(
        "--overlay-no-clip-to-boundary",
        dest="overlay_clip_to_boundary",
        action="store_false",
    )
    overlay_group.add_argument("--overlay-refine-discontinuous", action="store_true")
    overlay_group.add_argument("--overlay-frame-margin-km", type=float, default=0.5)
    overlay_group.add_argument(
        "--overlay-frame-center-radius-px", type=float, default=50.0
    )
    overlay_group.add_argument("--overlay-frame-center-fill", default="#ff0000")
    overlay_group.add_argument(
        "--overlay-same-street-gap-fill-max-m", type=float, default=30.0
    )
    overlay_group.add_argument(
        "--overlay-royal-mail-source-dir",
        default=DEFAULT_OVERLAY_ROYAL_MAIL_SOURCE_DIR,
    )
    overlay_group.add_argument(
        "--overlay-partial-match-min-address-spread-m", type=float, default=120.0
    )
    overlay_group.add_argument(
        "--overlay-partial-match-short-ratio", type=float, default=0.78
    )
    overlay_group.add_argument(
        "--overlay-partial-match-absolute-gap-m", type=float, default=50.0
    )
    overlay_group.add_argument(
        "--overlay-auto-supplement",
        action="store_true",
        help="Run the capped direct named-way supplement pass for suspicious streets.",
    )
    overlay_group.add_argument("--overlay-max-auto-supplements", type=int, default=12)
    overlay_group.add_argument(
        "--overlay-supplement-street",
        action="append",
        default=[],
        help="Force a direct named-way supplement. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--force-street",
        default=None,
        help=(
            "Ensure this street appears in the curated set. If it is not already "
            "selected, it replaces the final selected street."
        ),
    )
    parser.add_argument(
        "--ears",
        dest="ear_mode",
        action="store_const",
        const="two-stroke",
        help="Legacy shortcut for --ear-mode two-stroke.",
    )
    parser.add_argument(
        "--no-ears",
        dest="ear_mode",
        action="store_const",
        const="none",
        help="Legacy shortcut for --ear-mode none.",
    )
    parser.add_argument(
        "--no-note", action="store_true", help="Remove the curatorial note."
    )
    parser.add_argument("--ui", action="store_true", help="Open the desktop UI.")
    parser.add_argument(
        "--batch-single-folder",
        type=Path,
        help="Export every individual street SVG in this folder as a single-face artwork.",
    )
    parser.add_argument(
        "--batch-png", action="store_true", help="Write PNG files during batch-single export."
    )
    parser.add_argument(
        "--batch-pdf", action="store_true", help="Write PDF files during batch-single export."
    )
    parser.add_argument(
        "--signature-id",
        default=None,
        help=(
            "Private ID/key for the v20 provenance microdot seal. If omitted, "
            f"uses the {SIGNATURE_ENV_VAR} environment variable or the built-in placeholder."
        ),
    )
    args = parser.parse_args()

    if args.signature_id:
        os.environ[SIGNATURE_ENV_VAR] = args.signature_id

    if args.batch_single_folder:
        batch_output = args.output
        if batch_output.suffix:
            batch_output = batch_output.parent / batch_output.stem
        written, skipped = generate_single_faces_batch(
            args.batch_single_folder,
            batch_output,
            write_png=args.batch_png,
            write_pdf=args.batch_pdf,
            show_blush=args.show_blush,
            ear_mode=args.ear_mode,
            presentation_mode=args.presentation,
            street_stroke_multiplier=args.street_stroke,
            palette_key=args.palette,
            edition=args.edition,
            paper_key=args.paper,
            show_note=not args.no_note,
            print_title_font_size=args.title_font_size,
            print_subtitle_font_size=args.subtitle_font_size,
            curatorial_note_font_size=args.note_font_size,
            text_font_key=args.text_font,
            road_types=args.road_types,
            sort_by=args.sort_by,
        )
        print(f"Batch single-face exports written: {len(written)}")
        print(f"Batch single-face items skipped: {len(skipped)}")
        for item, reason in skipped:
            print(f"Skipped {item}: {reason}")
        print(f"Batch report: {batch_output / 'batch_single_export_report.csv'}")
        return

    area_selector = args.overlay_area or _overlay_area_from_inputs(args.inputs)
    if args.ui or (not args.inputs and not area_selector):
        launch_ui()
        return

    overlay_summary = None
    if area_selector:
        overlay_svg, overlay_summary = create_overlay_svg_from_area(args, area_selector)
        input_sources = [overlay_svg]
    else:
        input_sources = args.inputs

    input_files = collect_svg_inputs(input_sources)
    single_svg_mode = (
        len(input_sources) == 1
        and Path(input_sources[0]).is_file()
        and Path(input_sources[0]).suffix.lower() == ".svg"
    )
    selected_road_types = normalize_road_types(args.road_types)
    place = derive_gallery_place_from_inputs(input_sources, input_files)
    print_title, print_subtitle = make_gallery_heading_for_place(place)
    resolved_sort_by = normalize_gallery_sort_mode(args.sort_by)
    print_subtitle = subtitle_for_sort_mode(print_subtitle, resolved_sort_by)
    curatorial_note = make_curatorial_note(place.label)
    palette = get_face_palette(args.palette)
    specs = build_specs(input_files, palette=palette, road_types=selected_road_types)
    if single_svg_mode:
        apply_gallery_context_to_single_spec(
            specs, input_files, palette, selected_road_types
        )
    forced_search_specs = (
        build_specs(input_files, palette=palette, road_types=None)
        if args.force_street and args.force_street.strip() and selected_road_types
        else None
    )
    defaults = edition_defaults(args.edition)
    target_count = (
        None
        if args.target_count == 0
        else (
            args.target_count
            if args.target_count is not None
            else defaults["target_count"]
        )
    )
    specs, forced_street_note, forced_street_name = curate_specs_with_forced_street(
        specs, target_count, args.force_street, forced_search_specs, sort_by=resolved_sort_by
    )
    paper_key = args.paper or str(defaults["paper"])
    cols = args.cols if args.cols is not None else int(defaults["cols"])
    output_cols = min(cols, len(specs)) if specs else cols
    show_blush = (
        bool(defaults["show_blush"]) if args.show_blush is None else args.show_blush
    )
    ear_mode = normalize_ear_mode(
        str(defaults["ear_mode"]) if args.ear_mode is None else args.ear_mode
    )
    presentation_mode = normalize_presentation_mode(args.presentation)
    show_title = bool(defaults["title"])
    output_stem = gallery_output_stem(
        place.label,
        place.town_or_city,
        place.district,
        args.edition,
        paper_key,
        len(specs),
        output_cols,
        args.palette,
        show_blush,
        ear_mode,
        presentation_mode,
        not args.no_note,
        args.street_stroke,
        selected_road_types,
        forced_street_name,
        specs[0].name if single_svg_mode and len(specs) == 1 else None,
        resolved_sort_by,
    )
    svg_path = output_svg_path(args.output, output_stem)
    report_path = curation_report_path(svg_path)
    render_grid(
        specs,
        svg_path,
        args.png,
        out_pdf=args.pdf,
        cols=cols,
        show_blush=show_blush,
        ear_mode=ear_mode,
        presentation_mode=presentation_mode,
        street_stroke_multiplier=args.street_stroke,
        palette=palette,
        paper_key=paper_key,
        show_note=not args.no_note,
        show_title=show_title,
        print_title=print_title,
        print_subtitle=print_subtitle,
        curatorial_note=curatorial_note,
        provenance_area=place.label,
        print_title_font_size=args.title_font_size,
        print_subtitle_font_size=args.subtitle_font_size,
        curatorial_note_font_size=args.note_font_size,
        text_font_key=args.text_font,
        single_svg_mode=single_svg_mode,
    )
    write_curation_report(specs, report_path)
    faces_overlay_path = write_faces_overlay_from_selection(input_files, specs)
    print_forced_street_note(forced_street_note)
    if faces_overlay_path:
        print(f"Wrote faces overlay: {faces_overlay_path}")
    if overlay_summary:
        print(f"Created overlay SVG: {overlay_summary['final_overlay_svg']}")
        print(f"Overlay output: {overlay_summary['final_output_dir']}")
        print(
            "Overlay partial-match suspects: "
            f"{overlay_summary['partial_match_suspect_count']}"
        )
        print(
            "Overlay supplemented streets: "
            f"{overlay_summary['supplemented_street_count']}"
        )
    print(f"Wrote SVG: {svg_path}")
    if args.png:
        print(f"Wrote PNG: {args.png}")
    if args.pdf:
        print(f"Wrote PDF: {args.pdf}")
    print(f"Wrote report: {report_path}")
    print(
        f"Edition: {normalize_edition_key(args.edition)}, paper: {paper_key.upper()}, faces: {len(specs)}, columns: {output_cols}"
    )
    print(f"Heading: {print_title}")
    print(f"Subtitle: {print_subtitle}")
    print_report(specs)


if __name__ == "__main__":
    main()
