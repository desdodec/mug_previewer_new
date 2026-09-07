from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from mug_previewer import preprocess
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.manual_svg_workspace import ManualSvgWorkspace, find_inkscape_executable, launch_inkscape
from mug_previewer.preprocessed_export import render_authoritative_face_panel, export_preprocessed_provider_png
from mug_previewer.svg_edit_repair import correct_edited_svg
from mug_previewer.ui.state import load_preprocessed_catalogue
from mug_previewer.review_index import current_review_state, save_review_record


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
    }]}))
    for target in ('mug_previewer.preprocess.render_face_svg', 'mug_previewer.preprocess.production_triage_state',
                   'mug_previewer.rendering.face.render_face', 'mug_previewer.rendering.face.render_face_svg'):
        monkeypatch.setattr(target, lambda *a, **k: pytest.fail('must not generate or score'))
    catalogue = load_preprocessed_catalogue(tmp_path)
    return tmp_path, data, street, ManualSvgWorkspace.from_record(catalogue.find(data, street))


def test_working_copy_is_exact_and_repeated_open_retains_edits(manual):
    root, data, street, workspace = manual
    original = workspace.generated_svg.read_bytes()
    working = workspace.create_or_get_working_edit()
    assert working.name == 'face_edit.svg'
    assert working.read_bytes() == original
    working.write_text(svg('blue'))
    assert workspace.create_or_get_working_edit() == working
    assert working.read_text() == svg('blue')
    assert workspace.generated_svg.read_bytes() == original
    assert not list(root.glob('*.tmp'))


def test_repeated_repair_retains_inputs_and_failed_repair_retains_correction(manual):
    root, _, _, workspace = manual
    original = workspace.generated_svg.read_bytes()
    working = workspace.create_or_get_working_edit()
    working.write_text(svg('blue'))
    corrected = workspace.repair()
    first = corrected.read_bytes()
    with pytest.raises(FileExistsError):
        correct_edited_svg(workspace.generated_svg, working, corrected)
    working.write_text(svg('yellow'))
    assert not workspace.correction_current
    workspace.repair()
    assert workspace.correction_current
    assert corrected.read_bytes() != first
    second = corrected.read_bytes()
    working.write_text('<invalid')
    with pytest.raises(Exception):
        workspace.repair()
    assert not workspace.correction_current
    assert corrected.read_bytes() == second
    assert working.read_text() == '<invalid'
    assert workspace.generated_svg.read_bytes() == original
    assert not list(root.rglob('*.tmp'))


def test_full_workflow_edit_again_and_provider_authority(manual):
    root, data, street, workspace = manual
    original = workspace.generated_svg.read_bytes()
    working = workspace.create_or_get_working_edit()
    working.write_text(svg('blue'))
    workspace.repair()
    approved = workspace.approve(data, street, root)
    assert approved.state.value == 'MANUAL_APPROVED'
    assert approved.path.read_bytes() == workspace.corrected_svg.read_bytes()
    assert preprocess.resolve_authoritative_face_svg(root, data, street).path == approved.path
    assert render_authoritative_face_panel(root, data, street).image.getpixel((247, 231)) == (0, 0, 255, 255)
    workspace = ManualSvgWorkspace.from_record(load_preprocessed_catalogue(root).find(data, street))
    assert workspace.create_or_get_working_edit() == working
    working.write_text(svg('yellow'))
    with pytest.raises(ValueError, match='Repair'):
        workspace.approve(data, street, root)
    assert render_authoritative_face_panel(root, data, street).image.getpixel((247, 231)) == (0, 0, 255, 255)
    workspace.repair()
    assert render_authoritative_face_panel(root, data, street).image.getpixel((247, 231)) == (0, 0, 255, 255)
    workspace.approve(data, street, root)
    assert render_authoritative_face_panel(root, data, street).image.getpixel((247, 231)) == (255, 255, 0, 255)
    assert workspace.generated_svg.read_bytes() == original
    from PIL import Image
    export_preprocessed_provider_png(root, data, street, root / 'out.png', profile_id='inkthreadable_11oz_white')
    with Image.open(root / 'out.png') as image:
        assert image.convert('RGBA').getpixel((472, 531)) == (255, 255, 0, 255)


