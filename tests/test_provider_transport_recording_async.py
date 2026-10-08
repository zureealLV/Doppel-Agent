"""B2b3b definitions FIRST, ALL UNRUN; offline original httpx boundaries only.

No live network, wire/redirect coverage, native drain, official tariff or billing proof.
"""

import asyncio
import json

import httpx
import pytest

from doppel_agent.provider import (
    AsyncOpenAICompatibleProvider, Message, ModelTurn, ProviderCircuitOpen, ProviderRequestError,
    bounded_run_retries, next_model_turn,
)
from doppel_agent.provider_observation import BODY_LIMIT
from doppel_agent.runtime.provider_recording import ProviderReceiptError, ProviderReceiptFault, record_provider
from doppel_agent.runtime.service import ChildRuntimeSink


class Sink:
    def __init__(self):
        self.events = []

    async def emit(self, kind, **payload):
        self.events.append((kind, payload))


def payload(inputs=7, outputs=3):
    return {"choices": [{"message": {"content": "PRIVATE_PROVIDER_REASONING_KEY_PATH"}}],
            "usage": {"prompt_tokens": inputs, "input_tokens": inputs, "completion_tokens": outputs,
                      "total_tokens": inputs + outputs, "private": "PRIVATE_PROVIDER_REASONING_KEY_PATH"}}


def usage(inputs=7, outputs=3):
    return {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs}


def receipts(sink, kind):
    return [item for event, item in sink.events if event == kind]


def recorded(raw, sink, fault=None, *, engine="graph"):
    return record_provider(raw, sink, fault=fault if fault is not None else ProviderReceiptFault(), engine=engine)


async def no_sleep(delay):
    await asyncio.sleep(0)


def test_original_async_retry_prefix_has_exact_unique_attempts_and_known_failed_response_subtotal():
    async def scenario():
        requests, delays = [], []
        async def handler(request):
            requests.append(request)
            index = len(requests)
            if index == 1:
                data = payload(2, 1)
                data["usage"].update(prompt_cache_hit_tokens=1, prompt_cache_miss_tokens=1,
                                     completion_tokens_details={"reasoning_tokens": 1})
                return httpx.Response(429, json=data, headers={"Retry-After": "0.01"}, request=request)
            if index == 2:
                return httpx.Response(503, content=b"PRIVATE_NO_COUNTERS", headers={"Retry-After": "0.02"}, request=request)
            return httpx.Response(200, json=payload(), request=request)
        async def sleep(delay):
            delays.append(delay)
            assert len(receipts(sink, "provider.request_finished")) == len(delays)
            await asyncio.sleep(0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid/v1", "private-model", "private-key",
                client=client, sleep=sleep)
            sink, fault = Sink(), ProviderReceiptFault()
            turn = await next_model_turn(recorded(raw, sink, fault), [Message("user", "PRIVATE_PROMPT")], [])
            attempts = receipts(sink, "provider.request_finished")
            assert len(attempts) == 3 and len({item["attempt_id"] for item in attempts}) == 3
            assert [item["attempt_index"] for item in attempts] == [1, 2, 3]
            assert [item["outcome"] for item in attempts] == ["failed", "failed", "returned"]
            assert [item["failure"] for item in attempts] == ["rate_limited", "http_status", None]
            assert [item["usage"] for item in attempts] == [usage(2, 1), None, usage()]
            assert all(item["version"] == 2 for item in attempts)
            assert attempts[0]["usage_details"] == {"version": 1, "cached_input_tokens": 1,
                "uncached_input_tokens": 1, "reasoning_output_tokens": 1, "cache_state": "known", "reasoning_state": "known"}
            assert all(item["call_id"] == turn.provider_call_id and item["transport"] == "httpx_post" for item in attempts)
            call = receipts(sink, "provider.call_finished")[0]
            assert call["network_attempts"] == 3 and call["usage"] == usage()
            assert call["usage_basis"] == "original_bounded_response" and call["transport"]["coverage"] == "direct_original"
            assert call["transport"]["known_usage"] == 2 and call["transport"]["unknown_usage"] == 1
            assert call["transport"]["input_tokens"] == 9 and call["transport"]["output_tokens"] == 4
            assert call["transport"]["total_tokens"] == 13 and call["transport"]["subtotal_state"] == "partial"
            assert call["transport"]["unsettled"] == 0 and call["billing_complete"] is False
            assert delays == [0.01, 0.02] and len(requests) == 3 and not fault.broken
            assert turn.content == "PRIVATE_PROVIDER_REASONING_KEY_PATH" and "PRIVATE_" not in json.dumps(sink.events)
            assert not hasattr(raw, "sink") and not hasattr(raw, "provider_call_id") and not hasattr(raw, "run_id")
    asyncio.run(scenario())


