"""Native conversation projections through actual RunService lifecycle paths."""

import asyncio
import threading

import pytest

from bench.runtime_approval_harness import ApprovalSideEffectTrace, ScriptedApprovalProvider
from bench.runtime_fixtures import load_task_fixture, materialize_task_case
from doppel_agent.concurrency import QueueCapacityError
from doppel_agent.provider import MockProvider, ModelTurn
from doppel_agent.runtime.service import RunService


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("action", ["approve", "reject", "edit", "cancel", "expire"])
def test_reconstructed_native_approval_projects_one_turn(tmp_path, mode, action):
    async def scenario():
        fixture = load_task_fixture("approval-01")
        root = tmp_path / "workspace"
        materialize_task_case(fixture, root)
        before = (root / "note.txt").read_bytes()
        provider = ScriptedApprovalProvider(fixture, mode, root)
        service = RunService(root, provider=provider)
        await service.start()
        try:
            with ApprovalSideEffectTrace(root) as trace:
                conv = service.conversations.create(mode=mode)
                body = {"prompt": "bounded patch", "mode": mode, "conversation_id": conv["id"],
                        "effort": "deep", "permissions": {"workspace_write": True}, "idempotency_key": "approval-turn-key"}
                record, _ = await service.create(body)
                rid = record["run_id"]
                await service.scheduler.wait(rid)
                paused = await service.get(rid)
                assert paused["status"] == "interrupted"
                assert "fallback_runtime" not in paused["metadata"]
                assert [m["role"] for m in service.conversations.get(conv["id"])["messages"]] == ["user"]
                with pytest.raises(ValueError, match="active"):
                    await service.create({**body, "idempotency_key": "conflicting-turn"})
                await service.close()
                service = RunService(root, provider=provider, approval_ttl_seconds=0 if action == "expire" else 900)
                await service.start()
                assert service.conversations.get(conv["id"])["active_run_id"] == rid
                iid = paused["metadata"]["interrupts"][0]["id"]
                if action == "cancel":
                    assert await service.cancel(rid)
                    expected = "cancelled"
                elif action == "expire":
                    with pytest.raises(TimeoutError):
                        await service.resume(rid, iid, {"action": "approve"})
                    expected = "interrupted_expired"
                else:
                    decision = {"action": action}
                    if action == "edit":
                        calls = paused["metadata"]["interrupts"][0]["value"].get("tool_calls", [])
                        decision["tool_calls"] = [{**({"id": calls[0]["id"]} if mode == "graph" else {}),
                            "name": "propose_patch", "arguments": {
                            "changes": [{"path": "note.txt", "content": "edited native approval\n"}]}}]
                    await service.resume(rid, iid, decision)
                    await service.scheduler.wait(rid)
                    expected = "completed"
                result = await service.get(rid)
                assert result["status"] == expected, result
                detail = service.conversations.get(conv["id"])
                assert [m["role"] for m in detail["messages"]] == (["user", "assistant"] if expected == "completed" else ["user"])
                assert detail["active_run_id"] is None
                assert (await service.create(body))[1] is False
                with pytest.raises(ValueError):
                    await service.resume(rid, iid, {"action": "approve"})
                service.conversations.reconcile()
                assert service.conversations.get(conv["id"])["messages"] == detail["messages"]
                assert trace.trace["patch_apply_count"] == (1 if action in {"approve", "edit"} else 0)
                if action in {"reject", "cancel", "expire"}:
                    assert (root / "note.txt").read_bytes() == before
                if action == "edit":
                    assert (root / "note.txt").read_text() == "edited native approval\n"
                    proposals = [e for e in service.events.list(rid) if e["type"] == "patch.proposed"]
                    assert proposals[-1]["payload"]["edited"] is True
                    assert "+edited native approval" in proposals[-1]["payload"]["proposal"]["unified_diff"]
        finally:
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["provider", "timeout", "queue", "shutdown"])
def test_failure_projection_and_next_turn_clean_checkpoint(tmp_path, failure, monkeypatch):
    class Failing:
        async def anext_turn(self, *_args):
            if failure == "provider":
                raise RuntimeError("secret-provider-token-do-not-persist")
            await asyncio.sleep(60)
            return ModelTurn("unreachable")

    async def scenario():
        service = RunService(tmp_path, provider=Failing())
        await service.start()
        original_submit = service.scheduler.submit
        cid = service.conversations.create()["id"]
        body = {"prompt": "failure turn", "mode": "graph", "conversation_id": cid, "permissions": {}, "deadline_seconds": 1}
        try:
            if failure == "queue":
                async def full(*_args):
                    raise QueueCapacityError("scripted full")
                monkeypatch.setattr(service.scheduler, "submit", full)
                with pytest.raises(QueueCapacityError):
                    await service.create(body)
                rid = service.conversations.get(cid)["runs"][0]["run_id"]
                monkeypatch.setattr(service.scheduler, "submit", original_submit)
            else:
                record, _ = await service.create(body)
                rid = record["run_id"]
                if failure == "shutdown":
                    await service.close()
                try:
                    await service.scheduler.wait(rid)
                except (RuntimeError, TimeoutError, asyncio.CancelledError):
                    pass
            result = await service.get(rid)
            assert result["status"] in {"failed", "cancelled"}, result
            assert "secret-provider-token" not in str(result)
            assert "secret-provider-token" not in str(await service.list_events(rid))
            projected = service.conversations.get(cid)
            assert [m["role"] for m in projected["messages"]] == ["user"]
            assert projected["active_run_id"] is None
            old_thread = result["thread_id"]
            await service.close()
            service = RunService(tmp_path, provider=MockProvider())
            await service.start()
            next_run, _ = await service.create({**body, "prompt": "clean next turn", "deadline_seconds": 30})
            await service.scheduler.wait(next_run["run_id"])
            assert next_run["thread_id"] != old_thread
            assert (await service.get(next_run["run_id"]))["status"] == "completed"
            assert [m["role"] for m in service.conversations.get(cid)["messages"]] == ["user", "user", "assistant"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_cancelled_acceptance_caller_drains_committed_turn(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        entered, release = threading.Event(), threading.Event()
        original = service.runs.create
        cid = service.conversations.create()["id"]

        def create(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            if not release.wait(30):
                raise TimeoutError("acceptance gate not released")
            return result

        monkeypatch.setattr(service.runs, "create", create)
        task = asyncio.create_task(service.create({"prompt": "acceptance cancellation", "mode": "graph",
                                                 "permissions": {}, "conversation_id": cid}))
        try:
            assert await asyncio.to_thread(entered.wait, 30)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 30)
            detail = service.conversations.get(cid)
            assert detail["runs"][0]["status"] == "cancelled"
            assert [m["role"] for m in detail["messages"]] == ["user"]
            assert detail["active_run_id"] is None
            assert service.scheduler.active_count == 0
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_completed_write_under_cancellation_retains_turn_lease(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        entered, release = threading.Event(), threading.Event()
        original = service.runs.update
        cid = service.conversations.create()["id"]

        def update(rid, status, **fields):
            result = original(rid, status, **fields)
            if status == "completed":
                entered.set()
                if not release.wait(30):
                    raise TimeoutError("completion drain gate not released")
            return result

        monkeypatch.setattr(service.runs, "update", update)
        body = {"prompt": "first turn", "mode": "graph", "permissions": {}, "conversation_id": cid}
        try:
            record, _ = await service.create(body)
            rid = record["run_id"]
            assert await asyncio.to_thread(entered.wait, 30)
            assert (await service.get(rid))["status"] == "completed"
            assert service.conversations.get(cid)["active_run_id"] == rid
            with pytest.raises(ValueError, match="active"):
                await service.create({**body, "prompt": "must not inherit uncertain checkpoint"})
            assert await service.scheduler.cancel(rid)  # same cancellation path as shutdown
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(service.scheduler.wait(rid), 30)
            assert (await service.get(rid))["status"] == "cancelled"
            assert [m["role"] for m in service.conversations.get(cid)["messages"]] == ["user"]
            next_run, _ = await service.create({**body, "prompt": "new clean thread"})
            await service.scheduler.wait(next_run["run_id"])
            assert next_run["thread_id"] != record["thread_id"]
        finally:
            release.set()
            await service.close()

    asyncio.run(scenario())


def test_native_child_inherits_accepted_parent_model_snapshot(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path)
        service.settings.save_profile({"provider": "mock", "model": "accepted-model", "name": "before"}, profile_id="default")
        original_runtime = service._runtime
        children = []

        async def runtime(record):
            if record["mode"] == "graph" and "conversation_id" not in record:
                children.append(record)
            return await original_runtime(record)

        # Observe the real runtime setup; no fake result or replacement tool.
        monkeypatch.setattr(service, "_runtime", runtime)
        await service.start()
        try:
            parent, _ = await service.create({"prompt": "parent", "mode": "graph", "permissions": {"delegate": True}})
            await service.scheduler.wait(parent["run_id"])
            service.settings.save_profile({"provider": "mock", "model": "changed-default", "name": "after"}, profile_id="default")
            child = await service.spawn_subagent(parent["run_id"], "child investigation")
            await service.subagents.scheduler.wait(child["subagent_id"])
            assert len(children) == 1
            assert children[0]["profile_snapshot"]["model"] == "accepted-model"
            assert (await service.get_subagent(parent["run_id"], child["subagent_id"]))["status"] == "completed"
        finally:
            await service.close()

    asyncio.run(scenario())
