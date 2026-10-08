"""C2c2c FIRST original call-site definitions, ALL UNRUN; offline gates only.

Original journal/worker/request retained. Not provider billing/SDK/native proof.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import pytest

from doppel_agent.persistence.legacy_recovery import LegacyOperationRecoveryQueries
from doppel_agent.provider import ModelTurn
from doppel_agent.runtime.provider_recording import ProviderReceiptFault
from doppel_agent.web.server import JobManager


def manager_fixture(tmp_path):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    return manager, fault


def frames(manager):
    return [
        json.loads(line)
        for path in manager.store.root.glob("runs/*/events.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if "legacy.operation_" in line
    ]


def observe_original_worker(manager, monkeypatch):
    submitted = []
    original = manager.pool.submit

    def submit(*args, **kwargs):
        future = original(*args, **kwargs)
        submitted.append(future)
        return future

    monkeypatch.setattr(manager.pool, "submit", submit)
    return submitted


def test_original_probe_start_precedes_provider_and_finish_request_is_retained_by_close(
    tmp_path, monkeypatch
):
    manager, fault = manager_fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    observed = []
    append = manager.store.append_operation

    def original_append(identity, operation, *, outcome=None):
        if outcome == "returned":
            entered.set()
            release.wait()
        return append(identity, operation, outcome=outcome)

    class Provider:
        def next_turn(self, messages, tools):
            observed.append(frames(manager)[0]["kind"])
            return ModelTurn("exact offline reply")

    monkeypatch.setattr(manager.store, "append_operation", original_append)
    monkeypatch.setattr(manager, "_provider", lambda config: Provider())
    with ThreadPoolExecutor(max_workers=2) as fixture:
        probe = fixture.submit(manager.probe, {})
        close = None
        try:
            assert entered.wait(2)
            assert LegacyOperationRecoveryQueries(manager.store.root).read().quarantined
            close = fixture.submit(manager.close_owned)
            with pytest.raises(FutureTimeoutError):
                close.result(timeout=0.05)
            assert manager._pending_requests == 1 and observed == ["legacy.operation_started"]
        finally:
            release.set()
        assert probe.result(timeout=3) == {"ok": True, "reply": "exact offline reply"}
        assert close is not None
        close.result(timeout=3)
    assert not fault.broken and not LegacyOperationRecoveryQueries(manager.store.root).read().quarantined
    assert len(frames(manager)) == 2 and not list(manager.store.root.glob("runs/*/session.json"))


@pytest.mark.parametrize("stage", ["start", "finish"])
def test_original_probe_append_failure_never_returns_success_retries_or_private_error(
    tmp_path, monkeypatch, stage
):
    manager, fault = manager_fixture(tmp_path)
    calls = []
    append = manager.store.append_operation

    def original_append(identity, operation, *, outcome=None):
        if (stage == "start" and outcome is None) or (stage == "finish" and outcome == "returned"):
            raise OSError("PRIVATE_FILE_KEY_OR_SDK_ERROR")
        return append(identity, operation, outcome=outcome)

    class Provider:
        def next_turn(self, messages, tools):
            calls.append("original entered")
            return ModelTurn("offline")

    monkeypatch.setattr(manager.store, "append_operation", original_append)
    monkeypatch.setattr(manager, "_provider", lambda config: Provider())
    with pytest.raises(RuntimeError, match="^legacy_operation_evidence_unavailable$") as failure:
        manager.probe({})
    assert "PRIVATE" not in str(failure.value) and fault.broken
    assert len(calls) == (stage == "finish") and manager._pending_requests == 0
    with pytest.raises(RuntimeError, match="legacy_operation_evidence_unavailable"):
        manager.save_settings({"config": {}})
    with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
        manager.close_owned()


def test_original_provider_failure_has_failed_operation_not_returned_or_automatic_retry(
    tmp_path, monkeypatch
):
    manager, fault = manager_fixture(tmp_path)
    calls = []

    class Provider:
        def next_turn(self, messages, tools):
            calls.append("original entered")
            raise ValueError("PRIVATE_SDK_RESPONSE")

    monkeypatch.setattr(manager, "_provider", lambda config: Provider())
    try:
        with pytest.raises(RuntimeError, match="^provider_probe_unavailable$"):
            manager.probe({})
        journal = frames(manager)
        assert calls == ["original entered"] and fault.broken and len(journal) == 2
        assert journal[1]["payload"]["outcome"] == "failed" and "PRIVATE" not in repr(journal)
        assert LegacyOperationRecoveryQueries(manager.store.root).read().quarantined
    finally:
        manager.close_owned()


def test_actual_original_core_journal_closes_after_original_session_not_on_enqueue(tmp_path, monkeypatch):
    manager, fault = manager_fixture(tmp_path)
    snapshots = []
    submitted = observe_original_worker(manager, monkeypatch)
    append = manager.store.append_operation

    class Provider:
        def next_turn(self, messages, tools):
            return ModelTurn("offline original Core result")

    def original_append(identity, operation, *, outcome=None):
        if outcome is not None:
            snapshots.append(manager.store.read_session(identity)["answer"])
        return append(identity, operation, outcome=outcome)

    monkeypatch.setattr(manager, "_provider", lambda config: Provider())
    monkeypatch.setattr(manager.store, "append_operation", original_append)
    try:
        record = manager.submit({"prompt": "offline", "config": {}})
        assert len(submitted) == 1
        submitted[0].result(timeout=10)  # Join actual Core before closing future admission.
        manager.close_owned()
        assert manager.status(record["run_id"])["status"] == "completed"
        assert snapshots == ["offline original Core result"] and not fault.broken
        journal = frames(manager)
        assert len(journal) == 2 and all(item["run_id"] == record["run_id"] for item in journal)
        assert not LegacyOperationRecoveryQueries(manager.store.root).read().quarantined
    finally:
        manager.close_owned()


def test_original_acceptance_start_append_failure_precedes_job_and_pool_acceptance(tmp_path, monkeypatch):
    manager, fault = manager_fixture(tmp_path)
    calls = []

    def unavailable(*args, **kwargs):
        raise OSError("PRIVATE_SOURCE")

    def forbidden(*args, **kwargs):
        calls.append("forbidden original pool entry")
        raise AssertionError("no admission")

    monkeypatch.setattr(manager.store, "append_operation", unavailable)
    monkeypatch.setattr(manager.pool, "submit", forbidden)
    with pytest.raises(RuntimeError, match="legacy_operation_evidence_unavailable"):
        manager.submit({"prompt": "offline", "config": {"provider": "mock"}})
    assert calls == [] and not manager.jobs and fault.broken and manager._pending_submissions == 0
    with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
        manager.close_owned()


def test_original_worker_session_failure_is_unknown_no_retry_or_false_completed_job(tmp_path, monkeypatch):
    manager, fault = manager_fixture(tmp_path)
    writes = []
    submitted = observe_original_worker(manager, monkeypatch)

    class Provider:
        def next_turn(self, messages, tools):
            return ModelTurn("offline original result")

    def unavailable(*args, **kwargs):
        writes.append("one original write")
        raise OSError("PRIVATE_SESSION_ERROR")

    monkeypatch.setattr(manager, "_provider", lambda config: Provider())
    monkeypatch.setattr(manager.store, "write_session", unavailable)
    record = manager.submit({"prompt": "offline", "config": {}})
    assert len(submitted) == 1
    with pytest.raises(OSError, match="PRIVATE_SESSION_ERROR"):
        submitted[0].result(timeout=10)
    with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
        manager.close_owned()
    assert writes == ["one original write"] and fault.broken
    result = manager.status(record["run_id"])
    assert result["status"] == "failed" and result["answer"] == "legacy_operation_evidence_unavailable"
    assert len(frames(manager)) == 1 and LegacyOperationRecoveryQueries(manager.store.root).read().quarantined


def test_original_submit_throw_after_acceptance_retains_same_worker_no_replacement(tmp_path, monkeypatch):
    import doppel_agent.web.server as server_module

    manager, fault = manager_fixture(tmp_path)
    original_submit = manager.pool.submit
    entered, release = threading.Event(), threading.Event()
    submitted = []

    class Core:
        mcp_cleanup_failed = False  # Original fixture worker owns no MCP scope.
        mcp_execution_failed = False  # Preserve SAME lost-submit gate; no MCP execution scope.
        mcp_publication_failed = False  # Preserve original lost-submit input, no MCP publication scope.
        task_persistence_failed = (
            False  # Preserve original lost-submit gate; synthetic worker has no task SQL.
        )
        storage_persistence_failed = False  # SAME no-file-Core fixture; preserve lost-submit input/gate.
        process_cleanup_uncertain = False  # Same synthetic worker owns no process tree.

        def __init__(self, *args, **kwargs):
            pass

        def run(self, prompt, *, run_id, history):
            entered.set()
            release.wait()
            return {"run_id": run_id, "status": "completed", "answer": "original worker retained"}

    def original_uncertain_submit(operation):
        submitted.append(original_submit(operation))
        assert entered.wait(2)
        raise RuntimeError("PRIVATE_ACCEPTANCE_REPLY_LOST")

    monkeypatch.setattr(server_module, "Core", Core)
    monkeypatch.setattr(manager.pool, "submit", original_uncertain_submit)
    with ThreadPoolExecutor(max_workers=1) as fixture:
        close = None
        try:
            with pytest.raises(RuntimeError, match="^legacy_operation_evidence_unavailable$"):
                manager.submit({"prompt": "offline", "config": {"provider": "mock"}})
            assert fault.broken and len(submitted) == 1 and not submitted[0].done()
            close = fixture.submit(manager.close_owned)
            with pytest.raises(FutureTimeoutError):
                close.result(timeout=0.05)
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="legacy_submission_unresolved"):
            close.result(timeout=3)
        assert submitted[0].result(timeout=3) is None and len(submitted) == 1


def test_standalone_legacy_mock_keeps_original_offline_contract_without_new_markers(tmp_path):
    manager = JobManager(tmp_path)
    try:
        assert manager.probe({"provider": "mock"})["ok"]
        assert frames(manager) == []
    finally:
        manager.close_owned()


def test_native_pure_validation_refusal_does_not_invent_operation_or_quarantine(tmp_path):
    manager, fault = manager_fixture(tmp_path)
    try:
        with pytest.raises(ValueError):
            manager.submit({"prompt": "", "config": {"provider": "mock"}})
        with pytest.raises(ValueError):
            manager.probe({"provider": "unsupported_offline_fixture"})
        assert frames(manager) == [] and not manager._operation_fault.is_set() and not fault.broken
    finally:
        manager.close_owned()


def test_original_native_failure_hook_shared_service_fault_and_recreated_source(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from doppel_agent.api import create_app
    from doppel_agent.projects.desktop_kernel import DesktopKernel
    from doppel_agent.runtime.provider_recording import ProviderReceiptError
    from doppel_agent.runtime.service import RunService

    kernel = DesktopKernel(tmp_path)
    kernel.owner.path.parent.mkdir()
    kernel.owner.acquire()

    async def original_owner():
        kernel.app = create_app(tmp_path, provider=None, workspace_owner=kernel.owner.borrow())
        service = kernel.app.state.run_service
        await service.start()
        kernel.api = SimpleNamespace(started=True, should_exit=False)
        kernel.app.state.runtime_loop = asyncio.get_running_loop()
        manager = JobManager(
            tmp_path,
            effect_admission=kernel._assert_legacy_admission,
            effect_failure=kernel._mark_legacy_uncertain,
        )

        class Provider:
            def next_turn(self, messages, tools):
                raise ValueError("OFFLINE_PRIVATE_PROVIDER_FAILURE")

        monkeypatch.setattr(manager, "_provider", lambda config: Provider())
        try:
            with pytest.raises(RuntimeError, match="^provider_probe_unavailable$"):
                await asyncio.to_thread(manager.probe, {})
            assert service._provider_receipt_fault.broken and manager._pending_requests == 0
            with pytest.raises(ProviderReceiptError):
                service._assert_provider_admission()
            assert LegacyOperationRecoveryQueries(service.state_root).read().quarantined
        finally:
            try:
                manager.close_owned()
            finally:
                try:
                    await service.close()
                finally:
                    kernel.app.state.runtime_loop = None

    try:
        asyncio.run(original_owner())
        assert kernel.owner.held  # Original borrowed runtime cannot release outer owner.
    finally:
        kernel.owner.release()

    async def recreated_owner():
        service = RunService(tmp_path)
        try:
            await service.start()
            assert (
                service._metadata_ready
                and not service._started
                and service._legacy_startup_recovery.quarantined
            )
        finally:
            await service.close()

    asyncio.run(recreated_owner())