@pytest.mark.parametrize("failure,count,kind", [(401, 1, "http_status"), (429, 3, "rate_limited"),
    (503, 3, "http_status"), ("timeout", 3, "timeout"), ("connection", 3, "connection")])
def test_original_async_terminal_failures_count_only_admitted_entries_not_error_attempts_guess(failure, count, kind):
    async def scenario():
        calls = []
        async def handler(request):
            calls.append(1)
            if failure == "timeout":
                raise httpx.ReadTimeout("PRIVATE_TIMEOUT", request=request)
            if failure == "connection":
                raise httpx.ConnectError("PRIVATE_CONNECTION", request=request)
            return httpx.Response(failure, request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client, sleep=no_sleep)
            sink = Sink()
            with pytest.raises(ProviderRequestError) as error:
                await next_model_turn(recorded(raw, sink), [], [])
            assert error.value.kind == kind and len(calls) == count
            attempts = receipts(sink, "provider.request_finished")
            assert len(attempts) == count and all(item["usage"] is None for item in attempts)
            assert all(item["failure"] == kind and item["outcome"] == "failed" for item in attempts)
            call = receipts(sink, "provider.call_finished")[0]
            assert call["network_attempts"] == count and call["usage"] is None
            assert call["transport"]["input_tokens"] is None and call["transport"]["unknown_usage"] == count
            assert "PRIVATE_" not in json.dumps(sink.events)
    asyncio.run(scenario())


@pytest.mark.parametrize("raw,state,returned,known", [
    (b'{"choices":[{"message":{"content":"ok"}}],"usage":{"input_tokens":1,"input_tokens":2,"output_tokens":3}}', "malformed_body", True, False),
    (b'{"choices":[{"message":{"content":"ok"}}],"usage":{"input_tokens":1,"output_tokens":2},"private":1e999}', "malformed_body", True, False),
    (b"x" * (BODY_LIMIT + 1), "oversized_body", False, False),
    (b'{"choices":[],"usage":{"input_tokens":7,"output_tokens":3}}', "bounded_body", False, True),
    (json.dumps(payload(0, 0)).encode(), "bounded_body", True, True),
], ids=["duplicate-usage", "nonfinite", "oversized", "empty-choices", "zero-usage"])
def test_original_async_raw_counter_framing_is_independent_from_logical_parse(raw, state, returned, known):
    async def scenario():
        responses = []
        async def handler(request):
            response = httpx.Response(200, content=raw, request=request)
            responses.append(response)
            return response
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sink = Sink()
            provider = recorded(AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client), sink)
            if returned:
                await next_model_turn(provider, [], [])
            else:
                with pytest.raises(RuntimeError):
                    await next_model_turn(provider, [], [])
            attempt = receipts(sink, "provider.request_finished")[0]
            call = receipts(sink, "provider.call_finished")[0]
            assert attempt["response_state"] == state and (attempt["usage"] is not None) is known
            assert (call["usage"] is not None) is (known and returned)
            assert call["network_attempts"] == 1 and responses[0].is_closed
    asyncio.run(scenario())


