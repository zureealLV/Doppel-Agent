"""Offline native runtimes -> durable event -> SSE, before final history seal."""
import asyncio
import json
from threading import Event

import pytest

from doppel_agent.api.sse import run_event_stream
from doppel_agent.provider import ModelTurn
from doppel_agent.provider_streaming import stream_callback
from doppel_agent.runtime.service import RunService


@pytest.mark.parametrize("mode", ["graph", "deep", "legacy"])
def test_live_native_reply_precedes_final_message_and_persists_once(tmp_path, mode):
    async def scenario():
        release = Event()

        class Provider:
            async def anext_turn(self, messages, tools):
                await stream_callback()("首段")
                await asyncio.to_thread(release.wait, 5)
                await stream_callback()("，完成。")
                return ModelTurn("首段，完成。")

            def next_turn(self, messages, tools):
                stream_callback()("首段")
                assert release.wait(5)
                stream_callback()("，完成。")
                return ModelTurn("首段，完成。")

        service = RunService(tmp_path, provider=Provider())
        await service.start()
        try:
            conv = service.conversations.create(mode=mode)
            run, _ = await service.create({"conversation_id": conv["id"], "mode": mode,
                                           "prompt": "测试本地流式", "permissions": {}})
            stream = run_event_stream(service, run["run_id"], 0)
            while True:
                block = await asyncio.wait_for(anext(stream), 5)
                event = json.loads(next(line[6:] for line in block.splitlines() if line.startswith("data: ")))
                if event["type"] == "provider.content_delta":
                    assert event["payload"]["text"] == "首段"
                    break
            assert service.runs.get(run["run_id"])["status"] == "running"
            assert not [m for m in service.conversations.get(conv["id"])["messages"] if m["role"] == "assistant"]
            release.set()
            await service.scheduler.wait(run["run_id"])
            await stream.aclose()
            messages = service.conversations.get(conv["id"])["messages"]
            assert [m["content"] for m in messages if m["role"] == "assistant"] == ["首段，完成。"]
            deltas = [e["payload"]["text"] for e in service.events.list(run["run_id"])
                      if e["type"] == "provider.content_delta"]
            assert deltas == ["首段", "，完成。"]
        finally:
            release.set()
            await service.close()
    asyncio.run(scenario())
