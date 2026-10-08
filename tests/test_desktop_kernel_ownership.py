from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.persistence.ownership import WorkspaceOwner
from doppel_agent.projects.desktop_kernel import DesktopKernel
from doppel_agent.provider import MockProvider


def test_borrowed_runtime_owner_survives_lifespan_cleanup(tmp_path):
    root = tmp_path / ".doppel-agent"
    root.mkdir()
    owner = WorkspaceOwner(root / "local-mode.lock")
    owner.acquire()
    try:
        with TestClient(create_app(tmp_path, provider=MockProvider(), workspace_owner=owner.borrow()),
                        base_url="http://127.0.0.1") as client:
            assert client.get("/api/v1/health").status_code == 200
        assert owner.held
        contender = WorkspaceOwner(owner.path)
        with pytest.raises(RuntimeError, match="owned"):
            contender.acquire()
    finally:
        owner.release()


def test_borrowed_owner_cannot_acquire_without_outer_ownership(tmp_path):
    owner = WorkspaceOwner(tmp_path / "lock")
    with pytest.raises(RuntimeError, match="not held"):
        owner.borrow().acquire()


def test_native_metadata_bridge_completed_timeout_error_is_not_an_endless_pending_poll(tmp_path, monkeypatch):
    from concurrent.futures import Future
    import doppel_agent.projects.desktop_kernel as kernel_module

    kernel = DesktopKernel(tmp_path)
    kernel.owner.path.parent.mkdir()
    kernel.owner.acquire()
    kernel.api = SimpleNamespace(started=True, should_exit=False)
    loop = SimpleNamespace(is_running=lambda: True)
    service = SimpleNamespace(_grant_git_metadata=lambda *a: None, _revoke_git_metadata=lambda: None,
                              _get_git_metadata_grant=lambda: None)
    kernel.app = SimpleNamespace(state=SimpleNamespace(runtime_loop=loop, runtime_thread_id=-1, run_service=service))
    future = Future()
    future.set_exception(TimeoutError("private fixture not echoed by WindowApi"))

    def schedule(operation, target):
        assert target is loop
        operation.close()  # Fake dispatch, no original coroutine is abandoned.
        return future

    monkeypatch.setattr(kernel_module.asyncio, "run_coroutine_threadsafe", schedule)
    try:
        with pytest.raises(TimeoutError):
            kernel._native_git_metadata("read")
    finally:
        kernel.owner.release()


