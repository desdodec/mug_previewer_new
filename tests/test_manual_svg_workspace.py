from pathlib import Path
import json

import pytest
from PIL import Image

from mug_previewer import preprocess
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.manual_svg_workspace import (ManualSvgWorkspace, EditSaveMonitor,
    find_inkscape_executable, launch_inkscape, revert_generated)
from mug_previewer.canonical_svg import migrate_legacy_records
from mug_previewer.preprocessed_export import (render_authoritative_face_panel,
    render_preprocessed_wrap, export_preprocessed_provider_png)
from mug_previewer.ui.state import load_preprocessed_catalogue


def svg(colour):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462" viewBox="0 0 990 462">'
            f'<g class="front-composition"><rect width="495" height="462" fill="{colour}"/></g></svg>')


@pytest.fixture
def manual(tmp_path, monkeypatch):
    data = load_dataset(Path(__file__).parent / 'fixtures/workflow_v6_valid')
    street = data.streets[0]
    (tmp_path / 'face.svg').write_text(svg('red'))
    (tmp_path / 'preprocess_index.json').write_text(json.dumps({'records': [{
        'dataset_id': data.id, 'street_id': street.id, 'success': True,
        'production_state': 'MANUAL_REVIEW', 'svg_path': 'face.svg', 'generated_svg_path': 'face.svg',
        'preview_path': 'previews/face.png',
    }]}))
    preprocess._write_preview(svg('red'), tmp_path / 'previews/face.png')
    for target in ('mug_previewer.preprocess.render_face_svg', 'mug_previewer.preprocess.production_triage_state',
                   'mug_previewer.rendering.face.render_face', 'mug_previewer.rendering.face.render_face_svg'):
        monkeypatch.setattr(target, lambda *a, **k: pytest.fail('must not generate or score'))
    catalogue = load_preprocessed_catalogue(tmp_path)
    workspace = ManualSvgWorkspace.from_record(catalogue.find(data, street))
    workspace.create_or_get_working_edit()
    return tmp_path, data, street, workspace


def test_inkscape_opens_exact_canonical_svg_and_reopening_preserves_history(manual, monkeypatch):
    root, data, street, workspace = manual
    record = load_preprocessed_catalogue(root).find(data, street)
    original = workspace.generated_svg.read_bytes()
    calls = []
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.shutil.which', lambda name: '/installed/Inkscape.exe')
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.subprocess.Popen', lambda args, **kw: calls.append((args, kw)))
    assert launch_inkscape(workspace) == record.svg_path == record.editable_svg_path
    record.svg_path.write_text(svg('blue'))
    assert launch_inkscape(workspace) == record.svg_path
    assert calls == [([str(Path('/installed/Inkscape.exe')), str(record.svg_path)], {'shell': False})] * 2
    assert record.svg_path.read_text() == svg('blue')
    assert workspace.generated_svg.read_bytes() == original
    assert not list(root.rglob('*_edit.svg'))
    assert not list(root.rglob('*.approved.svg'))
    assert not list(root.rglob('corrected'))


def test_save_refreshes_cached_preview_without_changing_svg_bytes(manual):
    root, data, street, workspace = manual
    record = load_preprocessed_catalogue(root).find(data, street)
    original = workspace.generated_svg.read_bytes()
    before = record.preview_path.read_bytes()
    monitor = EditSaveMonitor(workspace, data, street, root)
    payload = (svg('blue') + '\n<!-- editor bytes retained -->\r\n').encode()
    record.svg_path.write_bytes(payload)
    assert not monitor.poll()
    assert monitor.poll()
    assert not monitor.poll()
    assert record.svg_path.read_bytes() == payload
    assert preprocess.resolve_authoritative_face_svg(root, data, street).path == record.svg_path
    assert record.preview_path.read_bytes() != before
    with Image.open(record.preview_path) as image:
        assert image.convert('RGBA').getpixel((247, 231)) == (0, 0, 255, 255)
    assert workspace.generated_svg.read_bytes() == original


@pytest.mark.parametrize('consumer', ['mug', 'export'])
def test_pending_save_uses_canonical_svg_in_preview_mug_and_export(manual, monkeypatch, consumer):
    root, data, street, workspace = manual
    canonical = workspace.working_svg
    payload = svg('blue').encode()
    canonical.write_bytes(payload)
    seen = []
    import mug_previewer.preprocessed_export as exporting
    raster = exporting.rasterize_face_svg
    def capture(source):
        seen.append((source, source.read_bytes()))
        return raster(source)
    monkeypatch.setattr(exporting, 'rasterize_face_svg', capture)
    if consumer == 'mug':
        from mug_previewer.ui.state import render_prepared_preview_pair
        image = render_prepared_preview_pair(root, data, street).wrap
    else:
        destination = root / 'export.png'
        export_preprocessed_provider_png(root, data, street, destination, profile_id='inkthreadable_11oz_white')
        with Image.open(destination) as source:
            image = source.convert('RGBA')
    assert seen and all(path == canonical and content == payload for path, content in seen)
    assert image.getpixel((472, 531)) == (0, 0, 255, 255)
    assert canonical.read_bytes() == payload
    with Image.open(load_preprocessed_catalogue(root).find(data, street).preview_path) as preview:
        assert preview.convert('RGBA').getpixel((247, 231)) == (0, 0, 255, 255)


