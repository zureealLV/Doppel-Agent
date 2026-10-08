"""C2c2a FIRST definitions, ALL UNRUN; original offline resources only.

Admission refuses a new observed boundary. It is not transport receipts, hidden
retry coverage, durable Legacy recovery, native validation or physical drain.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from doppel_agent.api import create_app
from doppel_agent.projects.desktop_kernel import DesktopKernel
from doppel_agent.provider import Message, ModelTurn, ToolCall
from doppel_agent.runtime.provider_recording import ProviderReceiptError, ProviderReceiptFault
from doppel_agent.web.server import ConsoleHandler, JobManager


def test_native_startup_and_same_live_runtime_fault_fence_original_legacy(tmp_path):
    kernel = DesktopKernel(tmp_path)
    kernel.owner.path.parent.mkdir()
    kernel.owner.acquire()
    service = None

    async def original_service():
        nonlocal service
        kernel.app = create_app(tmp_path, workspace_owner=kernel.owner.borrow())
        service = kernel.app.state.run_service
        kernel.api = SimpleNamespace(started=True, should_exit=False)
        with pytest.raises(RuntimeError, match="provider_receipt_unavailable"):
            kernel._assert_legacy_admission()
        await service.start()
        try:
            kernel.app.state.runtime_loop = asyncio.get_running_loop()
            kernel._assert_legacy_admission()
            original_loop = kernel.app.state.runtime_loop
            kernel.app.state.runtime_loop = None
            with pytest.raises(RuntimeError, match="provider_receipt_unavailable"):
                kernel._assert_legacy_admission()
            kernel.app.state.runtime_loop = original_loop
            service._provider_receipt_fault.mark_failed()
            with pytest.raises(ProviderReceiptError):
                kernel._assert_legacy_admission()
            assert not service.cleanup_complete and kernel.owner.held
        finally:
            try:
                await service.close()
            finally:
                kernel.app.state.runtime_loop = None

    try:
        with pytest.raises(RuntimeError, match="provider_receipt_unavailable"):
            kernel._assert_legacy_admission()
        asyncio.run(original_service())
        assert service.cleanup_complete and kernel.owner.held
        with pytest.raises(RuntimeError, match="provider_receipt_unavailable"):
            kernel._assert_legacy_admission()
    finally:
        kernel.owner.release()


def test_original_native_manager_refuses_mutations_before_private_lookup(tmp_path, monkeypatch):
    fault = ProviderReceiptFault()
    fault.mark_failed()
    manager = JobManager(tmp_path, effect_admission=fault.check)

    def forbidden(*args, **kwargs):
        raise AssertionError("private lookup or mutation forbidden")

    monkeypatch.setattr(manager, "_provider", forbidden)
    monkeypatch.setattr(manager.settings, "save_profile", forbidden)
    try:
        for operation in (
            lambda: manager.probe({}),
            lambda: manager.submit({}),
            lambda: manager.save_settings({"config": {}}),
        ):
            with pytest.raises(ProviderReceiptError):
                operation()
        assert manager._pending_requests == 0 and not manager.jobs
        assert isinstance(manager.public_settings(), dict)
    finally:
        manager.close_owned()


def test_same_entered_provider_call_settles_but_next_observed_call_is_refused(tmp_path):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Provider:
        def next_turn(self, messages, tools):
            calls.append("one original call")
            entered.set()
            release.wait()
            return ModelTurn("exact original returned reply")

    provider = manager._admitted_provider(Provider())
    try:
        with ThreadPoolExecutor(max_workers=1) as fixture:
            original = fixture.submit(provider.next_turn, [Message("user", "offline")], [])
            try:
                assert entered.wait(2)
                fault.mark_failed()
                with pytest.raises(ProviderReceiptError):
                    provider.next_turn([], [])
                assert calls == ["one original call"] and not original.done()
            finally:
                release.set()
            assert original.result(timeout=3).content == "exact original returned reply"
    finally:
        release.set()
        manager.close_owned()


def test_original_http_mutation_fenced_but_existing_approval_route_keeps_original_lease(tmp_path):
    fault = ProviderReceiptFault()
    fault.mark_failed()
    manager = JobManager(tmp_path, effect_admission=fault.check)
    handler = object.__new__(ConsoleHandler)
    handler.server = SimpleNamespace(manager=manager)
    observed = []
    handler._json = lambda status, payload: observed.append((status, payload))

    class Body:
        def read(self, size):
            assert manager._pending_requests == 1
            return b'{"allow": false}'

    handler.rfile = Body()
    handler.headers = {"Content-Length": "16"}
    handler.path = "/api/settings/profiles/fixture/delete"
    try:
        with manager.owned_request():
            handler._do_POST_owned()
        assert observed == [(503, {"error": "provider_receipt_unavailable"})]
        observed.clear()

        def decide(run_id, approval_id, allow):
            assert manager._pending_requests == 1
            assert (run_id, approval_id, allow) == ("original", "fixture", False)
            return True

        manager.decide = decide
        handler.path = "/api/runs/original/approvals/fixture/decision"
        with manager.owned_request():
            handler._do_POST_owned()
        assert observed == [(200, {"ok": True})] and manager._pending_requests == 0
    finally:
        manager.close_owned()


def test_original_core_post_model_guard_refuses_new_tool_after_same_owner_fault(tmp_path, monkeypatch):
    import doppel_agent.web.server as server_module

    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check)
    checks = []

    class Core:
        mcp_cleanup_failed = False  # Synthetic Core has no MCP lifetime.
        mcp_execution_failed = False  # SAME synthetic no-MCP gate, not outcome evidence.
        mcp_publication_failed = False  # SAME no-MCP fixture; no acknowledgement/receipt proof.
        task_persistence_failed = (
            False  # Same synthetic Core has no task SQL scope; keep original admission gate.
        )
        storage_persistence_failed = False  # SAME synthetic no-file-Core scope; original gate unchanged.

        def __init__(self, workspace, provider, *, check_cancelled, **kwargs):
            checks.append(check_cancelled)
            self.provider, self.guard = provider, check_cancelled

        def run(self, prompt, *, run_id, history):
            fault.mark_failed()
            self.guard()  # Original loop's post-model/pre-tool boundary, no new tool IO.
            raise AssertionError("new tool entry forbidden")

    monkeypatch.setattr(server_module, "Core", Core)
    try:
        record = manager.submit({"prompt": "offline", "config": {"provider": "mock"}})
        manager.close_owned()
        assert len(checks) == 1 and callable(checks[0])
        assert manager.status(record["run_id"])["status"] == "failed"
    finally:
        manager.close_owned()


def test_actual_original_core_post_model_boundary_refuses_returned_tool_call(tmp_path, monkeypatch):
    from doppel_agent.tools import ToolRegistry

    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check)
    calls = []
    tools = []
    submitted = []
    original_submit = manager.pool.submit

    def observe_submit(*args, **kwargs):
        future = original_submit(*args, **kwargs)
        submitted.append(future)
        return future

    monkeypatch.setattr(manager.pool, "submit", observe_submit)

    class Provider:
        def next_turn(self, messages, schemas):
            calls.append("exact original method")
            fault.mark_failed()
            return ModelTurn("offline", (ToolCall("fixture", "read_file", {"path": "fixture.txt"}),))

    def forbidden(*args, **kwargs):
        tools.append("forbidden new tool")
        raise AssertionError("new tool entry")

    monkeypatch.setattr(manager, "_provider", lambda config: Provider())
    monkeypatch.setattr(ToolRegistry, "execute", forbidden)
    try:
        record = manager.submit({"prompt": "offline", "config": {}})
        assert len(submitted) == 1
        submitted[0].result(timeout=10)
        manager.close_owned()
        result = manager.status(record["run_id"])
        assert calls == ["exact original method"] and tools == []
        assert result["status"] == "failed" and "provider_receipt_unavailable" in result["answer"]
    finally:
        manager.close_owned()
