from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from PIL import Image

from test_preprocessed_ui import _catalogue, _controller, _Var, _Widget
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus as Status
from mug_previewer.review_index import save_review_record, current_review_state
from mug_previewer.ui.state import load_preprocessed_catalogue
import mug_previewer.ui.artwork_panel as panel_module
from test_manual_svg_workspace import svg


class Var(_Var):
    def get(self):
        return self.value


class Widget(_Widget):
    def configure(self, **kwargs):
        super().configure(**kwargs)
        self.text = kwargs.get('text', getattr(self, 'text', ''))


def panel(tmp_path, state):
    catalogue = _catalogue(tmp_path, state)
    record = next(iter(catalogue.records.values()))
    for path in (record.generated_svg_path, record.svg_path, record.approved_svg_path):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(svg('red'))
    controller = _controller(catalogue, SimpleNamespace(id='0001', display_name='Street'))
    for name in ('artwork_var', 'qa_display_var', 'qa_status_var', 'qa_note_var', 'review_target_var', 'exclude_var'):
        setattr(controller, name, Var())
    controller.artwork_buttons = {key: Widget() for key in ('edit', 'working', 'repair', 'corrected', 'approve', 'approved', 'folder', 'refresh')}
    controller.save_review_button = Widget()
    controller.front_card = Widget()
    controller._show_error = lambda error: controller.status_var.set(error)
    controller._select_street()
    return controller


@pytest.mark.parametrize('status', ['pass', 'overlap', 'duplicates', 'missing', 'other', 'Do Not Use'])
def test_inclusion_gates_buttons_independent_of_hash(tmp_path, status):
    controller = panel(tmp_path, Status.AUTO_APPROVED)
    path = controller._formal_review_svg()
    save_review_record(tmp_path, 'area', '0001', status, path)
    path.write_text(svg('blue'))
    controller._select_street()
    assert controller.export_button.state == ('normal' if status == 'pass' else 'disabled')
    assert controller.exclude_var.value == (status != 'pass')


def test_default_inclusion_and_manual_edit_available(tmp_path):
    controller = panel(tmp_path, Status.MANUAL_REVIEW)
    assert controller.export_button.state == 'normal'
    assert controller.artwork_buttons['edit'].state == 'normal'
