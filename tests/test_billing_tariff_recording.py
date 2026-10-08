"""B2b5b FIRST definitions, ALL UNRUN; original offline provider paths only."""

import asyncio
from copy import deepcopy
import json
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from doppel_agent.billing_tariff import freeze_tariff
from doppel_agent.provider import AsyncOpenAICompatibleProvider, ModelTurn, next_model_turn
from doppel_agent.runtime.factory import create_runtime
from doppel_agent.persistence.events import EventStore
from doppel_agent.runtime.base import RunRequest
from doppel_agent.runtime.provider_recording import (
    ProviderReceiptError,
    ProviderReceiptFault,
    record_provider,
)
from doppel_agent.runtime.service import ChildRuntimeSink, DurableEventSink, EventNotifier, RunService
from test_billing_tariff import PROFILE, tariff
from test_provider_call_recording import Sink
from test_provider_usage_report import call_frames, request_frames, projection, row, usage


def price(currency="CNY"):
    return freeze_tariff(tariff(currency=currency), PROFILE, freeze_date="2026-10-06")


@pytest.mark.parametrize("async_method", [False, True])
def test_original_recorder_freezes_detached_price_before_entry_without_pricing_or_cached_provider_mutation(
    async_method,
):
    async def scenario():
        sink, fault, frozen = Sink(), ProviderReceiptFault(), price()

        class Provider:
            def next_turn(self, messages, tools):
                assert sink.events[-1][1]["price_receipt"] == price()
                return ModelTurn("offline", usage=usage())

        if async_method:

            async def method(self, messages, tools):
                return self.next_turn(messages, tools)

            Provider.anext_turn = method
        raw = Provider()
        provider = record_provider(raw, sink, fault=fault, engine="graph", billing_price_receipt=frozen)
        frozen["rates"]["input"] = "999"
        await next_model_turn(provider, [], [])
        assert set(vars(raw)) == set()
        for _kind, payload in sink.events:
            assert payload["version"] == 3 and payload["price_receipt"] == price()
            assert "amount" not in payload and "source_reference" not in json.dumps(payload)
        assert sink.events[0][1]["price_receipt"] is not sink.events[1][1]["price_receipt"]
        sink.events[0][1]["price_receipt"]["rates"]["input"] = "777"
        await next_model_turn(provider, [], [])
        assert sink.events[2][1]["price_receipt"] == price() and fault.broken is False

    asyncio.run(scenario())


def test_original_recorder_reuse_refuses_tariff_rebinding_and_bad_receipt_before_provider_entry():
    async def scenario():
        sink, fault = Sink(), ProviderReceiptFault()
        raw = object()
        provider = record_provider(raw, sink, fault=fault, engine="graph", billing_price_receipt=price())
        assert (
            record_provider(provider, sink, fault=fault, engine="graph", billing_price_receipt=price())
            is provider
        )
        with pytest.raises(ProviderReceiptError):
            record_provider(provider, sink, fault=fault, engine="graph", billing_price_receipt=price("USD"))
        assert fault.broken and not sink.events
        with pytest.raises(ValueError, match="^billing_receipt_invalid$"):
            record_provider(
                raw,
                sink,
                fault=ProviderReceiptFault(),
                engine="graph",
                billing_price_receipt={"currency": "CNY"},
            )

    asyncio.run(scenario())


