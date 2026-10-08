"""D1b S9 definitions: owned metadata grants, fake Git, no native commands."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.projects.desktop_kernel import DesktopKernel
from doppel_agent.provider import MockProvider
from doppel_agent.runtime import service as module
from doppel_agent.runtime.service import RunService
from doppel_agent.workspace.git_authorization import select_metadata_root


def linked(tmp_path):
    workspace, common = tmp_path / "project", tmp_path / "bare metadata"
    workspace.mkdir()
    local = common / "worktrees" / "fixture"
    local.mkdir(parents=True)
    (workspace / ".git").write_text(f"gitdir: {local}\n", encoding="utf-8")
    (local / "commondir").write_text("../..\n", encoding="utf-8")
    (local / "gitdir").write_text(f"{workspace / '.git'}\n", encoding="utf-8")
    return workspace, common, local


def test_ephemeral_grant_binds_actual_inspector_clears_on_change_close_and_is_not_restored(tmp_path, monkeypatch):
    workspace, common, local = linked(tmp_path)
    calls = []

    class Inspector:
        def __init__(self, workspace, **kwargs):
            calls.append(kwargs)

        async def status(self, **kwargs):
            return {"available": False, "reason": "fixture_not_git_proof", "repository_clean": None}

    monkeypatch.setattr(module, "GitInspector", Inspector)

    async def scenario():
        service = RunService(workspace, provider=MockProvider())
        try:
            grant = await service._grant_git_metadata(select_metadata_root(common))
            assert grant["active"] and not grant["persistent"] and not grant["command_or_workspace_write_grant"]
            assert (await service._get_git_metadata_grant()) == grant
            await service.git_status()
            assert calls[-1]["authorized_metadata_roots"] == (common,)
            assert calls[-1]["linked_binding"].local == local
            count = len(calls)
            (workspace / ".git").write_text(f"gitdir: {common / 'worktrees' / 'other'}\n", encoding="utf-8")
            invalid = await service.git_status()
            assert not invalid["available"] and invalid["reason"] == "git_metadata_authorization_changed"
            assert len(calls) == count and service._git_metadata_roots == () and service._git_metadata_binding is None
            (workspace / ".git").write_text(f"gitdir: {local}\n", encoding="utf-8")
            await service._grant_git_metadata(select_metadata_root(common))
        finally:
            await service.close()
        assert service._git_metadata_roots == () and service._git_metadata_binding is None
        reconstructed = RunService(workspace, provider=MockProvider())
        try:
            await reconstructed.start()
            assert not (await reconstructed._get_git_metadata_grant())["active"]
        finally:
            await reconstructed.close()

    asyncio.run(scenario())


def test_revoke_is_narrowing_only_under_quarantine_never_restarts_or_reopens_admission(tmp_path, monkeypatch):
    workspace, common, _ = linked(tmp_path)

    async def scenario():
        service = RunService(workspace, provider=MockProvider())
        try:
            await service._grant_git_metadata(select_metadata_root(common))
            service.process_supervisor._cleanup_failed = True  # Fake only, no process exists.

            async def forbidden():
                pytest.fail("revocation started/recovered resources")

            monkeypatch.setattr(service, "start", forbidden)
            assert not (await service._revoke_git_metadata())["active"]
            assert service._owner.held and service.process_supervisor.cleanup_failed
        finally:
            monkeypatch.undo()
            service.process_supervisor._cleanup_failed = False
            await service.close()

    asyncio.run(scenario())


def test_revocation_joins_original_git_reader_before_success_and_no_new_root_is_used(tmp_path, monkeypatch):
    workspace, common, _ = linked(tmp_path)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        inspected = []

        class Inspector:
            def __init__(self, workspace, **kwargs):
                inspected.append(kwargs["authorized_metadata_roots"])

            async def status(self, **kwargs):
                entered.set()
                await release.wait()
                return {"available": False, "reason": "fixture_not_git_proof"}

        monkeypatch.setattr(module, "GitInspector", Inspector)
        service = RunService(workspace, provider=MockProvider())
        read = revoke = None
        try:
            await service._grant_git_metadata(select_metadata_root(common))
            read = asyncio.create_task(service.git_status())
            await asyncio.wait_for(entered.wait(), 10)
            revoke = asyncio.create_task(service._revoke_git_metadata())
            await asyncio.sleep(0)
            assert not revoke.done() and service._owner.held and service._git_metadata_roots == (common,)
            release.set()
            await read
            assert not (await revoke)["active"]
            await service.git_status()
            assert inspected == [(common,), ()]
        finally:
            release.set()
            for task in (read, revoke):
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_actual_kernel_bridge_dispatches_only_original_lifespan_loop_no_http_authority(tmp_path, monkeypatch):
    workspace, common, _ = linked(tmp_path)
    kernel = DesktopKernel(workspace)
    kernel.owner.path.parent.mkdir()
    kernel.owner.acquire()
    app = create_app(workspace, provider=MockProvider(), workspace_owner=kernel.owner.borrow())
    kernel.app = app
    kernel.api = SimpleNamespace(started=True, should_exit=False)
    calls = []
    original = app.state.run_service._grant_git_metadata

    async def observed(selected):
        calls.append((asyncio.get_running_loop(), threading.get_ident()))
        return await original(selected)

    monkeypatch.setattr(app.state.run_service, "_grant_git_metadata", observed)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            grant = kernel._native_git_metadata("grant", select_metadata_root(common))
            assert grant["active"] and calls == [(app.state.runtime_loop, app.state.runtime_thread_id)]
            assert calls[0][1] != threading.get_ident()
            for path in ("/api/v1/changes/metadata-authorizations", "/api/v1/changes/metadata-authorize"):
                response = client.post(path, json={"path": str(common), "confirmed": True})
                assert response.status_code == 404 and response.headers["cache-control"] == "no-store"
            assert kernel._native_git_metadata("read") == grant
            assert not kernel._native_git_metadata("revoke")["active"]
            kernel._closing.set()
            with pytest.raises(RuntimeError, match="unavailable"):
                kernel._native_git_metadata("grant", select_metadata_root(common))
        assert app.state.runtime_loop is None and kernel.owner.held
        assert app.state.run_service._git_metadata_roots == ()
    finally:
        kernel.owner.release()


def test_cancelled_grant_worker_drains_before_close_owner_release_and_never_publishes_late_authority(tmp_path, monkeypatch):
    workspace, common, _ = linked(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = module.capture_linked_metadata

    def gated(*args):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("fixture metadata gate timeout")
        return original(*args)

    monkeypatch.setattr(module, "capture_linked_metadata", gated)

    async def scenario():
        service = RunService(workspace, provider=MockProvider())
        grant = closing = None
        try:
            await service.start()
            grant = asyncio.create_task(service._grant_git_metadata(select_metadata_root(common, workspace=workspace)))
            assert await asyncio.to_thread(entered.wait, 10)
            grant.cancel()
            await asyncio.sleep(0)
            grant.cancel()
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(0)
            assert not grant.done() and not closing.done() and service._owner.held
            release.set()
            error = (await asyncio.gather(grant, return_exceptions=True))[0]
            assert isinstance(error, asyncio.CancelledError)
            await closing
            assert service.cleanup_complete and not service._owner.held and service._git_metadata_binding is None
        finally:
            release.set()
            for task in (grant, closing):
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())
