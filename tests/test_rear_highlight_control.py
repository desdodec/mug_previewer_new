from dataclasses import fields, replace
import queue
from types import SimpleNamespace

import pytest

from mug_previewer import batch_export, preprocessed_export
from mug_previewer.design import DesignOptions, build_render_options
from mug_previewer.rendering.artwork import render_wrap_result
from mug_previewer.rendering.context_map import _scale_highlight_stroke
from mug_previewer.ui.app import MugPreviewerApp
from mug_previewer.ui.state import AppState, render_prepared_preview_pair
from test_batch_export import prepared, PROVIDERS
from test_batch_export_ui import panel, Widget, capture_threads


def test_rear_control_updates_existing_design_options():
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    app.state = AppState()
    app.front_weight_var = Widget(1.0)
    app.rear_weight_var = Widget(1.25)
    app.front_weight_display = Widget()
    app.rear_weight_display = Widget()
    app._design_changed()
    assert app.state.design_options == DesignOptions(rear_highlight_weight=1.25)
    assert app.rear_weight_display.get() == '1.25\u00d7'


def test_rear_width_changes_no_other_render_options_or_geometry(prepared):
    _, data, _, _ = prepared
    street = data.streets[0]
    baseline_options = build_render_options(DesignOptions(), area=data.display_name)
    changed_options = build_render_options(DesignOptions(rear_highlight_weight=1.25), area=data.display_name)
    assert replace(changed_options, context_options=baseline_options.context_options) == baseline_options
    assert replace(changed_options.context_options, highlight_stroke_scale=baseline_options.context_options.highlight_stroke_scale) == baseline_options.context_options
    baseline = render_wrap_result(data, street, baseline_options)
    changed = render_wrap_result(data, street, changed_options)
    assert baseline.image.size == changed.image.size
    assert baseline.front_panel.tobytes() == changed.front_panel.tobytes()
    assert baseline.rear_panel.tobytes() != changed.rear_panel.tobytes()
    for field in fields(baseline.context):
        if field.name not in ('image', 'projected_highlight_width_px'):
            assert getattr(baseline.context, field.name) == getattr(changed.context, field.name)


def test_stroke_scaling_preserves_every_other_svg_attribute():
    svg = '<svg viewBox="0 0 100 100"><path d="M1 2 L30 40" stroke="red" class="highlighted-street" stroke-width="4.00"/><path d="M5 6 L70 80" stroke="black" stroke-width="2.00"/></svg>'
    assert _scale_highlight_stroke(svg, 1.25) == svg.replace('stroke-width="4.00"', 'stroke-width="5.00"')


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('weight, renderer_scale', [(1.0, 0.85), (0.75, 0.6375)])
def test_prepared_preview_single_and_batch_render_selected_width(prepared, monkeypatch, provider, weight, renderer_scale):
    root, data, records, write = prepared
    from test_batch_export import svg
    from mug_previewer.review_index import save_review_record
    edited = root / '0000.svg'
    edited.write_text(svg('blue'))
    records[0]['production_state'] = 'MANUAL_APPROVED'
    write()
    save_review_record(root, data.id, data.streets[0].id, 'pass', edited)
    before = svg_snapshot(root)
    def forbidden(*args, **kwargs):
        pytest.fail('Prepared faces must never be regenerated')
    for target in ('mug_previewer.rendering.artwork.render_face',
                   'mug_previewer.rendering.face.render_face_svg',
                   'mug_previewer.preprocess.render_face_svg'):
        monkeypatch.setattr(target, forbidden)
    data = replace(data, streets=data.streets[:1])
    app = design_controller()
    app.rear_weight_var.set(weight)
    app._design_changed()
    selected = app.state.design_options
    assert selected.rear_highlight_weight == weight
    assert svg_snapshot(root) == before
    seen = []
    original = preprocessed_export.render_context_map_result
    def capture(dataset, street, options):
        seen.append(options)
        return original(dataset, street, options)
    monkeypatch.setattr(preprocessed_export, 'render_context_map_result', capture)
    preview = render_prepared_preview_pair(root, data, data.streets[0], design_options=selected)
    single = preprocessed_export.export_preprocessed_provider_png(root, data, data.streets[0], root / 'single.png', profile_id=provider, design_options=selected)
    plan = batch_export.build_batch_plan(root, data, provider, root / 'batch', design_options=selected)
    assert plan.design_options == selected
    result = batch_export.execute_batch_export(plan)
    assert result.summary['exported'] == 1
    expected = build_render_options(selected, area=data.display_name).context_options
    assert seen == [expected, expected, expected]
    assert [options.highlight_stroke_scale for options in seen] == pytest.approx([renderer_scale] * 3)
    assert svg_snapshot(root) == before
    assert preview.wrap.getpixel((472, 531)) == (0, 0, 255, 255)
    from PIL import Image
    with Image.open(single) as single_image, Image.open(plan.items[0].destination) as batch_image:
        expected_size = (2362, 1063) if provider == PROVIDERS[0] else (2475, 1155)
        assert single_image.size == batch_image.size == expected_size
        assert single_image.tobytes() == batch_image.tobytes()
    baseline = preprocessed_export.render_preprocessed_wrap(root, data, data.streets[0])
    explicit_default = preprocessed_export.render_preprocessed_wrap(root, data, data.streets[0], design_options=DesignOptions())
    assert baseline.tobytes() == explicit_default.tobytes()
    assert (preview.wrap.tobytes() == baseline.tobytes()) == (weight == 1.0)
    assert svg_snapshot(root) == before


