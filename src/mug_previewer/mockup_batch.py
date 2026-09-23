"""Batch export of screen/customer mockups from prepared authoritative artwork."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from PIL import Image, PngImagePlugin

from .batch_export import (
    BatchExportItem,
    BatchExportPlan,
    Eligibility,
    _atomic_report,
    _recheck,
    build_batch_plan,
    production_filename,
    sanitize_filename,
)
from .design import DesignOptions
from .preprocessed_export import render_preprocessed_wrap
from .preview.mockup import MugPreviewOptions, PreviewOrientation, render_mug_preview


READINESS_PROFILE_ID = "inkthreadable_11oz_white"
STUDIO_MOCKUP_STYLE_ID = "studio_white_mug"
MOCKUP_RENDERER_VERSION = "4"


@dataclass(frozen=True)
class MockupBatchItem:
    source: BatchExportItem
    front_destination: Path | None = None
    rear_destination: Path | None = None
    front_exists: bool = False
    rear_exists: bool = False
    front_matches_current: bool = False
    rear_matches_current: bool = False


@dataclass(frozen=True)
class MockupBatchSummary:
    total: int
    ready: int
    excluded: int
    unrenderable: int
    asset_errors: int
    existing_pairs: int
    existing_images: int


@dataclass(frozen=True)
class MockupBatchPlan:
    root: Path
    dataset: object
    destination_root: Path
    items: tuple[MockupBatchItem, ...]
    summary: MockupBatchSummary
    source_plan: BatchExportPlan
    replace_existing: bool = False
    design_options: DesignOptions | None = None
    style_id: str = STUDIO_MOCKUP_STYLE_ID

    @property
    def output_directory(self) -> Path:
        return self.destination_root / "mockups" / sanitize_filename(self.dataset.id)

    @property
    def front_directory(self) -> Path:
        return self.output_directory / "front"

    @property
    def rear_directory(self) -> Path:
        return self.output_directory / "rear"


@dataclass(frozen=True)
class MockupBatchItemResult:
    item: MockupBatchItem
    result: str
    reason: str | None = None


@dataclass(frozen=True)
class MockupBatchProgress:
    current: int
    total: int
    result: MockupBatchItemResult


@dataclass(frozen=True)
class MockupBatchResult:
    results: tuple[MockupBatchItemResult, ...]
    report_path: Path
    summary: dict
    cancelled: bool


def _mockup_stem(item: BatchExportItem) -> str:
    return Path(production_filename(item.dataset_id, item.street_id, item.street_name)).stem


def _resolved_design_options(options: DesignOptions | None) -> DesignOptions:
    return options or DesignOptions()


def _mockup_metadata(
    design_options: DesignOptions | None,
    style_id: str,
    source_digest: str | None,
) -> dict[str, str]:
    design = _resolved_design_options(design_options)
    return {
        "mug_previewer_mockup_style": style_id,
        "mug_previewer_mockup_renderer_version": MOCKUP_RENDERER_VERSION,
        "mug_previewer_front_feature_weight": f"{design.front_feature_weight:.2f}",
        "mug_previewer_rear_highlight_weight": f"{design.rear_highlight_weight:.2f}",
        "mug_previewer_source_svg_sha256": source_digest or "",
    }


def _mockup_output_matches(
    path: Path,
    design_options: DesignOptions | None,
    style_id: str,
    source_digest: str | None,
) -> bool:
    """True only when an existing PNG was rendered with the current mockup settings."""
    if not path.is_file():
        return False
    expected = _mockup_metadata(design_options, style_id, source_digest)
    try:
        with Image.open(path) as image:
            return all(str(image.info.get(key, "")) == value for key, value in expected.items())
    except (OSError, ValueError):
        return False


def build_mockup_batch_plan(
    root,
    dataset,
    destination_root,
    *,
    replace_existing: bool = False,
    design_options: DesignOptions | None = None,
    style_id: str = STUDIO_MOCKUP_STYLE_ID,
) -> MockupBatchPlan:
    """Plan one front/rear studio mockup pair for every QA-ready prepared face."""
    if style_id != STUDIO_MOCKUP_STYLE_ID:
        raise ValueError(f"Unsupported mockup style: {style_id}")
    if destination_root is None or not str(destination_root).strip():
        raise ValueError("Choose a destination folder first")

    destination_root = Path(destination_root).resolve()
    # Reuse the production batch's authoritative-artwork/QA gate.  The provider
    # selected here is only a stable readiness profile; no provider PNG is made.
    source_plan = build_batch_plan(
        root,
        dataset,
        READINESS_PROFILE_ID,
        destination_root / ".mockup-readiness",
        replace_existing=False,
        design_options=design_options,
    )

    output_root = destination_root / "mockups" / sanitize_filename(dataset.id)
    front_dir = output_root / "front"
    rear_dir = output_root / "rear"
    items: list[MockupBatchItem] = []
    seen: set[str] = set()
    for source in source_plan.items:
        if source.eligibility is Eligibility.READY:
            stem = _mockup_stem(source)
            front = front_dir / f"{stem}_front.png"
            rear = rear_dir / f"{stem}_rear.png"
            for destination in (front, rear):
                key = str(destination).casefold()
                if key in seen:
                    raise ValueError(f"Mockup filename collision: {destination.name}")
                seen.add(key)
            front_exists = front.exists()
            rear_exists = rear.exists()
            items.append(
                MockupBatchItem(
                    source,
                    front,
                    rear,
                    front_exists,
                    rear_exists,
                    _mockup_output_matches(
                        front, design_options, style_id, source.authoritative_svg_sha256
                    ) if front_exists else False,
                    _mockup_output_matches(
                        rear, design_options, style_id, source.authoritative_svg_sha256
                    ) if rear_exists else False,
                )
            )
        else:
            items.append(MockupBatchItem(source))

    ready = sum(i.source.eligibility is Eligibility.READY for i in items)
    summary = MockupBatchSummary(
        total=len(items),
        ready=ready,
        excluded=sum(i.source.eligibility is Eligibility.EXCLUDED for i in items),
        unrenderable=sum(i.source.eligibility is Eligibility.UNRENDERABLE for i in items),
        asset_errors=sum(i.source.eligibility is Eligibility.ASSET_ERROR for i in items),
        existing_pairs=sum(i.front_matches_current and i.rear_matches_current for i in items),
        existing_images=sum(i.front_exists for i in items) + sum(i.rear_exists for i in items),
    )
    return MockupBatchPlan(
        Path(root).resolve(),
        dataset,
        destination_root,
        tuple(items),
        summary,
        source_plan,
        replace_existing,
        design_options,
        style_id,
    )


def _save_png(
    image: Image.Image,
    destination: Path,
    *,
    design_options: DesignOptions | None,
    style_id: str,
    source_digest: str | None,
) -> None:
    metadata = PngImagePlugin.PngInfo()
    for key, value in _mockup_metadata(design_options, style_id, source_digest).items():
        metadata.add_text(key, value)
    image.convert("RGBA").save(destination, format="PNG", optimize=True, pnginfo=metadata)


def _publish(temporary: Path, destination: Path, *, replace_existing: bool) -> bool:
    """Publish one staged image. Return False when an existing file was preserved."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if replace_existing:
        os.replace(temporary, destination)
        return True
    try:
        os.link(temporary, destination)
    except FileExistsError:
        return False
    return True


