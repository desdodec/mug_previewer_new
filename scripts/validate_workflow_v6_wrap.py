"""Render and validate canonical wraps from the real workflow-v6 datasets.

This is a developer integration check. It keeps external dataset paths out of
unit tests and delegates all loading and rendering to the public APIs.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / 'src'))

from mug_previewer.datasets.loader import load_dataset
from mug_previewer.rendering.artwork import PixelBox, render_wrap_result
from mug_previewer.rendering.context_map import REAR_PANEL_PX
from mug_previewer.rendering.face import FRONT_PANEL_PX


WORKFLOW_ROOT = Path(r"E:\Python_Stuff\OS_Mail_Addresses\workflow_outputs_v6")
DEFAULT_OUTPUT_DIRECTORY = Path("output") / "workflow_v6_wrap_validation"
EXPECTED_WRAP_SIZE = (2362, 1063)
EXPECTED_FRONT_BOX = PixelBox(0, 90, 945, 882)
EXPECTED_REAR_BOX = PixelBox(1417, 90, 945, 882)


@dataclass(frozen=True)
class ValidationCase:
    dataset_label: str
    dataset_directory: str
    street_id: str
    filename: str


CASES = (
    ValidationCase("Stoke Newington", "20260824_101707_stoke_newington_streets_parks_water_boundary_clip", "0246", "validation_stoke_0246_stoke_newington_church_street.png"),
    ValidationCase("Stoke Newington", "20260824_101707_stoke_newington_streets_parks_water_boundary_clip", "0182", "validation_stoke_0182_nile_close.png"),
    ValidationCase("Stoke Newington", "20260824_101707_stoke_newington_streets_parks_water_boundary_clip", "0206", "validation_stoke_0206_queen_elizabeths_close.png"),
    ValidationCase("Hebden Bridge", "20260820_112815_hebden_bridge_streets_parks_water_boundary_clip", "0220", "validation_hebden_0220_throstle_bower.png"),
    ValidationCase("Hebden Bridge", "20260820_112815_hebden_bridge_streets_parks_water_boundary_clip", "0036", "validation_hebden_0036_burnley_road.png"),
)


def _box_values(box: PixelBox) -> tuple[int, int, int, int]:
    """Return explicit ``(x, y, width, height)`` values for reporting."""
    return box.x, box.y, box.width, box.height


def _validate_result(result: object) -> None:
    """Assert public rendering invariants without duplicating renderer logic."""
    front = result.front_panel  # type: ignore[attr-defined]
    rear = result.rear_panel  # type: ignore[attr-defined]
    wrap = result.image  # type: ignore[attr-defined]
    context = result.context  # type: ignore[attr-defined]
    assert front.mode == "RGBA" and front.size == FRONT_PANEL_PX
    assert rear.mode == "RGBA" and rear.size == REAR_PANEL_PX
    assert wrap.mode == "RGBA" and wrap.size == EXPECTED_WRAP_SIZE
    assert context.framing_mode == "metric"
    assert result.front_placed_box == EXPECTED_FRONT_BOX  # type: ignore[attr-defined]
    assert result.rear_placed_box == EXPECTED_REAR_BOX  # type: ignore[attr-defined]


def run(cases: Iterable[ValidationCase], workflow_root: Path, output_directory: Path) -> list[dict[str, object]]:
    """Run all cases, save masters, and return serialisable diagnostics."""
    output_directory.mkdir(parents=True, exist_ok=True)
    datasets = {case.dataset_directory: load_dataset(workflow_root / case.dataset_directory) for case in cases}
    records: list[dict[str, object]] = []
    for case in cases:
        dataset = datasets[case.dataset_directory]
        output_path = output_directory / case.filename
        record: dict[str, object] = {
            "dataset": case.dataset_label,
            "street_id": case.street_id,
            "output_png": str(output_path.resolve()),
        }
        try:
            street = dataset.get_street(case.street_id)
            if street is None:
                raise LookupError(f"Canonical street ID was not found: {case.street_id}")
            result = render_wrap_result(dataset, street)
            _validate_result(result)
            result.image.save(output_path, dpi=(300, 300))
            record.update({
                "street_name": street.display_name,
                "street_metric_span_m": street.bbox_span_m,
                "dataset_p90_m": dataset.statistics.context_scale.get("bbox_span_p90_m"),
                "rear_framing_mode": result.context.framing_mode,
                "rear_context_width_m": result.context.final_context_width_m,
                "front_size_px": result.front_panel.size,
                "rear_size_px": result.rear_panel.size,
                "wrap_size_px": result.image.size,
                "front_wrap_bounds_xywh": _box_values(result.front_placed_box),
                "rear_wrap_bounds_xywh": _box_values(result.rear_placed_box),
                "status": "success",
            })
        except Exception as error:  # Keep independent cases visible in one report.
            record.update({"status": "failure", "error": f"{type(error).__name__}: {error}"})
        records.append(record)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-root", type=Path, default=WORKFLOW_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    args = parser.parse_args(argv)
    records = run(CASES, args.workflow_root, args.output_dir)
    print(json.dumps(records, indent=2, default=str))
    return 0 if all(record["status"] == "success" for record in records) else 1


if __name__ == "__main__":
    sys.exit(main())