def test_cancelled_original_post_seals_unknown_attempt_without_no_charge_claim():
    async def scenario():
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sink, fault = Sink(), ProviderReceiptFault()
            task = asyncio.create_task(next_model_turn(recorded(AsyncOpenAICompatibleProvider(
                "https://fixture.invalid", "m", client=client), sink, fault), [], []))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            attempt = receipts(sink, "provider.request_finished")[0]
            call = receipts(sink, "provider.call_finished")[0]
            assert attempt["outcome"] == "cancelled" and attempt["method_entered"] and attempt["usage"] is None
            assert attempt["response_state"] == "not_observed" and call["outcome"] == "cancelled"
            assert call["network_attempts"] == 1 and call["transport"]["unsettled"] == 0
            assert not fault.broken and call["billing_complete"] is False and call["account_cap_guaranteed"] is False
    asyncio.run(scenario())


@pytest.mark.parametrize("where,expected_calls", [("provider.request_started", 0), ("provider.request_finished", 1)])
def test_original_request_append_notification_join_survives_repeated_cancellation(where, expected_calls):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Delayed(Sink):
            async def emit(self, kind, **data):
                if kind == where:
                    entered.set()
                    await release.wait()
                await super().emit(kind, **data)
        async def handler(request):
            calls.append(1)
            return httpx.Response(200, json=payload(), request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sink, fault = Delayed(), ProviderReceiptFault()
            task = asyncio.create_task(next_model_turn(recorded(AsyncOpenAICompatibleProvider(
                "https://fixture.invalid", "m", client=client), sink, fault), [], []))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done() and len(calls) == expected_calls
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert len(sink.events) == 4 and not fault.broken
            attempt = receipts(sink, "provider.request_finished")[0]
            call = receipts(sink, "provider.call_finished")[0]
            assert attempt["method_entered"] is bool(expected_calls)
            assert attempt["outcome"] == ("returned" if expected_calls else "cancelled")
            assert attempt["usage"] == (usage() if expected_calls else None)
            assert call["outcome"] == "cancelled" and call["usage"] is None
            assert call["transport"]["unsettled"] == 0 and call["network_attempts"] == expected_calls
            assert call["transport"]["known_usage"] == expected_calls
    asyncio.run(scenario())


@pytest.mark.parametrize("where,expected_calls", [("provider.request_started", 0), ("provider.request_finished", 1)])
def test_original_async_receipt_failure_is_latched_inside_retained_append_even_when_cancel_masks_error(where, expected_calls):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Broken(Sink):
            async def emit(self, kind, **data):
                if kind == where:
                    entered.set()
                    await release.wait()
                    raise OSError("PRIVATE_APPEND_NOTIFY_FAILURE")
                await super().emit(kind, **data)
        async def handler(request):
            calls.append(1)
            return httpx.Response(503, json=payload(), request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client, sleep=no_sleep)
            raw._failures = raw.circuit_failure_threshold
            raw._open_until = 0
            sink, fault = Broken(), ProviderReceiptFault()
            task = asyncio.create_task(next_model_turn(recorded(raw, sink, fault, engine="deep"), [], []))
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
            assert fault.broken and len(calls) == expected_calls and raw._half_open_probe is None
            assert receipts(sink, "provider.call_finished") == []
            with pytest.raises(ProviderReceiptError):
                await next_model_turn(recorded(raw, sink, fault, engine="graph"), [], [])
            assert len(calls) == expected_calls and "PRIVATE_" not in json.dumps(sink.events)
    asyncio.run(scenario())


def test_half_open_preflight_failure_and_circuit_open_have_zero_admitted_requests():
    async def scenario():
        calls = []
        async def handler(request):
            calls.append(1)
            return httpx.Response(200, json=payload(), request=request)
        class BadMessage:
            def to_api(self):
                raise ValueError("PRIVATE_PREFLIGHT")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client, clock=lambda: 100)
            raw._failures = raw.circuit_failure_threshold
            raw._open_until = 99
            sink = Sink()
            with pytest.raises(ValueError):
                await next_model_turn(recorded(raw, sink), [BadMessage()], [])
            assert raw._half_open_probe is None
            call = receipts(sink, "provider.call_finished")[0]
            assert call["network_attempts"] == 0 and call["transport"]["coverage"] == "direct_original"
            raw._open_until = 101
            with pytest.raises(ProviderCircuitOpen):
                await next_model_turn(recorded(raw, sink), [], [])
            assert receipts(sink, "provider.call_finished")[-1]["network_attempts"] == 0 and calls == []
            raw._open_until = 99
            assert (await next_model_turn(recorded(raw, sink), [], [])).content == "PRIVATE_PROVIDER_REASONING_KEY_PATH"
    asyncio.run(scenario())