def test_original_async_retry_each_actual_bounded_response_keeps_same_frozen_price_on_start_and_finish():
    async def scenario():
        calls = []

        async def handler(request):
            calls.append(request)
            failed = len(calls) == 1
            return httpx.Response(
                503 if failed else 200,
                request=request,
                headers={"Retry-After": "0.01"},
                json={
                    "choices": [{"message": {"content": "offline"}}],
                    "usage": usage(2, 1) if failed else usage(),
                },
            )

        async def sleep(_delay):
            await asyncio.sleep(0)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider(
                "https://fixture.invalid", "fixture", client=client, sleep=sleep
            )
            sink = Sink()
            provider = record_provider(
                raw, sink, fault=ProviderReceiptFault(), engine="deep", billing_price_receipt=price()
            )
            await next_model_turn(provider, [], [])
        requests = [(kind, payload) for kind, payload in sink.events if kind.startswith("provider.request_")]
        assert len(calls) == 2 and len(requests) == 4
        assert all(
            payload["version"] == 3 and payload["price_receipt"] == price() for _, payload in sink.events
        )
        finishes = [payload for kind, payload in requests if kind.endswith("finished")]
        assert [payload["outcome"] for payload in finishes] == ["failed", "returned"]
        assert [payload["usage"] for payload in finishes] == [usage(2, 1), usage()]
        assert all(payload["billing_complete"] is False for payload in finishes)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_original_factory_receipt_is_detached_and_deep_fallback_inherits_same_original_tariff(tmp_path, mode):
    frozen, raw = price(), object()
    runtime = create_runtime(mode, tmp_path, raw, billing_price_receipt=frozen)
    frozen["rates"]["input"] = "777"
    assert runtime._billing_price_receipt == price() and runtime.provider is raw
    if mode == "deep":
        assert runtime._fallback._billing_price_receipt == price()
        assert runtime._fallback._billing_price_receipt is not runtime._billing_price_receipt


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_original_runtime_child_sink_keeps_same_frozen_tariff_and_actual_outer_lineage(tmp_path, mode):
    async def scenario():
        class Scripted:
            def next_turn(self, messages, tools):
                return ModelTurn("offline", usage=usage())

        runtime = create_runtime(
            mode, tmp_path, Scripted(), billing_price_receipt=price(), core_options={"max_steps": 3}
        )
        store = EventStore(tmp_path / "runtime.sqlite3")
        root, child = "a" * 32, "d" * 32
        sink = ChildRuntimeSink(DurableEventSink(store, EventNotifier(), root, "e" * 32), root, child, 2)
        result = await runtime.run(RunRequest("offline", run_id=child, thread_id=child), sink)
        assert result.status == "completed"
        rows = store.list(root)
        receipts = [
            entry["payload"]
            for entry in rows
            if entry["type"] == "subagent.runtime"
            and entry["payload"]["runtime_kind"].startswith("provider.call_")
        ]
        assert len(receipts) == 2
        for frame in receipts:
            assert (frame["parent_run_id"], frame["subagent_id"], frame["generation"]) == (root, child, 2)
            assert frame["runtime_payload"]["version"] == 3
            assert frame["runtime_payload"]["price_receipt"] == price()
        report = projection(rows)
        assert report["selected"]["known_units"] == 1
        assert report["scopes"]["root"]["known_units"] == 0
        assert report["scopes"]["children"][0]["subagent_id"] == child
        assert report["scopes"]["children"][0]["known_units"] == 1

    asyncio.run(scenario())


def test_original_service_revalidates_stored_binding_before_any_provider_key_or_current_settings_lookup():
    async def scenario():
        def forbidden(*_args, **_kwargs):
            pytest.fail("invalid stored receipt entered provider/key/current-settings path")

        owner = SimpleNamespace(
            _assert_process_admission=lambda: None,
            _assert_provider_admission=lambda: None,
            _provider=forbidden,
        )
        for mode in ("legacy", "graph", "deep"):
            record = {
                "mode": mode,
                "request": {},
                "profile_snapshot": {**PROFILE, "model": "changed-model", "billing_price_receipt": price()},
            }
            with pytest.raises(ValueError, match="^billing_receipt_invalid$"):
                await RunService._runtime(owner, record)

    asyncio.run(scenario())


