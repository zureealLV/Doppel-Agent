"""Cancellation at service setup awaits must reach one durable terminal state."""

import asyncio
import threading

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.base import RuntimeResult
from doppel_agent.runtime.service import RunService


@pytest.mark.parametrize(("operation", "phase"), [
    ("create", "running_update"), ("resume", "running_update"),
    ("create", "running_event"),
    ("create", "runtime_setup"), ("resume", "runtime_setup"),
    ("create", "completion_update"), ("resume", "completion_update"),
    ("create", "completion_event"), ("resume", "completion_event"),
])
def test_setup_cancellation_does_not_leave_durable_running(tmp_path, monkeypatch, operation, phase):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        entered = threading.Event()
        release_thread = threading.Event()
        release_async = asyncio.Event()
        original_update = service.runs.update
        original_append = service.events.append
        original_runtime = service._runtime

        def update(*args, **kwargs):
            if (phase, args[1]) in {("running_update", "running"), ("completion_update", "completed")}:
                entered.set()
                if not release_thread.wait(3):
                    raise TimeoutError("running update gate not released")
            return original_update(*args, **kwargs)

        def append(*args, **kwargs):
            if args[2] == "run.status_changed" and (phase, args[3].get("status")) in {
                ("running_event", "running"), ("completion_event", "completed"),
            }:
                entered.set()
                if not release_thread.wait(3):
                    raise TimeoutError("running event gate not released")
            return original_append(*args, **kwargs)

        async def runtime(record):
            if phase == "runtime_setup":
                entered.set()
                await release_async.wait()
            if phase.startswith("completion_"):
                class CompletedRuntime:
                    async def run(self, *_args):
                        return RuntimeResult(record["run_id"], record["thread_id"], "completed", "fixture answer", "graph")

                    resume = run

                return CompletedRuntime()
            return await original_runtime(record)

        monkeypatch.setattr(service.runs, "update", update)
        monkeypatch.setattr(service.events, "append", append)
        monkeypatch.setattr(service, "_runtime", runtime)
        request = {"prompt": "cancel setup", "mode": "graph", "permissions": {}, "deadline_seconds": 30}
        try:
            if operation == "create":
                record, _ = await service.create(request)
            else:
                record, _ = service.runs.create("resume-run", "resume-thread", "graph", request, None)
                service.runs.update(record["run_id"], "interrupted", metadata={"interrupts": [{"id": "approval", "value": {}}]})
                await service.resume(record["run_id"], "approval", {"approved": True})
            run_id = record["run_id"]
            assert await asyncio.to_thread(entered.wait, 2), phase
            assert await service.cancel(run_id)
            if phase.endswith("_update") or phase == "running_event":
                assert (await service.get(run_id))["status"] != "cancelled"
                assert not service.scheduler._jobs[run_id].future.done()
            release_thread.set()
            release_async.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(service.scheduler.wait(run_id), 3)
            # The scheduler future is not done until owned setup writes have
            # drained, so no background write can restore "running" afterwards.
            terminal = await service.get(run_id)
            assert terminal["status"] == "cancelled", terminal
            events = await service.list_events(run_id)
            assert sum(event["type"] == "run.cancelled" for event in events) == 1
            assert not await service.cancel(run_id)
            assert service.scheduler.active_count == 0
        finally:
            release_thread.set()
            release_async.set()
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["create", "resume"])
@pytest.mark.parametrize("action", ["cancel", "shutdown"])
def test_scheduler_cancel_before_operation_entry_still_runs_service_cleanup(tmp_path, monkeypatch, operation, action):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        original_create_task = asyncio.create_task
        cancellation_tasks = []

        def create_task(coro, *, name=None, **kwargs):
            if name and name.startswith("doppel-run-") and not name.startswith("doppel-run-worker-"):
                run_id = name.removeprefix("doppel-run-")
                # FIFO ready queue: cancellation runs after registration but
                # before the scheduled operation coroutine's very first step.
                cancellation = service.scheduler.cancel(run_id) if action == "cancel" else service.scheduler.shutdown()
                cancellation_tasks.append(original_create_task(cancellation))
            return original_create_task(coro, name=name, **kwargs)

        monkeypatch.setattr(asyncio, "create_task", create_task)
        request = {"prompt": "cancel before entry", "mode": "graph", "permissions": {}}
        try:
            if operation == "create":
                record, _ = await service.create(request)
            else:
                record, _ = service.runs.create("resume-run", "resume-thread", "graph", request, None)
                service.runs.update(record["run_id"], "interrupted", metadata={"interrupts": [{"id": "approval"}]})
                await service.resume(record["run_id"], "approval", {"approved": True})
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(service.scheduler.wait(record["run_id"]), 3)
            assert await asyncio.gather(*cancellation_tasks) == ([True] if action == "cancel" else [None])
            assert (await service.get(record["run_id"]))["status"] == "cancelled"
            events = await service.list_events(record["run_id"])
            assert sum(event["type"] == "run.cancelled" for event in events) == 1
        finally:
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_count", [1, 2])
def test_repeated_cancel_drains_terminal_write_before_scheduler_finishes(tmp_path, monkeypatch, cancel_count):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        setup_entered = asyncio.Event()
        terminal_entered = threading.Event()
        release = threading.Event()
        original_update = service.runs.update

        async def runtime(_record):
            setup_entered.set()
            await asyncio.Event().wait()

        def update(*args, **kwargs):
            if args[1] == "cancelled":
                terminal_entered.set()
                if not release.wait(3):
                    raise TimeoutError("terminal write gate not released")
            return original_update(*args, **kwargs)

        monkeypatch.setattr(service, "_runtime", runtime)
        monkeypatch.setattr(service.runs, "update", update)
        try:
            record, _ = await service.create({"prompt": "cancel terminal", "mode": "graph", "permissions": {}})
            run_id = record["run_id"]
            await asyncio.wait_for(setup_entered.wait(), 2)
            task = service.scheduler._running[run_id]
            assert await service.cancel(run_id)
            assert await asyncio.to_thread(terminal_entered.wait, 2)
            for _ in range(cancel_count):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
            assert not service.scheduler._jobs[run_id].future.done()
            assert (await service.get(run_id))["status"] == "running"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(service.scheduler.wait(run_id), 3)
            assert (await service.get(run_id))["status"] == "cancelled"
            assert sum(event["type"] == "run.cancelled" for event in await service.list_events(run_id)) == 1
        finally:
            release.set()
            await service.close()

    asyncio.run(scenario())
