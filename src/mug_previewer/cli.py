from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .config import load_settings
from .datasets.discovery import discover_datasets
from .datasets.loader import DatasetLoadError, load_dataset
from .datasets.validation import validate_dataset
from .rendering.context_map import ContextRenderError, render_context_map_result
from .rendering.face import FaceRenderError, FaceRenderOptions, render_face


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mug-previewer")
    parser.add_argument("--dataset-root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    datasets = commands.add_parser("datasets").add_subparsers(dest="operation", required=True)
    datasets.add_parser("list")
    dataset = commands.add_parser("dataset").add_subparsers(dest="operation", required=True)
    for name in ("inspect", "validate"):
        child = dataset.add_parser(name)
        child.add_argument("path", type=Path)
    streets = dataset.add_parser("streets")
    streets.add_argument("path", type=Path)
    streets.add_argument("--search", default="")
    streets.add_argument("--limit", type=int, default=25)
    render = commands.add_parser("render").add_subparsers(dest="operation", required=True)
    face = render.add_parser("face")
    face.add_argument("--dataset", type=Path, required=True)
    face.add_argument("--street-id", required=True)
    face.add_argument("--output", type=Path, required=True)
    face.add_argument("--area", help="Display-area text; defaults to the dataset display name.")
    context = render.add_parser("context")
    context.add_argument("--dataset", type=Path, required=True)
    context.add_argument("--street-id", required=True)
    context.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "datasets":
        root = load_settings(dataset_root=args.dataset_root).dataset_root
        if root is None:
            print("No dataset root is configured. Set MUG_PREVIEWER_DATASET_ROOT or pass --dataset-root.")
            return 2
        found = discover_datasets(root)
        print(f"Datasets found: {len(found)}")
        for index, item in enumerate(found, 1):
            print(f"\n{index}. {item.dataset.display_name}\n   {item.dataset.id}")
        return 0

    if args.command == "render":
        try:
            data = load_dataset(args.dataset)
        except DatasetLoadError as error:
            print(f"Dataset error: {error}")
            return 2
        street = data.get_street(args.street_id)
        if street is None:
            print(f"Street not found: {args.street_id}")
            return 2
        try:
            if args.operation == "face":
                image = render_face(street, FaceRenderOptions(area=args.area if args.area is not None else data.display_name))
            else:
                context_result = render_context_map_result(data, street)
                image = context_result.image
        except (FaceRenderError, ContextRenderError) as error:
            print(f"Render error: {error}")
            return 2
        args.output.parent.mkdir(parents=True, exist_ok=True)
        image.save(args.output, format="PNG")
        if args.operation == "context":
            print(
                f"Rendered context: {street.id} {street.display_name}\nOutput: {args.output}\n"
                f"Size: {image.width}x{image.height}\nFraming: {context_result.framing_mode}\n"
                f"Final context width: {context_result.final_context_width_m}"
            )
        else:
            print(f"Rendered face: {street.id} {street.display_name}\nOutput: {args.output}\nSize: {image.width}x{image.height}")
        return 0

    if args.operation == "validate":
        result = validate_dataset(args.path)
        print(f"Valid: {'yes' if result.valid else 'no'}")
        for message in result.errors:
            print(f"Error: {message}")
        for message in result.warnings:
            print(f"Warning: {message}")
        return 0 if result.valid else 2
    try:
        data = load_dataset(args.path)
    except DatasetLoadError as error:
        print(f"Dataset error: {error}")
        return 2
    if args.operation == "inspect":
        print(f"Dataset\n-------\nName: {data.display_name}\nFormat: {data.format_name}\nPath: {data.path}")
        print(f"\nStreet data\n-----------\nIndex rows: {data.index_row_count}\nUsable streets: {len(data.streets)}")
        print(f"Glyphs resolved: {len(data.streets)}\nContext maps resolved: {sum(item.context_path is not None for item in data.streets)}")
        print(f"\nLayers\n------\nStreets: {'yes' if data.paths.source_streets_path else 'no'}\nParks: {'yes' if data.capabilities.parks_layer else 'no'}\nWater: {'yes' if data.capabilities.water_layer else 'no'}\nBoundary: {'yes' if data.capabilities.boundary_layer else 'no'}")
        print(f"\nMetric framing\n--------------\nAvailable: {'yes' if data.capabilities.metric_context_framing else 'no'}")
        for key, label in (("bbox_span_p50_m", "P50 bbox span"), ("bbox_span_p75_m", "P75 bbox span"), ("bbox_span_p90_m", "P90 bbox span"), ("bbox_span_p95_m", "P95 bbox span"), ("recommended_context_width_m", "Recommended context width")):
            if key in data.statistics.context_scale:
                print(f"{label}: {data.statistics.context_scale[key]}")
        for message in data.warnings:
            print(f"Warning: {message}")
        return 0
    results = data.find_streets(args.search) if args.search else list(data.streets)
    print(f"Streets: {len(results)} matching\nID      Street\n------  ------")
    for item in results[:max(0, args.limit)]:
        print(f"{item.id:<6}  {item.display_name}")
    return 0