def test_cancel_during_original_retry_backoff_keeps_known_failed_prefix_and_releases_only_owned_probe():
    async def scenario():
        sleeping = asyncio.Event()
        calls = []
        async def handler(request):
            calls.append(1)
            return httpx.Response(503, json=payload(2, 1), headers={"Retry-After": "0.01"}, request=request)
        async def sleep(delay):
            sleeping.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client, sleep=sleep)
            raw._failures = raw.circuit_failure_threshold
            raw._open_until = 0
            sink, fault = Sink(), ProviderReceiptFault()
            task = asyncio.create_task(next_model_turn(recorded(raw, sink, fault), [], []))
            await asyncio.wait_for(sleeping.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            call = receipts(sink, "provider.call_finished")[0]
            assert calls == [1] and call["network_attempts"] == 1 and call["transport"]["known_usage"] == 1
            assert call["transport"]["input_tokens"] == 2 and call["transport"]["total_tokens"] == 3
            assert call["outcome"] == "cancelled" and call["usage"] is None and not fault.broken
            assert raw._half_open_probe is None and len(receipts(sink, "provider.request_finished")) == 1
    asyncio.run(scenario())


def test_original_half_open_lease_cannot_be_released_or_reset_by_stale_other_call():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, request=req))) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client, clock=lambda: 100)
            raw._failures = raw.circuit_failure_threshold
            raw._open_until = 99
            old = await raw._enter_circuit()
            assert old is not None
            await raw._release_probe(old)
            new = await raw._enter_circuit()
            await raw._release_probe(old)
            await raw._record_success(old)
            await raw._record_failure(old)
            await raw._record_success(None)  # A normal call predating half-open cannot decide another probe.
            assert raw._half_open_probe is new and raw._failures == raw.circuit_failure_threshold
            with pytest.raises(ProviderCircuitOpen):
                await raw._enter_circuit()
            await raw._release_probe(new)
            assert raw._half_open_probe is None
    asyncio.run(scenario())


def test_original_run_retry_allowance_is_not_reset_by_new_call_observer():
    async def scenario():
        attempts, delays = {}, []
        async def handler(request):
            key = json.loads(request.content)["messages"][0]["content"]
            attempts[key] = attempts.get(key, 0) + 1
            return httpx.Response(503 if attempts[key] == 1 else 200,
                json=payload(1, 0), headers={"Retry-After": "0.01"}, request=request)
        async def sleep(delay):
            delays.append(delay)
            await asyncio.sleep(0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client,
                sleep=sleep, circuit_failure_threshold=10)
            sink, fault = Sink(), ProviderReceiptFault()
            @bounded_run_retries(fresh=True)
            async def segment():
                for key in ("a", "b", "c"):
                    await next_model_turn(recorded(raw, sink, fault), [Message("user", key)], [])
            with pytest.raises(ProviderRequestError):
                await segment()
            assert attempts == {"a": 2, "b": 2, "c": 1} and delays == [0.01, 0.01]
            assert [call["network_attempts"] for call in receipts(sink, "provider.call_finished")] == [2, 2, 1]
    asyncio.run(scenario())


def test_two_original_child_scopes_share_cached_async_provider_without_cross_call_request_linkage():
    async def scenario():
        calls = {}
        async def handler(request):
            key = json.loads(request.content)["messages"][0]["content"]
            calls[key] = calls.get(key, 0) + 1
            await asyncio.sleep(0)
            return httpx.Response(503 if calls[key] == 1 else 200,
                json=payload(1, 0), headers={"Retry-After": "0.01"}, request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client,
                sleep=no_sleep, circuit_failure_threshold=10)
            parent, fault = Sink(), ProviderReceiptFault()
            a, b, c = "a" * 32, "b" * 32, "c" * 32
            first = recorded(raw, ChildRuntimeSink(parent, a, b, 2), fault)
            second = recorded(raw, ChildRuntimeSink(parent, a, c, 3), fault)
            one, two = await asyncio.gather(next_model_turn(first, [Message("user", "one")], []),
                                            next_model_turn(second, [Message("user", "two")], []))
            assert one.provider_call_id != two.provider_call_id and calls == {"one": 2, "two": 2}
            assert len(parent.events) == 12
            for kind, outer in parent.events:
                assert kind == "subagent.runtime" and outer["parent_run_id"] == a
                scope = (b, 2) if outer["runtime_payload"]["call_id"] == one.provider_call_id else (c, 3)
                assert (outer["subagent_id"], outer["generation"]) == scope
            assert not hasattr(raw, "sink") and not hasattr(raw, "run_id")
    asyncio.run(scenario())


