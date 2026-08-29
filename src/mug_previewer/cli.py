from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Sequence

from .config import load_settings
from .datasets.discovery import discover_datasets
from .datasets.loader import DatasetLoadError, load_dataset
from .datasets.validation import validate_dataset
from .rendering.artwork import WrapRenderError, WrapRenderOptions, render_wrap_result
from .rendering.context_map import ContextRenderError, render_context_map_result
from .rendering.face import FaceRenderError, FaceRenderOptions, render_face


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mug-previewer")
    parser.add_argument("--dataset-root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ui", help="Launch the desktop Mug Previewer UI.")
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
    face.add_argument("--manual-overrides", type=Path, default=Path("data") / "manual_overrides.json")
    context = render.add_parser("context")
    context.add_argument("--dataset", type=Path, required=True)
    context.add_argument("--street-id", required=True)
    context.add_argument("--output", type=Path, required=True)
    wrap = render.add_parser("wrap")
    wrap.add_argument("--dataset", type=Path, required=True)
    wrap.add_argument("--street-id", required=True)
    wrap.add_argument("--output", type=Path, required=True)
    wrap.add_argument("--area", help="Display-area text; defaults to the dataset display name.")
    wrap.add_argument("--manual-overrides", type=Path, default=Path("data") / "manual_overrides.json")
    diagnostics = commands.add_parser("diagnostics", help="Run experimental developer diagnostics.")
    diagnostic_front = diagnostics.add_subparsers(dest="operation", required=True).add_parser(
        "front-candidates", help="Score bounded street-feature candidates; does not alter rendering.",
    )
    diagnostic_front.add_argument("--dataset", type=Path, required=True)
    diagnostic_front.add_argument("--street-id", action="append", required=True)
    diagnostic_front.add_argument("--output-dir", type=Path, required=True)
    diagnostic_front.add_argument("--area", help="Display-area text; defaults to the dataset display name.")
    diagnostic_operations = next(action for action in diagnostics._actions if isinstance(action, argparse._SubParsersAction))
    diagnostic_triage = diagnostic_operations.add_parser(
        'production-triage', help='Apply conservative production placement triage and write a manifest.',
    )
    diagnostic_triage.add_argument('--dataset', type=Path, required=True)
    diagnostic_triage.add_argument('--street-id', action='append')
    diagnostic_triage.add_argument('--output-dir', type=Path, required=True)
    diagnostic_triage.add_argument('--area', help='Display-area text; defaults to the dataset display name.')
    diagnostic_review = diagnostic_operations.add_parser(
        'manual-review', help='Generate a human-review queue with comparison artifacts.',
    )
    diagnostic_review.add_argument('--dataset', type=Path, required=True)
    diagnostic_review.add_argument('--street-id', action='append')
    diagnostic_review.add_argument('--output-dir', type=Path, required=True)
    diagnostic_review.add_argument('--area', help='Display-area text; defaults to the dataset display name.')
    diagnostic_review.add_argument('--overrides', type=Path, default=Path("data") / "manual_overrides.json")
    diagnostic_review.add_argument('--include-resolved', action='store_true')
    manual = commands.add_parser("manual-review", help="Resolve MANUAL_REVIEW streets with constrained placements.")
    manual_operations = manual.add_subparsers(dest="operation", required=True)
    for name in ("list", "show", "approve-standard", "approve-transform", "clear"):
        child = manual_operations.add_parser(name)
        child.add_argument("--dataset", type=Path, required=True)
        child.add_argument("--overrides", type=Path, default=Path("data") / "manual_overrides.json")
    manual_operations.choices["show"].add_argument("--street-id", required=True)
    manual_operations.choices["approve-standard"].add_argument("--street-id", required=True)
    manual_operations.choices["approve-standard"].add_argument("--note")
    transform = manual_operations.choices["approve-transform"]
    transform.add_argument("--street-id", required=True)
    transform.add_argument("--orientation", type=int, required=True)
    transform.add_argument("--scale", type=float, required=True)
    transform.add_argument("--y-offset", type=int, required=True)
    transform.add_argument("--note")
    manual_operations.choices["clear"].add_argument("--street-id", required=True)
    manual_operations.choices["list"].add_argument("--include-resolved", action="store_true")
    preview_manual = manual_operations.add_parser("preview")
    preview_manual.add_argument("--dataset", type=Path, required=True)
    preview_manual.add_argument("--overrides", type=Path, default=Path("data") / "manual_overrides.json")
    preview_manual.add_argument("--street-id", required=True)
    preview_manual.add_argument("--orientation", type=int)
    preview_manual.add_argument("--scale", type=float)
    preview_manual.add_argument("--y-offset", type=int)
    preview_manual.add_argument("--load-diagnostic", action="store_true")
    preview_manual.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "ui":
        from .ui.app import launch
        return launch(dataset_root=args.dataset_root)

    if args.command == "manual-review":
        return _run_manual_review_command(args)

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

    if args.command == "diagnostics":
        try:
            data = load_dataset(args.dataset)
        except DatasetLoadError as error:
            print(f"Dataset error: {error}")
            return 2
        from .diagnostics.front_candidates import analyse_front_candidates, write_diagnostic_report

        if args.operation == 'manual-review':
            area = args.area if args.area is not None else data.display_name
            requested = args.street_id or [street.id for street in data.streets]
            streets = [data.get_street(street_id) for street_id in requested]
            missing = [street_id for street_id, street in zip(requested, streets) if street is None]
            if missing:
                print('Street not found: {}'.format(', '.join(missing)))
                return 2
            from .diagnostics.front_candidates import ProductionTriageStatus
            from .diagnostics.manual_review import write_manual_review_batch
            from .manual import ManualOverrideError, load_manual_overrides

            try:
                batch = write_manual_review_batch(
                    data.id, (street for street in streets if street is not None), args.output_dir, area=area,
                    override_store=load_manual_overrides(args.overrides), include_resolved=args.include_resolved,
                )
            except ManualOverrideError as error:
                print(f'Manual override error: {error}')
                return 2
            print('Production triage complete.')
            print()
            print('AUTO_APPROVED')
            print('  STANDARD: {}'.format(batch.auto_standard))
            print('  ADAPTED: {}'.format(batch.auto_adapted))
            print()
            print('MANUALLY_APPROVED')
            print('  STANDARD: {}'.format(batch.manual_standard))
            print('  OVERRIDE: {}'.format(batch.manual_override))
            print()
            print('MANUAL_REVIEW')
            print('  PENDING: {}'.format(batch.pending_manual_review))
            print()
            print('UNRENDERABLE_INPUT')
            print('  {}'.format(batch.counts[ProductionTriageStatus.UNRENDERABLE_INPUT]))
            print()
            print('Manual review artifacts:')
            print(batch.comparison_dir)
            print()
            print('Manual review manifest:')
            print(batch.manifest_path)
            print('Summary:')
            print(batch.summary_path)
            if batch.items:
                print()
                print('Manual review required:')
                for item in batch.items:
                    print('{} / {} / {}'.format(item.dataset, item.street_id, item.street_name))
            return 0

        if args.operation == 'production-triage':
            area = args.area if args.area is not None else data.display_name
            requested = args.street_id or [street.id for street in data.streets]
            streets = [data.get_street(street_id) for street_id in requested]
            missing = [street_id for street_id, street in zip(requested, streets) if street is None]
            if missing:
                print('Street not found: {}'.format(', '.join(missing)))
                return 2
            from .diagnostics.front_candidates import ProductionTriageStatus, select_production_placement

            args.output_dir.mkdir(parents=True, exist_ok=True)
            manifest = args.output_dir / 'production_triage_manifest.csv'
            counts = {status: 0 for status in ProductionTriageStatus}
            records = []
            for street in streets:
                assert street is not None
                decision = select_production_placement(street, area=area)
                counts[decision.triage_status] += 1
                transform = decision.transform
                records.append({
                    'dataset': data.display_name, 'street_id': street.id, 'street_name': street.display_name,
                    'triage_status': decision.triage_status.value, 'diagnostic_class': decision.diagnostic_class or '',
                    'placement': decision.placement_class or '', 'orientation': '' if transform is None else transform.orientation_deg,
                    'scale': '' if transform is None else transform.scale, 'y_offset': '' if transform is None else transform.y_offset,
                    'reason_codes': ';'.join(decision.reason_codes),
                })
            with manifest.open('w', encoding='utf-8', newline='') as handle:
                fields = ('dataset', 'street_id', 'street_name', 'triage_status', 'diagnostic_class', 'placement', 'orientation', 'scale', 'y_offset', 'reason_codes')
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(records)
            automatic = counts[ProductionTriageStatus.AUTO_APPROVED]
            auto_standard = sum(1 for item in records if item.get('placement') == 'STANDARD')
            auto_adapted = sum(1 for item in records if item.get('placement') == 'ADAPTED')
            print(f'Processing complete. Successfully converted {automatic} streets automatically.')
            print('AUTO_APPROVED / STANDARD: {}'.format(auto_standard))
            print('AUTO_APPROVED / ADAPTED: {}'.format(auto_adapted))
            print('MANUAL_REVIEW: {}'.format(counts[ProductionTriageStatus.MANUAL_REVIEW]))
            print('UNRENDERABLE_INPUT: {}'.format(counts[ProductionTriageStatus.UNRENDERABLE_INPUT]))
            print(f'Manifest: {manifest}')
            return 0

        area = args.area if args.area is not None else data.display_name
        missing = [street_id for street_id in args.street_id if data.get_street(street_id) is None]
        if missing:
            print(f"Street not found: {', '.join(missing)}")
            return 2
        print("Street | Current | Best | Orientation | Scale | Y offset | Classification")
        for street_id in args.street_id:
            street = data.get_street(street_id)
            assert street is not None
            analysis = analyse_front_candidates(street, area=area)
            output = args.output_dir / _diagnostic_slug(street.display_name)
            write_diagnostic_report(analysis, output)
            print(
                f"{street.display_name} | {analysis.current.score:.1f} | {analysis.best.score:.1f} | "
                f"{analysis.best.orientation_deg} | {analysis.best.scale:.2f} | {analysis.best.y_offset} | "
                f"{analysis.classification}"
            )
        print(f"Diagnostic output: {args.output_dir}")
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
        area = args.area if hasattr(args, "area") and args.area is not None else data.display_name
        manual_override = None
        if args.operation in {"face", "wrap"}:
            from .manual import ManualOverrideError, approved_override_for_street, load_manual_overrides
            try:
                manual_override = approved_override_for_street(
                    data, street, load_manual_overrides(args.manual_overrides), area=area,
                )
            except ManualOverrideError as error:
                print(f"Manual override error: {error}")
                return 2
        if args.operation == "wrap":
            from .diagnostics.front_candidates import ProductionTriageStatus, select_production_placement

            decision = select_production_placement(street, area=area)
            if decision.triage_status is not ProductionTriageStatus.AUTO_APPROVED and manual_override is None:
                print(
                    f"Production triage: {decision.triage_status.value}; "
                    "final automatic wrap export was not produced."
                )
                print(f"Reason codes: {', '.join(decision.reason_codes)}")
                return 2
        try:
            if args.operation == "face":
                image = render_face(street, FaceRenderOptions(area=area, manual_override=manual_override))
            elif args.operation == "context":
                context_result = render_context_map_result(data, street)
                image = context_result.image
            else:
                wrap_result = render_wrap_result(
                    data,
                    street,
                    WrapRenderOptions(face_options=FaceRenderOptions(area=area, manual_override=manual_override)),
                )
                image = wrap_result.image
        except (FaceRenderError, ContextRenderError, WrapRenderError) as error:
            print(f"Render error: {error}")
            return 2
        args.output.parent.mkdir(parents=True, exist_ok=True)
        image.save(args.output, format="PNG", dpi=(300, 300))
        if args.operation == "context":
            print(
                f"Rendered context: {street.id} {street.display_name}\nOutput: {args.output}\n"
                f"Size: {image.width}x{image.height}\nFraming: {context_result.framing_mode}\n"
                f"Final context width: {context_result.final_context_width_m}"
            )
        elif args.operation == "wrap":
            print(
                f"Rendered wrap: {street.id} {street.display_name}\nOutput: {args.output}\n"
                f"Size: {image.width}x{image.height}\nFront panel: {wrap_result.front_panel.width}x{wrap_result.front_panel.height} "
                f"at {wrap_result.front_placed_box}\nRear panel: {wrap_result.rear_panel.width}x{wrap_result.rear_panel.height} "
                f"at {wrap_result.rear_placed_box}\nFraming: {wrap_result.context.framing_mode}\n"
                f"Final context width: {wrap_result.context.final_context_width_m}"
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


def _diagnostic_slug(value: str) -> str:
    """Use a predictable folder name without adding a diagnostics dependency."""
    return "".join(character.lower() if character.isalnum() else "_" for character in value).strip("_") or "street"


def _run_manual_review_command(args: argparse.Namespace) -> int:
    from .diagnostics.front_candidates import ProductionTriageStatus, select_production_placement
    from .manual import (
        ManualOverrideError, ManualPlacementOverride, clear_manual_override,
        load_manual_overrides, save_manual_override,
    )

    try:
        data = load_dataset(args.dataset)
        store = load_manual_overrides(args.overrides)
    except (DatasetLoadError, ManualOverrideError) as error:
        print(f"Manual-review error: {error}")
        return 2

    if args.operation == "list":
        rows = []
        for street in data.streets:
            decision = select_production_placement(street, area=data.display_name)
            if decision.triage_status is not ProductionTriageStatus.MANUAL_REVIEW:
                continue
            override = store.get(data.id, street.id)
            if override is not None and override.approved and not args.include_resolved:
                continue
            rows.append((street, override, decision))
        print(f"Manual review queue: {len(rows)}")
        for street, override, _decision in rows:
            status = "pending" if override is None else override.status.value
            print(f"{street.id}  {street.display_name}  {status}")
        return 0

    street = data.get_street(args.street_id)
    if street is None:
        print(f"Street not found: {args.street_id}")
        return 2
    decision = select_production_placement(street, area=data.display_name)
    if decision.triage_status is ProductionTriageStatus.UNRENDERABLE_INPUT:
        print(f"Manual-review error: unrenderable input ({', '.join(decision.reason_codes)})")
        return 2
    if decision.triage_status is not ProductionTriageStatus.MANUAL_REVIEW:
        print("Manual-review error: only MANUAL_REVIEW streets may receive a human override.")
        return 2

    if args.operation == "show":
        current = store.get(data.id, street.id)
        candidate = decision.best_candidate or decision.standard
        assert decision.standard is not None and candidate is not None
        print(f"Street: {data.display_name} / {data.id} / {street.id} / {street.display_name}")
        print(f"STANDARD: 0 deg, 1.00, Y+0")
        print(f"Best diagnostic: {candidate.orientation_deg} deg, {candidate.scale:.2f}, Y{candidate.y_offset:+d}")
        print(f"Reason codes: {', '.join(decision.reason_codes)}")
        print(f"Resolution: {'pending' if current is None else current.status.value}")
        return 0

    try:
        if args.operation == "approve-standard":
            override = ManualPlacementOverride.approved_standard(data.id, street.id, street.display_name, note=args.note)
            save_manual_override(override, args.overrides)
        elif args.operation == "approve-transform":
            override = ManualPlacementOverride.approved_transform(
                data.id, street.id, street.display_name, orientation_deg=args.orientation,
                scale=args.scale, y_offset=args.y_offset, note=args.note,
            )
            save_manual_override(override, args.overrides)
        elif args.operation == "clear":
            clear_manual_override(data.id, street.id, args.overrides)
            print(f"Cleared manual override: {data.id} / {street.id} / {street.display_name}")
            return 0
        else:
            return _render_manual_preview(args, data, street, decision)
    except ManualOverrideError as error:
        print(f"Manual-review error: {error}")
        return 2
    print(f"Saved manual override: {data.id} / {street.id} / {street.display_name}")
    print(f"{override.orientation_deg} deg, {override.scale:.2f}, Y{override.y_offset:+d}")
    return 0


def _render_manual_preview(args: argparse.Namespace, data: object, street: object, decision: object) -> int:
    """Render an unsaved constrained edit with the production face renderer."""
    from .manual import ManualOverrideError, ManualPlacementOverride

    if args.load_diagnostic:
        candidate = decision.best_candidate or decision.standard
        orientation, scale, y_offset = candidate.orientation_deg, candidate.scale, candidate.y_offset
    else:
        values = (args.orientation, args.scale, args.y_offset)
        if any(value is None for value in values):
            print("Manual-review error: preview requires orientation, scale, and y-offset, or --load-diagnostic.")
            return 2
        orientation, scale, y_offset = args.orientation, args.scale, args.y_offset
    try:
        override = ManualPlacementOverride.approved_transform(
            data.id, street.id, street.display_name,
            orientation_deg=orientation, scale=scale, y_offset=y_offset,
        )
        image = render_face(street, FaceRenderOptions(area=data.display_name, manual_override=override))
    except (ManualOverrideError, FaceRenderError) as error:
        print(f"Manual-review error: {error}")
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, format="PNG", dpi=(300, 300))
    print(f"Rendered manual preview: {args.output}")
    return 0
