"""C2b FIRST definitions, ALL UNRUN; original service + disposable SQL only.

No paid model/key/SDK/native/OS-drain proof. Reader/pump gates are original owned
handles, not a synthetic completed close task or replacement executor.
"""

import asyncio
from threading import Event
from unittest.mock import AsyncMock, Mock

import pytest

from doppel_agent.api import create_app
from doppel_agent.persistence.provider_recovery import ProviderReceiptRecoveryQueries
from doppel_agent.provider import MockProvider
from doppel_agent.runtime.provider_recording import ProviderReceiptError
from doppel_agent.runtime.service import RunService
from fastapi.testclient import TestClient


RUN, THREAD = "a" * 32, "b" * 32
REQUEST = dict(
    prompt="offline fixture",
    mode="graph",
    effort="quick",
    deadline_seconds=30,
    permissions=dict(workspace_write=False, command_execute=False, mcp_execute=False, delegate=False),
)


@pytest.mark.parametrize("fallback_fails", [False, True])
def test_original_deep_graph_fallback_sealed_calls_do_not_quarantine_restart(tmp_path, fallback_fails):
    """Native07 regression: registered Deep mode is not every provider engine."""
    from doppel_agent.provider import ModelTurn

    class FailDeepProvider:
        calls = 0

        async def anext_turn(self, messages, tools):
            self.calls += 1
            if self.calls == 1 or fallback_fails:
                raise ValueError("isolated provider failure")
            return ModelTurn("original Graph fallback completed")

    async def scenario():
        provider = FailDeepProvider()
        service = RunService(tmp_path, provider=provider)
        await service.start()
        record, _ = await service.create({**REQUEST, "mode": "deep"})
        if fallback_fails:
            with pytest.raises(ValueError, match="isolated provider failure"):
                await service.scheduler.wait(record["run_id"])
        else:
            await service.scheduler.wait(record["run_id"])
        terminal = await service.get(record["run_id"])
        assert terminal["status"] == ("failed" if fallback_fails else "completed")
        assert not terminal["lease_active"] and terminal["mode"] == "deep"
        events = await service.list_events(record["run_id"])
        starts = [e for e in events if e["type"] == "provider.call_started"]
        assert [e["payload"]["engine"] for e in starts] == ["deep", "graph"]
        assert provider.calls == 2
        fallback = next(e for e in events if e["type"] == "deep.fallback")
        assert starts[0]["seq"] < fallback["seq"] < starts[1]["seq"]
        await service.close()
        again = RunService(tmp_path, provider=MockProvider())
        await again.start()
        try:
            assert not again._provider_startup_recovery.quarantined
            assert again._started and not again._provider_receipt_fault.broken
            assert again.events.list(record["run_id"]) == events
            assert again.scheduler.accepted_count == 0  # No implicit replay.
        finally:
            await again.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("foreign_thread", [False, True])
def test_original_native_thread_scope_is_registered_exactly_not_a_provider_call_id(
    tmp_path, mode, foreign_thread
):
    """S9 FIRST: native-prefixed original threads, exact SQL registration still required."""
    import sqlite3

    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        record, _ = await service.create({**REQUEST, "mode": mode})
        await service.scheduler.wait(record["run_id"])
        assert record["thread_id"].startswith("native-") and len(record["thread_id"]) == 39
        await service.close()
        if foreign_thread:
            with sqlite3.connect(service.runs.database) as db:
                db.execute(
                    "UPDATE runtime_events SET thread_id=? WHERE type LIKE 'provider.%'",
                    ("native-" + "c" * 32,),
                )
        again = RunService(tmp_path, provider=MockProvider())
        await again.start()
        try:
            assert again._provider_startup_recovery.quarantined is foreign_thread
            assert again._started is (not foreign_thread)
            assert (await again.get(record["run_id"]))["thread_id"] == record["thread_id"]
            assert again.scheduler.accepted_count == 0
        finally:
            await again.close()

    asyncio.run(scenario())


