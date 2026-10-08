"""S7 C UNRUN definitions: original API/parent engines/child Graph/store/scheduler.

No seeded parent, replaced runtime/factory/manager or real provider. Scripted
model assertions are deterministic plumbing oracles, not paid-model quality.
"""

import asyncio
import threading

import httpx
import pytest

from doppel_agent.api import create_app
from doppel_agent.provider import ModelTurn, ToolCall


class ChildProvider:
    def __init__(self):
        self.observed = []

    def next_turn(self, messages, tools):
        latest = next(item.content for item in reversed(messages) if item.role == "user")
        names = {tool["function"]["name"] for tool in tools}
        self.observed.append((latest, tuple(messages), names))
        if latest.startswith("parent-"):
            return ModelTurn(content="original parent answer")
        assert not ({"propose_patch", "run_command", "task", "delegate"} & names)
        assert not any(name.startswith("mcp__") for name in names)
        assert "read_file" in names
        if latest == "child-first":
            if messages[-1].role == "tool":
                assert messages[-1].content == "CHILD_READ_ORACLE"
                return ModelTurn(content="child:CHILD_READ_ORACLE")
            return ModelTurn(tool_calls=(ToolCall("child-read-1", "read_file", {"path": "evidence.txt"}),))
        assert latest.startswith("child-follow-") or latest.startswith("capacity-")
        return ModelTurn(content="answer:" + latest)