def test_mismatched_owner_rejected_before_target_state_created(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    owner = WorkspaceOwner(tmp_path / "other.lock")
    with pytest.raises(ValueError, match="must match"):
        create_app(target, workspace_owner=owner.borrow())
    assert not (target / ".doppel-agent").exists()


class Worker:
    def __init__(self, live=False):
        self.live = live
        self.joins = 0

    def join(self, timeout):
        self.joins += 1

    def is_alive(self):
        return self.live


def configured_kernel(tmp_path):
    kernel = DesktopKernel(tmp_path)
    kernel.owner.path.parent.mkdir()
    kernel.owner.acquire()
    socket_calls = []
    kernel.api_socket = SimpleNamespace(close=lambda: socket_calls.append("close"))
    kernel.api = SimpleNamespace(should_exit=False)
    kernel.api_worker = Worker()
    kernel._api_started = True
    kernel._legacy_closer = Worker()
    kernel._legacy_closer_started = True
    kernel.app = SimpleNamespace(state=SimpleNamespace(run_service=SimpleNamespace(cleanup_complete=True)))
    return kernel, socket_calls


@pytest.mark.parametrize("pending_worker", ["api_worker", "_legacy_closer"])
def test_kernel_retains_owner_and_listener_until_actual_workers_finish(tmp_path, pending_worker):
    kernel, socket_calls = configured_kernel(tmp_path)
    getattr(kernel, pending_worker).live = True
    with pytest.raises(RuntimeError, match="draining"):
        kernel.close()
    assert kernel.owner.held and not socket_calls
    getattr(kernel, pending_worker).live = False
    kernel.close()
    assert not kernel.owner.held and socket_calls == ["close"]
    kernel.close()
    assert socket_calls == ["close"]


def test_failed_runtime_cleanup_keeps_outer_owner(tmp_path):
    kernel, socket_calls = configured_kernel(tmp_path)
    kernel.app.state.run_service.cleanup_complete = False
    try:
        with pytest.raises(RuntimeError, match="runtime cleanup failed"):
            kernel.close()
        assert kernel.owner.held and not socket_calls
    finally:
        kernel.owner.release()


def test_failed_legacy_cleanup_keeps_outer_owner(tmp_path):
    kernel, socket_calls = configured_kernel(tmp_path)
    kernel._legacy_error = RuntimeError("PRIVATE_PATH")
    try:
        with pytest.raises(RuntimeError, match="Legacy cleanup failed") as error:
            kernel.close()
        assert "PRIVATE_PATH" not in str(error.value)
        assert kernel.owner.held and not socket_calls
    finally:
        kernel.owner.release()


def test_terminal_api_thread_before_lifespan_closes_unstarted_service(tmp_path):
    kernel, socket_calls = configured_kernel(tmp_path)
    service = kernel.app.state.run_service
    service.cleanup_complete = False
    kernel.app.state.runtime_lifespan_entered = False
    closed = []

    async def close():
        closed.append("close")
        service.cleanup_complete = True

    service.close = close
    kernel.close()
    assert closed == ["close"] and socket_calls == ["close"]
    assert not kernel.owner.held


def test_entered_lifespan_cleanup_failure_never_retried_on_foreign_loop(tmp_path):
    kernel, socket_calls = configured_kernel(tmp_path)
    kernel.app.state.runtime_lifespan_entered = True
    kernel.app.state.run_service.cleanup_complete = False
    calls = []

    async def unsafe_close():
        calls.append("foreign-loop")

    kernel.app.state.run_service.close = unsafe_close
    try:
        with pytest.raises(RuntimeError, match="runtime cleanup failed"):
            kernel.close()
        assert not calls and not socket_calls and kernel.owner.held
    finally:
        kernel.owner.release()


@pytest.mark.parametrize("phase", ["legacy", "app", "socket", "api"])
def test_partial_kernel_startup_unwinds_owned_resources(tmp_path, monkeypatch, phase):
    import doppel_agent.api as api_module
    import doppel_agent.desktop as desktop_module
    import doppel_agent.projects.desktop_kernel as kernel_module
    import uvicorn

    calls = []

    class ImmediateWorker:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

        def join(self, timeout):
            pass

        def is_alive(self):
            return False

    class Legacy:
        server_port = 54321
        manager = SimpleNamespace(close_owned=lambda: calls.append("drain-legacy"))

        def __init__(self, *args, effect_admission, effect_failure):
            # Native gate exists at original Legacy construction, before app/API.
            assert callable(effect_admission)
            assert callable(effect_failure)
            with pytest.raises(RuntimeError, match='provider_receipt_unavailable'):
                effect_admission()
            if phase == "legacy":
                raise RuntimeError("fixture startup failure")

        def serve_forever(self):
            pass

        def shutdown(self):
            calls.append("shutdown-legacy")

        def server_close(self):
            calls.append("close-legacy")

    service = SimpleNamespace(cleanup_complete=False)

    async def close_service():
        calls.append("close-service")
        service.cleanup_complete = True

    service.close = close_service

    def app(*args, **kwargs):
        if phase == "app":
            raise RuntimeError("fixture startup failure")
        return SimpleNamespace(state=SimpleNamespace(run_service=service, runtime_lifespan_entered=False))

    def bind():
        if phase == "socket":
            raise RuntimeError("fixture startup failure")
        return SimpleNamespace(close=lambda: calls.append("close-socket"))

    class Api:
        started = False
        should_exit = False

        def __init__(self, config):
            pass

        def run(self, sockets):
            raise RuntimeError("fixture API failed before lifespan")

    monkeypatch.setattr(kernel_module.threading, "Thread", ImmediateWorker)
    monkeypatch.setattr(desktop_module, "ConsoleServer", Legacy)
    monkeypatch.setattr(api_module, "create_app", app)
    monkeypatch.setattr(desktop_module, "_bind_browser_api_socket", bind)
    monkeypatch.setattr(uvicorn, "Config", lambda *args, **kwargs: None)
    monkeypatch.setattr(uvicorn, "Server", Api)
    kernel = DesktopKernel(tmp_path)
    with pytest.raises(RuntimeError):
        kernel.start()
    assert not kernel.owner.held
    assert ("close-legacy" in calls) == (phase != "legacy")
    assert ("close-service" in calls) == (phase in {"socket", "api"})
    assert ("close-socket" in calls) == (phase == "api")
    before = list(calls)
    kernel.close()
    assert calls == before
