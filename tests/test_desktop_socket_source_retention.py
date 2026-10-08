"""C original listener allocation/close definitions FIRST, ALL UNRUN.

Local API proxies/disposable owner ONLY; not OS port, worker or native proof.
"""

from types import SimpleNamespace

import pytest

import doppel_agent.desktop as desktop
from doppel_agent.projects.desktop_kernel import DesktopKernel


@pytest.mark.parametrize("phase", ["restricted", "bind", "listen"])
@pytest.mark.parametrize("closed_then_throw", [False, True])
def test_original_browser_socket_failed_close_keeps_same_candidate_and_no_second_factory(
    monkeypatch, phase, closed_then_throw
):
    calls, allocated = [], []

    class Socket:
        closed = False

        def __init__(self, *args):
            allocated.append(self)

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            if phase == "bind":
                raise OSError("PRIVATE_BIND")

        def getsockname(self):
            return ("127.0.0.1", 6665 if phase == "restricted" else 12345)

        def listen(self, backlog):
            if phase == "listen":
                raise OSError("PRIVATE_LISTEN")

        def close(self):
            calls.append(self)
            self.closed = closed_then_throw
            raise OSError("PRIVATE_CLOSE")

    monkeypatch.setattr(desktop.socket, "socket", Socket)
    with pytest.raises(desktop.BrowserSocketCleanupError) as error:
        desktop._bind_browser_api_socket()
    source = error.value.source
    assert source.resource is allocated[0] and len(allocated) == 1
    assert source.factory_attempted and source.factory_returned and source.close_attempted
    assert source.cleanup_uncertain and not source.close_returned and calls == [source.resource]
    assert "PRIVATE_" not in str(error.value)
    with pytest.raises(desktop.BrowserSocketCleanupError):
        desktop._close_original_browser_socket(source)
    assert calls == [source.resource]


@pytest.mark.parametrize("phase", ["opaque", "unusable"])
def test_original_socket_factory_unknown_retains_pre_factory_source_without_inventing_close(
    monkeypatch, phase
):
    calls = []
    resource = object()

    def factory(*args):
        calls.append(args)
        if phase == "opaque":
            raise OSError("PRIVATE_ALLOCATION_NO_RETURN")
        return resource

    monkeypatch.setattr(desktop.socket, "socket", factory)
    with pytest.raises(desktop.BrowserSocketCleanupError) as error:
        desktop._bind_browser_api_socket()
    source = error.value.source
    assert source.factory_attempted and source.factory_returned is (phase == "unusable")
    assert source.resource is (resource if phase == "unusable" else None)
    assert source.cleanup_uncertain and not source.close_attempted and len(calls) == 1
    with pytest.raises(desktop.BrowserSocketCleanupError):
        desktop._close_original_browser_socket(source)
    assert not source.close_attempted and len(calls) == 1


@pytest.mark.parametrize("phase", ["before", "closed_then_throw"])
def test_original_kernel_listener_close_failure_keeps_same_socket_owner_and_attempt(tmp_path, phase):
    kernel = DesktopKernel(tmp_path)
    kernel.owner.path.parent.mkdir()
    kernel.owner.acquire()
    calls = []

    class Socket:
        closed = False

        def close(self):
            calls.append(self)
            self.closed = phase == "closed_then_throw"
            raise OSError("PRIVATE_API_SOCKET_CLOSE")

    original = kernel.api_socket = Socket()
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="listener cleanup is unresolved") as error:
                kernel.close()
            assert "PRIVATE_" not in str(error.value)
        assert (
            calls == [original] and kernel.api_socket is original and kernel.owner.held and not kernel._closed
        )
        assert kernel._api_socket_close_attempted and not kernel._api_socket_close_returned
        assert kernel._startup_cleanup_sources[id(original)] is original
    finally:
        kernel.owner.release()  # Isolated fixture teardown only, no production recovery.


def test_original_kernel_unknown_socket_factory_keeps_source_before_start_unwind(tmp_path, monkeypatch):
    import doppel_agent.api as api_module
    import doppel_agent.projects.desktop_kernel as kernel_module

    calls = []

    class Worker:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

        def join(self, timeout):
            pass

        def is_alive(self):
            return False

    legacy = SimpleNamespace(
        server_port=12345,
        serve_forever=lambda: None,
        shutdown=lambda: calls.append("shutdown"),
        server_close=lambda: calls.append("server close"),
    )
    monkeypatch.setattr(desktop, "ConsoleServer", lambda *args, **kwargs: legacy)
    monkeypatch.setattr(
        api_module, "create_app", lambda *args, **kwargs: SimpleNamespace(state=SimpleNamespace())
    )
    monkeypatch.setattr(kernel_module.threading, "Thread", Worker)

    def opaque(*args):
        raise OSError("PRIVATE_SOCKET_LOST_RETURN")

    monkeypatch.setattr(desktop.socket, "socket", opaque)
    kernel = DesktopKernel(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="startup cleanup is unresolved"):
            kernel.start()
        source = next(iter(kernel._startup_cleanup_sources.values()))
        assert source.factory_attempted and not source.factory_returned and source.cleanup_uncertain
        assert kernel.api_socket is None and kernel.owner.held and not kernel._closed
        assert calls == ["shutdown", "server close"]
        with pytest.raises(RuntimeError, match="startup cleanup is unresolved"):
            kernel.close()
        assert calls == ["shutdown", "server close"]
    finally:
        kernel.owner.release()
