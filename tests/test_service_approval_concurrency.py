"""Concurrent approval regressions for the consolidated v0.15 acceptance phase.

Not executed while the implementation batch is still open. The stale-read gate
observes real service/store responses; it does not replace a runtime or tool.
"""

import asyncio
import threading

import pytest

from bench.runtime_approval_harness import ApprovalSideEffectTrace, ScriptedApprovalProvider
from bench.runtime_fixtures import load_task_fixture, materialize_task_case
from doppel_agent.runtime.service import RunService
from doppel_agent.concurrency import QueueCapacityError


async def pending_service(workspace):
    fixture = load_task_fixture("approval-01")
    materialize_task_case(fixture, workspace)
    service = RunService(workspace, provider=ScriptedApprovalProvider(fixture, "graph", workspace))
    await service.start()
    record, _ = await service.create({"prompt": "bounded patch", "mode": "graph", "effort": "deep",
                                      "permissions": {"workspace_write": True}, "deadline_seconds": 60})
    await service.scheduler.wait(record["run_id"])
    record = await service.get(record["run_id"])
    return service, record, record["metadata"]["interrupts"][0]["id"]


def test_cancelled_resume_caller_drains_its_own_committed_sqlite_claim(tmp_path, monkeypatch):
    async def scenario():
        service, record, interrupt_id = await pending_service(tmp_path / "agent")
        original = service.runs.claim_interrupt
        entered, release = threading.Event(), threading.Event()

        def claim(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            if not release.wait(30):
                raise TimeoutError("fixture claim drain gate not released")
            return result

        monkeypatch.setattr(service.runs, "claim_interrupt", claim)
        resume = None
        try:
            with ApprovalSideEffectTrace(service.workspace) as trace:
                resume = asyncio.create_task(service.resume(record["run_id"], interrupt_id, {"action": "approve"}))
                assert await asyncio.to_thread(entered.wait, 10)
                resume.cancel()
                await asyncio.sleep(0)
                resume.cancel()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(resume, 30)
                events = await service.list_events(record["run_id"])
                assert (await service.get(record["run_id"]))["status"] == "cancelled"
                assert sum(event["type"] == "approval.decided" for event in events) == 1
                assert sum(event["type"] == "run.cancelled" for event in events) == 1
                assert service.scheduler.active_count == 0
                assert trace.trace["patch_apply_count"] == 0
        finally:
            release.set()
            if resume is not None and not resume.done():
                await asyncio.gather(resume, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_committed_approval_dispatch_failure_is_terminal_not_blindly_replayed(tmp_path, monkeypatch):
    async def scenario():
        service, record, interrupt_id = await pending_service(tmp_path / "agent")
        original = service.scheduler.submit
        before = (service.workspace / "note.txt").read_bytes()

        async def unavailable(*_args):
            raise QueueCapacityError("fixture capacity failure")

        try:
            monkeypatch.setattr(service.scheduler, "submit", unavailable)
            with pytest.raises(QueueCapacityError):
                await service.resume(record["run_id"], interrupt_id, {"action": "approve"})
            assert (await service.get(record["run_id"]))["status"] == "failed"
            monkeypatch.setattr(service.scheduler, "submit", original)
            with pytest.raises(ValueError):
                await service.resume(record["run_id"], interrupt_id, {"action": "approve"})
            events = await service.list_events(record["run_id"])
            assert sum(event["type"] == "approval.decided" for event in events) == 1
            assert sum(event["type"] == "run.failed" for event in events) == 1
            assert (service.workspace / "note.txt").read_bytes() == before
        finally:
            await service.close()

    asyncio.run(scenario())


def test_cancellation_after_claim_before_dispatch_prevents_native_patch(tmp_path, monkeypatch):
    async def scenario():
        service, record, interrupt_id = await pending_service(tmp_path / "agent")
        entered, release = asyncio.Event(), asyncio.Event()
        original_notify = service.notifier.notify
        gated = False

        async def notify(run_id):
            nonlocal gated
            current = await service.get(run_id)
            if current["status"] == "queued" and not gated:
                gated = True
                entered.set()
                await release.wait()
            await original_notify(run_id)

        monkeypatch.setattr(service.notifier, "notify", notify)
        try:
            with ApprovalSideEffectTrace(service.workspace) as trace:
                resume = asyncio.create_task(service.resume(record["run_id"], interrupt_id, {"action": "approve"}))
                async with asyncio.timeout(30):
                    await entered.wait()
                    assert await service.cancel(record["run_id"])
                    release.set()
                    await resume
                    with pytest.raises(asyncio.CancelledError):
                        await service.scheduler.wait(record["run_id"])
                events = await service.list_events(record["run_id"])
                assert (await service.get(record["run_id"]))["status"] == "cancelled"
                assert sum(event["type"] == "approval.decided" for event in events) == 1
                assert sum(event["type"] == "run.cancelled" for event in events) == 1
                assert trace.trace["patch_apply_count"] == 0
        finally:
            release.set()
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("separate_service", [False, True])
def test_concurrent_stale_approval_reads_have_one_durable_decision_and_one_patch(tmp_path, monkeypatch, mode, separate_service):
    async def scenario():
        fixture = load_task_fixture("approval-01")
        workspace = tmp_path / "agent"
        materialize_task_case(fixture, workspace)
        provider = ScriptedApprovalProvider(fixture, mode, workspace)
        first = RunService(workspace, provider=provider)
        second = None
        await first.start()
        try:
            record, _ = await first.create({"prompt": "Approve this bounded patch once.", "mode": mode, "effort": "deep",
                                            "permissions": {"workspace_write": True}, "deadline_seconds": 60})
            run_id = record["run_id"]
            await first.scheduler.wait(run_id)
            paused = await first.get(run_id)
            assert paused["status"] == "interrupted"
            interrupt_id = paused["metadata"]["interrupts"][0]["id"]
            if separate_service:
                second = RunService(workspace, provider=provider)
                await second.start()  # interrupted records must survive service reconstruction
            contenders = (first, second or first)
            reached = 0
            release = asyncio.Event()

            for service in set(contenders):
                original_get = service.get

                async def get(key, original=original_get):
                    nonlocal reached
                    result = await original(key)
                    task = asyncio.current_task()
                    if result and result["status"] == "interrupted" and task and task.get_name().startswith("approval-race-"):
                        reached += 1
                        if reached == 2:
                            release.set()
                        await asyncio.wait_for(release.wait(), 10)
                    return result

                monkeypatch.setattr(service, "get", get)

            with ApprovalSideEffectTrace(workspace) as trace:
                tasks = [asyncio.create_task(service.resume(run_id, interrupt_id, {"action": "approve"}), name=f"approval-race-{index}")
                         for index, service in enumerate(contenders)]
                async with asyncio.timeout(30):  # finite deadlock guard, not a latency claim
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    assert sum(isinstance(result, dict) for result in results) == 1, results
                    assert sum(isinstance(result, ValueError) for result in results) == 1, results
                    winner = contenders[next(index for index, result in enumerate(results) if isinstance(result, dict))]
                    await winner.scheduler.wait(run_id)
                events = await first.list_events(run_id)
                assert sum(event["type"] == "approval.decided" for event in events) == 1
                assert (await first.get(run_id))["status"] == "completed"
                assert trace.trace["patch_apply_count"] == 1
                assert trace.trace["command_calls"] == []
        finally:
            await first.close()
            if second is not None:
                await second.close()

    asyncio.run(scenario())