def test_batch_start_rejects_width_changed_without_invalidation(panel, prepared, monkeypatch):
    root, data, _, _ = prepared
    panel.app.state.selected_dataset = data
    panel.plan = batch_export.build_batch_plan(root, data, PROVIDERS[0], root / 'batch', design_options=DesignOptions())
    panel.app.state.design_options = DesignOptions(rear_highlight_weight=1.25)
    calls = capture_threads(monkeypatch)
    panel.start_batch()
    assert not calls
    assert panel.plan is None
    assert panel.start.options['state'] == 'disabled'


def test_prepared_workspace_exposes_rear_control(prepared):
    import tkinter as tk
    root_path, data, _, _ = prepared
    root = tk.Tk()
    root.withdraw()
    try:
        app = MugPreviewerApp(root, dataset_root=root_path, preprocessed=root_path)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        label = next(w for w in descendants(app) if 'text' in w.keys() and w.cget('text') == 'Rear highlighted-street width')
        assert label.master.winfo_manager() == 'grid'
        scale = next(w for w in label.master.winfo_children() if isinstance(w, tk.Scale))
        assert str(scale.cget('variable')) == str(app.rear_weight_var)
        assert scale.get() == 1.0
        scale.set(1.25)
        app._design_changed()
        assert app.state.design_options.rear_highlight_weight == 1.25
    finally:
        root.destroy()


def svg_snapshot(root):
    return {path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob('*.svg')}


def design_controller():
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    app.state = AppState()
    app.front_weight_var = Widget(1.0)
    app.rear_weight_var = Widget(1.0)
    app.front_weight_display = Widget()
    app.rear_weight_display = Widget()
    return app


@pytest.mark.parametrize('during_planning', [False, True])
def test_width_invalidates_plan_and_rebuild_captures_new_options(panel, prepared, monkeypatch, during_planning):
    root, data, _, _ = prepared
    app = design_controller()
    app.state.selected_dataset = data
    app.dataset_box = Widget()
    app._shutting_down = False
    app.preprocessed_catalogue = SimpleNamespace(root=root)
    app.batch_panel = panel
    panel.app = app
    old = batch_export.build_batch_plan(root, data, PROVIDERS[0], root / 'batch', design_options=app.state.design_options)
    panel.plan = old
    panel.busy = during_planning
    generation = panel.generation
    app.rear_weight_var.set(0.75)
    app._design_changed()
    assert panel.plan is None
    if during_planning:
        panel.events.put(('plan', generation, old))
        panel.drain()
        assert panel.plan is None and not panel.busy
    calls = capture_threads(monkeypatch)
    panel.start_batch()
    assert not calls
    assert panel.start.options['state'] == 'disabled'
    panel.refresh_plan()
    generation, args, options = calls.pop()['args']
    assert options['design_options'] == DesignOptions(rear_highlight_weight=0.75)
    panel.plan_worker(generation, args, options)
    panel.drain()
    assert panel.plan.design_options == app.state.design_options
    panel.start_batch()
    assert calls[-1]['args'][1].design_options.rear_highlight_weight == 0.75


def test_reset_width_invalidates_batch_plan(panel):
    app = design_controller()
    app.batch_panel = panel
    app.rear_weight_var.set(0.75)
    app._design_changed()
    panel.plan = object()
    app._reset_design()
    assert panel.plan is None
    assert app.state.design_options == DesignOptions()


def test_preview_button_passes_current_design_to_worker(prepared, monkeypatch):
    root, data, _, _ = prepared
    app = design_controller()
    app.state.selected_dataset = data
    app.state.selected_street = data.streets[0]
    app.rear_weight_var.set(0.75)
    app._design_changed()
    app._render_generation = 0
    app.render_button = Widget()
    app.status_var = Widget()
    app.preprocessed_catalogue = SimpleNamespace(root=root)
    app._render_results = queue.SimpleQueue()
    calls = capture_threads(monkeypatch)
    app._start_render()
    received = []
    monkeypatch.setattr('mug_previewer.ui.app.render_prepared_preview_pair',
                        lambda *args, **kwargs: received.append(kwargs['design_options']))
    calls[0]['target'](*calls[0]['args'])
    assert received == [DesignOptions(rear_highlight_weight=0.75)]


@pytest.mark.parametrize('provider, action', [
    (PROVIDERS[0], '_start_inkthreadable_export'),
    (PROVIDERS[1], '_start_printify_export'),
])
def test_single_export_button_passes_current_design_to_worker(prepared, monkeypatch, provider, action):
    root, data, _, _ = prepared
    app = design_controller()
    app.state.selected_dataset = data
    app.state.selected_street = data.streets[0]
    app.rear_weight_var.set(0.75)
    app._design_changed()
    app.preprocessed_catalogue = SimpleNamespace(root=root)
    app.current_production_status = SimpleNamespace(export_allowed=True)
    app.root = SimpleNamespace(after=lambda *args: None)
    app.status_var = Widget()
    app._set_export_buttons_state = lambda state: None
    app._qa_export_error = lambda: None
    monkeypatch.setattr('mug_previewer.ui.app.filedialog.asksaveasfilename', lambda **kwargs: str(root / 'single.png'))
    calls = capture_threads(monkeypatch)
    getattr(app, action)()
    received = []
    monkeypatch.setattr('mug_previewer.ui.app.export_preprocessed_provider_png',
                        lambda *args, **kwargs: received.append(kwargs))
    calls[0]['target'](*calls[0]['args'])
    assert received == [{'profile_id': provider, 'design_options': DesignOptions(rear_highlight_weight=0.75)}]
