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
    for name in ('artwork_var', 'qa_display_var', 'qa_status_var', 'qa_note_var', 'review_target_var'):
        setattr(controller, name, Var())
    controller.artwork_buttons = {key: Widget() for key in ('edit', 'working', 'repair', 'corrected', 'approve', 'approved', 'folder', 'refresh')}
    controller.save_review_button = Widget()
    controller.front_card = Widget()
    controller._show_error = lambda error: controller.status_var.set(error)
    controller._select_street()
    return controller


@pytest.mark.parametrize('state,enabled', [(Status.MANUAL_REVIEW, True), (Status.AUTO_APPROVED, False),
                                           (Status.MANUAL_APPROVED, False), (Status.UNRENDERABLE_INPUT, False)])
def test_manual_action_states_and_production_label(tmp_path, state, enabled):
    # Legacy manually approved records with no indexed generated source fail closed.
    controller = panel(tmp_path, state)
    assert controller.artwork_buttons['edit'].state == ('normal' if enabled else 'disabled')
    assert controller.artwork_buttons['approve'].state == 'disabled'
    assert state.value in controller.artwork_var.value
    if state is Status.MANUAL_APPROVED:
        assert controller.artwork_buttons['approved'].state == 'normal'
        assert controller.artwork_buttons['edit'].text == 'Edit Again in Inkscape'
        assert controller.front_card.text == 'Approved Preview'


def test_working_corrected_states_and_transient_preview_reset(tmp_path, monkeypatch):
    controller = panel(tmp_path, Status.MANUAL_REVIEW)
    workspace = controller._workspace()
    workspace.create_or_get_working_edit().write_text(svg('blue'))
    controller._refresh_artwork()
    assert controller.artwork_buttons['working'].state == 'normal'
    assert controller.artwork_buttons['repair'].state == 'normal'
    assert controller.artwork_buttons['approve'].state == 'disabled'
    monkeypatch.setattr(panel_module, 'rasterize_face_svg', lambda payload: Image.new('RGBA', (495, 462), 'blue'))
    before = (tmp_path / 'preprocess_index.json').read_bytes()
    controller._preview_artwork('working')
    assert controller.front_card.text == 'Working Edit Preview'
    assert controller.export_button.state == 'disabled'
    assert not (tmp_path / 'review_index.json').exists()
    assert (tmp_path / 'preprocess_index.json').read_bytes() == before
    controller._repair_artwork()
    assert controller.front_card.text == 'Corrected SVG Preview'
    assert controller.artwork_buttons['approve'].state == 'normal'
    controller._select_street()
    assert controller.front_card.text == 'Generated Preview'
    assert controller._review_target is None


@pytest.mark.parametrize('status', ['pass', 'overlap', 'duplicates', 'missing', 'other', 'Do Not Use'])
def test_review_status_gates_buttons_and_stale_label(tmp_path, status):
    controller = panel(tmp_path, Status.AUTO_APPROVED)
    path = controller._formal_review_svg()
    save_review_record(tmp_path, 'area', '0001', status, path)
    controller._select_street()
    assert controller.export_button.state == ('normal' if status == 'pass' else 'disabled')
    assert status in controller.qa_display_var.value
    path.write_text(svg('blue'))
    controller._select_street()
    assert '(stale)' in controller.qa_display_var.value
    assert controller.export_button.state == 'disabled'
    assert controller.printify_export_button.state == 'disabled'


def test_approval_reloads_state_and_enables_edit_again(tmp_path):
    controller = panel(tmp_path, Status.MANUAL_REVIEW)
    controller._workspace().create_or_get_working_edit().write_text(svg('blue'))
    controller._workspace().repair()
    controller._approve_artwork()
    assert controller._selected_artwork_record().state is Status.MANUAL_APPROVED
    assert 'MANUAL_APPROVED' in controller.artwork_var.value
    assert controller.front_card.text == 'Approved Preview'
    assert controller.artwork_buttons['edit'].state == 'normal'
    assert controller.export_button.state == 'disabled'
    assert 'NOT REVIEWED' in controller.qa_display_var.value
    assert controller.state.current_front_preview.getpixel((247, 231)) == (0, 0, 255, 255)


def test_approval_failure_does_not_claim_approved(tmp_path, monkeypatch):
    controller = panel(tmp_path, Status.MANUAL_REVIEW)
    controller._workspace().create_or_get_working_edit()
    controller._workspace().repair()
    def fail(*args, **kwargs):
        raise ValueError('test approval failure')
    monkeypatch.setattr('mug_previewer.manual_svg_workspace.approve_manual_svg', fail)
    controller._approve_artwork()
    assert controller._selected_artwork_record().state is Status.MANUAL_REVIEW
    assert controller.export_button.state == 'disabled'
    assert 'test approval failure' in controller.status_var.value


