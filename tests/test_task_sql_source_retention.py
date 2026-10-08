"""C2c2g5f FIRST original TaskManager SQL/owner definitions, ALL UNRUN.

Actual disposable SQLite, original Core/pool/factory only. API method returns and
explicit fixture teardown aren't native/physical SDK/remote/billing proof.
"""

import asyncio
import sqlite3
import threading

import pytest

from doppel_agent.core import Core
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.provider_recording import ProviderReceiptFault
from doppel_agent.tasks.manager import TaskManager, TaskPersistenceError


def observe(monkeypatch, database, events, phase):
    import doppel_agent.tasks.manager as module

    connect = module.sqlite3.connect
    connections = []

    class Connection:
        def __init__(self, raw):
            self.raw = raw
            self.closes = 0

        @property
        def row_factory(self):
            return self.raw.row_factory

        @row_factory.setter
        def row_factory(self, value):
            if phase == "row_factory":
                raise ValueError("PRIVATE_ROW_FACTORY")
            self.raw.row_factory = value

        def execute(self, sql, *args):
            if phase == "setup" and sql.startswith("PRAGMA"):
                raise OSError("PRIVATE_PRAGMA")
            if phase in {"body", "body_close"} and sql.startswith("SELECT"):
                raise sqlite3.OperationalError("PRIVATE_QUERY")
            return self.raw.execute(sql, *args)

        def executescript(self, *args):
            return self.raw.executescript(*args)

        def executemany(self, *args):
            return self.raw.executemany(*args)

        def __enter__(self):
            events.append("original enter")
            if phase == "enter":
                raise ValueError("PRIVATE_ENTER")
            self.raw.__enter__()
            return self

        def __exit__(self, kind, error, trace):
            result = self.raw.__exit__(kind, error, trace)
            events.append("original rollback" if kind else "original commit")
            if phase == "commit" and kind is None:
                raise OSError("PRIVATE_COMMIT_RETURN")
            if phase == "rollback" and kind is not None:
                raise ValueError("PRIVATE_ROLLBACK_RETURN")
            if phase == "same_error_rollback" and kind is not None:
                raise error
            return result

        def close(self):
            self.closes += 1
            events.append("original close attempted")
            if phase in {"close", "body_close"}:
                raise ValueError("PRIVATE_CLOSE")
            self.raw.close()
            if phase == "closed_then_throw":
                raise OSError("PRIVATE_CLOSED_THROW")
            events.append("original close returned")

    def observed(path, *args, **kwargs):
        if str(path) not in {str(database), database.resolve().as_uri() + "?mode=ro"}:
            return connect(path, *args, **kwargs)
        # Fixture-only raw handle allows explicit teardown on the fixture thread
        # after original owned-worker return. Production thread policy unchanged.
        options = {**kwargs, "check_same_thread": False}
        raw = connect(path, *args, **options)
        instance = Connection(raw)
        connections.append(instance)
        events.append(("original factory", str(path), kwargs.get("uri")))
        return instance

    monkeypatch.setattr(module.sqlite3, "connect", observed)
    return connections


@pytest.mark.parametrize(
    "phase", ["row_factory", "setup", "enter", "body", "commit", "rollback", "same_error_rollback"]
)
def test_original_task_sql_failure_before_known_close_preserves_quarantine_not_fake_resource_unknown(
    tmp_path, monkeypatch, phase
):
    events = []
    fault = ProviderReceiptFault()
    path = tmp_path / "offline.sqlite3"

    def failed():
        events.append("same owner fault")
        fault.mark_failed()

    manager = TaskManager(path, failure=failed, cleanup_failure=fault.retain_cleanup)
    connections = observe(monkeypatch, path, events, phase)
    with pytest.raises(TaskPersistenceError, match="^legacy_task_persistence_unavailable$") as error:
        if phase in {"rollback", "same_error_rollback"}:
            manager.get("unknown fixture task")
        else:
            manager.list("offline")
    assert error.value.source is manager and error.value.__suppress_context__
    assert manager.failed and manager.cleanup_uncertain and fault.broken
    assert not manager.resource_cleanup_uncertain and not fault.cleanup_uncertain
    assert events.index("same owner fault") < events.index("original close attempted")
    assert len(connections) == 1 and connections[0].closes == 1 and manager._unresolved_connections == {}
    manager.check_resource_cleanup()
    with pytest.raises(TaskPersistenceError):
        manager.create("offline", "forbidden replacement")
    assert len(connections) == 1 and connections[0].closes == 1


