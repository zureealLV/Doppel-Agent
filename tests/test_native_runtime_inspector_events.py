"""D inspector uses measured Graph node/model/tool events, not UI invention."""
import asyncio

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.service import RunService


def test_native_graph_events_project_actual_nodes(tmp_path):
    (tmp_path / "note.txt").write_text("offline evidence", encoding="utf-8")

    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        try:
            conv = service.conversations.create()
            run, _ = await service.create({"conversation_id": conv["id"], "mode": "graph", "prompt": "read note.txt", "permissions": {}})
            await service.scheduler.wait(run["run_id"])
            events = service.events.list(run["run_id"])
            started = [e["payload"]["node"] for e in events if e["type"] == "graph.node_started"]
            finished = [e["payload"]["node"] for e in events if e["type"] == "graph.node_finished"]
            assert started == finished == ["reason", "tools", "reason"]
            assert [e["payload"]["tool"] for e in events if e["type"] == "graph.tool_started"] == ["read_file"]
            assert all(e["payload"]["usage"] is None for e in events if e["type"] == "graph.model_finished")
            assert service.conversations.get(conv["id"])["messages"][1]["content"].startswith("Tool result:")
        finally:
            await service.close()

    asyncio.run(scenario())