@pytest.mark.parametrize("close_failed", [False, True])
def test_original_response_close_is_joined_before_cancel_and_failed_close_quarantines_owner(close_failed):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        class Response(httpx.Response):
            observed_close_done = False
            async def aclose(self):
                entered.set()
                await release.wait()
                if close_failed:
                    raise httpx.ReadTimeout("PRIVATE_CLOSE_FAILURE")
                await super().aclose()
                self.observed_close_done = True
        response = Response(200, json=payload(), request=httpx.Request("POST", "https://fixture.invalid"))
        calls = []
        class Client:
            async def post(self, *args, **kwargs):
                calls.append(1)
                return response
        raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=Client())
        sink, fault = Sink(), ProviderReceiptFault()
        task = asyncio.create_task(next_model_turn(recorded(raw, sink, fault), [], []))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not response.observed_close_done
        finally:
            release.set()
            # A masked close failure still has the same original owner latch.
            with pytest.raises((asyncio.CancelledError, ProviderReceiptError)):
                await task
        attempt = receipts(sink, "provider.request_finished")[0]
        assert attempt["usage"] == usage() and calls == [1]
        assert fault.broken is close_failed and response.observed_close_done is not close_failed
        if close_failed:
            assert attempt["failure"] == "response_cleanup" and attempt["outcome"] == "failed"
            assert receipts(sink, "provider.call_finished") == []
            with pytest.raises(ProviderReceiptError):
                await next_model_turn(recorded(raw, sink, fault), [], [])
        else:
            assert attempt["outcome"] == "returned" and attempt["failure"] is None
            assert receipts(sink, "provider.call_finished")[0]["transport"]["unsettled"] == 0
    asyncio.run(scenario())


def test_retired_opaque_async_context_blocks_late_original_entry_before_http_method():
    async def scenario():
        release = asyncio.Event()
        workers, calls = [], []
        async def handler(request):
            calls.append(1)
            return httpx.Response(200, json=payload(), request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client)
            async def late():
                await release.wait()
                return await raw.anext_turn([], [])
            class Opaque:
                async def anext_turn(self, messages, tools):
                    workers.append(asyncio.create_task(late()))
                    return ModelTurn("opaque result")
            sink, fault = Sink(), ProviderReceiptFault()
            await next_model_turn(recorded(Opaque(), sink, fault), [], [])
            assert receipts(sink, "provider.call_finished")[0]["network_attempts"] is None
            release.set()
            for worker in workers:
                with pytest.raises(ProviderReceiptError):
                    await worker
            assert fault.broken and calls == [] and receipts(sink, "provider.request_started") == []
    asyncio.run(scenario())


@pytest.mark.parametrize("where,expected_calls", [("provider.request_started", 0), ("provider.request_finished", 1)])
def test_unmasked_original_async_request_receipt_failure_never_retries_provider_or_sink(where, expected_calls):
    async def scenario():
        calls = []
        class Broken(Sink):
            async def emit(self, kind, **data):
                if kind == where:
                    raise OSError("PRIVATE_RECEIPT_FAILURE")
                await super().emit(kind, **data)
        async def handler(request):
            calls.append(1)
            return httpx.Response(503, json=payload(), request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client, sleep=no_sleep)
            raw._failures = raw.circuit_failure_threshold
            raw._open_until = 0
            sink, fault = Broken(), ProviderReceiptFault()
            provider = recorded(raw, sink, fault)
            with pytest.raises(ProviderReceiptError):
                await next_model_turn(provider, [], [])
            assert len(calls) == expected_calls and fault.broken and raw._half_open_probe is None
            with pytest.raises(ProviderReceiptError):
                await next_model_turn(provider, [], [])
            assert len(calls) == expected_calls and receipts(sink, "provider.call_finished") == []
    asyncio.run(scenario())


