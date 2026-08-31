from __future__ import annotations

import queue
from types import SimpleNamespace

import mug_previewer.ui.app as app_module
from mug_previewer.ui.app import MugPreviewerApp


class _Root:
    def __init__(self) -> None:
        self.after_calls: list[tuple[object, ...]] = []

    def after(self, *args: object) -> str:
        self.after_calls.append(args)
        return "after-id"


def _controller(data: object, street: object) -> MugPreviewerApp:
    controller = MugPreviewerApp.__new__(MugPreviewerApp)
    controller.root = _Root()
    controller.state = SimpleNamespace(selected_dataset=data, selected_street=street)
    controller._render_generation = 1
    controller._render_results = queue.SimpleQueue()
    return controller


def test_render_worker_queues_success_and_tk_poller_applies_it(monkeypatch) -> None:
    data = SimpleNamespace(id="area", display_name="Area")
    street = SimpleNamespace(id="0001", display_name="Ready")
    controller = _controller(data, street)
    pair = object()
    finished: list[tuple[object, object, object]] = []
    controller._render_finished = lambda received_pair, received_data, received_street: finished.append(
        (received_pair, received_data, received_street),
    )
    monkeypatch.setattr(app_module, "render_preview_pair", lambda *_args, **_kwargs: pair)

    controller._render_worker(1, data, street, object())

    assert controller.root.after_calls == []
    assert finished == []
    controller._drain_render_results()

    assert finished == [(pair, data, street)]
    assert controller.root.after_calls == [(25, controller._drain_render_results)]


def test_render_worker_queues_failure_for_tk_poller(monkeypatch) -> None:
    data = SimpleNamespace(id="area", display_name="Area")
    street = SimpleNamespace(id="0001", display_name="Broken")
    controller = _controller(data, street)
    failures: list[str] = []
    controller._render_failed = failures.append

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("render input invalid")

    monkeypatch.setattr(app_module, "render_preview_pair", fail)
    controller._render_worker(1, data, street, object())

    assert controller.root.after_calls == []
    assert failures == []
    controller._drain_render_results()

    assert failures == ["render input invalid"]
    assert controller.root.after_calls == [(25, controller._drain_render_results)]


def test_render_worker_does_not_call_finished_or_failed_directly(monkeypatch) -> None:
    data = SimpleNamespace(id="area", display_name="Area")
    street = SimpleNamespace(id="0001", display_name="Ready")
    controller = _controller(data, street)
    completed: list[object] = []
    controller._render_finished = lambda *_args: completed.append("finished")
    controller._render_failed = lambda *_args: completed.append("failed")
    monkeypatch.setattr(app_module, "render_preview_pair", lambda *_args, **_kwargs: object())

    controller._render_worker(1, data, street, object())

    assert completed == []
    assert controller.root.after_calls == []


def test_stale_render_result_is_ignored_by_tk_poller() -> None:
    data = SimpleNamespace(id="area", display_name="Area")
    street_a = SimpleNamespace(id="0001", display_name="Old")
    street_b = SimpleNamespace(id="0002", display_name="New")
    controller = _controller(data, street_a)
    finished: list[object] = []
    controller._render_finished = lambda *_args: finished.append("finished")
