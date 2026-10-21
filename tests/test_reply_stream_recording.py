"""Token previews stay ordered in the original runtime sink, not a second log."""
import asyncio

import pytest

from doppel_agent.provider import ModelTurn, next_model_turn
from doppel_agent.provider_streaming import stream_callback
from doppel_agent.runtime.provider_recording import ProviderReceiptError, ProviderReceiptFault, record_provider


class Sink:
    def __init__(self):
        self.events = []

    async def emit(self, kind, **payload):
        self.events.append((kind, payload))


@pytest.mark.parametrize("sync", [False, True])
def test_tokens_reach_original_sink_before_model_returns_and_share_call_identity(sync):
    async def scenario():
        sink = Sink()

        class Sync:
            def next_turn(self, messages, tools):
                callback = stream_callback()
                callback("你好")
                assert [kind for kind, _ in sink.events] == ["provider.call_started", "provider.content_delta"]
                callback(" Lv")
                return ModelTurn("你好 Lv")

        class Async:
            async def anext_turn(self, messages, tools):
                callback = stream_callback()
                await callback("你好")
                assert [kind for kind, _ in sink.events] == ["provider.call_started", "provider.content_delta"]
                await callback(" Lv")
                return ModelTurn("你好 Lv")

        provider = record_provider(Sync() if sync else Async(), sink, fault=ProviderReceiptFault(),
                                   engine="graph", stream_content=True)
        result = await next_model_turn(provider, [], [])
        assert result.content == "你好 Lv"
        assert [p["text"] for kind, p in sink.events if kind == "provider.content_delta"] == ["你好", " Lv"]
        assert all(p.get("call_id") == result.provider_call_id for _, p in sink.events)
        assert stream_callback() is None
    asyncio.run(scenario())


def test_token_sink_failure_fences_provider_without_reattempt_or_detached_delta():
    async def scenario():
        class Broken(Sink):
            async def emit(self, kind, **payload):
                if kind == "provider.content_delta":
                    raise OSError("private")
                await super().emit(kind, **payload)

        class Provider:
            async def anext_turn(self, messages, tools):
                await stream_callback()("visible")
                pytest.fail("failed delta must not be ignored")

        fault, sink = ProviderReceiptFault(), Broken()
        provider = record_provider(Provider(), sink, fault=fault, engine="graph", stream_content=True)
        with pytest.raises(ProviderReceiptError):
            await next_model_turn(provider, [], [])
        assert fault.broken and len(sink.events) == 1
        assert stream_callback() is None
    asyncio.run(scenario())
