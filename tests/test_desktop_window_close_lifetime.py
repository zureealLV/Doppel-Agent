"""C FIRST original launch/window close definitions, ALL UNRUN.

Local window/kernel proxies ONLY. No WebView/WinForms launch or native drain proof.
"""

import sys
from types import SimpleNamespace

import pytest

import doppel_agent.desktop as desktop
import doppel_agent.projects.desktop_kernel as kernel_module


@pytest.mark.parametrize("phase", ["host", "window", "event", "start"])
def test_original_launch_scope_unwinds_same_started_kernel_when_host_or_window_setup_fails(
    tmp_path, monkeypatch, phase
):
    calls, kernels = [], []

    class Kernel:
        url = "http://127.0.0.1:12345/"

        def __init__(self, workspace):
            kernels.append(self)

        def start(self):
            calls.append(("start kernel", self))

        def close(self):
            calls.append(("close kernel", self))

    original = desktop.WindowApi

    def host(kernel, **kwargs):
        if phase == "host":
            raise OSError("PRIVATE_HOST_SETUP")
        return original(kernel, **kwargs)

    class Event:
        def __iadd__(self, callback):
            if phase == "event":
                raise OSError("PRIVATE_EVENT_ATTACH")
            return self

    def create(*args, **kwargs):
        if phase == "window":
            raise OSError("PRIVATE_WINDOW_SETUP")
        return SimpleNamespace(events=SimpleNamespace(shown=Event(), closing=Event(), closed=Event()))

    def start(**kwargs):
        if phase == "start":
            raise OSError("PRIVATE_WEBVIEW_START")

    monkeypatch.setattr(kernel_module, "DesktopKernel", Kernel)
    monkeypatch.setattr(desktop, "WindowApi", host)
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create, start=start))
    monkeypatch.setattr(desktop, "import_module", lambda name: object())
    with pytest.raises(OSError):
        desktop.launch_desktop(tmp_path)
    assert calls == [("start kernel", kernels[0]), ("close kernel", kernels[0])]


def host_fixture():
    calls = []
    kernel = SimpleNamespace(
        url="http://127.0.0.1:12345/", close=lambda: calls.append("original kernel close")
    )
    host = desktop.WindowApi(kernel)
    window = SimpleNamespace(
        destroy=lambda: calls.append("destroy"), minimize=lambda: calls.append("minimize")
    )
    return host, window, calls


def test_original_close_button_drains_before_destroy_and_native_reentry_does_not_deadlock(monkeypatch):
    host, window, calls = host_fixture()

    def destroy():
        assert host._projects_closed.is_set()
        assert host._native_closing() is True  # Same-thread callback while action lock held.
        calls.append("destroy")

    window.destroy = destroy
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[window]))
    assert host.window_action("close")
    assert calls == ["original kernel close", "destroy"]
    host._close_projects()
    assert calls == ["original kernel close", "destroy"]
    assert host._native_closing() is True
    with pytest.raises(ValueError, match="unknown window action"):
        host.window_action("delete-files")  # Preserve invalid-action contract even after close.


def test_original_os_close_vetoes_pending_cleanup_and_joins_same_kernel_on_explicit_close_again(monkeypatch):
    host, window, calls = host_fixture()
    first = True

    def close():
        nonlocal first
        calls.append("join original kernel")
        if first:
            first = False
            raise RuntimeError("PRIVATE_DRAIN_PENDING")

    host._kernel.close = close
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[window]))
    assert host._native_closing() is False
    assert host._closing.is_set() and not host._projects_closed.is_set()
    assert host.window_action("minimize") is False
    assert not host.project_list()["ok"] and not host.project_choose()["ok"]
    assert not host.project_switch("a" * 32, True)["ok"]
    assert host._native_closing() is True
    assert calls == ["join original kernel", "join original kernel"]
    host._close_projects()
    assert len(calls) == 2  # A returned close doesn't run again at launch finally.


def test_original_busy_os_close_returns_false_without_waiting_for_js_action_lock():
    host, window, calls = host_fixture()
    host._action_lock.acquire()
    try:
        assert host._native_closing() is False
        assert host._closing.is_set() and calls == []
    finally:
        host._action_lock.release()
    assert host._native_closing() is True and calls == ["original kernel close"]


