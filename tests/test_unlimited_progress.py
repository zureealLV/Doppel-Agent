"""Long native tasks are stopped by completion/cancellation, not preset turn caps."""
import asyncio

import pytest

from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.service import RunService


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_native_task_passes_32_tool_turns_and_keeps_public_progress(tmp_path, mode):
    (tmp_path / "evidence.txt").write_text("evidence", encoding="utf-8")

    class Provider:
        calls = 0

        def next_turn(self, messages, tools):
            self.calls += 1
            if self.calls > 32:
                return ModelTurn("最终总结：检查完成")
            descriptor = next(t["function"] for t in tools if t["function"]["name"] == "read_file")
            key = "file_path" if "file_path" in descriptor["parameters"]["properties"] else "path"
            return ModelTurn(f"阶段 {self.calls}：继续核查证据", (ToolCall(
                f"read-{self.calls}", "read_file", {key: "evidence.txt"}),))

        async def anext_turn(self, messages, tools):
            return self.next_turn(messages, tools)

    async def scenario():
        provider = Provider()
        service = RunService(tmp_path, provider=provider)
        await service.start()
        try:
            conv = service.conversations.create(mode=mode)
            record, _ = await service.create({"conversation_id": conv["id"], "mode": mode,
                "prompt": "完整检查，分阶段汇报", "deadline_seconds": 60, "permissions": {}})
            result = await asyncio.wait_for(service.scheduler.wait(record["run_id"]), 60)
            assert result.status == "completed", result
            assert provider.calls == 33
            assert result.answer == "最终总结：检查完成"
            events = service.events.list(record["run_id"], limit=2000)
            turns = [e["payload"] for e in events if e["type"] == "assistant.turn"]
            assert len(turns) == 33
            assert turns[0]["phase"] == "progress" and turns[-1]["phase"] == "final"
            assert not any(e["type"].endswith("budget_exhausted") for e in events)
            started = [e["payload"] for e in events if e["type"].endswith("tool_started")]
            finished = [e["payload"] for e in events if e["type"].endswith("tool_finished")]
            assert len(started) == len(finished) == 32
            assert {e["tool_call_id"] for e in started} == {e["tool_call_id"] for e in finished}
        finally:
            await service.close()
    asyncio.run(scenario())