def seed(service):
    service.runs.create(RUN, THREAD, "graph", REQUEST, None)
    service.events.append(
        RUN,
        THREAD,
        "provider.call_started",
        dict(
            version=2,
            call_id="c" * 32,
            engine="graph",
            actor="runtime_model",
            path="native_async",
            boundary="provider_method_not_transport_attempt",
        ),
    )


def pumps(service, monkeypatch):
    result = [AsyncMock(), AsyncMock(), AsyncMock()]
    for target, mock in zip(
        (service.scheduler, service.subagents, service.work_order_orchestrator), result, strict=True
    ):
        monkeypatch.setattr(target, "start", mock)
    return result


def test_startup_unknown_keeps_original_owner_readonly_without_recovery_or_pumps(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        seed(service)
        started = pumps(service, monkeypatch)
        recover, reconcile = Mock(), Mock()
        monkeypatch.setattr(service.runs, "recover_incomplete", recover)
        monkeypatch.setattr(service.conversations, "reconcile", reconcile)
        await service.start()
        assert service._metadata_ready and not service._started and service._owner.held
        assert service._close_task is None and not service.cleanup_complete
        assert service._provider_receipt_fault.broken
        assert all(mock.call_count == 0 for mock in started) and not recover.called and not reconcile.called
        assert (await service.get(RUN))["status"] == "queued"
        report = await service.run_report(RUN)
        assert report["service"] == dict(
            read_only=True, owner_held=True, failed_close_diagnostic=False, execution_admission="quarantined"
        )
        assert not (service.state_root / "tool-executions.sqlite3").exists()
        assert (await service.work_order_queue())["scheduler"]["active"] == 0
        assert await service.list_work_orders() is not None
        assert await service.cancel(RUN)  # Original cancellation, never resumed model IO.
        await service.close()
        assert service.cleanup_complete and not service._owner.held

    asyncio.run(scenario())


def test_every_new_effect_entry_refuses_before_settings_keys_acceptance_or_child_io(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path)
        seed(service)
        await service.start()
        key = Mock(side_effect=AssertionError("real key lookup forbidden"))
        monkeypatch.setattr(service.settings, "api_key", key)
        acceptance, child = Mock(), AsyncMock()
        monkeypatch.setattr(service.runs, "create", acceptance)
        monkeypatch.setattr(service.subagents, "spawn", child)
        for operation in (
            lambda: service.create(REQUEST),
            lambda: service._create_owned(REQUEST),
            lambda: service.resume(RUN, "fixture", {}),
            lambda: service._provider(None, "graph"),
            lambda: service.spawn_subagent(RUN, "fixture"),
            lambda: service.follow_up_subagent(RUN, "d" * 32, "fixture"),
            lambda: service.activate_work_order("fixture", expected_revision=1, settings={}),
            lambda: service.control_work_order("fixture", "resume", expected_revision=1),
            lambda: service.prepare_verification(RUN, "fixture", "fixture", operation_id="fixture"),
            lambda: service.mcp_discover("fixture", probe=True),
        ):
            with pytest.raises(ProviderReceiptError):
                value = operation()
                if asyncio.iscoroutine(value):
                    await value
        assert not key.called and not acceptance.called and not child.called
        assert not service.work_order_orchestrator._available()
        await service.close()

    asyncio.run(scenario())


def test_metadata_reads_or_repeat_start_cannot_clear_monotonic_quarantine(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        seed(service)
        await service.start()
        reader = Mock(side_effect=AssertionError("must not rescan or reset within owner life"))
        monkeypatch.setattr(ProviderReceiptRecoveryQueries, "read", reader)
        await service.start()
        await service.run_report(RUN)
        await service.workspace_selection()
        assert not reader.called and service._provider_receipt_fault.broken
        with pytest.raises(ProviderReceiptError):
            await service.create(REQUEST)
        await service.close()

    asyncio.run(scenario())


def test_owner_recreation_rechecks_original_unfinished_receipt_even_after_successful_close(tmp_path):
    async def scenario():
        first = RunService(tmp_path, provider=MockProvider())
        seed(first)
        await first.start()
        await first.close()
        next_owner = RunService(tmp_path, provider=MockProvider())
        await next_owner.start()
        assert (
            next_owner._metadata_ready
            and not next_owner._started
            and next_owner._provider_receipt_fault.broken
        )
        assert (await next_owner.run_report(RUN))["service"]["execution_admission"] == "quarantined"
        await next_owner.close()

    asyncio.run(scenario())


def test_live_owner_fault_blocks_new_effects_but_original_cancel_and_readonly_evidence_remain(
    tmp_path, monkeypatch
):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        service.runs.create(RUN, THREAD, "graph", REQUEST, None)
        service._provider_receipt_fault.mark_failed()
        with pytest.raises(ProviderReceiptError):
            await service.create(REQUEST)
        cancel = AsyncMock(return_value=True)
        monkeypatch.setattr(service, "_cancel_owned", cancel)
        assert await service.cancel(RUN) and cancel.call_count == 1
        assert (await service.run_report(RUN))["service"]["execution_admission"] == "quarantined"
        await service.close()

    asyncio.run(scenario())


def test_clean_original_journal_starts_existing_pumps_only_after_owner_joined_read(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        observed = []
        original = ProviderReceiptRecoveryQueries.read

        def read(reader):
            observed.append((service._owner.held, service._started, service.scheduler.active_count))
            return original(reader)

        monkeypatch.setattr(ProviderReceiptRecoveryQueries, "read", read)
        await service.start()
        assert observed == [(True, False, 0)]
        assert service._started and service._metadata_ready and not service._provider_receipt_fault.broken
        assert service.work_order_orchestrator._worker is not None
        await service.close()
        assert service.cleanup_complete and not service._owner.held

    asyncio.run(scenario())


def test_quarantine_preserves_original_pause_cancel_control_but_no_new_resume(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        seed(service)
        await service.start()
        control = AsyncMock(return_value={"fixture": True})
        monkeypatch.setattr(service.work_order_orchestrator, "control", control)
        for action in ("pause", "cancel"):
            assert await service.control_work_order("fixture", action, expected_revision=1) == {
                "fixture": True
            }
        with pytest.raises(ProviderReceiptError):
            await service.control_work_order("fixture", "resume", expected_revision=1)
        assert control.call_count == 2
        await service.close()

    asyncio.run(scenario())


def test_startup_reader_is_joined_with_original_owner_through_repeated_cancellation(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        entered, release = Event(), Event()
        held = []
        original = ProviderReceiptRecoveryQueries.read

        def read(reader):
            entered.set()
            release.wait()
            held.append(service._owner.held)
            return original(reader)

        monkeypatch.setattr(ProviderReceiptRecoveryQueries, "read", read)
        started = pumps(service, monkeypatch)
        task = asyncio.create_task(service.start())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and service._owner.held and all(mock.call_count == 0 for mock in started)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert held == [True] and service.cleanup_complete and not service._owner.held

    asyncio.run(scenario())


def test_close_requested_during_startup_read_never_starts_old_execution_pumps(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        entered, release = Event(), Event()
        original = ProviderReceiptRecoveryQueries.read

        def read(reader):
            entered.set()
            release.wait()
            return original(reader)

        monkeypatch.setattr(ProviderReceiptRecoveryQueries, "read", read)
        started = pumps(service, monkeypatch)
        task = asyncio.create_task(service.start())
        closing = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(0)
            assert service._owner.held and not closing.done()
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="closed"):
            await task
        assert closing is not None
        await closing
        assert all(mock.call_count == 0 for mock in started) and service.cleanup_complete

    asyncio.run(scenario())


def test_original_http_lifespan_serves_readonly_quarantine_and_fixed_no_store_refusal(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    seed(app.state.run_service)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        report = client.get(f"/api/v1/reports/runs/{RUN}")
        assert report.status_code == 200 and report.json()["service"]["failed_close_diagnostic"] is False
        assert report.json()["service"]["execution_admission"] == "quarantined"
        denied = client.post("/api/v1/runs", json=REQUEST)
        assert denied.status_code == 503 and denied.headers["cache-control"] == "no-store"
        assert denied.json() == {"error": "provider_receipt_unavailable"}
        assert app.state.run_service._close_task is None