@pytest.mark.parametrize("parent_mode", ["graph", "deep"])
def test_actual_parent_child_graph_lineage_complete_paging_stale_cas_and_lifetime_cap(tmp_path, parent_mode):
    async def scenario():
        evidence = tmp_path / "evidence.txt"
        evidence.write_text("CHILD_READ_ORACLE", encoding="utf-8")
        provider = ChildProvider()
        app = create_app(tmp_path, provider=provider)
        service = app.state.run_service
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                response = await client.post("/api/v1/runs", json={"prompt": "parent-original", "mode": parent_mode,
                    "permissions": {"delegate": True, "workspace_write": True, "command_execute": True, "mcp_execute": True}})
                assert response.status_code == 202
                parent = response.json()["run_id"]
                await asyncio.wait_for(service.scheduler.wait(parent), 10)
                original = await service.get(parent)
                assert original["status"] == "completed" and "fallback_runtime" not in original["metadata"]
                assert original["mode"] == parent_mode
                denied = await client.post("/api/v1/runs", json={"prompt": "parent-denied", "mode": parent_mode})
                assert denied.status_code == 202
                denied_id = denied.json()["run_id"]
                await asyncio.wait_for(service.scheduler.wait(denied_id), 10)
                denied_base = f"/api/v1/subagent-review/runs/{denied_id}"
                calls_before = len(provider.observed)
                assert (await client.get(denied_base)).status_code == 403
                assert (await client.post(denied_base + "/spawn", json={"confirmed": True, "prompt": "child-first"})).status_code == 403
                assert len(provider.observed) == calls_before
                base = f"/api/v1/subagent-review/runs/{parent}"
                snapshot = await client.get(base)
                assert snapshot.status_code == 200 and snapshot.json()["total"] == 0
                calls_before = len(provider.observed)
                assert (await client.post(base + "/spawn", json={"confirmed": False, "prompt": "child-first"})).status_code == 422
                assert len(provider.observed) == calls_before
                spawned = await client.post(base + "/spawn", json={"confirmed": True, "prompt": "child-first"})
                assert spawned.status_code == 202 and spawned.json()["completion_verified"] is False
                child = spawned.json()["record"]["subagent_id"]
                first = await asyncio.wait_for(service.subagents.wait(child), 10)
                assert first["status"] == "completed" and first["generation"] == 1 and first["answer"] == "child:CHILD_READ_ORACLE"
                assert await service.get(child) is None  # child store, not a new independent run
                for generation in range(1, 18):
                    followed = await client.post(base + f"/{child}/follow-ups", json={"confirmed": True,
                        "expected_generation": generation, "prompt": f"child-follow-{generation + 1}"})
                    assert followed.status_code == 202 and followed.json()["record"]["generation"] == generation + 1
                    row = await asyncio.wait_for(service.subagents.wait(child), 10)
                    assert row["generation"] == generation + 1 and row["status"] == "completed"
                snapshot = (await client.get(base)).json()
                current = snapshot["items"][0]
                assert current["generation"] == 18 and current["history_total"] == 17
                assert current["history_offset"] == 1 and len(current["history"]) == 16 and current["history_truncated"] is True
                assert snapshot["child_mode"] == "graph" and snapshot["profile_inheritance"] == "parent_snapshot"
                assert snapshot["capabilities"]["workspace_read"] is True
                assert all(snapshot["capabilities"][key] is False for key in ("workspace_write", "command_execute", "mcp_execute", "delegate"))
                page = await client.get(base + f"/{child}/history?expected_generation=18&offset=0&limit=16")
                assert page.status_code == 200 and page.json()["history_total"] == 17
                assert page.json()["history"][0] == {"prompt": "child-first", "answer": "child:CHILD_READ_ORACLE"}
                assert len(page.json()["history"]) == 16
                tail = await client.get(base + f"/{child}/history?expected_generation=18&offset=16&limit=16")
                assert tail.status_code == 200 and len(tail.json()["history"]) == 1
                before = await service.subagents.get(child)
                calls_before = len(provider.observed)
                for suffix, body in (("follow-ups", {"confirmed": True, "expected_generation": 1, "prompt": "stale"}),
                                     ("cancel", {"confirmed": True, "expected_generation": 1})):
                    refused = await client.post(base + f"/{child}/{suffix}", json=body)
                    assert refused.status_code == 409 and refused.headers["cache-control"] == "no-store"
                assert await service.subagents.get(child) == before and len(provider.observed) == calls_before
                for number in range(3):
                    value = await client.post(base + "/spawn", json={"confirmed": True, "prompt": f"capacity-{number}"})
                    assert value.status_code == 202
                    assert (await asyncio.wait_for(service.subagents.wait(value.json()["record"]["subagent_id"]), 10))["status"] == "completed"
                rejected = await client.post(base + "/spawn", json={"confirmed": True, "prompt": "capacity-overflow"})
                assert rejected.status_code == 409
                full = (await client.get(base)).json()
                assert full["total"] == 4 and full["counts"]["durable_active_for_parent"] == 0
                events = await service.list_events(parent)
                runtime_events = [item for item in events if item["type"] == "subagent.runtime" and item["payload"]["subagent_id"] == child]
                assert {item["payload"]["generation"] for item in runtime_events} == set(range(1, 19))
                assert all(item["run_id"] == parent and item["thread_id"] == original["thread_id"]
                           and item["payload"]["parent_run_id"] == parent for item in runtime_events)
                assert any(item["payload"]["runtime_kind"] == "graph.tool_finished" and item["payload"]["generation"] == 1
                           and item["payload"]["runtime_payload"]["tool"] == "read_file" for item in runtime_events)
                assert sum(item["type"] == "runtime.finished" and item["payload"]["status"] == "completed" for item in events) == 1
                assert (await service.get(parent))["answer"] == "original parent answer" and evidence.read_text() == "CHILD_READ_ORACLE"
        assert not service._owner.held
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_original_child_graph_cancellation_cleanup_is_joined_before_owner_release(tmp_path, action):
    async def scenario():
        started, release = threading.Event(), threading.Event()
        class Provider:
            def next_turn(self, messages, tools):
                latest = next(item.content for item in reversed(messages) if item.role == "user")
                if latest == "parent-original":
                    return ModelTurn(content="original parent")
                assert latest == "gated-child"
                started.set()
                assert release.wait(10)
                return ModelTurn(content="worker returned after cancellation")
        app = create_app(tmp_path, provider=Provider())
        service = app.state.run_service
        closing = None
        async with app.router.lifespan_context(app):
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                    parent = await client.post("/api/v1/runs", json={"prompt": "parent-original", "mode": "graph", "permissions": {"delegate": True}})
                    assert parent.status_code == 202
                    rid = parent.json()["run_id"]
                    await asyncio.wait_for(service.scheduler.wait(rid), 10)
                    base = f"/api/v1/subagent-review/runs/{rid}"
                    value = await client.post(base + "/spawn", json={"confirmed": True, "prompt": "gated-child"})
                    assert value.status_code == 202
                    cid = value.json()["record"]["subagent_id"]
                    assert await asyncio.to_thread(started.wait, 5)
                    if action == "cancel":
                        wrong = await client.post(base + f"/{cid}/cancel", json={"confirmed": True, "expected_generation": 2})
                        assert wrong.status_code == 409 and not release.is_set()
                        requested = await client.post(base + f"/{cid}/cancel", json={"confirmed": True, "expected_generation": 1})
                        assert requested.status_code == 200 and requested.json()["cancel_requested"] is True
                        assert requested.json()["physical_drain_verified"] is False
                    closing = asyncio.create_task(service.close())
                    for _ in range(20):
                        await asyncio.sleep(0)
                    assert service._owner.held and not closing.done()
                    release.set()
                    await asyncio.wait_for(closing, 10)
                    assert not service._owner.held
                    child = service.subagents.store.get(cid)
                    assert child["status"] == "cancelled" and child["generation"] == 1
                    assert service.runs.get(rid)["answer"] == "original parent"
            finally:
                release.set()
                if closing is not None:
                    await asyncio.gather(closing, return_exceptions=True)
    asyncio.run(scenario())