@pytest.mark.parametrize('failure', ['invalid', 'dimensions', 'preview', 'index'])
def test_failed_save_restores_last_good_svg_preview_and_index(manual, monkeypatch, failure):
    root, data, street, workspace = manual
    monitor = EditSaveMonitor(workspace, data, street, root)
    workspace.working_svg.write_text(svg('blue'))
    monitor.poll()
    assert monitor.poll()
    record = load_preprocessed_catalogue(root).find(data, street)
    paths = [record.svg_path, record.preview_path, root / 'preprocess_index.json', workspace.generated_svg]
    previous = {path: path.read_bytes() for path in paths}
    payload = '<invalid' if failure == 'invalid' else svg('yellow')
    if failure == 'dimensions':
        payload = payload.replace('990', '900')
    if failure in ('preview', 'index'):
        def fail(*args, **kwargs):
            raise OSError('test failure')
        monkeypatch.setattr(preprocess, '_write_preview' if failure == 'preview' else '_write_index', fail)
    workspace.working_svg.write_text(payload)
    assert not monitor.poll()
    with pytest.raises(ValueError, match='last-known-good SVG restored'):
        monitor.poll()
    assert {path: path.read_bytes() for path in paths} == previous
    assert not monitor.poll()
    assert not list(root.rglob('*.tmp'))


def test_revert_restores_generated_bytes_in_same_open_svg_and_allows_next_save(manual):
    root, data, street, workspace = manual
    original = workspace.generated_svg.read_bytes()
    canonical = workspace.working_svg
    canonical.write_text(svg('blue'))
    assert render_authoritative_face_panel(root, data, street).image.getpixel((247, 231)) == (0, 0, 255, 255)
    revert_generated(data, street, root)
    record = load_preprocessed_catalogue(root).find(data, street)
    assert record.svg_path == canonical
    assert canonical.read_bytes() == original == workspace.generated_svg.read_bytes()
    assert record.state.value == 'MANUAL_REVIEW'
    canonical.write_text(svg('yellow'))
    assert render_authoritative_face_panel(root, data, street).image.getpixel((247, 231)) == (255, 255, 0, 255)
    assert workspace.generated_svg.read_bytes() == original


@pytest.mark.parametrize('failure', [None, 'preview', 'index', 'invalid'])
def test_legacy_approved_migration_is_transactional_and_retains_old_files(manual, monkeypatch, failure):
    root, data, street, workspace = manual
    index = root / preprocess.INDEX_FILENAME
    records = json.loads(index.read_text())['records']
    canonical = workspace.working_svg
    approved = root / 'face.approved.svg'
    payload = b'<invalid' if failure == 'invalid' else (svg('blue') + '\r\n').encode()
    approved.write_bytes(payload)
    old_edit = root / 'face_edit.svg'
    old_edit.write_text(svg('yellow'))
    corrected = root / 'corrected' / old_edit.name
    corrected.parent.mkdir()
    corrected.write_text(svg('lime'))
    records[0].update(svg_path=approved.name, approved_svg_path=approved.name, production_state='MANUAL_APPROVED')
    index.write_text(json.dumps({'records': records}))
    paths = [canonical, approved, old_edit, corrected, index, root / 'previews/face.png']
    before = {path: path.read_bytes() for path in paths}
    if failure in ('preview', 'index'):
        def fail(*args, **kwargs):
            raise OSError('migration failure')
        monkeypatch.setattr(preprocess, '_write_preview' if failure == 'preview' else '_write_index', fail)
    if failure:
        with pytest.raises((OSError, ValueError)):
            migrate_legacy_records(root)
        assert {path: path.read_bytes() for path in paths} == before
        return
    assert migrate_legacy_records(root) == 1
    assert migrate_legacy_records(root) == 0
    record = load_preprocessed_catalogue(root).find(data, street)
    assert record.svg_path == record.editable_svg_path == canonical
    assert record.approved_svg_path is None
    assert canonical.read_bytes() == approved.read_bytes() == payload
    assert old_edit.read_bytes() == before[old_edit] and corrected.read_bytes() == before[corrected]
    with Image.open(record.preview_path) as preview:
        assert preview.convert('RGBA').getpixel((247, 231)) == (0, 0, 255, 255)
    assert preprocess.resolve_authoritative_face_svg(root, data, street).path == canonical
    revert_generated(data, street, root)
    assert canonical.read_bytes() == before[canonical]