@pytest.mark.parametrize("phase", ["close", "closed_then_throw"])
def test_original_task_failed_close_retains_exact_connection_attempt_source_and_no_retry(
    tmp_path, monkeypatch, phase
):
    path = tmp_path / "offline.sqlite3"
    events = []
    fault = ProviderReceiptFault()
    manager = TaskManager(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    connections = observe(monkeypatch, path, events, phase)
    try:
        with pytest.raises(TaskPersistenceError) as error:
            manager.create("offline", "original task")
        frame = next(iter(manager._unresolved_connections.values()))
        assert error.value.source is manager and frame.connection is connections[0]
        assert frame.connect_attempted and not frame.close_returned and manager.resource_cleanup_uncertain
        assert fault.cleanup_uncertain and next(iter(fault._cleanup_sources.values())) is manager
        with pytest.raises(TaskPersistenceError):
            manager.check_resource_cleanup()
        with pytest.raises(TaskPersistenceError):
            manager.list("offline")
        assert next(iter(manager._unresolved_connections.values())) is frame
        assert len(connections) == 1 and connections[0].closes == 1
    finally:
        for connection in connections:
            connection.raw.close()  # Disposable teardown, no recovery grant.


def test_original_task_known_validation_rollback_close_and_missing_read_keep_compatibility(
    tmp_path, monkeypatch
):
    path = tmp_path / "offline.sqlite3"
    notifications = []
    events = []
    assert TaskManager.read_existing(path, "offline") == [] and not path.exists()
    manager = TaskManager(path, failure=lambda: notifications.append("fault"))
    connections = observe(monkeypatch, path, events, "healthy")
    with pytest.raises(ValueError, match="unknown task"):
        manager.get("unknown fixture")
    assert not manager.failed and not manager.resource_cleanup_uncertain and notifications == []
    assert events.count("original rollback") == 1 and len(connections) == 1 and connections[0].closes == 1


def test_original_task_unreturned_factory_keeps_same_attempt_not_absent_handle_as_closed(
    tmp_path, monkeypatch
):
    import doppel_agent.tasks.manager as module

    path = tmp_path / "offline.sqlite3"
    manager = TaskManager(path)
    raw_connect = module.sqlite3.connect
    handles = []
    calls = []

    def opaque(*args, **kwargs):
        handles.append(raw_connect(*args, **kwargs))
        calls.append("original allocation")
        raise OSError("PRIVATE_FACTORY_NO_RETURN")

    monkeypatch.setattr(module.sqlite3, "connect", opaque)
    try:
        with pytest.raises(TaskPersistenceError):
            manager.list("offline")
        frame = next(iter(manager._unresolved_connections.values()))
        assert frame.connection is None and frame.connect_attempted and not frame.close_returned
        assert manager.resource_cleanup_uncertain
        with pytest.raises(TaskPersistenceError):
            manager.list("offline")
        assert calls == ["original allocation"]
    finally:
        for handle in handles:
            handle.close()  # Only fixture has opaque original handle.


def test_actual_ephemeral_readonly_reader_retains_original_source_without_schema_or_database_repair(
    tmp_path, monkeypatch
):
    path = tmp_path / "offline.sqlite3"
    writer = TaskManager(path)
    writer.create("offline", "original task")
    before = path.read_bytes()
    events = []
    fault = ProviderReceiptFault()
    connections = observe(monkeypatch, path, events, "close")
    try:
        with pytest.raises(TaskPersistenceError) as error:
            TaskManager.read_existing(
                path, "offline", failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup
            )
        source = error.value.source
        frame = next(iter(source._unresolved_connections.values()))
        assert source._read_only and frame.connection is connections[0]
        assert next(iter(fault._cleanup_sources.values())) is source and fault.cleanup_uncertain
        factories = [event for event in events if isinstance(event, tuple)]
        assert len(factories) == 1 and factories[0][1].endswith("?mode=ro") and factories[0][2] is True
        assert path.read_bytes() == before and connections[0].closes == 1
    finally:
        for connection in connections:
            connection.raw.close()


@pytest.mark.parametrize("phase", ["close", "commit"])
def test_actual_core_task_constructor_failure_retains_source_before_assignment_and_prevents_provider_reentry(
    tmp_path, monkeypatch, phase
):
    events = []
    calls = []
    path = tmp_path / ".doppel-agent" / "tasks.sqlite3"
    fault = ProviderReceiptFault()

    class Provider:
        def next_turn(self, *_args):
            calls.append("forbidden original turn")
            return ModelTurn(content="forbidden")

    core = Core(
        tmp_path, Provider(), task_failure=fault.mark_failed, task_cleanup_failure=fault.retain_cleanup
    )
    connections = observe(monkeypatch, path, events, phase)
    try:
        with pytest.raises(TaskPersistenceError):
            core.run("offline constructor failure")
        assert calls == [] and core.task_persistence_failed and fault.broken and core._task_manager is None
        assert core.task_cleanup_uncertain is (phase == "close") and fault.cleanup_uncertain is (
            phase == "close"
        )
        if phase == "close":
            source = next(iter(core._unresolved_task_sources.values()))
            assert next(iter(source._unresolved_connections.values())).connection is connections[0]
            assert next(iter(fault._cleanup_sources.values())) is source
        with pytest.raises(TaskPersistenceError):
            core.run("forbidden replacement")
        assert calls == [] and len(connections) == 1 and connections[0].closes == 1
    finally:
        for connection in connections:
            connection.raw.close()


def test_actual_task_tool_sql_close_failure_is_fatal_to_original_loop_not_second_provider_turn(
    tmp_path, monkeypatch
):
    path = tmp_path / ".doppel-agent" / "tasks.sqlite3"
    calls = []
    observed_lists = []

    class Provider:
        def next_turn(self, _messages, _tools):
            calls.append("original turn")
            assert len(calls) == 1
            # Keep live original factory list, never infer failure from an empty copy.
            observed_lists.append(observe(monkeypatch, path, [], "close"))
            return ModelTurn(
                tool_calls=(ToolCall("task", "task_create", {"title": "original", "dependencies": []}),)
            )

    core = Core(tmp_path, Provider())
    try:
        result = core.run("offline task")
        assert result["status"] == "failed" and calls == ["original turn"] and core.task_persistence_failed
        assert core.task_cleanup_uncertain and not core.mcp_cleanup_failed and not core.mcp_execution_failed
        source = next(iter(core._unresolved_task_sources.values()))
        actual = observed_lists[0]
        assert next(iter(source._unresolved_connections.values())).connection is actual[0]
        assert len(actual) == 1 and actual[0].closes == 1
        with pytest.raises(TaskPersistenceError):
            core.run("forbidden new turn")
        assert calls == ["original turn"] and len(actual) == 1
    finally:
        for items in observed_lists:
            for connection in items:
                connection.raw.close()


@pytest.mark.parametrize("entry", ["readonly", "worker_constructor"])
def test_actual_native_manager_retains_original_task_source_before_ephemeral_reader_or_worker_return(
    tmp_path, monkeypatch, entry
):
    from doppel_agent.web.server import JobManager

    path = tmp_path / ".doppel-agent" / "tasks.sqlite3"
    manager = JobManager(tmp_path)
    events = []
    if entry == "readonly":
        TaskManager(path).create("a" * 32, "offline task")
    connections = observe(monkeypatch, path, events, "close")
    try:
        if entry == "readonly":
            manager.store.write_session("a" * 32, {"run_id": "a" * 32, "status": "completed"})
            with pytest.raises(TaskPersistenceError):
                manager.tasks("a" * 32)
        else:
            futures = []
            original_submit = manager.pool.submit

            def submit(operation):
                future = original_submit(operation)
                futures.append(future)
                return future

            monkeypatch.setattr(manager.pool, "submit", submit)
            accepted = manager.submit({"prompt": "offline task source", "config": {"provider": "mock"}})
            futures[0].result(timeout=3)
            core = manager._cores[accepted["run_id"]]
            assert core.task_persistence_failed and core.task_cleanup_uncertain
            assert not core.mcp_cleanup_failed and manager.status(accepted["run_id"])["status"] == "failed"
        source = next(iter(manager._unresolved_metadata_sources.values()))
        frame = next(iter(source._unresolved_connections.values()))
        assert frame.connection is connections[0] and manager._operation_fault.is_set()
        with pytest.raises(RuntimeError, match="legacy_operation_evidence_unavailable"):
            manager.submit({"prompt": "forbidden new IO", "config": {"provider": "mock"}})
        with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
            manager.close_owned()
        assert (
            next(iter(manager._unresolved_metadata_sources.values())) is source and connections[0].closes == 1
        )
    finally:
        manager.pool.shutdown(wait=True)
        for connection in connections:
            connection.raw.close()


@pytest.mark.parametrize("reviewed", [False, True])
def test_actual_legacy_factory_core_task_failure_binds_same_root_fault_and_retained_original(
    tmp_path, monkeypatch, reviewed
):
    from doppel_agent.provider import MockProvider
    from doppel_agent.runtime.base import RunRequest
    from doppel_agent.runtime.factory import create_runtime

    async def scenario():
        fault = ProviderReceiptFault()
        runtime = create_runtime(
            "legacy", tmp_path, MockProvider(), reviewed_legacy=reviewed, provider_receipt_fault=fault
        )
        path = tmp_path / ".doppel-agent" / "tasks.sqlite3"
        connections = observe(monkeypatch, path, [], "close")
        try:
            with pytest.raises(TaskPersistenceError):
                await runtime.run(RunRequest("offline", run_id="a" * 32, thread_id="b" * 32))
            core = next(iter(runtime._unresolved_task_cores.values()))
            source = next(iter(core._unresolved_task_sources.values()))
            assert (
                fault.broken
                and fault.cleanup_uncertain
                and next(iter(fault._cleanup_sources.values())) is source
            )
            assert next(iter(source._unresolved_connections.values())).connection is connections[0]
            assert not runtime._unresolved_mcp_cores and not core.mcp_cleanup_failed
            with pytest.raises(RuntimeError, match="provider_receipt_unavailable"):
                await runtime.run(RunRequest("forbidden replacement", run_id="c" * 32, thread_id="d" * 32))
            assert len(connections) == 1 and connections[0].closes == 1
        finally:
            for connection in connections:
                connection.raw.close()

    asyncio.run(scenario())


def test_actual_reviewed_resume_task_constructor_failure_keeps_original_pending_decision_and_source(
    tmp_path, monkeypatch
):
    from doppel_agent.runtime.base import RunRequest, ResumeCommand
    from doppel_agent.runtime.factory import create_runtime

    calls = []

    class Provider:
        def next_turn(self, *_args):
            calls.append("original model turn")
            assert len(calls) == 1
            return ModelTurn(
                tool_calls=(
                    ToolCall(
                        "pending", "propose_patch", {"changes": [{"path": "never.py", "content": "offline"}]}
                    ),
                )
            )

    async def scenario():
        fault = ProviderReceiptFault()
        runtime = create_runtime(
            "legacy",
            tmp_path,
            Provider(),
            reviewed_legacy=True,
            core_options={"allow_write": True},
            provider_receipt_fault=fault,
        )
        interrupted = await runtime.run(
            RunRequest("offline original pending", run_id="a" * 32, thread_id="b" * 32)
        )
        assert interrupted.status == "interrupted" and not (tmp_path / "never.py").exists()
        command = ResumeCommand(
            interrupted.run_id,
            interrupted.thread_id,
            {"action": "approve", "_legacy_interrupt_id": interrupted.metadata["interrupts"][0]["id"]},
        )
        connections = observe(monkeypatch, tmp_path / ".doppel-agent" / "tasks.sqlite3", [], "close")
        try:
            with pytest.raises(TaskPersistenceError):
                await runtime.resume(command)
            source = next(iter(fault._cleanup_sources.values()))
            assert (
                fault.cleanup_uncertain
                and calls == ["original model turn"]
                and not (tmp_path / "never.py").exists()
            )
            assert next(iter(source._unresolved_connections.values())).connection is connections[0]
            assert len(runtime._unresolved_task_cores) == 1 and not runtime._unresolved_mcp_cores
            with pytest.raises(RuntimeError, match="provider_receipt_unavailable"):
                await runtime.resume(command)
            assert len(connections) == 1 and connections[0].closes == 1
            # Actual original review load still has the SAME pending decision;
            # source-specific disposable receipt oracle, not effect replay/reset.
            _raw, _payload, frame = runtime.reviews.load(
                command.run_id, command.thread_id, command.value["_legacy_interrupt_id"]
            )
            assert frame.pending_calls[0].id == "pending"
        finally:
            for connection in connections:
                connection.raw.close()

    asyncio.run(scenario())


def test_actual_original_legacy_worker_sql_failure_fences_before_gated_close_and_drains_repeated_cancellation(
    tmp_path, monkeypatch
):
    import doppel_agent.tasks.manager as module
    from doppel_agent.runtime.base import RunRequest
    from doppel_agent.runtime.factory import create_runtime

    waiting, release = threading.Event(), threading.Event()
    observed_lists = []
    calls = []

    class Provider:
        def next_turn(self, *_args):
            calls.append("original turn")
            assert len(calls) == 1
            observed_lists.append(
                observe(monkeypatch, tmp_path / ".doppel-agent" / "tasks.sqlite3", [], "body_close")
            )
            original_connect = module.sqlite3.connect

            def gated(*args, **kwargs):
                connection = original_connect(*args, **kwargs)
                if str(args[0]) != str(tmp_path / ".doppel-agent" / "tasks.sqlite3"):
                    return connection  # Other original DBs keep their actual unchanged handles.
                close = connection.close

                def original_close():
                    waiting.set()
                    assert release.wait(3)
                    return close()

                connection.close = original_close
                return connection

            monkeypatch.setattr(module.sqlite3, "connect", gated)
            return ModelTurn(tool_calls=(ToolCall("original", "task_list", {}),))

    async def scenario():
        fault = ProviderReceiptFault()
        runtime = create_runtime("legacy", tmp_path, Provider(), provider_receipt_fault=fault)
        caller = asyncio.create_task(runtime.run(RunRequest("offline", run_id="a" * 32, thread_id="b" * 32)))
        try:
            assert await asyncio.to_thread(waiting.wait, 2)
            assert (
                fault.broken and not fault.cleanup_uncertain
            )  # SQL receipt failure known, close still entered.
            caller.cancel()
            await asyncio.sleep(0)
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done()  # SAME original Core/SQL worker retained until its close returns/fails.
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        source = next(iter(fault._cleanup_sources.values()))
        actual = observed_lists[0]
        assert (
            fault.cleanup_uncertain
            and next(iter(source._unresolved_connections.values())).connection is actual[0]
        )
        assert (
            actual[0].closes == 1 and calls == ["original turn"] and len(runtime._unresolved_task_cores) == 1
        )
        for connection in actual:
            connection.raw.close()  # Explicit disposable cleanup only.

    asyncio.run(scenario())


def test_actual_service_original_runtime_factory_task_failed_close_keeps_same_failed_close_task_and_owner(
    tmp_path, monkeypatch
):
    from doppel_agent.provider import MockProvider
    from doppel_agent.runtime.base import RunRequest
    from doppel_agent.runtime.service import RunService

    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        # Actual service factory/root binding, NOT full durable run/child/fallback admission proof.
        runtime = await service._runtime(
            {"mode": "legacy", "request": {"permissions": {}}, "profile_snapshot": {}}
        )
        connections = observe(monkeypatch, tmp_path / ".doppel-agent" / "tasks.sqlite3", [], "close")
        try:
            with pytest.raises(TaskPersistenceError):
                await runtime.run(RunRequest("offline", run_id="a" * 32, thread_id="b" * 32))
            assert (
                runtime.provider_receipt_fault is service._provider_receipt_fault
                and service._provider_receipt_fault.cleanup_uncertain
            )
            with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
                await service.close()
            original_close = service._close_task
            assert service._owner.held and not service.cleanup_complete
            with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
                await service.close()
            assert (
                service._close_task is original_close and service._owner.held and connections[0].closes == 1
            )
        finally:
            for connection in connections:
                connection.raw.close()
            service._owner.release()  # Disposable teardown only, not recovery authorization.

    asyncio.run(scenario())