def test_original_async_deadline_refuses_retry_without_fabricating_next_attempt():
    async def scenario():
        calls, delays = [], []
        async def handler(request):
            calls.append(1)
            return httpx.Response(429, json=payload(2, 1), headers={"Retry-After": "11"}, request=request)
        async def sleep(delay):
            delays.append(delay)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client, sleep=sleep,
                clock=lambda: 100, retry_budget_seconds=10)
            sink = Sink()
            with pytest.raises(ProviderRequestError) as error:
                await next_model_turn(recorded(raw, sink), [], [])
            assert calls == [1] and delays == [] and error.value.attempts == 1
            assert len(receipts(sink, "provider.request_started")) == 1
            assert receipts(sink, "provider.call_finished")[0]["transport"]["total_tokens"] == 3
    asyncio.run(scenario())


def test_hard_preflight_interrupt_releases_original_probe_but_preserves_unresolved_call_quarantine():
    async def scenario():
        class HardStop(BaseException):
            pass
        class BadMessage:
            def to_api(self):
                raise HardStop("PRIVATE_HARD_PREFLIGHT")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, request=req))) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client)
            raw._failures = raw.circuit_failure_threshold
            raw._open_until = 0
            sink, fault = Sink(), ProviderReceiptFault()
            with pytest.raises(HardStop):
                await next_model_turn(recorded(raw, sink, fault), [BadMessage()], [])
            assert fault.broken and raw._half_open_probe is None and len(sink.events) == 1
            assert receipts(sink, "provider.call_finished") == [] and receipts(sink, "provider.request_started") == []
    asyncio.run(scenario())


@pytest.mark.parametrize("subclass", [False, True])
def test_original_async_provider_cannot_touch_circuit_or_client_from_copied_foreign_loop_context(subclass):
    async def scenario():
        calls = []
        async def handler(request):
            calls.append(1)
            return httpx.Response(200, json=payload(), request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            class NativeSubclass(AsyncOpenAICompatibleProvider):
                pass
            provider_type = NativeSubclass if subclass else AsyncOpenAICompatibleProvider
            raw = provider_type("https://fixture.invalid", "m", client=client)
            raw._failures = raw.circuit_failure_threshold
            raw._open_until = 0
            class Opaque:
                def next_turn(self, messages, tools):
                    return asyncio.run(raw.anext_turn(messages, tools))
            sink, fault = Sink(), ProviderReceiptFault()
            with pytest.raises(ProviderReceiptError):
                await next_model_turn(recorded(Opaque(), sink, fault), [], [])
            assert fault.broken and raw._half_open_probe is None and calls == []
            assert len(sink.events) == 1 and receipts(sink, "provider.request_started") == []
    asyncio.run(scenario())


def test_opaque_nested_original_async_methods_have_only_partial_observed_coverage_and_reset_scope():
    async def scenario():
        calls = []
        async def handler(request):
            calls.append(1)
            return httpx.Response(200, json=payload(1, 0), request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = AsyncOpenAICompatibleProvider("https://fixture.invalid", "m", client=client)
            class Opaque:
                async def anext_turn(self, messages, tools):
                    await raw.anext_turn(messages, tools)
                    return await raw.anext_turn(messages, tools)
            sink = Sink()
            await next_model_turn(recorded(Opaque(), sink), [], [])
            call = receipts(sink, "provider.call_finished")[0]
            assert call["network_attempts"] is None and call["transport"]["coverage"] == "nested_original"
            assert call["transport"]["entered"] == 2 and call["transport"]["total_tokens"] == 2
            assert len(sink.events) == 6 and len(calls) == 2
            old = list(sink.events)
            turn = await raw.anext_turn([], [])
            assert sink.events == old and turn.provider_call_id is None and len(calls) == 3
    asyncio.run(scenario())
