from pathlib import Path
import json
import pytest
from PIL import Image
from test_manual_svg_workspace import manual, svg
from test_batch_export import prepared, plan, fake_export
from mug_previewer.manual_svg_workspace import EditSaveMonitor, revert_generated
from mug_previewer.preprocess import resolve_authoritative_face_svg
from mug_previewer.review_index import set_excluded, current_review_state
from mug_previewer.ui.state import load_preprocessed_catalogue
from mug_previewer.ui.face_grid import cached_thumbnail
from mug_previewer import batch_export


def test_saved_edit_promotes_and_refreshes_preview_preserving_original(manual):
    root, data, street, workspace = manual
    original = workspace.generated_svg.read_bytes()
    working = workspace.create_or_get_working_edit()
    monitor = EditSaveMonitor(workspace, data, street, root)
    working.write_text(svg('blue'))
    assert not monitor.poll()
    assert monitor.poll()
    authority = resolve_authoritative_face_svg(root, data, street).path
    assert authority != workspace.generated_svg
    assert b'blue' in authority.read_bytes()
    assert workspace.generated_svg.read_bytes() == original
    record = load_preprocessed_catalogue(root).find(data, street)
    with Image.open(record.preview_path) as image:
        assert image.getpixel((247, 231))[:3] == (0, 0, 255)
    previous, preview = authority.read_bytes(), record.preview_path.read_bytes()
    working.write_text('<invalid')
    assert not monitor.poll()
    with pytest.raises(Exception):
        monitor.poll()
    assert authority.read_bytes() == previous
    assert record.preview_path.read_bytes() == preview
    assert workspace.generated_svg.read_bytes() == original
    revert_generated(data, street, root)
    assert resolve_authoritative_face_svg(root, data, street).path == working
    assert working.read_bytes() == original


@pytest.mark.parametrize('status', ['pass', 'Do Not Use', 'overlap', 'duplicates', 'missing', 'other'])
def test_hashless_legacy_migration_and_persistent_override(manual, status):
    root, data, street, workspace = manual
    (root / 'svg_review_results_old.json').write_text(json.dumps([
        dict(svg_name='face.svg', status=status)]))
    assert current_review_state(root, data.id, street.id, workspace.working_svg).export_blocked == (status != 'pass')
    set_excluded(root, data.id, street.id, True)
    workspace.working_svg.write_text(svg('yellow'))
    assert current_review_state(root, data.id, street.id, workspace.working_svg).export_blocked
    set_excluded(root, data.id, street.id, False)
    assert not current_review_state(root, data.id, street.id, workspace.working_svg).export_blocked


def test_batch_exports_all_valid_included(prepared, monkeypatch):
    root, data, records, write = prepared
    (root / 'svg_review_results.json').unlink()
    (root / 'review_index.json').unlink()
    records[0]['production_state'] = 'MANUAL_REVIEW'
    write()
    set_excluded(root, data.id, '0001', True)
    calls = []
    monkeypatch.setattr(batch_export, 'export_preprocessed_provider_png', fake_export(calls))
    result = batch_export.execute_batch_export(plan(prepared))
    assert result.summary['exported'] == 10
    assert len(calls) == 10
    assert '0000' in [call[0] for call in calls]
    assert '0001' not in [call[0] for call in calls]


def test_cached_grid_preview_uses_png_only(tmp_path, monkeypatch):
    path = tmp_path / 'preview.png'
    Image.new('RGBA', (990, 462), 'blue').save(path)
    for target in ('mug_previewer.rendering.svg_raster.rasterize_face_svg',
                   'mug_previewer.preprocess.render_face_svg'):
        monkeypatch.setattr(target, lambda *a, **k: pytest.fail('grid rendered SVG'))
    cached_thumbnail.cache_clear()
    args = str(path), path.stat().st_mtime_ns, 320
    image = cached_thumbnail(*args)
    assert cached_thumbnail(*args) is image
    assert image.width <= 320


def test_scroll_grid_keeps_only_visible_png_cards(manual, monkeypatch):
    import tkinter as tk
    from types import SimpleNamespace
    from mug_previewer.ui.face_grid import FaceGrid
    root_path, data, street, workspace = manual
    catalogue = load_preprocessed_catalogue(root_path)
    app = SimpleNamespace(state=SimpleNamespace(selected_dataset=data),
                          preprocessed_catalogue=catalogue, workflow_items={}, _shutting_down=False)
    for target in ('mug_previewer.rendering.svg_raster.rasterize_face_svg',
                   'mug_previewer.preprocess.render_face_svg'):
        monkeypatch.setattr(target, lambda *a, **k: pytest.fail('scroll rendered artwork'))
    root = tk.Tk()
    root.withdraw()
    grid = FaceGrid(root, app)
    try:
        grid.set_streets([street] * 200)
        assert grid.columns.get() == 2
        assert 0 < len(grid.cards) < 20
        before = set(grid.cards)
        grid.scroll('moveto', 1.0)
        assert 0 < len(grid.cards) < 20
        assert not before.intersection(grid.cards)
    finally:
        grid.executor.shutdown(wait=True)
        root.destroy()


def test_manual_review_single_export_defaults_included_and_respects_checkbox(manual):
    from mug_previewer.preprocessed_export import export_preprocessed_provider_png
    root, data, street, workspace = manual
    destination = root / 'included.png'
    export_preprocessed_provider_png(root, data, street, destination,
                                    profile_id='inkthreadable_11oz_white')
    assert destination.is_file()
    set_excluded(root, data.id, street.id, True)
    with pytest.raises(ValueError, match='Excluded'):
        export_preprocessed_provider_png(root, data, street, root / 'excluded.png',
                                        profile_id='inkthreadable_11oz_white')
    assert not (root / 'excluded.png').exists()
