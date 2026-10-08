"""S8 B2b2 definitions FIRST, ALL UNRUN; no network/billing/native proof."""

import asyncio
import json
from threading import Event

import pytest

from doppel_agent.provider import ModelTurn, ProviderRequestError, next_model_turn
from doppel_agent.runtime.provider_adapter import ProviderAdapter
from doppel_agent.runtime.provider_recording import (
    ProviderReceiptError, ProviderReceiptFault, receipt_sink, record_provider,
)
from doppel_agent.runtime.service import ChildRuntimeSink


class Sink:
    def __init__(self):
        self.events = []

    async def emit(self, kind, **payload):
        self.events.append((kind, payload))


def assert_returned(sink, turn, *, engine="graph", actor="runtime_model", path="sync_worker", usage=None):
    assert len(sink.events) == 2
    started_kind, started = sink.events[0]
    finished_kind, finished = sink.events[1]
    assert started_kind == "provider.call_started" and finished_kind == "provider.call_finished"
    assert started["call_id"] == finished["call_id"] == turn.provider_call_id
    assert len(turn.provider_call_id) == 32 and all(c in "0123456789abcdef" for c in turn.provider_call_id)
    common = {"version": 2, "call_id": turn.provider_call_id, "engine": engine, "actor": actor,
              "path": path, "boundary": "provider_method_not_transport_attempt"}
    assert started == common
    assert finished == {**common, "outcome": "returned", "method_entered": True,
        "usage": usage, "usage_state": "known" if usage is not None else "unknown",
        "usage_basis": "returned_model_turn", "failure": None,
        "network_attempts": None, "network_attempts_basis": None,
        "transport": {"coverage": "opaque", "boundary": "admitted_transport_method_not_wire_request",
            "intents": 0, "entered": 0, "sealed": 0, "unsettled": 0, "returned": 0, "failed": 0,
            "cancelled": 0, "known_usage": 0, "unknown_usage": 0, "input_tokens": None, "output_tokens": None,
            "total_tokens": None,
            "subtotal_state": "unknown", "overflow": False}, "price_receipt": None,
        "billing_complete": False, "account_cap_guaranteed": False}


