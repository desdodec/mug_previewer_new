from types import SimpleNamespace
from dataclasses import replace
from concurrent.futures import Future
import tkinter as tk
import pytest
from test_manual_svg_workspace import manual, svg
from test_preprocessed_ui import _controller, _Var
from mug_previewer.ui.face_grid import FaceGrid
from mug_previewer.ui.state import load_preprocessed_catalogue
from mug_previewer.manual_svg_workspace import EditSaveMonitor
from mug_previewer.preprocess import resolve_authoritative_face_svg


@pytest.fixture
def selection_app(manual):
    path, data, street, workspace = manual
    app = _controller(load_preprocessed_catalogue(path), street)
    app.state.selected_dataset = data
    app.current_face_var = _Var()
    app._shutting_down = False
    app.workflow_items = {}
    root = tk.Tk()
    root.withdraw()
    app.street_list = tk.Listbox(root, exportselection=False)
    app.street_list.insert(0, street.display_name)
    app.face_grid = FaceGrid(root, app)
    other = replace(street, id='9999')
    app.state.filtered_streets = [street, other]
    app.street_list.insert(1, other.display_name)
    app.state.selected_street = other
    app.street_list.selection_set(1)
    app.face_grid.set_streets([street, other])
    try:
        yield app, street, workspace
    finally:
        app.face_grid.executor.shutdown(wait=True)
        root.destroy()


def test_card_click_selects_global_street_and_cached_preview(selection_app):
    app, street, _ = selection_app
    card = app.face_grid.cards[0][0]
    # Invoke the actual Tk click binding, including its captured street.
    command = card.bind('<Button-1>').split('[', 1)[1].split()[0]
    card.tk.call(command, *(['0'] * 19))
    assert app.state.selected_street == street
    assert app.street_list.curselection() == (0,)
    assert card.cget('text') == 'Current face'


def test_list_selection_highlights_grid(selection_app):
    app, street, _ = selection_app
    app.street_list.selection_clear(0, tk.END)
    app.street_list.selection_set(0)
    app._select_street()
    assert app.state.selected_street == street
    assert app.face_grid.cards[0][0].cget('text') == 'Current face'


def test_preview_mug_uses_global_selection(selection_app, monkeypatch):
    app, street, _ = selection_app
    app.face_grid.select(street)
    app.state.design_options = None
    calls = []
    monkeypatch.setattr('mug_previewer.ui.app.threading.Thread',
                        lambda **kwargs: SimpleNamespace(start=lambda: calls.append(kwargs)))
    app._start_render()
    assert calls[0]['args'][2] is app.state.selected_street


def test_save_refreshes_generated_label_to_edited(selection_app):
    app, street, workspace = selection_app
    app.face_grid.select(street)
    assert app.current_face_var.value.endswith('Using: Generated face')
    working = workspace.create_or_get_working_edit()
    app.face_grid.watch_edit(workspace, app.state.selected_dataset, street)
    working.write_text(svg('blue'))
    monitors = tuple(app.face_grid.monitors.items())
    assert app.face_grid.check_saves(monitors) == ([], [])
    app.face_grid.pending = Future()
    app.face_grid.poll()
    assert app.status_var.value == 'Edit detected \u2014 updating face...'
    app.face_grid.pending.set_result(app.face_grid.check_saves(monitors))
    app._reload_workflow = lambda: None
    app.face_grid.monitors.clear()
    app.face_grid.poll()
    assert app.current_face_var.value.endswith('Using: Edited face')
    assert app.status_var.value == 'Face updated from Inkscape.'


@pytest.mark.parametrize('valid', [True, False])
def test_reopened_unpromoted_edit_is_recovered_safely(manual, valid):
    root, data, street, workspace = manual
    original = workspace.generated_svg.read_bytes()
    working = workspace.create_or_get_working_edit()
    working.write_text(svg('blue') if valid else '<invalid')
    workspace.create_or_get_working_edit()
    monitor = EditSaveMonitor(workspace, data, street, root)
    assert not monitor.poll()
    if valid:
        assert monitor.poll()
        assert b'blue' in resolve_authoritative_face_svg(root, data, street).path.read_bytes()
        assert load_preprocessed_catalogue(root).find(data, street).preview_path.is_file()
        reopened = EditSaveMonitor(workspace, data, street, root)
        assert not reopened.poll()
        assert not reopened.poll()
    else:
        with pytest.raises(Exception):
            monitor.poll()
        assert resolve_authoritative_face_svg(root, data, street).path.read_bytes() == original
    assert workspace.generated_svg.read_bytes() == original
