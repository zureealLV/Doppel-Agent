"""Cancellation at service setup awaits must reach one durable terminal state."""

import asyncio
import threading

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.base import RuntimeResult
from doppel_agent.runtime.service import RunService


CANCELLATION_DRAIN_WATCHDOG_SECONDS = 30


async def wait_for_cancelled(scheduler, run_id, *, timeout=CANCELLATION_DRAIN_WATCHDOG_SECONDS):
    """Finite deadlock guard, not a cancellation latency or persistence SLA.

    Scheduler completion intentionally waits for owned SQLite IO. Correctness
    below still requires a cancelled future, durable state, one terminal event
    and no remaining jobs; a timeout or successful completion is a failure.
    """
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(scheduler.wait(run_id), timeout)


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
            await wait_for_cancelled(service.scheduler, run_id)
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
            await wait_for_cancelled(service.scheduler, record["run_id"])
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
            await wait_for_cancelled(service.scheduler, run_id)
            assert (await service.get(run_id))["status"] == "cancelled"
            assert sum(event["type"] == "run.cancelled" for event in await service.list_events(run_id)) == 1
        finally:
            release.set()
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["resume_completion_event", "pre_entry_create"])
def test_cancellation_watchdog_allows_slow_owned_terminal_io(tmp_path, monkeypatch, case):
    """Inject > old 3s watchdog IO, not a sleep-based scheduling assumption."""
    import time
    real_service = RunService
    observed = []

    def slow_service(*args, **kwargs):
        service = real_service(*args, **kwargs)
        original = service.runs.update

        def update(run_id, status, **fields):
            if status == "cancelled":
                # Explicit fault latency: cancellation must drain this owned IO.
                # No production cancellation latency SLA is asserted by these tests.
                time.sleep(4)
                observed.append((service, run_id))
            return original(run_id, status, **fields)

        service.runs.update = update
        return service

    monkeypatch.setitem(globals(), "RunService", slow_service)
    try:
        if case == "resume_completion_event":
            test_setup_cancellation_does_not_leave_durable_running(tmp_path, monkeypatch, "resume", "completion_event")
        else:
            test_scheduler_cancel_before_operation_entry_still_runs_service_cleanup(tmp_path, monkeypatch, "create", "cancel")
    finally:
        # This proof runs even on the old watchdog's TimeoutError: the real
        # service still drained IO and reached one durable cancelled state.
        assert len(observed) == 1
        service, run_id = observed[0]
        assert service.runs.get(run_id)["status"] == "cancelled"
        assert sum(row["type"] == "run.cancelled" for row in service.events.list(run_id)) == 1
        assert service.scheduler._jobs[run_id].future.cancelled()
        assert service.scheduler.active_count == 0
        print("slow-IO proof: durable cancelled, exactly one event, drained future, zero active jobs")


def test_cancellation_watchdog_still_rejects_a_stuck_wait():
    class StuckScheduler:
        async def wait(self, _run_id):
            await asyncio.Event().wait()

    async def scenario():
        with pytest.raises(TimeoutError):
            await wait_for_cancelled(StuckScheduler(), "stuck", timeout=0.02)

    asyncio.run(scenario())


def test_cancellation_watchdog_rejects_successful_non_cancelled_completion():
    class SuccessfulScheduler:
        async def wait(self, _run_id):
            return "completed"

    async def scenario():
        with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
            await wait_for_cancelled(SuccessfulScheduler(), "not-cancelled", timeout=0.02)

    asyncio.run(scenario())
