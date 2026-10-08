"""Data-only admission callbacks; no provider/native operations (S9 definitions)."""

import asyncio
import sqlite3
from uuid import uuid4

import pytest

from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.work_orders import WorkOrderStore
from doppel_agent.tasks.orchestrator import WorkOrderOrchestrator
from doppel_agent.tasks.work_orders import plan_from_payload


class FixtureAdmission:
    def __init__(self, runs):
        self.runs = runs
        self.requests = []
        self.cancelled = []
        self.entered = None
        self.gate = None
        self.drain_cancel = True

    async def create(self, request):
        self.requests.append(dict(request))
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await asyncio.wait_for(self.gate.wait(), timeout=10)
        def accept():
            intent = WorkOrderStore(self.runs.database).intent_by_key(request["idempotency_key"])
            return self.runs.create(uuid4().hex, uuid4().hex, request["mode"], request,
                request["idempotency_key"], native=True, frozen_input_snapshot=intent["input_snapshot"])[0]

        return await asyncio.to_thread(accept)

    async def cancel(self, run_id):
        self.cancelled.append(run_id)
        await asyncio.to_thread(self.runs.update, run_id, "cancelled")
        if self.drain_cancel:
            await asyncio.to_thread(self.runs.release_conversation_turn, run_id)
        return True


