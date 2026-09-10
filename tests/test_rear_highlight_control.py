from dataclasses import fields, replace

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
def test_prepared_preview_single_and_batch_render_selected_width(prepared, monkeypatch, provider):
    root, data, _, _ = prepared
    data = replace(data, streets=data.streets[:1])
    selected = DesignOptions(rear_highlight_weight=1.25)
    seen = []
    original = preprocessed_export.render_context_map_result
    def capture(dataset, street, options):
        seen.append(options)
        return original(dataset, street, options)
    monkeypatch.setattr(preprocessed_export, 'render_context_map_result', capture)
    preview = render_prepared_preview_pair(root, data, data.streets[0], design_options=selected)
    single = preprocessed_export.export_preprocessed_provider_png(root, data, data.streets[0], root / 'single.png', profile_id=provider, design_options=selected)
    plan = batch_export.build_batch_plan(root, data, provider, root / 'batch', design_options=selected)
    result = batch_export.execute_batch_export(plan)
    assert result.summary['exported'] == 1
    expected = build_render_options(selected, area=data.display_name).context_options
    assert seen == [expected, expected, expected]
    from PIL import Image
    with Image.open(single) as single_image, Image.open(plan.items[0].destination) as batch_image:
        expected_size = (2362, 1063) if provider == PROVIDERS[0] else (2475, 1155)
        assert single_image.size == batch_image.size == expected_size
        assert single_image.tobytes() == batch_image.tobytes()
    baseline = preprocessed_export.render_preprocessed_wrap(root, data, data.streets[0])
    explicit_default = preprocessed_export.render_preprocessed_wrap(root, data, data.streets[0], design_options=DesignOptions())
    assert baseline.tobytes() == explicit_default.tobytes()
    assert preview.wrap.tobytes() != baseline.tobytes()


def test_batch_start_uses_width_selected_after_planning(panel, prepared, monkeypatch):
    root, data, _, _ = prepared
    panel.app.state.selected_dataset = data
    panel.plan = batch_export.build_batch_plan(root, data, PROVIDERS[0], root / 'batch', design_options=DesignOptions())
    panel.app.state.design_options = DesignOptions(rear_highlight_weight=1.25)
    calls = capture_threads(monkeypatch)
    panel.start_batch()
    assert calls[0]['args'][1].design_options == panel.app.state.design_options


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
