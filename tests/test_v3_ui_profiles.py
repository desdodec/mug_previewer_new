from types import SimpleNamespace

from mug_previewer.providers import get_provider_profile
from mug_previewer.ui.app import MugPreviewerApp
from mug_previewer.ui.batch_export_panel import BatchExportPanel


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_v3_print_profile_detail_is_explicit():
    profile = get_provider_profile("inkthreadable_11oz_white")
    detail = MugPreviewerApp._production_profile_detail_text(profile)
    assert "2550 × 1125 px @ 300 DPI" in detail
    assert "Front 25.00%" in detail
    assert "Rear 75.00%" in detail
    assert "Inward 0.00 mm" in detail


def test_prodigi_print_profile_selects_matching_preview_geometry():
    profile = get_provider_profile("prodigi_h_mug_w")
    calibration = MugPreviewerApp._preview_calibration_for_print_profile(profile)
    assert calibration.provider_name == "Prodigi"
    assert calibration.sku == "H-MUG-W"


def test_selected_production_profile_defaults_to_inkthreadable_without_widgets():
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    profile = app._selected_production_profile()
    assert profile.id == "inkthreadable_11oz_white"


def test_batch_profile_can_be_synchronised_from_workspace():
    panel = BatchExportPanel.__new__(BatchExportPanel)
    panel.provider = Value("Inkthreadable")
    calls = []
    panel.invalidate = lambda: calls.append(True)
    panel.set_provider_profile("prodigi_h_mug_w")
    assert panel.provider.get() == "Prodigi"
    assert calls == [True]
    panel.set_provider_profile("prodigi_h_mug_w")
    assert calls == [True]


def test_selected_export_action_uses_current_v3_profile():
    app = MugPreviewerApp.__new__(MugPreviewerApp)
    prodigi = get_provider_profile("prodigi_h_mug_w")
    label = MugPreviewerApp._production_profile_label(prodigi)
    app.production_profile_by_label = {label: prodigi}
    app.production_profile_var = Value(label)
    calls = []
    app._start_provider_export = lambda **kwargs: calls.append(kwargs)
    app._start_selected_profile_export()
    assert calls == [{
        "profile_id": "prodigi_h_mug_w",
        "provider_label": "Prodigi",
        "filename_suffix": "prodigi",
    }]



def test_batch_provider_change_updates_workspace_profile():
    panel = BatchExportPanel.__new__(BatchExportPanel)
    panel.provider = Value("Prodigi")
    panel.invalidate = lambda: None
    prodigi = get_provider_profile("prodigi_h_mug_w")
    ink = get_provider_profile("inkthreadable_11oz_white")
    prodigi_label = MugPreviewerApp._production_profile_label(prodigi)
    ink_label = MugPreviewerApp._production_profile_label(ink)
    app = SimpleNamespace(
        production_profile_by_label={ink_label: ink, prodigi_label: prodigi},
        production_profile_var=Value(ink_label),
    )
    calls = []
    app._production_profile_changed = lambda: calls.append(True)
    panel.app = app
    panel._provider_changed()
    assert app.production_profile_var.get() == prodigi_label
    assert calls == [True]
