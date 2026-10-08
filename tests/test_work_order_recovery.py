"""S3 isolated RunService regression definitions; execute only in S9."""

import asyncio
import sqlite3
from uuid import uuid4

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.base import RuntimeResult
from doppel_agent.runtime.service import RunService
from doppel_agent.tasks.work_orders import plan_from_payload


def plan(access="read"):
    return plan_from_payload({"title": "offline order", "tasks": [
        {"id": "first", "title": "first", "prompt": "first offline fixture", "access": access},
        {"id": "second", "title": "second", "prompt": "second offline fixture", "dependencies": ["first"]},
    ]})


async def wait_order(service, identifier, status):
    async with asyncio.timeout(15):
        while True:
            record = await service.get_work_order(identifier)
            if record["status"] == status:
                return record
            await asyncio.sleep(0.01)


def test_continuous_pump_completes_dependencies_with_existing_mock_runtime(tmp_path):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        try:
            order, _ = await service.create_work_order(plan())
            assert service.scheduler.accepted_count == 0
            await service.activate_work_order(order["work_order_id"], expected_revision=1, settings={})
            terminal = await wait_order(service, order["work_order_id"], "succeeded")
            assert [t["status"] for t in terminal["tasks"]] == ["succeeded", "succeeded"]
            assert service.scheduler.accepted_count == 2
            assert len({a["run_id"] for a in terminal["attempts"]}) == 2
            for attempt in terminal["attempts"]:
                run = await service.get(attempt["run_id"])
                assert run["status"] == "completed" and not run["lease_active"]
                assert run["profile_snapshot"] == terminal["execution"]["profiles"]["scripted"]
                assert not any(run["request"]["permissions"].values())
        finally:
            await service.close()
        assert service.cleanup_complete and service.work_order_orchestrator._worker.done()

    asyncio.run(scenario())


def test_restart_maps_accepted_intent_after_existing_lost_lease_recovery(tmp_path):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        store = service.work_orders
        order, _ = store.create(plan())
        scope = service._freeze_work_order_scope({}, plan().tasks)
        store.activate(order["work_order_id"], expected_revision=1, settings=scope)
        intent = store.reserve_next(order["work_order_id"], expected_revision=1)
        original, _ = service.runs.create(uuid4().hex, uuid4().hex, "graph", intent["request"],
            intent["admission_key"], native=True, profile_snapshot=intent["profile_snapshot"])
        try:
            await service.start()
            record = await service.get_work_order(order["work_order_id"])
            assert record["status"] == "failed"
            assert record["attempts"][0]["run_id"] == original["run_id"]
            assert record["tasks"][1]["status"] == "blocked"
            assert service.scheduler.accepted_count == 0
        finally:
            await service.close()

    asyncio.run(scenario())