@pytest.mark.parametrize('failure', ['preview', 'index'])
def test_failed_reapproval_keeps_previous_production_assets(manual, monkeypatch, failure):
    root, data, street, workspace = manual
    workspace.create_or_get_working_edit().write_text(svg('blue'))
    workspace.repair()
    workspace.approve(data, street, root)
    record = load_preprocessed_catalogue(root).find(data, street)
    paths = [record.approved_svg_path, record.preview_path, root / 'preprocess_index.json', workspace.generated_svg]
    previous = {path: path.read_bytes() for path in paths}
    workspace.working_svg.write_text(svg('yellow'))
    workspace.repair()
    def fail(*args, **kwargs):
        raise OSError('test failure')
    monkeypatch.setattr(preprocess, '_write_preview' if failure == 'preview' else '_write_index', fail)
    with pytest.raises(OSError, match='test failure'):
        workspace.approve(data, street, root)
    assert {path: path.read_bytes() for path in paths} == previous
    assert not list(root.rglob('*.tmp'))
    assert not list(root.glob('.approval-*'))


@pytest.mark.parametrize('which', ['original', 'working'])
def test_repair_replace_rejects_input_targets(manual, which):
    _, _, _, workspace = manual
    workspace.create_or_get_working_edit()
    output = workspace.generated_svg if which == 'original' else workspace.working_svg
    with pytest.raises(ValueError, match='separate'):
        correct_edited_svg(workspace.generated_svg, workspace.working_svg, output, replace=True)


def test_missing_generated_does_not_create_empty_working_file(manual):
    _, _, _, workspace = manual
    workspace.generated_svg.unlink()
    with pytest.raises(ValueError, match='integrity'):
        workspace.create_or_get_working_edit()
    assert not workspace.working_svg.exists()


def test_collision_with_another_indexed_generated_file_is_rejected(manual):
    from dataclasses import replace
    _, _, _, workspace = manual
    workspace.working_svg.write_text(svg('blue'))
    workspace = replace(workspace, protected_svg_paths=(workspace.working_svg,))
    with pytest.raises(ValueError, match='collides'):
        workspace.create_or_get_working_edit()
    assert workspace.working_svg.read_text() == svg('blue')


def test_inkscape_launch_is_nonblocking_and_reopens_same_working_file(manual, monkeypatch):
    _, _, _, workspace = manual
    calls = []
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.shutil.which', lambda name: '/installed/Inkscape.exe')
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.subprocess.Popen', lambda args, **kw: calls.append((args, kw)))
    assert find_inkscape_executable() == Path('/installed/Inkscape.exe')
    working = launch_inkscape(workspace)
    working.write_text(svg('blue'))
    assert launch_inkscape(workspace) == working
    assert calls == [([str(Path('/installed/Inkscape.exe')), str(working)], {'shell': False})] * 2
    assert working.read_text() == svg('blue')


def test_configured_inkscape_overrides_discovery(manual, monkeypatch):
    root, _, _, workspace = manual
    executable = root / 'inkscape.exe'
    executable.touch()
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.shutil.which', lambda name: pytest.fail('override wins'))
    calls = []
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.subprocess.Popen', lambda args, **kw: calls.append(args))
    launch_inkscape(workspace, executable)
    assert calls[0][0] == str(executable)


def test_missing_inkscape_reports_browse_instruction(manual, monkeypatch):
    _, _, _, workspace = manual
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.find_inkscape_executable', lambda configured=None: None)
    with pytest.raises(FileNotFoundError, match='Choose the Inkscape executable'):
        launch_inkscape(workspace)
    assert workspace.has_working


def test_approved_review_becomes_stale_after_new_approval(manual):
    root, data, street, workspace = manual
    workspace.create_or_get_working_edit()
    workspace.repair()
    approved = workspace.approve(data, street, root)
    save_review_record(root, data.id, street.id, 'pass', approved.path)
    assert not current_review_state(root, data.id, street.id, approved.path).export_blocked
    workspace.working_svg.write_text(svg('blue'))
    assert not current_review_state(root, data.id, street.id, approved.path).stale
    workspace.repair()
    workspace.approve(data, street, root)
    review = current_review_state(root, data.id, street.id, approved.path)
    assert review.stale and review.export_blocked
    with pytest.raises(ValueError, match='stale'):
        export_preprocessed_provider_png(root, data, street, root / 'blocked.png', profile_id='inkthreadable_11oz_white')
    assert not (root / 'blocked.png').exists()