def test_original_close_button_failure_never_destroys_window(monkeypatch):
    host, window, calls = host_fixture()

    def close():
        calls.append("original failed cleanup")
        raise RuntimeError("PRIVATE_CLEANUP_UNKNOWN")

    host._kernel.close = close
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[window]))
    assert host.window_action("close") is False
    assert host._closing.is_set() and not host._projects_closed.is_set()
    assert calls == ["original failed cleanup"]


@pytest.mark.parametrize("effect_first", [False, True])
def test_original_destroy_exception_keeps_same_window_attempt_without_second_destroy(
    monkeypatch, effect_first
):
    host, window, calls = host_fixture()

    def destroy():
        calls.append("original destroy attempt")
        if effect_first:
            host._native_closed(window)  # Original identity event, not simulated OS proof.
        raise OSError("PRIVATE_WINDOW_DESTROY")

    window.destroy = destroy
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[window]))
    assert host.window_action("close") is False
    source = host._window_lifetime
    assert source.window is window and source.destroy_attempted and not source.destroy_returned
    assert source.destroy_uncertain and source.closed_observed.is_set() is effect_first
    assert host._projects_closed.is_set()  # Separate resource close from window disposal receipt.
    assert host.window_action("close") is False
    assert host.window_action("minimize") is False
    host._close_projects()
    assert calls == ["original kernel close", "original destroy attempt"]
    assert host._native_closing() is True  # OS close may proceed; no second JS destroy/cleanup.


def test_returned_original_destroy_is_request_receipt_not_closed_event_or_second_destroy(monkeypatch):
    host, window, calls = host_fixture()
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[window]))
    assert host.window_action("close") is True
    source = host._window_lifetime
    assert source.destroy_attempted and source.destroy_returned and not source.destroy_uncertain
    assert not source.closed_observed.is_set()
    host._native_closed(SimpleNamespace())
    assert not source.closed_observed.is_set()  # Foreign event cannot settle original window.
    assert host.window_action("close") is True
    assert calls == ["original kernel close", "destroy"]
    host._native_closed(window)
    assert source.closed_observed.is_set() and source.window is window


def test_same_original_window_is_retained_when_global_window_list_changes(monkeypatch):
    host, window, calls = host_fixture()
    host._bind_native_window(window)
    foreign = SimpleNamespace(destroy=lambda: calls.append("foreign destroy"))
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[foreign]))
    assert host.window_action("close") is True
    assert calls == ["original kernel close", "destroy"]
    assert host._window_lifetime.window is window


def test_original_closed_event_fences_controls_without_claiming_project_cleanup(monkeypatch):
    host, window, calls = host_fixture()
    host._bind_native_window(window)
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[window]))
    host._native_closed(window)
    assert host._closing.is_set() and not host._projects_closed.is_set()
    assert host.window_action("minimize") is False
    assert not host.project_list()["ok"]
    assert host.window_action("close") is True
    assert calls == ["original kernel close"]  # Still drains original resources, no destroy replay.
    assert host._projects_closed.is_set() and not host._window_lifetime.destroy_attempted


def test_original_launch_binds_same_closed_event_identity_before_webview_start(tmp_path, monkeypatch):
    calls, windows = [], []

    class Kernel:
        url = "http://127.0.0.1:12345/"

        def __init__(self, workspace):
            pass

        def start(self):
            pass

        def close(self):
            calls.append("kernel close")

    class Event:
        def __init__(self):
            self.callbacks = []

        def __iadd__(self, callback):
            self.callbacks.append(callback)
            return self

    def create(*args, **kwargs):
        window = SimpleNamespace(
            events=SimpleNamespace(shown=Event(), closing=Event(), closed=Event()), host=kwargs["js_api"]
        )
        windows.append(window)
        return window

    def start(**kwargs):
        window = windows[0]
        assert window.host._window_lifetime.window is window
        assert window.events.closed.callbacks == [window.host._native_closed]
        window.events.closed.callbacks[0](window)
        assert window.host._window_lifetime.closed_observed.is_set()

    monkeypatch.setattr(kernel_module, "DesktopKernel", Kernel)
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create, start=start))
    monkeypatch.setattr(desktop, "import_module", lambda name: object())
    desktop.launch_desktop(tmp_path)
    assert calls == ["kernel close"]
