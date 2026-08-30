from __future__ import annotations

import queue
from types import SimpleNamespace

import mug_previewer.ui.app as app_module
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus
from mug_previewer.ui.app import MugPreviewerApp
from mug_previewer.ui.production import ProductionStatus
from mug_previewer.ui import production


class _Var:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class _Widget:
    def __init__(self) -> None:
        self.state = "disabled"

    def configure(self, **kwargs: str) -> None:
        self.state = kwargs.get("state", self.state)


class _Root:
    def after(self, *_args: object) -> str:
        return "after-id"


def _status(readiness: str = "ready", title: str = "Ready for Production", *, export: bool = True, review: bool = False) -> ProductionStatus:
    return ProductionStatus(readiness, title, "detail", export, review)


def _controller(data: object, street: object) -> MugPreviewerApp:
    controller = MugPreviewerApp.__new__(MugPreviewerApp)
    controller.root = _Root()
    controller.state = SimpleNamespace(selected_dataset=data, selected_street=street)
    controller._production_status_generation = 0
    controller._production_status_cache = {}
    controller._production_status_results = queue.SimpleQueue()
    controller.current_production_status = None
    controller.status_var = _Var()
    controller.production_var = _Var()
    controller.render_button = _Widget()
    controller.export_button = _Widget()
    controller.printify_export_button = _Widget()
    controller.review_street_button = _Widget()
    return controller


class _InlineThread:
    def __init__(self, *, target, args, daemon) -> None:
        self.target, self.args = target, args

    def start(self) -> None:
        self.target(*self.args)


def test_scope_status_uses_validated_manifest_without_retriaging(monkeypatch, tmp_path) -> None:
    data = SimpleNamespace(id="hebden", display_name="Hebden Bridge")
    street = SimpleNamespace(id="0036", display_name="Burnley Road")
    monkeypatch.setattr(production, "_production_scope_index", lambda: {
        ("Hebden Bridge", "0036"): {
            "triage_status": "AUTO_APPROVED", "production_placement": "STANDARD", "reason_codes": "standard_healthy",
        },
    })
    monkeypatch.setattr(production, "select_production_placement", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not retriage scope row")))

    result = production.production_status(data, street, override_path=tmp_path / "overrides.json")

    assert (result.title, result.export_allowed, result.review_required) == ("Ready for Production", True, False)


def test_auto_status_worker_completion_enables_ready_actions(monkeypatch) -> None:
    data = SimpleNamespace(id="area", display_name="Area")
    street = SimpleNamespace(id="0001", display_name="Burnley-like")
    controller = _controller(data, street)
    monkeypatch.setattr(app_module.threading, "Thread", _InlineThread)
    monkeypatch.setattr(app_module, "production_status", lambda *_args: _status())

    controller._request_production_status(data, street)
    controller._drain_production_status_results()

    assert controller.production_var.value == "Ready for Production: detail"
    assert (controller.render_button.state, controller.export_button.state, controller.printify_export_button.state) == ("normal", "normal", "normal")
    assert controller.review_street_button.state == "disabled"


def test_worker_exception_reaches_terminal_failure_state(monkeypatch) -> None:
    data = SimpleNamespace(id="area", display_name="Area")
    street = SimpleNamespace(id="0001", display_name="Broken")
    controller = _controller(data, street)
    monkeypatch.setattr(app_module.threading, "Thread", _InlineThread)

    def fail(*_args: object) -> ProductionStatus:
        raise RuntimeError("diagnostic unavailable")

    monkeypatch.setattr(app_module, "production_status", fail)
    controller._request_production_status(data, street)
    controller._drain_production_status_results()

    assert controller.production_var.value.startswith("Status Check Failed:")
    assert (controller.render_button.state, controller.export_button.state, controller.printify_export_button.state, controller.review_street_button.state) == ("disabled", "disabled", "disabled", "disabled")


def test_stale_worker_result_cannot_overwrite_new_selection(monkeypatch) -> None:
    data = SimpleNamespace(id="area", display_name="Area")
    street_a = SimpleNamespace(id="0001", display_name="A")
    street_b = SimpleNamespace(id="0002", display_name="B")
    controller = _controller(data, street_a)
    pending = []

    class _QueuedThread(_InlineThread):
        def start(self) -> None:
            pending.append(self)

    monkeypatch.setattr(app_module.threading, "Thread", _QueuedThread)
    monkeypatch.setattr(app_module, "production_status", lambda _data, street: _status(title=f"Ready {street.id}"))
    controller._request_production_status(data, street_a)
    controller.state.selected_street = street_b
    controller._request_production_status(data, street_b)

    pending[0].target(*pending[0].args)
    controller._drain_production_status_results()
    assert controller.current_production_status is None
    pending[1].target(*pending[1].args)
    controller._drain_production_status_results()

    assert controller.production_var.value == "Ready 0002: detail"


def test_status_cache_reuse_and_override_invalidation(monkeypatch) -> None:
    data = SimpleNamespace(id="area", display_name="Area")
    street = SimpleNamespace(id="0001", display_name="Reviewable")
    controller = _controller(data, street)
    calls = 0

    def resolve(*_args: object) -> ProductionStatus:
        nonlocal calls
        calls += 1
        return _status()

    monkeypatch.setattr(app_module.threading, "Thread", _InlineThread)
    monkeypatch.setattr(app_module, "production_status", resolve)
    controller._request_production_status(data, street)
    controller._drain_production_status_results()
    controller._request_production_status(data, street)
    assert calls == 1

    controller._production_status_resolution_changed((data.id, street.id))
    controller._drain_production_status_results()
    assert calls == 2
def test_dataset_switch_invalidates_old_result(monkeypatch) -> None:
    data_a = SimpleNamespace(id="area-a", display_name="Area A")
    street_a = SimpleNamespace(id="0001", display_name="A")
    data_b = SimpleNamespace(id="area-b", display_name="Area B")
    street_b = SimpleNamespace(id="0001", display_name="B")
    controller = _controller(data_a, street_a)
    pending = []

    class _QueuedThread(_InlineThread):
        def start(self) -> None:
            pending.append(self)

    monkeypatch.setattr(app_module.threading, "Thread", _QueuedThread)
    monkeypatch.setattr(app_module, "production_status", lambda data, _street: _status(title=f"Ready {data.id}"))
    controller._request_production_status(data_a, street_a)
    controller.state.selected_dataset = data_b
    controller.state.selected_street = street_b
    controller._invalidate_active_production_status_request()
    controller._request_production_status(data_b, street_b)

    pending[0].target(*pending[0].args)
    controller._drain_production_status_results()
    assert controller.current_production_status is None
    pending[1].target(*pending[1].args)
    controller._drain_production_status_results()

    assert controller.production_var.value == "Ready area-b: detail"