def execute_mockup_batch(plan: MockupBatchPlan, *, on_progress=None, cancel_event=None) -> MockupBatchResult:
    """Render native-resolution front/rear studio mockups without touching print masters."""
    started = datetime.now(timezone.utc).isoformat()
    plan.front_directory.mkdir(parents=True, exist_ok=True)
    plan.rear_directory.mkdir(parents=True, exist_ok=True)
    results: list[MockupBatchItemResult] = []
    attempted = 0
    cancelled = False
    progress_errors: list[str] = []

    for item in plan.items:
        source = item.source
        if source.eligibility is not Eligibility.READY:
            results.append(MockupBatchItemResult(item, "NOT_ELIGIBLE", source.reason))
            continue
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            results.append(MockupBatchItemResult(item, "CANCELLED", "Cancelled before this item started"))
            continue

        try:
            current = _recheck(plan.source_plan, source)
            front_exists = item.front_destination.exists()
            rear_exists = item.rear_destination.exists()
            front_current = (
                _mockup_output_matches(
                    item.front_destination,
                    plan.design_options,
                    plan.style_id,
                    source.authoritative_svg_sha256,
                )
                if front_exists else False
            )
            rear_current = (
                _mockup_output_matches(
                    item.rear_destination,
                    plan.design_options,
                    plan.style_id,
                    source.authoritative_svg_sha256,
                )
                if rear_exists else False
            )
            if front_current and rear_current and not plan.replace_existing:
                result = MockupBatchItemResult(
                    item,
                    "SKIPPED_EXISTING",
                    "Both mockup views already match the current design settings",
                )
            else:
                street = plan.dataset.get_street(source.street_id)
                if street is None:
                    raise ValueError("Prepared street is missing from the selected source dataset")
                wrap = render_preprocessed_wrap(
                    plan.root,
                    plan.dataset,
                    street,
                    design_options=plan.design_options,
                    require_production_approved=False,
                )
                front = render_mug_preview(
                    wrap,
                    MugPreviewOptions(orientation=PreviewOrientation.FRONT_HANDLE_RIGHT),
                )
                rear = render_mug_preview(
                    wrap,
                    MugPreviewOptions(orientation=PreviewOrientation.REAR_HANDLE_LEFT),
                )

                with tempfile.TemporaryDirectory(dir=plan.output_directory, prefix=".mockup-") as staging:
                    staging_path = Path(staging)
                    front_tmp = staging_path / "front.png"
                    rear_tmp = staging_path / "rear.png"
                    _save_png(
                        front,
                        front_tmp,
                        design_options=plan.design_options,
                        style_id=plan.style_id,
                        source_digest=source.authoritative_svg_sha256,
                    )
                    _save_png(
                        rear,
                        rear_tmp,
                        design_options=plan.design_options,
                        style_id=plan.style_id,
                        source_digest=source.authoritative_svg_sha256,
                    )
                    _recheck(plan.source_plan, current)

                    published_front = True
                    published_rear = True
                    if plan.replace_existing or not front_current:
                        published_front = _publish(
                            front_tmp,
                            item.front_destination,
                            replace_existing=plan.replace_existing or front_exists,
                        )
                    if plan.replace_existing or not rear_current:
                        published_rear = _publish(
                            rear_tmp,
                            item.rear_destination,
                            replace_existing=plan.replace_existing or rear_exists,
                        )

                if not plan.replace_existing and not published_front and not published_rear:
                    result = MockupBatchItemResult(
                        item,
                        "SKIPPED_EXISTING",
                        "Both destinations appeared during export with matching settings",
                    )
                else:
                    result = MockupBatchItemResult(item, "EXPORTED")
        except Exception as error:
            result = MockupBatchItemResult(item, "FAILED", str(error))

        results.append(result)
        attempted += 1
        if on_progress is not None:
            try:
                on_progress(MockupBatchProgress(attempted, plan.summary.ready, result))
            except Exception as error:
                progress_errors.append(str(error))

    summary = asdict(plan.summary) | {
        key.lower(): sum(r.result == key for r in results)
        for key in ("EXPORTED", "FAILED", "SKIPPED_EXISTING", "CANCELLED")
    }
    payload = {
        "version": 1,
        "kind": "studio_mockups",
        "style_id": plan.style_id,
        "dataset_id": plan.dataset.id,
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "cancelled": cancelled,
        "replace_existing": plan.replace_existing,
        "design_options": asdict(_resolved_design_options(plan.design_options)),
        "summary": summary,
        "progress_errors": progress_errors,
        "items": [
            {
                "dataset_id": r.item.source.dataset_id,
                "street_id": r.item.source.street_id,
                "street_name": r.item.source.street_name,
                "eligibility": r.item.source.eligibility.value,
                "front_destination": str(r.item.front_destination) if r.item.front_destination else None,
                "rear_destination": str(r.item.rear_destination) if r.item.rear_destination else None,
                "result": r.result,
                "reason": r.reason,
            }
            for r in results
        ],
    }
    report_path = plan.output_directory / "mockup_export_report.json"
    _atomic_report(report_path, payload)
    return MockupBatchResult(tuple(results), report_path, summary, cancelled)
