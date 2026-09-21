from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from mug_previewer.batch_export import (
    BatchExportItem,
    BatchExportSummary,
    Eligibility,
)
from mug_previewer.design import DesignOptions
from mug_previewer import mockup_batch


def _source_plan(tmp_path: Path, dataset, items):
    summary = BatchExportSummary(
        total=len(items),
        ready=sum(i.eligibility is Eligibility.READY for i in items),
        manual_review=0,
        qa_blocked=0,
        excluded=sum(i.eligibility is Eligibility.EXCLUDED for i in items),
        unrenderable=sum(i.eligibility is Eligibility.UNRENDERABLE for i in items),
        asset_errors=sum(i.eligibility is Eligibility.ASSET_ERROR for i in items),
        existing=0,
        not_reviewed=0,
    )
    return SimpleNamespace(
        root=tmp_path,
        dataset=dataset,
        provider_id=mockup_batch.READINESS_PROFILE_ID,
        destination_root=tmp_path / ".mockup-readiness",
        output_directory=tmp_path / ".mockup-readiness" / mockup_batch.READINESS_PROFILE_ID,
        items=tuple(items),
        summary=summary,
        replace_existing=False,
        design_options=DesignOptions(),
    )


def test_mockup_plan_creates_front_and_rear_destinations(tmp_path: Path, monkeypatch) -> None:
    dataset = SimpleNamespace(id="Hebden Bridge")
    ready = BatchExportItem(
        dataset_id=dataset.id,
        street_id="0042",
        street_name="Aspinall Street",
        production_state="READY",
        eligibility=Eligibility.READY,
    )
    excluded = BatchExportItem(
        dataset_id=dataset.id,
        street_id="0043",
        street_name="Other Street",
        production_state="READY",
        eligibility=Eligibility.EXCLUDED,
        reason="Excluded by QA",
    )
    source_plan = _source_plan(tmp_path, dataset, [ready, excluded])
    monkeypatch.setattr(mockup_batch, "build_batch_plan", lambda *a, **k: source_plan)

    plan = mockup_batch.build_mockup_batch_plan(
        tmp_path,
        dataset,
        tmp_path / "exports",
        design_options=DesignOptions(),
    )

    assert plan.summary.total == 2
    assert plan.summary.ready == 1
    assert plan.summary.excluded == 1
    item = plan.items[0]
    assert item.front_destination.name.endswith("_front.png")
    assert item.rear_destination.name.endswith("_rear.png")
    assert item.front_destination.parent.name == "front"
    assert item.rear_destination.parent.name == "rear"
    assert plan.output_directory == tmp_path / "exports" / "mockups" / "Hebden_Bridge"


def test_mockup_executor_renders_one_wrap_and_two_native_views(tmp_path: Path, monkeypatch) -> None:
    street = SimpleNamespace(id="0042", display_name="Aspinall Street")
    dataset = SimpleNamespace(id="Hebden Bridge", get_street=lambda sid: street if sid == street.id else None)
    source = BatchExportItem(
        dataset_id=dataset.id,
        street_id=street.id,
        street_name=street.display_name,
        production_state="READY",
        eligibility=Eligibility.READY,
    )
    source_plan = _source_plan(tmp_path, dataset, [source])
    output = tmp_path / "exports"
    front_path = output / "mockups" / "Hebden_Bridge" / "front" / "Hebden_Bridge_0042_Aspinall_Street_front.png"
    rear_path = output / "mockups" / "Hebden_Bridge" / "rear" / "Hebden_Bridge_0042_Aspinall_Street_rear.png"
    item = mockup_batch.MockupBatchItem(source, front_path, rear_path)
    plan = mockup_batch.MockupBatchPlan(
        tmp_path,
        dataset,
        output,
        (item,),
        mockup_batch.MockupBatchSummary(1, 1, 0, 0, 0, 0, 0),
        source_plan,
        False,
        DesignOptions(),
    )

    wrap = Image.new("RGBA", (2362, 1063), (255, 255, 255, 0))
    calls = []

    monkeypatch.setattr(mockup_batch, "_recheck", lambda _plan, value: value)
    monkeypatch.setattr(
        mockup_batch,
        "render_preprocessed_wrap",
        lambda *a, **k: wrap,
    )

    def fake_render(source_image, options):
        assert source_image is wrap
        calls.append(options.orientation)
        return Image.new("RGBA", (1024, 1536), (10, 20, 30, 255))

    monkeypatch.setattr(mockup_batch, "render_mug_preview", fake_render)

    result = mockup_batch.execute_mockup_batch(plan)

    assert result.summary["exported"] == 1
    assert calls == [
        mockup_batch.PreviewOrientation.FRONT_HANDLE_RIGHT,
        mockup_batch.PreviewOrientation.REAR_HANDLE_LEFT,
    ]
    with Image.open(front_path) as front:
        assert front.size == (1024, 1536)
    with Image.open(rear_path) as rear:
        assert rear.size == (1024, 1536)
    assert result.report_path.is_file()


def test_mockup_executor_preserves_existing_pair_when_skip_policy(tmp_path: Path, monkeypatch) -> None:
    street = SimpleNamespace(id="0042", display_name="Aspinall Street")
    dataset = SimpleNamespace(id="Hebden Bridge", get_street=lambda sid: street)
    source = BatchExportItem(
        dataset_id=dataset.id,
        street_id=street.id,
        street_name=street.display_name,
        production_state="READY",
        eligibility=Eligibility.READY,
    )
    source_plan = _source_plan(tmp_path, dataset, [source])
    output = tmp_path / "exports"
    front_path = output / "mockups" / "Hebden_Bridge" / "front" / "front.png"
    rear_path = output / "mockups" / "Hebden_Bridge" / "rear" / "rear.png"
    front_path.parent.mkdir(parents=True)
    rear_path.parent.mkdir(parents=True)
    front_path.write_bytes(b"front-original")
    rear_path.write_bytes(b"rear-original")
    item = mockup_batch.MockupBatchItem(source, front_path, rear_path, True, True)
    plan = mockup_batch.MockupBatchPlan(
        tmp_path,
        dataset,
        output,
        (item,),
        mockup_batch.MockupBatchSummary(1, 1, 0, 0, 0, 1, 2),
        source_plan,
    )

    monkeypatch.setattr(mockup_batch, "_recheck", lambda _plan, value: value)
    monkeypatch.setattr(
        mockup_batch,
        "render_preprocessed_wrap",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("existing pair must not render")),
    )

    result = mockup_batch.execute_mockup_batch(plan)

    assert result.summary["skipped_existing"] == 1
    assert front_path.read_bytes() == b"front-original"
    assert rear_path.read_bytes() == b"rear-original"