def test_next_manual_review_skips_other_states_and_stops(tmp_path):
    controller = panel(tmp_path, Status.MANUAL_REVIEW)
    payload = json.loads((tmp_path / 'preprocess_index.json').read_text())
    for index, state in enumerate(['AUTO_APPROVED', 'MANUAL_APPROVED', 'UNRENDERABLE_INPUT', 'MANUAL_REVIEW'], 2):
        payload['records'].append(dict(payload['records'][0], street_id=f'{index:04d}', production_state=state))
    (tmp_path / 'preprocess_index.json').write_text(json.dumps(payload))
    controller.state.filtered_streets = [SimpleNamespace(id=f'{i:04d}', display_name=f'Street {i}') for i in range(1, 6)]
    controller.state.selected_street = controller.state.filtered_streets[0]
    controller.street_list.selection_clear = lambda *args: None
    controller.street_list.selection_set = lambda index: setattr(controller.street_list, 'index', index)
    controller.street_list.see = lambda index: None
    controller._next_manual_review()
    assert controller.state.selected_street.id == '0005'
    controller._next_manual_review()
    assert 'No manual-review streets remaining' in controller.status_var.value
    assert controller.state.selected_street.id == '0005'


def test_explicit_working_review_hashes_viewed_asset_and_becomes_stale_for_production(tmp_path):
    controller = panel(tmp_path, Status.MANUAL_REVIEW)
    controller._workspace().create_or_get_working_edit().write_text(svg('blue'))
    controller._preview_artwork('working')
    controller.qa_status_var.set('pass')
    controller.qa_note_var.set('Working review')
    controller._save_artwork_review()
    review = current_review_state(tmp_path, 'area', '0001', controller._workspace().working_svg)
    assert not review.stale and review.record.note == 'Working review'
    assert current_review_state(tmp_path, 'area', '0001', controller._formal_review_svg()).stale


def test_changed_working_svg_requires_preview_before_review_save(tmp_path):
    controller = panel(tmp_path, Status.MANUAL_REVIEW)
    working = controller._workspace().create_or_get_working_edit()
    controller._preview_artwork('working')
    working.write_text(svg('blue'))
    controller._save_artwork_review()
    assert 'changed since preview' in controller.status_var.value
    assert not (tmp_path / 'review_index.json').exists()


def test_manual_filter_limits_list_and_clears_transient_preview(tmp_path):
    controller = panel(tmp_path, Status.MANUAL_REVIEW)
    review_street = controller.state.filtered_streets[0]
    auto_street = SimpleNamespace(id='0002', display_name='Automatic')
    controller.state.selected_dataset.streets = [review_street, auto_street]
    controller.search_var = Var()
    controller.workflow_var = SimpleNamespace(get=lambda: 'Manual Review')
    from mug_previewer.batch_export import Eligibility
    controller.workflow_items = {'0001': SimpleNamespace(eligibility=Eligibility.MANUAL_REVIEW)}
    controller._production_status_generation = 0
    inserted = []
    controller.street_list.delete = lambda *args: None
    controller.street_list.insert = lambda *args: inserted.append(args[-1])
    controller._apply_filter()
    assert controller.state.filtered_streets == [review_street]
    assert len(inserted) == 1
    assert controller.state.selected_street is None
    assert controller.artwork_buttons['edit'].state == 'disabled'


def test_cached_png_cannot_grant_pass_to_unseen_changed_svg(tmp_path):
    controller = panel(tmp_path, Status.AUTO_APPROVED)
    path = controller._formal_review_svg()
    path.write_text(svg('blue'))
    controller._select_street()  # Still displays the old cached preview.
    assert controller.save_review_button.state == 'disabled'
    controller.qa_status_var.set('pass')
    controller.qa_note_var.set('')
    controller._save_artwork_review()
    assert 'Preview Current SVG for QA' in controller.status_var.value
    assert not (tmp_path / 'svg_review_results.json').exists()
    controller._preview_artwork('authoritative')
    assert controller.state.current_front_preview.getpixel((247, 231)) == (0, 0, 255, 255)
    assert controller.save_review_button.state == 'normal'
    controller._save_artwork_review()
    assert current_review_state(tmp_path, 'area', '0001', path).production_export_allowed
    assert controller.export_button.state == 'normal'