def test_original_service_factory_uses_stored_snapshot_not_current_tariff_for_all_modes_or_history(
    tmp_path, monkeypatch
):
    async def scenario():
        captures = []

        def factory(mode, workspace, provider, **options):
            captures.append((mode, options["billing_price_receipt"]))
            return SimpleNamespace()

        def forbidden():
            pytest.fail("runtime read latest tariff settings")

        monkeypatch.setattr("doppel_agent.runtime.service.create_runtime", factory)
        owner = SimpleNamespace(
            _assert_process_admission=lambda: None,
            _assert_provider_admission=lambda: None,
            _provider=lambda *_args: object(),
            workspace=tmp_path,
            state_root=tmp_path,
            resources=None,
            process_supervisor=object(),
            _provider_receipt_fault=ProviderReceiptFault(),
            _tools=lambda _permissions: object(),
            settings=SimpleNamespace(public=forbidden, api_key=forbidden),
        )
        original = {**PROFILE, "billing_price_receipt": price()}
        for mode in ("legacy", "graph", "deep"):
            await RunService._runtime(
                owner, {"mode": mode, "request": {"permissions": {}}, "profile_snapshot": original}
            )
            await RunService._runtime(
                owner, {"mode": mode, "request": {"permissions": {}}, "profile_snapshot": PROFILE}
            )
        assert [frozen for _, frozen in captures] == [price(), None, price(), None, price(), None]
        original["billing_price_receipt"]["rates"]["input"] = "777"
        assert captures[0][1] == price()  # Original factory argument independently detached.

    asyncio.run(scenario())


def test_original_sync_late_return_keeps_frozen_tariff_under_repeated_caller_cancel():
    async def scenario():
        entered, release = Event(), Event()

        class Scripted:
            def next_turn(self, messages, tools):
                entered.set()
                assert release.wait(5)
                return ModelTurn("late", usage=usage())

        sink = Sink()
        provider = record_provider(
            Scripted(), sink, fault=ProviderReceiptFault(), engine="graph", billing_price_receipt=price()
        )
        task = asyncio.create_task(next_model_turn(provider, [], []))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and len(sink.events) == 1
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(sink.events) == 2 and sink.events[-1][1]["outcome"] == "returned"
        assert sink.events[-1][1]["usage"] == usage() and sink.events[-1][1]["price_receipt"] == price()

    asyncio.run(scenario())


def priced_rows():
    request = request_frames()
    call = call_frames(requests=[request], coverage="direct_original")
    for payload in (*call, *request):
        payload.update(version=3, price_receipt=price())
    return [
        row(1, "provider.call_started", call[0]),
        row(2, "provider.request_started", request[0]),
        row(3, "provider.request_finished", request[1]),
        row(4, "provider.call_finished", call[1]),
        row(5, "graph.model_finished", {"provider_call_id": call[0]["call_id"], "usage": usage()}),
    ]


def test_original_report_version3_price_linkage_retains_primary_no_price_export_until_closed_projection_pairing():
    result = projection(priced_rows())
    assert result["selected"]["known_units"] == result["selected"]["request_units"] == 1
    assert result["selected"]["total_tokens"] == 10 and result["counts"]["callbacks_matched"] == 1
    assert result["samples"]["items"][0]["receipt_version"] == 3
    assert "price_receipt" not in json.dumps(result) and "tariff_sha256" not in json.dumps(result)
    assert result["billing_complete"] is False


@pytest.mark.parametrize(
    "change", ["malformed", "different_start_finish", "different_parent", "missing", "mixed_version"]
)
def test_original_report_corrupt_or_rebound_price_receipts_never_select_or_upgrade_history(change):
    rows = deepcopy(priced_rows())
    if change == "malformed":
        rows[2]["payload"]["price_receipt"]["rates"]["input"] = "777"
    elif change == "different_start_finish":
        rows[2]["payload"]["price_receipt"] = price("USD")
    elif change == "different_parent":
        rows[1]["payload"]["price_receipt"] = rows[2]["payload"]["price_receipt"] = price("USD")
    elif change == "missing":
        rows[1]["payload"]["price_receipt"] = None
    else:
        for index in (1, 2):
            rows[index]["payload"].update(version=2, price_receipt=None)
    result = projection(rows)
    assert result["selected"]["known_units"] == 0
    assert result["counts"]["callbacks_matched"] == 0
    assert result["compatibility"]["known_units"] == 1
    assert result["state"] in {"partial", "unknown"}
