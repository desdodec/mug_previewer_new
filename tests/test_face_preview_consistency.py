from pathlib import Path
import json

import pytest
from PIL import Image

from mug_previewer import preprocess
from mug_previewer.canonical_svg import history_paths
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.manual_svg_workspace import EditSaveMonitor, ManualSvgWorkspace
from mug_previewer.ui.state import load_preprocessed_catalogue, load_preprocessed_preview


FIXTURE = Path(__file__).parent / "fixtures" / "workflow_v6_valid"


def svg(colour: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462" viewBox="0 0 990 462">'
        f'<g class="front-composition"><rect width="495" height="462" fill="{colour}"/></g></svg>'
    )


def prepared_workspace(tmp_path):
    """Create one prepared face with canonical/history/preview all initially in sync."""
    data = load_dataset(FIXTURE)
    street = data.streets[0]
    folder = tmp_path / "faces" / data.id
    folder.mkdir(parents=True)
    canonical = folder / f"{street.id}_consistency.svg"
    preview = tmp_path / "previews" / data.id / f"{street.id}_consistency.png"

    initial = svg("red").encode()
    canonical.write_bytes(initial)
    preprocess._write_preview(initial, preview)

    generated, good = history_paths(canonical)
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_bytes(initial)
    good.write_bytes(initial)

    (tmp_path / preprocess.INDEX_FILENAME).write_text(
        json.dumps(
            {
                "records": [
                    {
                        "dataset_id": data.id,
                        "street_id": street.id,
                        "success": True,
                        "production_state": "MANUAL_REVIEW",
                        "svg_path": canonical.relative_to(tmp_path).as_posix(),
                        "generated_svg_path": generated.relative_to(tmp_path).as_posix(),
                        "preview_path": preview.relative_to(tmp_path).as_posix(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return data, street, canonical, preview


def preview_pixel(root, data, street):
    catalogue = load_preprocessed_catalogue(root)
    record = catalogue.find(data, street)
    assert record is not None
    image = load_preprocessed_preview(record)
    return image.convert("RGBA").getpixel((247, 231)), record


def accept_edit(root, data, street, colour):
    catalogue = load_preprocessed_catalogue(root)
    record = catalogue.find(data, street)
    workspace = ManualSvgWorkspace.from_record(record, catalogue.records.values())
    workspace.create_or_get_working_edit()
    monitor = EditSaveMonitor(workspace, data, street, root)
    workspace.working_svg.write_text(svg(colour), encoding="utf-8")
    assert monitor.poll() is False
    assert monitor.poll() is True
    return workspace


def test_normal_accepted_edit_survives_catalogue_reload(tmp_path):
    data, street, canonical, _preview = prepared_workspace(tmp_path)
    accept_edit(tmp_path, data, street, "blue")

    pixel, record = preview_pixel(tmp_path, data, street)
    assert pixel == (0, 0, 255, 255)
    assert canonical.read_text(encoding="utf-8") == svg("blue")
    assert record.svg_path == canonical
    assert record.state is not None and record.state.value == "MANUAL_APPROVED"


def test_saved_canonical_from_previous_session_is_visible_without_clicking_edit(tmp_path):
    """Regression: a save must not stay hidden behind an old cached preview after restart."""
    data, street, canonical, _preview = prepared_workspace(tmp_path)

    # Simulate Inkscape saving just before the app closes/crashes, before the
    # background monitor has had a chance to publish a new cached PNG.
    canonical.write_text(svg("blue"), encoding="utf-8")

    pixel, record = preview_pixel(tmp_path, data, street)
    assert record.svg_path == canonical
    assert pixel == (0, 0, 255, 255)


def test_second_saved_edit_is_visible_after_restart_without_reopening_inkscape(tmp_path):
    """A later edit must not require Edit in Inkscape to make the grid catch up."""
    data, street, canonical, _preview = prepared_workspace(tmp_path)
    accept_edit(tmp_path, data, street, "blue")

    # Previous accepted preview is blue; a later save reaches disk as yellow
    # and the app exits before its save monitor finishes.
    canonical.write_text(svg("yellow"), encoding="utf-8")

    pixel, record = preview_pixel(tmp_path, data, street)
    assert record.svg_path == canonical
    assert pixel == (255, 255, 0, 255)


def test_missing_cached_preview_is_rebuilt_from_authoritative_svg(tmp_path):
    data, street, canonical, preview = prepared_workspace(tmp_path)
    canonical.write_text(svg("blue"), encoding="utf-8")
    preview.unlink()

    pixel, record = preview_pixel(tmp_path, data, street)
    assert record.svg_path == canonical
    assert preview.is_file()
    assert pixel == (0, 0, 255, 255)


def test_corrupt_cached_preview_is_rebuilt_from_authoritative_svg(tmp_path):
    data, street, canonical, preview = prepared_workspace(tmp_path)
    canonical.write_text(svg("blue"), encoding="utf-8")
    preview.write_bytes(b"not a png")

    pixel, record = preview_pixel(tmp_path, data, street)
    assert record.svg_path == canonical
    assert pixel == (0, 0, 255, 255)
    with Image.open(preview) as image:
        image.verify()


def test_valid_in_sync_preview_is_not_rewritten_just_by_reloading(tmp_path):
    data, street, _canonical, preview = prepared_workspace(tmp_path)
    before = preview.read_bytes()
    before_mtime = preview.stat().st_mtime_ns

    pixel, _record = preview_pixel(tmp_path, data, street)

    assert pixel == (255, 0, 0, 255)
    assert preview.read_bytes() == before
    assert preview.stat().st_mtime_ns == before_mtime


def test_invalid_pending_svg_is_not_silently_used_as_a_preview(tmp_path):
    data, street, canonical, preview = prepared_workspace(tmp_path)
    previous_preview = preview.read_bytes()
    canonical.write_text("<invalid", encoding="utf-8")

    # Startup/viewing may recover the last-known-good face or report a clear
    # data error, but it must never publish the malformed bytes as artwork.
    try:
        pixel, _record = preview_pixel(tmp_path, data, street)
    except (OSError, ValueError):
        assert preview.read_bytes() == previous_preview
    else:
        assert pixel == (255, 0, 0, 255)
        assert preview.read_bytes() == previous_preview