def test_configured_inkscape_overrides_discovery(manual, monkeypatch):
    root, _, _, workspace = manual
    executable = root / 'inkscape.exe'
    executable.touch()
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.shutil.which', lambda name: pytest.fail('override wins'))
    calls = []
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.subprocess.Popen', lambda args, **kw: calls.append(args))
    launch_inkscape(workspace, executable)
    assert calls[0] == [str(executable), str(workspace.working_svg)]


def test_missing_inkscape_reports_browse_instruction(manual, monkeypatch):
    _, _, _, workspace = manual
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.find_inkscape_executable', lambda configured=None: None)
    with pytest.raises(FileNotFoundError, match='Choose the Inkscape executable'):
        launch_inkscape(workspace)


def test_missing_canonical_does_not_open_backup(manual):
    _, _, _, workspace = manual
    workspace.working_svg.unlink()
    with pytest.raises(ValueError, match='integrity'):
        workspace.create_or_get_working_edit()
    assert workspace.generated_svg.is_file()

@pytest.mark.parametrize('original_suffix', ['', '.generated'])
@pytest.mark.parametrize('entry', ['catalogue', 'preview'])
def test_migration_preserves_legacy_generated_original_before_overwriting_canonical(tmp_path, original_suffix, entry):
    data = load_dataset(Path(__file__).parent / 'fixtures/workflow_v6_valid')
    street = data.streets[0]
    folder = tmp_path / 'faces' / data.id
    folder.mkdir(parents=True)
    canonical = folder / '0001_st_johns.svg'
    generated = folder / f'0001_st_johns{original_suffix}.svg'
    approved = folder / '0001_st_johns.approved.svg'
    original = svg('red').encode()
    edited = (svg('blue') + '\r\n<!-- legacy bytes -->').encode()
    generated.write_bytes(original)
    approved.write_bytes(edited)
    (tmp_path / preprocess.INDEX_FILENAME).write_text(json.dumps({'records': [{
        'dataset_id': data.id, 'street_id': street.id, 'success': True,
        'production_state': 'MANUAL_APPROVED',
        'svg_path': approved.relative_to(tmp_path).as_posix(),
        'approved_svg_path': approved.relative_to(tmp_path).as_posix(),
        'generated_svg_path': generated.relative_to(tmp_path).as_posix(),
    }]}))
    if entry == 'catalogue':
        record = load_preprocessed_catalogue(tmp_path).find(data, street)
        assert record.svg_path == canonical
    else:
        panel = render_authoritative_face_panel(tmp_path, data, street)
        assert panel.resolution.path == canonical
        assert panel.image.getpixel((247, 231)) == (0, 0, 255, 255)
    record = load_preprocessed_catalogue(tmp_path).find(data, street)
    assert record.generated_svg_path.read_bytes() == original
    assert canonical.read_bytes() == approved.read_bytes() == edited
    revert_generated(data, street, tmp_path)
    assert canonical.read_bytes() == original


def test_second_save_during_render_is_not_overwritten_or_published_with_old_preview(manual, monkeypatch):
    root, data, street, workspace = manual
    monitor = EditSaveMonitor(workspace, data, street, root)
    record = load_preprocessed_catalogue(root).find(data, street)
    previous_preview = record.preview_path.read_bytes()
    render = preprocess._write_preview
    def second_save(payload, destination):
        render(payload, destination)
        workspace.working_svg.write_text(svg('yellow'))
    monkeypatch.setattr(preprocess, '_write_preview', second_save)
    workspace.working_svg.write_text(svg('blue'))
    assert not monitor.poll()
    with pytest.raises(ValueError, match='waiting for the latest save'):
        monitor.poll()
    assert workspace.working_svg.read_text() == svg('yellow')
    assert record.preview_path.read_bytes() == previous_preview
    monkeypatch.setattr(preprocess, '_write_preview', render)
    assert not monitor.poll()
    assert monitor.poll()
    with Image.open(record.preview_path) as preview:
        assert preview.convert('RGBA').getpixel((247, 231)) == (255, 255, 0, 255)


def test_selecting_face_before_timer_reports_invalid_save_recovery(manual):
    from test_preprocessed_ui import _controller, _Var
    root, data, street, workspace = manual
    app = _controller(load_preprocessed_catalogue(root), street)
    app.state.selected_dataset = data
    app.state.selected_street = street
    app.current_face_var = _Var()
    warnings = []
    app._show_error = warnings.append
    original = workspace.working_svg.read_bytes()
    workspace.working_svg.write_text('<invalid')
    app._refresh_current_face_label()
    assert workspace.working_svg.read_bytes() == original
    assert len(warnings) == 1 and 'last-known-good SVG restored' in warnings[0]