def test_frozen_mock_model_does_not_fall_back_to_edited_live_settings(tmp_path):
    async def scenario():
        service = RunService(tmp_path)
        profile = {"id": "offline", "provider": "mock", "model": "mock-original", "base_url": ""}
        service.settings.public = lambda: {"active_profile_id": "offline", "profiles": [profile]}
        order, _ = service.work_orders.create(plan())
        scope = service._freeze_work_order_scope({}, plan().tasks)
        service.work_orders.activate(order["work_order_id"], expected_revision=1, settings=scope)
        profile.update(provider="openai", model="must-not-be-used", base_url="https://invalid.example")

        def forbidden(*args, **kwargs):
            raise AssertionError("snapshot must not request live profile or any key")

        service.settings.profile = forbidden
        service.settings.api_key = forbidden
        try:
            await service.start()
            terminal = await wait_order(service, order["work_order_id"], "succeeded")
            for attempt in terminal["attempts"]:
                run = await service.get(attempt["run_id"])
                assert run["profile_snapshot"]["model"] == "mock-original"
                assert run["profile_snapshot"]["provider"] == "mock"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_write_classification_uses_existing_workspace_lock_without_grants(tmp_path):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        entered, release = asyncio.Event(), asyncio.Event()

        class FixtureRuntime:
            async def run(self, request, sink):
                if request.prompt.startswith("first"):
                    entered.set()
                    await release.wait()
                return RuntimeResult(request.run_id, request.thread_id, "completed", "fixture", "fixture")

        async def runtime(record):
            return FixtureRuntime()

        service._runtime = runtime
        try:
            order, _ = await service.create_work_order(plan("write"))
            async with service.workspace_locks.read(service.workspace):
                await service.activate_work_order(order["work_order_id"], expected_revision=1, settings={})
                async with asyncio.timeout(5):
                    while True:
                        record = await service.get_work_order(order["work_order_id"])
                        if record["attempts"] and record["attempts"][0]["run_id"]:
                            run = await service.get(record["attempts"][0]["run_id"])
                            if run["status"] == "running":
                                break
                        await asyncio.sleep(0.01)
                assert not entered.is_set()
                assert run["write_scope"] is True
                assert not any(run["request"]["permissions"].values())
            await asyncio.wait_for(entered.wait(), timeout=5)
            release.set()
            await wait_order(service, order["work_order_id"], "succeeded")
        finally:
            release.set()
            await service.close()

    asyncio.run(scenario())


def test_service_close_stops_admission_before_runtime_drain_and_reconciles(tmp_path):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        entered = asyncio.Event()

        class WaitingRuntime:
            async def run(self, request, sink):
                entered.set()
                await asyncio.Event().wait()

        async def runtime(record):
            return WaitingRuntime()

        service._runtime = runtime
        order, _ = await service.create_work_order(plan())
        await service.activate_work_order(order["work_order_id"], expected_revision=1, settings={})
        await asyncio.wait_for(entered.wait(), timeout=5)
        await asyncio.wait_for(service.close(), timeout=10)
        record = service.work_orders.get(order["work_order_id"])
        assert service.cleanup_complete and service.scheduler.active_count == 0
        assert service.work_order_orchestrator._worker.done()
        assert service.scheduler.accepted_count == 1
        assert record["attempts"][0]["runtime_lease_active"] == 0
        assert record["tasks"][0]["status"] == "cancelled"
        assert record["tasks"][1]["status"] == "blocked"

    asyncio.run(scenario())


def test_public_admission_cannot_spoof_private_work_order_namespace(tmp_path):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        try:
            with pytest.raises(ValueError, match="reserved"):
                await service.create({"prompt": "spoof", "mode": "graph", "permissions": {},
                                      "idempotency_key": "work-order:" + uuid4().hex})
            assert service.scheduler.accepted_count == 0
            with sqlite3.connect(service.runs.database) as db:
                assert db.execute("SELECT COUNT(*) FROM runtime_runs").fetchone()[0] == 0
        finally:
            await service.close()

    asyncio.run(scenario())


def test_activation_replay_preserves_frozen_default_after_global_config_change(tmp_path):
    async def scenario():
        service = RunService(tmp_path)
        current = {"active_profile_id": "first", "profiles": [
            {"id": "first", "provider": "mock", "model": "original", "base_url": ""},
        ]}
        service.settings.public = lambda: current
        service.work_order_orchestrator._available = lambda: False
        try:
            order, _ = await service.create_work_order(plan())
            first = await service.activate_work_order(order["work_order_id"], expected_revision=1, settings={})
            current["active_profile_id"] = "other"
            current["profiles"] = [{"id": "other", "provider": "mock", "model": "changed", "base_url": ""}]
            replay = await service.activate_work_order(order["work_order_id"], expected_revision=1, settings={})
            assert replay["execution"] == first["execution"]
            assert replay["execution"]["profile_id"] == "first"
            assert service.scheduler.accepted_count == 0
            with pytest.raises(ValueError, match="draft"):
                await service.activate_work_order(order["work_order_id"], expected_revision=1, settings={"profile_id": "other"})
        finally:
            await service.close()

    asyncio.run(scenario())
