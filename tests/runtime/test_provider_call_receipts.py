"""S8 B2b2 original runtimes/sink definitions FIRST, UNRUN; scripted only."""

import asyncio
import json

import httpx
import pytest

from doppel_agent.persistence.events import EventStore
from doppel_agent.provider import AsyncOpenAICompatibleProvider, ModelTurn
from doppel_agent.runtime.base import RunRequest
from doppel_agent.runtime.deep import DeepAgentRuntime
from doppel_agent.runtime.factory import create_runtime
from doppel_agent.runtime.provider_recording import ProviderReceiptError, ProviderReceiptFault
from doppel_agent.runtime.service import DurableEventSink, EventNotifier, RunService


class ScriptedProvider:
    def __init__(self):
        self.calls = []

    def next_turn(self, messages, tools):
        self.calls.append((messages, tools))
        return ModelTurn("offline receipt fixture", usage={"prompt_tokens": 7, "completion_tokens": 3})


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_original_factory_runtime_records_one_logical_call_in_original_sqlite_sink(tmp_path, mode):
    async def scenario():
        provider, fault = ScriptedProvider(), ProviderReceiptFault()
        runtime = create_runtime(mode, tmp_path, provider, provider_receipt_fault=fault,
                                 core_options={"max_steps": 3})
        store = EventStore(tmp_path / "runtime.sqlite3")
        request = RunRequest("fixture", run_id="a" * 32, thread_id="b" * 32)
        sink = DurableEventSink(store, EventNotifier(), request.run_id, request.thread_id)
        result = await runtime.run(request, sink)
        assert result.status == "completed" and len(provider.calls) == 1
        assert "fallback_runtime" not in result.metadata
        rows = store.list(request.run_id)
        calls = [row for row in rows if row["type"] in {"provider.call_started", "provider.call_finished"}]
        assert len(calls) == 2
        assert calls[0]["payload"]["call_id"] == calls[1]["payload"]["call_id"]
        assert calls[1]["payload"]["usage"] == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
        assert calls[1]["payload"]["engine"] == mode
        assert calls[1]["payload"]["network_attempts"] is None
        assert runtime.provider is provider and runtime.provider_receipt_fault is fault
        # Old successful completion callbacks explicitly correlate, not yet a
        # v3 report/dedup proof; Legacy's raw JSONL remains separate and intact.
        for row in rows:
            if row["type"] in {"graph.model_finished", "deep.model_finished"}:
                assert row["payload"]["provider_call_id"] == calls[1]["payload"]["call_id"]
        if mode == "legacy":
            original = (tmp_path / ".doppel-agent" / "runs" / request.run_id / "events.jsonl").read_text()
            assert '"model_usage"' in original
        assert fault.broken is False
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["graph", "deep"])
def test_original_factory_async_transport_retries_persist_in_same_runtime_sqlite_and_link_callback(tmp_path, mode):
    """B2b3b source definition, ALL UNRUN; real original factory/store, offline HTTP."""
    async def scenario():
        journal = []
        async def handler(request):
            journal.append(request)
            failed = len(journal) == 1
            return httpx.Response(503 if failed else 200, request=request,
                headers={"Retry-After": "0.01"}, json={"choices": [{"message": {"content": "offline fixture"}}],
                    "usage": {"prompt_tokens": 2 if failed else 7, "completion_tokens": 1 if failed else 3}})
        async def sleep(delay):
            await asyncio.sleep(0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AsyncOpenAICompatibleProvider("https://fixture.invalid", "fixture", client=client, sleep=sleep)
            fault = ProviderReceiptFault()
            runtime = create_runtime(mode, tmp_path, provider, provider_receipt_fault=fault,
                                     core_options={"max_steps": 3})
            store = EventStore(tmp_path / "runtime.sqlite3")
            request = RunRequest("fixture", run_id="a" * 32, thread_id="b" * 32)
            sink = DurableEventSink(store, EventNotifier(), request.run_id, request.thread_id)
            result = await runtime.run(request, sink)
            assert result.status == "completed" and "fallback_runtime" not in result.metadata
            rows = store.list(request.run_id)
            started = [row["payload"] for row in rows if row["type"] == "provider.call_started"]
            finished = [row["payload"] for row in rows if row["type"] == "provider.call_finished"]
            attempts = [row["payload"] for row in rows if row["type"] == "provider.request_finished"]
            assert len(journal) == len(attempts) == 2 and len(started) == len(finished) == 1
            call_id = started[0]["call_id"]
            assert finished[0]["call_id"] == call_id and finished[0]["network_attempts"] == 2
            assert all(item["version"] == 2 and item["call_id"] == call_id for item in attempts)
            assert [item["attempt_index"] for item in attempts] == [1, 2]
            assert [item["outcome"] for item in attempts] == ["failed", "returned"]
            assert finished[0]["transport"]["total_tokens"] == 13
            assert finished[0]["usage"] == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
            for row in rows:
                if row["type"] in {"graph.model_finished", "deep.model_finished"}:
                    assert row["payload"]["provider_call_id"] == call_id
            assert runtime.provider is provider and runtime.provider_receipt_fault is fault and not fault.broken
        # Sync Legacy/native HTTP server integration and report de-dup remain
        # D/B2b4/S9 obligations, not passed by these two async runtime definitions.
    asyncio.run(scenario())


def test_deep_wrapped_receipt_fault_cannot_trigger_original_fallback(tmp_path, monkeypatch):
    async def scenario():
        runtime = DeepAgentRuntime(tmp_path, ScriptedProvider())
        entered = []
        async def sdk_wrapped_failure(_request, _sink):
            runtime.provider_receipt_fault.mark_failed()
            raise RuntimeError("opaque SDK wrapper PRIVATE_ERROR")
        async def forbidden_fallback(*_args):
            entered.append("fallback")
            raise AssertionError("receipt fault must not re-enter the provider through Graph")
        monkeypatch.setattr(runtime, "_invoke", sdk_wrapped_failure)
        monkeypatch.setattr(runtime._fallback, "run", forbidden_fallback)
        with pytest.raises(ProviderReceiptError):
            await runtime.run(RunRequest("fixture"))
        assert entered == [] and runtime._fallback.provider_receipt_fault is runtime.provider_receipt_fault
    asyncio.run(scenario())


def test_service_all_original_runtime_modes_inherit_one_original_owner_fault(tmp_path):
    async def scenario():
        raw = ScriptedProvider()
        service = RunService(tmp_path, provider=raw)
        await service.start()
        try:
            runtimes = []
            for mode in ("legacy", "graph", "deep"):
                record = {"run_id": "a" * 32, "thread_id": "b" * 32, "mode": mode,
                          "request": {"prompt": "fixture", "permissions": {}, "effort": "balanced"}}
                runtimes.append(await service._runtime(record))
            assert all(runtime.provider_receipt_fault is service._provider_receipt_fault for runtime in runtimes)
            assert runtimes[2]._fallback.provider_receipt_fault is service._provider_receipt_fault
            service._provider_receipt_fault.mark_failed()
            for index, runtime in enumerate(runtimes):
                with pytest.raises(ProviderReceiptError):
                    await runtime.run(RunRequest("fixture", run_id=f"{index + 1:032x}"))
            assert raw.calls == []
        finally:
            await service.close()
    asyncio.run(scenario())


def test_deep_builtin_models_have_closed_actors_not_fabricated_manual_child_ids(tmp_path):
    async def scenario():
        runtime = DeepAgentRuntime(tmp_path, ScriptedProvider(), max_subagents=2)
        store = EventStore(tmp_path / "runtime.sqlite3")
        sink = DurableEventSink(store, EventNotifier(), "a" * 32, "b" * 32)
        specs = runtime._subagents(sink=sink)
        for spec in specs:
            await spec["model"]._agenerate([])
        finishes = [row["payload"] for row in store.list("a" * 32) if row["type"] == "provider.call_finished"]
        assert {row["actor"] for row in finishes} == {"deep_builtin_investigator", "deep_builtin_verifier"}
        assert len({row["call_id"] for row in finishes}) == 2
        assert "subagent_id" not in json.dumps(finishes) and "parent_run_id" not in json.dumps(finishes)
        assert all(row["engine"] == "deep" and row["billing_complete"] is False for row in finishes)
    asyncio.run(scenario())