def setup(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store = WorkOrderStore(path)
    plan = plan_from_payload({"title": "fixture", "tasks": [
        {"id": "read", "title": "read", "prompt": "read fixture"},
        {"id": "next", "title": "next", "prompt": "next fixture", "dependencies": ["read"]},
    ]})
    order, _ = store.create(plan)
    runtime = FixtureAdmission(RuntimeRunStore(path))
    return store, runtime, order["work_order_id"]


def test_step_uses_one_durable_admission_and_does_not_replay_a_live_run(tmp_path):
    async def scenario():
        store, runtime, identifier = setup(tmp_path)
        owner = WorkOrderOrchestrator(store, runtime.create, runtime.cancel)
        await owner.activate(identifier, expected_revision=1, settings={"profile_id": "mock-fixture"})
        first = await owner.step(identifier)
        second = await owner.step(identifier)
        assert len(runtime.requests) == 1
        assert first["attempts"][0]["run_id"] == second["attempts"][0]["run_id"]
        assert second["tasks"][0]["status"] == "dispatching"
        await owner.close()

    asyncio.run(scenario())


def test_caller_cancellation_and_close_drain_the_same_admission(tmp_path):
    async def scenario():
        store, runtime, identifier = setup(tmp_path)
        runtime.entered, runtime.gate = asyncio.Event(), asyncio.Event()
        owner = WorkOrderOrchestrator(store, runtime.create, runtime.cancel)
        await owner.activate(identifier, expected_revision=1, settings={})
        posting = asyncio.create_task(owner.step(identifier))
        await asyncio.wait_for(runtime.entered.wait(), timeout=5)
        posting.cancel()
        await asyncio.sleep(0)
        posting.cancel()
        closing = asyncio.create_task(owner.close())
        await asyncio.sleep(0)
        assert not closing.done()
        runtime.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(posting, timeout=5)
        await asyncio.wait_for(closing, timeout=5)
        assert len(runtime.requests) == 1 and store.get(identifier)["attempts"][0]["run_id"]
        with pytest.raises(RuntimeError, match="closing"):
            await owner.step(identifier)

    asyncio.run(scenario())


def test_cancel_waits_for_flying_acceptance_then_retains_runtime_drain_truth(tmp_path):
    async def scenario():
        store, runtime, identifier = setup(tmp_path)
        runtime.entered, runtime.gate = asyncio.Event(), asyncio.Event()
        runtime.drain_cancel = False
        owner = WorkOrderOrchestrator(store, runtime.create, runtime.cancel)
        await owner.activate(identifier, expected_revision=1, settings={})
        posting = asyncio.create_task(owner.step(identifier))
        await asyncio.wait_for(runtime.entered.wait(), timeout=5)
        cancelling = asyncio.create_task(owner.control(identifier, "cancel", expected_revision=1))
        await asyncio.sleep(0)
        assert not cancelling.done()
        runtime.gate.set()
        accepted = await asyncio.wait_for(posting, timeout=5)
        cancelled = await asyncio.wait_for(cancelling, timeout=5)
        run_id = accepted["attempts"][0]["run_id"]
        assert runtime.cancelled == [run_id]
        assert cancelled["status"] == "cancelled" and cancelled["tasks"][0]["status"] == "running"
        assert cancelled["attempts"][0]["runtime_lease_active"] == 1
        await asyncio.to_thread(runtime.runs.release_conversation_turn, run_id)
        assert (await owner.step(identifier))["tasks"][0]["status"] == "cancelled"
        await owner.close()

    asyncio.run(scenario())


def test_known_local_rejection_is_abandoned_without_raw_error_or_provider_call(tmp_path):
    async def scenario():
        store, runtime, identifier = setup(tmp_path)

        async def reject(request):
            raise ValueError("PRIVATE_PATH_NOT_A_REAL_KEY")

        owner = WorkOrderOrchestrator(store, reject, runtime.cancel)
        await owner.activate(identifier, expected_revision=1, settings={})
        record = await owner.step(identifier)
        assert record["status"] == "failed" and record["dispatch_error"] == "admission_rejected"
        assert record["attempts"][0]["run_id"] is None
        assert record["attempts"][0]["status"] == "failed"
        assert store.pending_intents(identifier) == []
        assert "PRIVATE_PATH" not in str(record)
        await owner.close()

    asyncio.run(scenario())


def test_unknown_admission_failure_pauses_and_preserves_key_until_explicit_resume(tmp_path):
    async def scenario():
        store, runtime, identifier = setup(tmp_path)
        requests = []

        async def unknown_once(request):
            requests.append(dict(request))
            if len(requests) == 1:
                raise RuntimeError("PRIVATE_AMBIGUOUS_ERROR")
            return await runtime.create(request)

        owner = WorkOrderOrchestrator(store, unknown_once, runtime.cancel)
        await owner.activate(identifier, expected_revision=1, settings={})
        record = await owner.step(identifier)
        assert record["status"] == "paused" and record["dispatch_error"] == "admission_failed"
        assert record["attempts"][0]["status"] == "reserved"
        await owner.step(identifier)
        assert len(requests) == 1
        await owner.control(identifier, "resume", expected_revision=1)
        record = await owner.step(identifier)
        assert requests[0] == requests[1]
        assert len(record["attempts"]) == 1 and record["attempts"][0]["run_id"]
        assert "PRIVATE_AMBIGUOUS" not in str(record)
        await owner.close()

    asyncio.run(scenario())


def test_acceptance_response_without_runtime_persistence_is_not_success(tmp_path):
    async def scenario():
        store, runtime, identifier = setup(tmp_path)

        async def fabricated(request):
            return {"run_id": "not-durable", "answer": "success"}

        owner = WorkOrderOrchestrator(store, fabricated, runtime.cancel)
        await owner.activate(identifier, expected_revision=1, settings={})
        record = await owner.step(identifier)
        assert record["dispatch_error"] == "admission_not_persisted"
        assert record["status"] == "paused" and record["attempts"][0]["run_id"] is None
        await owner.close()

    asyncio.run(scenario())


def test_restart_mapping_after_runtime_acceptance_does_not_accept_another_run(tmp_path):
    async def scenario():
        store, runtime, identifier = setup(tmp_path)
        store.activate(identifier, expected_revision=1, settings={})
        intent = store.reserve_next(identifier, expected_revision=1)
        original = await runtime.create(intent["request"])
        reopened = WorkOrderStore(store.database)
        owner = WorkOrderOrchestrator(reopened, runtime.create, runtime.cancel)
        record = await owner.step(identifier)
        assert record["attempts"][0]["run_id"] == original["run_id"]
        assert len(runtime.requests) == 1
        # RunService's existing startup recovery terminalizes lost active leases.
        await asyncio.to_thread(runtime.runs.recover_incomplete)
        record = await owner.step(identifier)
        assert record["status"] == "failed" and len(runtime.requests) == 1
        await owner.close()

    asyncio.run(scenario())


def test_queue_backpressure_does_not_reserve_or_attempt_admission(tmp_path):
    async def scenario():
        store, runtime, identifier = setup(tmp_path)
        available = False
        owner = WorkOrderOrchestrator(store, runtime.create, runtime.cancel,
                                      admission_available=lambda: available)
        await owner.activate(identifier, expected_revision=1, settings={})
        record = await owner.step(identifier)
        assert record["status"] == "queued" and record["attempts"] == []
        assert runtime.requests == [] and store.pending_intents(identifier) == []
        available = True
        assert (await owner.step(identifier))["attempts"][0]["run_id"]
        await owner.close()

    asyncio.run(scenario())


def test_recovery_pages_beyond_recent_listing_cap_without_dispatch(tmp_path):
    async def scenario():
        store, runtime, first = setup(tmp_path)
        identifiers = [first]
        single = plan_from_payload({"title": "page fixture", "tasks": [
            {"id": "only", "title": "only", "prompt": "offline page fixture"},
        ]})
        for _ in range(104):
            identifiers.append(store.create(single)[0]["work_order_id"])
        for identifier in identifiers:
            store.activate(identifier, expected_revision=1, settings={})
        seen = []
        original = store.reconcile

        def reconcile(identifier):
            seen.append(identifier)
            return original(identifier)

        store.reconcile = reconcile
        owner = WorkOrderOrchestrator(store, runtime.create, runtime.cancel)
        await owner.recover()
        assert seen == sorted(identifiers) and runtime.requests == []
        await owner.close()

    asyncio.run(scenario())


def test_pump_scan_io_failure_retries_boundedly_without_raw_payload(tmp_path, caplog):
    async def scenario():
        store, runtime, identifier = setup(tmp_path)
        owner = WorkOrderOrchestrator(store, runtime.create, runtime.cancel)
        await owner.activate(identifier, expected_revision=1, settings={})
        await owner.start()
        original = store.candidates
        recovered = asyncio.Event()
        calls = 0

        def candidates(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("PRIVATE_DB_PATH_SENTINEL")
            return original(**kwargs)

        original_create = runtime.create

        async def create(request):
            result = await original_create(request)
            recovered.set()
            return result

        store.candidates = candidates
        owner._create_run = create
        owner.wake()
        await asyncio.wait_for(recovered.wait(), timeout=5)
        await owner.close()
        assert calls >= 2 and len(runtime.requests) == 1
        assert "PRIVATE_DB_PATH_SENTINEL" not in caplog.text
        assert "OperationalError" in caplog.text

    asyncio.run(scenario())


def test_final_reconciliation_after_closed_admission_requires_actual_drain(tmp_path):
    async def scenario():
        store, runtime, identifier = setup(tmp_path)
        owner = WorkOrderOrchestrator(store, runtime.create, runtime.cancel)
        await owner.activate(identifier, expected_revision=1, settings={})
        record = await owner.step(identifier)
        run_id = record["attempts"][0]["run_id"]
        with pytest.raises(RuntimeError, match="closed"):
            await owner.reconcile_after_drain()
        await owner.close()
        runtime.runs.update(run_id, "completed")
        await owner.reconcile_after_drain()
        assert store.get(identifier)["tasks"][0]["status"] == "running"
        runtime.runs.release_conversation_turn(run_id)
        await owner.reconcile_after_drain()
        assert store.get(identifier)["tasks"][0]["status"] == "succeeded"
        assert len(runtime.requests) == 1

    asyncio.run(scenario())