def test_original_sync_boundary_canonical_zero_and_private_response_unchanged():
    async def scenario():
        class Provider:
            def next_turn(self, messages, tools):
                return original

        private = "UNRECOGNIZABLE_PROVIDER_CREDENTIAL PRIVATE_REASONING C:/PRIVATE"
        original = ModelTurn(private, usage={"prompt_tokens": 0, "completion_tokens": 0, "private": private},
                             provider_call_id="f" * 32)
        sink, fault = Sink(), ProviderReceiptFault()
        provider = record_provider(Provider(), sink, fault=fault, engine="graph")
        turn = await next_model_turn(provider, [], [])
        assert turn.content == private and turn.usage is original.usage
        assert turn.provider_call_id != original.provider_call_id
        assert_returned(sink, turn, usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        assert private not in json.dumps(sink.events) and fault.broken is False
    asyncio.run(scenario())


def test_original_async_boundary_seals_known_usage_and_same_actor():
    async def scenario():
        class Provider:
            async def anext_turn(self, messages, tools):
                return ModelTurn("fixture", usage={"input_tokens": 7, "prompt_tokens": 7,
                                                  "output_tokens": 3, "total_tokens": 10})
        sink = Sink()
        provider = record_provider(Provider(), sink, fault=ProviderReceiptFault(), engine="deep",
                                   actor="deep_builtin_investigator")
        turn = await next_model_turn(provider, [], [])
        assert_returned(sink, turn, engine="deep", actor="deep_builtin_investigator", path="native_async",
                        usage={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})
    asyncio.run(scenario())


@pytest.mark.parametrize("usage", [None, {}, {"total_tokens": 10},
    {"input_tokens": True, "output_tokens": 2}, {"input_tokens": 1, "output_tokens": 2, "total_tokens": 4}])
def test_missing_bad_usage_never_becomes_zero_or_heuristic_in_call_receipt(usage):
    async def scenario():
        class Provider:
            def next_turn(self, messages, tools):
                return ModelTurn("xxxxxxxx", usage=usage)
        sink = Sink()
        turn = await next_model_turn(record_provider(Provider(), sink, fault=ProviderReceiptFault(), engine="graph"), [], [])
        assert_returned(sink, turn)
    asyncio.run(scenario())


def test_original_sync_worker_late_response_sealed_before_repeated_cancel_settles():
    async def scenario():
        entered, release = Event(), Event()
        class Provider:
            def next_turn(self, messages, tools):
                entered.set()
                assert release.wait(5)
                return ModelTurn("late", usage={"input_tokens": 4, "output_tokens": 2})
        sink = Sink()
        provider = record_provider(Provider(), sink, fault=ProviderReceiptFault(), engine="graph")
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
        assert sink.events[-1][1]["outcome"] == "returned"
        assert sink.events[-1][1]["usage"] == {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
        assert len(sink.events) == 2  # No synthetic cancelled duplicate after returned worker receipt.
    asyncio.run(scenario())


def test_original_finish_append_notification_is_joined_under_repeated_cancel():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        class Delayed(Sink):
            async def emit(self, kind, **payload):
                if kind == "provider.call_finished":
                    entered.set()
                    await release.wait()
                await super().emit(kind, **payload)
        class Provider:
            def next_turn(self, messages, tools):
                return ModelTurn("late", usage={"input_tokens": 1, "output_tokens": 0})
        sink = Delayed()
        task = asyncio.create_task(next_model_turn(record_provider(Provider(), sink,
            fault=ProviderReceiptFault(), engine="graph"), [], []))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert sink.events[-1][1]["outcome"] == "returned"
    asyncio.run(scenario())


def test_async_interrupted_method_has_unknown_usage_not_no_charge():
    async def scenario():
        entered = asyncio.Event()
        class Provider:
            async def anext_turn(self, messages, tools):
                entered.set()
                await asyncio.Event().wait()
        sink, fault = Sink(), ProviderReceiptFault()
        task = asyncio.create_task(next_model_turn(record_provider(Provider(), sink, fault=fault, engine="deep"), [], []))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sink.events[-1][1]["outcome"] == "cancelled"
        assert sink.events[-1][1]["usage"] is None
        assert sink.events[-1][1]["method_entered"] is True
        assert sink.events[-1][1]["failure"] == "cancelled"
        assert fault.broken is False  # Actual interrupted provider is not an append failure.
    asyncio.run(scenario())


def test_cancel_during_original_start_append_never_enters_provider_method():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Delayed(Sink):
            async def emit(self, kind, **payload):
                if kind == "provider.call_started":
                    entered.set()
                    await release.wait()
                await super().emit(kind, **payload)
        class Provider:
            async def anext_turn(self, messages, tools):
                calls.append(1)
                return ModelTurn("not reached")
        sink, fault = Delayed(), ProviderReceiptFault()
        task = asyncio.create_task(next_model_turn(record_provider(Provider(), sink, fault=fault, engine="deep"), [], []))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert calls == [] and len(sink.events) == 2 and fault.broken is False
        assert sink.events[-1][1]["method_entered"] is False
        assert sink.events[-1][1]["outcome"] == "cancelled"
        assert sink.events[-1][1]["network_attempts"] is None
    asyncio.run(scenario())


@pytest.mark.parametrize("where", ["provider.call_started", "provider.call_finished"])
def test_original_append_failure_sticky_owner_latch_blocks_all_later_provider_calls(where):
    async def scenario():
        calls = []
        class Provider:
            def next_turn(self, messages, tools):
                calls.append("entered")
                return ModelTurn("done", usage={"input_tokens": 1, "output_tokens": 0})
        class Broken(Sink):
            async def emit(self, kind, **payload):
                if kind == where:
                    raise OSError("PRIVATE_EVENT_ERROR")
                await super().emit(kind, **payload)
        sink, fault, raw = Broken(), ProviderReceiptFault(), Provider()
        with pytest.raises(ProviderReceiptError, match="^provider_receipt_unavailable$"):
            await next_model_turn(record_provider(raw, sink, fault=fault, engine="graph"), [], [])
        assert fault.broken is True and len(calls) == (0 if where.endswith("started") else 1)
        for engine in ("graph", "deep", "legacy"):
            with pytest.raises(ProviderReceiptError):
                await next_model_turn(record_provider(raw, Sink(), fault=fault, engine=engine), [], [])
        assert len(calls) == (0 if where.endswith("started") else 1)
    asyncio.run(scenario())


def test_append_failure_latch_not_masked_by_caller_cancellation():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Broken(Sink):
            async def emit(self, kind, **payload):
                entered.set()
                await release.wait()
                raise OSError("PRIVATE_APPEND_FAILURE")
        class Provider:
            async def anext_turn(self, messages, tools):
                calls.append(1)
                return ModelTurn("not reached")
        fault = ProviderReceiptFault()
        task = asyncio.create_task(next_model_turn(record_provider(Provider(), Broken(), fault=fault, engine="deep"), [], []))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert fault.broken is True and calls == []
    asyncio.run(scenario())


def test_original_provider_error_is_preserved_but_receipt_has_fixed_failure_and_no_text():
    async def scenario():
        original = ProviderRequestError("PRIVATE_PROVIDER_ERROR", kind="rate_limited", attempts=3, status_code=429)
        class Provider:
            async def anext_turn(self, messages, tools):
                raise original
        sink, fault = Sink(), ProviderReceiptFault()
        with pytest.raises(ProviderRequestError) as captured:
            await next_model_turn(record_provider(Provider(), sink, fault=fault, engine="graph"), [], [])
        assert captured.value is original
        finish = sink.events[-1][1]
        assert finish["outcome"] == "failed" and finish["failure"] == "rate_limited"
        assert finish["usage"] is None and finish["network_attempts"] is None
        assert "PRIVATE_PROVIDER_ERROR" not in json.dumps(sink.events)
        assert fault.broken is False  # Final exception attempts is NOT actual successful retry coverage.
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", [None, [], {}, "PRIVATE_UNRECOGNIZABLE_KIND"])
def test_bad_provider_error_class_never_breaks_the_original_failed_receipt(kind):
    async def scenario():
        original = ProviderRequestError("PRIVATE_ERROR", kind=kind, attempts=1)
        class Provider:
            async def anext_turn(self, messages, tools):
                raise original
        sink = Sink()
        with pytest.raises(ProviderRequestError):
            await next_model_turn(record_provider(Provider(), sink, fault=ProviderReceiptFault(), engine="graph"), [], [])
        assert sink.events[-1][1]["failure"] == "provider_failure"
        assert "PRIVATE_" not in json.dumps(sink.events)
    asyncio.run(scenario())


def test_invalid_turn_has_fixed_failed_receipt_and_no_callback_link():
    async def scenario():
        class Provider:
            async def anext_turn(self, messages, tools):
                return {"private": "PRIVATE_RESULT"}
        sink = Sink()
        with pytest.raises(RuntimeError, match="provider_model_turn_invalid"):
            await next_model_turn(record_provider(Provider(), sink, fault=ProviderReceiptFault(), engine="graph"), [], [])
        assert sink.events[-1][1]["failure"] == "invalid_model_turn"
        assert "PRIVATE_RESULT" not in json.dumps(sink.events)
    asyncio.run(scenario())


def test_gate_admission_precedes_call_receipt_and_known_late_sync_is_inside_gate():
    async def scenario():
        enter, release = asyncio.Event(), asyncio.Event()
        class Limits:
            def provider(self, profile):
                class Gate:
                    async def __aenter__(self):
                        assert profile == "fixed"
                        enter.set()
                        await release.wait()
                    async def __aexit__(self, *args):
                        return False
                return Gate()
        class Provider:
            def next_turn(self, messages, tools):
                return ModelTurn("done", usage={"input_tokens": 0, "output_tokens": 0})
        raw, limits, sink = Provider(), Limits(), Sink()
        original = ProviderAdapter(raw, profile_id="fixed", limits=limits)
        recorded = record_provider(original, sink, fault=ProviderReceiptFault(), engine="graph")
        task = asyncio.create_task(next_model_turn(recorded, [], []))
        await asyncio.wait_for(enter.wait(), 5)
        assert sink.events == [] and original.provider is raw and original.limits is limits
        release.set()
        result = await task
        assert_returned(sink, result, usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    asyncio.run(scenario())


def test_two_sinks_shared_raw_provider_keep_frozen_child_scope_and_unique_call_ids():
    async def scenario():
        class Provider:
            async def anext_turn(self, messages, tools):
                await asyncio.sleep(0)
                return ModelTurn("done", usage={"input_tokens": 1, "output_tokens": 0})
        raw, parent, fault = Provider(), Sink(), ProviderReceiptFault()
        a, b, c = "a" * 32, "b" * 32, "c" * 32
        one = record_provider(raw, ChildRuntimeSink(parent, a, b, 2), fault=fault, engine="graph")
        two = record_provider(raw, ChildRuntimeSink(parent, a, c, 3), fault=fault, engine="graph")
        first, second = await asyncio.gather(next_model_turn(one, [], []), next_model_turn(two, [], []))
        assert first.provider_call_id != second.provider_call_id
        for kind, payload in parent.events:
            assert kind == "subagent.runtime" and payload["parent_run_id"] == a
            expected = (b, 2) if payload["runtime_payload"]["call_id"] == first.provider_call_id else (c, 3)
            assert (payload["subagent_id"], payload["generation"]) == expected
        assert not hasattr(raw, "sink") and not hasattr(raw, "run_id")
    asyncio.run(scenario())


def test_sync_owner_loop_use_refused_without_deadlock_or_provider_entry():
    async def scenario():
        calls = []
        class Provider:
            def next_turn(self, messages, tools):
                calls.append(1)
                return ModelTurn("not reached")
        sink, fault = Sink(), ProviderReceiptFault()
        provider = record_provider(Provider(), sink, fault=fault, engine="legacy")
        with pytest.raises(ProviderReceiptError):
            provider.next_turn([], [])
        assert calls == [] and sink.events == [] and fault.broken is True
    asyncio.run(scenario())


def test_receipt_wrapper_cannot_rebind_original_scope_or_double_wrap():
    async def scenario():
        class Provider:
            def next_turn(self, messages, tools):
                return ModelTurn("done")
        sink, fault = Sink(), ProviderReceiptFault()
        original = record_provider(Provider(), sink, fault=fault, engine="graph")
        assert record_provider(original, sink, fault=fault, engine="graph") is original
        with pytest.raises(ProviderReceiptError):
            record_provider(original, Sink(), fault=fault, engine="graph")
        assert fault.broken is True
    asyncio.run(scenario())


def test_unsealed_hard_provider_interrupt_quarantines_original_owner_without_fabricated_finish():
    async def scenario():
        class HardStop(BaseException):
            pass
        class Provider:
            async def anext_turn(self, messages, tools):
                raise HardStop("PRIVATE_HARD_INTERRUPT")
        sink, fault = Sink(), ProviderReceiptFault()
        recorded = record_provider(Provider(), sink, fault=fault, engine="graph")
        with pytest.raises(HardStop):
            await next_model_turn(recorded, [], [])
        assert fault.broken and len(sink.events) == 1 and sink.events[0][0] == "provider.call_started"
        with pytest.raises(ProviderReceiptError):
            await next_model_turn(recorded, [], [])
        assert "PRIVATE_" not in json.dumps(sink.events)
    asyncio.run(scenario())


def test_compat_completion_append_failure_sets_original_fault_before_sdk_can_wrap_error():
    async def scenario():
        class Broken(Sink):
            async def emit(self, kind, **payload):
                raise OSError("PRIVATE_CALLBACK_APPEND_ERROR")
        fault = ProviderReceiptFault()
        wrapped = receipt_sink(Broken(), fault)
        assert receipt_sink(wrapped, fault) is wrapped
        with pytest.raises(ProviderReceiptError, match="^provider_receipt_unavailable$"):
            await wrapped.emit("deep.model_finished", usage={"input_tokens": 1, "output_tokens": 0})
        assert fault.broken is True
    asyncio.run(scenario())


def test_opaque_nested_recorder_error_is_not_an_ordinary_model_fallback_signal():
    async def scenario():
        class Provider:
            async def anext_turn(self, messages, tools):
                raise ProviderReceiptError()
        sink, fault = Sink(), ProviderReceiptFault()
        with pytest.raises(ProviderReceiptError):
            await next_model_turn(record_provider(Provider(), sink, fault=fault, engine="deep"), [], [])
        assert fault.broken is True
        assert len(sink.events) == 1  # Failed nested recording isn't a invented sealed failure/no-effect proof.
    asyncio.run(scenario())
