"""B2b3a definitions FIRST, ALL UNRUN; offline stubs, not wire/billing proof."""

import asyncio
import io
import json
from threading import Event
from urllib.error import HTTPError, URLError

import pytest

from doppel_agent.provider import ModelTurn, OpenAICompatibleProvider, ProviderRequestError, next_model_turn
from doppel_agent.provider_observation import BODY_LIMIT, empty_usage_details, response_projection
from doppel_agent.provider_usage import MAX_SAFE
from doppel_agent.runtime.provider_recording import ProviderReceiptError, ProviderReceiptFault, record_provider
from doppel_agent.runtime.service import ChildRuntimeSink


class Sink:
    def __init__(self):
        self.events = []

    async def emit(self, kind, **payload):
        self.events.append((kind, payload))


class Response(io.BytesIO):
    status = 200


def body(usage=None, *, broken_tool=False):
    message = {"content": "PRIVATE_RESPONSE_REASONING_PATH_KEY"}
    if broken_tool:
        message["tool_calls"] = [{"id": "private", "function": {"name": "private", "arguments": "[]"}}]
    return json.dumps({"choices": [{"message": message}], "usage": usage}).encode()


def normalized(inputs=7, outputs=3):
    return {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs}


def receipts(sink, kind):
    return [payload for event_kind, payload in sink.events if event_kind == kind]


def test_original_sync_receipts_link_bounded_response_without_mutating_cached_provider(monkeypatch):
    async def scenario():
        sink, fault = Sink(), ProviderReceiptFault()
        raw = OpenAICompatibleProvider("https://fixture.invalid/v1", "private-model", "private-key")
        before = dict(vars(raw))
        observed = []

        def urlopen(request, *, timeout):
            observed.append((request, timeout))
            assert [kind for kind, _ in sink.events] == ["provider.call_started", "provider.request_started"]
            return Response(body({"prompt_tokens": 7, "input_tokens": 7, "completion_tokens": 3,
                                  "private": "PRIVATE_RESPONSE_REASONING_PATH_KEY"}))

        monkeypatch.setattr("doppel_agent.provider.urlopen", urlopen)
        turn = await next_model_turn(record_provider(raw, sink, fault=fault, engine="graph"), [], [])
        assert turn.content == "PRIVATE_RESPONSE_REASONING_PATH_KEY" and vars(raw) == before
        assert len(observed) == 1 and fault.broken is False
        started = receipts(sink, "provider.request_started")[0]
        finished = receipts(sink, "provider.request_finished")[0]
        assert started == {"version": 2, "call_id": turn.provider_call_id,
            "attempt_id": finished["attempt_id"], "attempt_index": 1, "engine": "graph",
            "actor": "runtime_model", "transport": "urllib_urlopen",
            "boundary": "admitted_transport_method_not_wire_request"}
        assert len(finished["attempt_id"]) == 32
        assert finished == {**started, "outcome": "returned", "method_entered": True,
            "http_status": 200, "response_state": "bounded_body", "usage": normalized(),
            "usage_state": "known", "usage_details": empty_usage_details(), "failure": None, "price_receipt": None,
            "billing_complete": False, "account_cap_guaranteed": False}
        call = receipts(sink, "provider.call_finished")[0]
        assert call["version"] == 2 and call["network_attempts"] == 1
        assert call["network_attempts_basis"] == "admitted_transport_method_not_wire_request"
        assert call["usage"] == normalized() and call["usage_basis"] == "original_bounded_response"
        assert call["transport"]["coverage"] == "direct_original"
        assert call["transport"]["intents"] == call["transport"]["entered"] == call["transport"]["sealed"] == 1
        assert call["transport"]["known_usage"] == 1 and call["transport"]["unknown_usage"] == 0
        assert call["transport"]["unsettled"] == 0
        assert "PRIVATE_" not in json.dumps(sink.events) and "private-key" not in json.dumps(sink.events)
    asyncio.run(scenario())


@pytest.mark.parametrize("raw,state", [
    (b'{"usage":{"input_tokens":1,"input_tokens":2,"output_tokens":3}}', "malformed_body"),
    (b'{"usage":{"input_tokens":1,"output_tokens":2},"private":NaN}', "malformed_body"),
    (b'{"usage":{"input_tokens":1,"output_tokens":2},"private":1e999}', "malformed_body"),
    (b"not-json-PRIVATE", "malformed_body"),
    (b"x" * (BODY_LIMIT + 1), "oversized_body"),
    (b'{"usage":{"input_tokens":true,"output_tokens":2}}', "bounded_body"),
    (b'{"usage":{"total_tokens":0}}', "bounded_body"),
    (b'{"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":4}}', "bounded_body"),
], ids=["duplicate-usage", "nan", "infinity", "not-json", "oversized", "boolean", "total-only", "wrong-total"])
def test_independent_raw_usage_refuses_duplicates_nonfinite_oversize_and_invalid_pairs(raw, state):
    assert response_projection(raw) == (state, None)


def test_valid_response_pair_survives_invalid_tool_arguments_without_claiming_logical_success(monkeypatch):
    async def scenario():
        monkeypatch.setattr("doppel_agent.provider.urlopen", lambda *a, **kw: Response(body(
            {"prompt_tokens": 7, "completion_tokens": 3}, broken_tool=True)))
        sink = Sink()
        with pytest.raises(RuntimeError, match="invalid provider response"):
            await next_model_turn(record_provider(OpenAICompatibleProvider("https://fixture.invalid", "m"),
                sink, fault=ProviderReceiptFault(), engine="legacy"), [], [])
        attempt = receipts(sink, "provider.request_finished")[0]
        call = receipts(sink, "provider.call_finished")[0]
        assert attempt["outcome"] == "failed" and attempt["failure"] == "invalid_response"
        assert attempt["usage"] == normalized() and call["usage"] is None
        assert call["transport"]["input_tokens"] == 7 and call["transport"]["output_tokens"] == 3
        assert call["network_attempts"] == 1 and call["outcome"] == "failed"
    asyncio.run(scenario())


def test_ambiguous_raw_usage_cannot_be_upgraded_by_permissive_model_turn_parser(monkeypatch):
    async def scenario():
        raw = b'{"choices":[{"message":{"content":"ok"}}],"usage":{"input_tokens":1,"input_tokens":2,"output_tokens":3}}'
        monkeypatch.setattr("doppel_agent.provider.urlopen", lambda *a, **kw: Response(raw))
        sink = Sink()
        turn = await next_model_turn(record_provider(OpenAICompatibleProvider("https://fixture.invalid", "m"),
            sink, fault=ProviderReceiptFault(), engine="graph"), [], [])
        assert turn.usage["input_tokens"] == 2  # Compatibility parser unchanged, NOT receipt authority.
        assert receipts(sink, "provider.request_finished")[0]["usage"] is None
        assert receipts(sink, "provider.call_finished")[0]["usage"] is None
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 429, 503])
def test_original_http_error_closes_original_bounded_response_and_preserves_numeric_usage(monkeypatch, status):
    async def scenario():
        fp = Response(body({"input_tokens": 0, "output_tokens": 0}))
        original = HTTPError("https://private.invalid", status, "PRIVATE_ERROR", {}, fp)
        def urlopen(*args, **kwargs):
            raise original
        monkeypatch.setattr("doppel_agent.provider.urlopen", urlopen)
        sink = Sink()
        with pytest.raises(ProviderRequestError) as error:
            await next_model_turn(record_provider(OpenAICompatibleProvider("https://fixture.invalid", "m"),
                sink, fault=ProviderReceiptFault(), engine="graph"), [], [])
        assert error.value.__cause__ is original and error.value.attempts == 1 and fp.closed
        attempt = receipts(sink, "provider.request_finished")[0]
        assert attempt["http_status"] == status and attempt["usage"] == normalized(0, 0)
        assert attempt["failure"] == ("rate_limited" if status == 429 else "http_status")
        call = receipts(sink, "provider.call_finished")[0]
        assert call["usage"] is None and call["transport"]["known_usage"] == 1
        assert call["billing_complete"] is False and "PRIVATE_" not in json.dumps(sink.events)
    asyncio.run(scenario())


@pytest.mark.parametrize("error,failure", [(URLError("PRIVATE_NETWORK"), "connection"),
    (URLError(TimeoutError("PRIVATE_TIMEOUT")), "timeout"), (TimeoutError("PRIVATE_TIMEOUT"), "timeout")])
def test_original_sync_transport_failure_seals_one_unknown_attempt(monkeypatch, error, failure):
    async def scenario():
        def urlopen(*args, **kwargs):
            raise error
        monkeypatch.setattr("doppel_agent.provider.urlopen", urlopen)
        sink = Sink()
        with pytest.raises(ProviderRequestError):
            await next_model_turn(record_provider(OpenAICompatibleProvider("https://fixture.invalid", "m"),
                sink, fault=ProviderReceiptFault(), engine="graph"), [], [])
        attempt = receipts(sink, "provider.request_finished")[0]
        assert attempt["failure"] == failure and attempt["usage"] is None
        assert attempt["response_state"] == "not_observed" and attempt["method_entered"] is True
        call = receipts(sink, "provider.call_finished")[0]
        assert call["network_attempts"] == 1 and call["transport"]["input_tokens"] is None
        assert call["transport"]["output_tokens"] is None and call["transport"]["total_tokens"] is None
    asyncio.run(scenario())


def test_original_preflight_failure_has_zero_admitted_entries_not_free_billing(monkeypatch):
    async def scenario():
        class BadMessage:
            def to_api(self):
                raise ValueError("PRIVATE_PREFLIGHT")
        def forbidden(*args, **kwargs):
            raise AssertionError("preflight must not enter transport")
        monkeypatch.setattr("doppel_agent.provider.urlopen", forbidden)
        sink = Sink()
        with pytest.raises(ValueError, match="PRIVATE_PREFLIGHT"):
            await next_model_turn(record_provider(OpenAICompatibleProvider("https://fixture.invalid", "m"),
                sink, fault=ProviderReceiptFault(), engine="graph"), [BadMessage()], [])
        assert len(sink.events) == 2
        call = receipts(sink, "provider.call_finished")[0]
        assert call["network_attempts"] == 0 and call["usage"] is None and call["billing_complete"] is False
        assert call["transport"]["coverage"] == "direct_original" and "PRIVATE_" not in json.dumps(sink.events)
    asyncio.run(scenario())


@pytest.mark.parametrize("where", ["provider.request_started", "provider.request_finished"])
def test_original_request_append_failure_fences_next_entry_without_method_or_receipt_retry(monkeypatch, where):
    async def scenario():
        calls = []
        def urlopen(*args, **kwargs):
            calls.append(1)
            return Response(body({"input_tokens": 1, "output_tokens": 0}))
        monkeypatch.setattr("doppel_agent.provider.urlopen", urlopen)
        class Broken(Sink):
            async def emit(self, kind, **payload):
                if kind == where:
                    raise OSError("PRIVATE_APPEND")
                await super().emit(kind, **payload)
        sink, fault = Broken(), ProviderReceiptFault()
        provider = record_provider(OpenAICompatibleProvider("https://fixture.invalid", "m"), sink,
                                   fault=fault, engine="graph")
        with pytest.raises(ProviderReceiptError):
            await next_model_turn(provider, [], [])
        assert fault.broken and len(calls) == (0 if where.endswith("started") else 1)
        with pytest.raises(ProviderReceiptError):
            await next_model_turn(provider, [], [])
        assert len(calls) == (0 if where.endswith("started") else 1)
        assert receipts(sink, "provider.call_finished") == []
    asyncio.run(scenario())


def test_original_sync_late_response_and_request_finish_notification_join_repeated_cancel(monkeypatch):
    async def scenario():
        entered, release = Event(), Event()
        notified, finish_release = asyncio.Event(), asyncio.Event()
        class Delayed(Sink):
            async def emit(self, kind, **payload):
                if kind == "provider.request_finished":
                    notified.set()
                    await finish_release.wait()
                await super().emit(kind, **payload)
        def urlopen(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return Response(body({"input_tokens": 4, "output_tokens": 2}))
        monkeypatch.setattr("doppel_agent.provider.urlopen", urlopen)
        sink = Delayed()
        task = asyncio.create_task(next_model_turn(record_provider(OpenAICompatibleProvider("https://fixture.invalid", "m"),
            sink, fault=ProviderReceiptFault(), engine="graph"), [], []))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            task.cancel()
            release.set()
            await asyncio.wait_for(notified.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            finish_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert receipts(sink, "provider.request_finished")[0]["outcome"] == "returned"
        assert receipts(sink, "provider.call_finished")[0]["usage"] == normalized(4, 2)
        assert len(sink.events) == 4
    asyncio.run(scenario())


def test_opaque_nested_original_calls_are_partial_and_reset_after_return(monkeypatch):
    async def scenario():
        calls = []
        def urlopen(*args, **kwargs):
            calls.append(1)
            return Response(body({"input_tokens": 1, "output_tokens": 0}))
        monkeypatch.setattr("doppel_agent.provider.urlopen", urlopen)
        raw = OpenAICompatibleProvider("https://fixture.invalid", "m")
        class Opaque:
            def next_turn(self, messages, tools):
                raw.next_turn(messages, tools)
                return raw.next_turn(messages, tools)
        sink = Sink()
        await next_model_turn(record_provider(Opaque(), sink, fault=ProviderReceiptFault(), engine="graph"), [], [])
        finish = receipts(sink, "provider.call_finished")[0]
        assert finish["network_attempts"] is None and finish["transport"]["coverage"] == "nested_original"
        assert finish["transport"]["entered"] == 2 and finish["transport"]["input_tokens"] == 2
        assert [item["attempt_index"] for item in receipts(sink, "provider.request_finished")] == [1, 2]
        old_events = list(sink.events)
        await asyncio.to_thread(raw.next_turn, [], [])  # Unrecorded direct call; context restored, no accidental old-run link.
        assert sink.events == old_events and calls == [1, 1, 1]
    asyncio.run(scenario())


def test_context_resets_after_failed_original_call_and_unrecorded_provider_has_no_old_scope(monkeypatch):
    async def scenario():
        replies = [body({"input_tokens": 1, "output_tokens": 0}, broken_tool=True), body()]
        monkeypatch.setattr("doppel_agent.provider.urlopen", lambda *a, **kw: Response(replies.pop(0)))
        raw, sink = OpenAICompatibleProvider("https://fixture.invalid", "m"), Sink()
        with pytest.raises(RuntimeError, match="invalid provider response"):
            await next_model_turn(record_provider(raw, sink, fault=ProviderReceiptFault(), engine="graph"), [], [])
        frozen = list(sink.events)
        direct = await asyncio.to_thread(raw.next_turn, [], [])
        assert direct.provider_call_id is None and sink.events == frozen
    asyncio.run(scenario())


@pytest.mark.parametrize("cleanup_error", [OSError("PRIVATE_CLOSE"), TimeoutError("PRIVATE_CLOSE"),
                                          asyncio.CancelledError("PRIVATE_CLOSE")])
def test_original_response_close_failure_seals_failed_attempt_but_quarantines_owner(monkeypatch, cleanup_error):
    async def scenario():
        calls = []
        class BadClose(Response):
            def __exit__(self, *args):
                # Deliberately don't close original handle: no false drain.
                raise cleanup_error
        response = BadClose(body({"input_tokens": 7, "output_tokens": 3}))
        def urlopen(*args, **kwargs):
            calls.append(1)
            return response
        monkeypatch.setattr("doppel_agent.provider.urlopen", urlopen)
        sink, fault = Sink(), ProviderReceiptFault()
        provider = record_provider(OpenAICompatibleProvider("https://fixture.invalid", "m"), sink,
                                   fault=fault, engine="graph")
        try:
            with pytest.raises(ProviderReceiptError):
                await next_model_turn(provider, [], [])
            attempt = receipts(sink, "provider.request_finished")[0]
            assert attempt["failure"] == "response_cleanup"
            assert attempt["outcome"] == ("cancelled" if isinstance(cleanup_error, asyncio.CancelledError) else "failed")
            assert attempt["usage"] == normalized() and not response.closed and fault.broken
            assert receipts(sink, "provider.call_finished") == []
            with pytest.raises(ProviderReceiptError):
                await next_model_turn(provider, [], [])
            assert calls == [1]
        finally:
            response.close()  # Future definition fixture owns this exact handle, not forced user cleanup.
    asyncio.run(scenario())


def test_shared_original_cached_sync_provider_keeps_two_original_child_scopes(monkeypatch):
    async def scenario():
        from threading import Barrier
        barrier = Barrier(2)
        def urlopen(*args, **kwargs):
            barrier.wait(5)
            return Response(body({"input_tokens": 1, "output_tokens": 0}))
        monkeypatch.setattr("doppel_agent.provider.urlopen", urlopen)
        raw, parent, fault = OpenAICompatibleProvider("https://fixture.invalid", "m"), Sink(), ProviderReceiptFault()
        a, b, c = "a" * 32, "b" * 32, "c" * 32
        first = record_provider(raw, ChildRuntimeSink(parent, a, b, 2), fault=fault, engine="graph")
        second = record_provider(raw, ChildRuntimeSink(parent, a, c, 3), fault=fault, engine="graph")
        one, two = await asyncio.gather(next_model_turn(first, [], []), next_model_turn(second, [], []))
        assert one.provider_call_id != two.provider_call_id and len(parent.events) == 8
        for kind, outer in parent.events:
            assert kind == "subagent.runtime" and outer["parent_run_id"] == a
            scope = (b, 2) if outer["runtime_payload"]["call_id"] == one.provider_call_id else (c, 3)
            assert (outer["subagent_id"], outer["generation"]) == scope
        assert not hasattr(raw, "sink") and not hasattr(raw, "run_id") and not fault.broken
    asyncio.run(scenario())


def test_opaque_unjoined_nested_original_worker_is_unsettled_not_success_or_drain(monkeypatch):
    async def scenario():
        entered, release = Event(), Event()
        workers = []
        def urlopen(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return Response(body({"input_tokens": 1, "output_tokens": 0}))
        monkeypatch.setattr("doppel_agent.provider.urlopen", urlopen)
        raw = OpenAICompatibleProvider("https://fixture.invalid", "m")
        class Opaque:
            async def anext_turn(self, messages, tools):
                workers.append(asyncio.create_task(asyncio.to_thread(raw.next_turn, messages, tools)))
                assert await asyncio.to_thread(entered.wait, 5)
                return ModelTurn("detached result")
        sink, fault = Sink(), ProviderReceiptFault()
        try:
            with pytest.raises(ProviderReceiptError):
                await next_model_turn(record_provider(Opaque(), sink, fault=fault, engine="deep"), [], [])
            assert fault.broken and not workers[0].done()
            assert receipts(sink, "provider.call_finished") == []
        finally:
            release.set()
            for worker in workers:
                with pytest.raises(ProviderReceiptError):
                    await worker
        assert len(receipts(sink, "provider.request_started")) == 1
        assert receipts(sink, "provider.request_finished") == []  # Unresolved evidence retained for C, not erased.
    asyncio.run(scenario())


def test_observed_subtotal_overflow_stays_unknown_total_without_rounding_or_clamping(monkeypatch):
    async def scenario():
        monkeypatch.setattr("doppel_agent.provider.urlopen", lambda *a, **kw: Response(body(
            {"input_tokens": MAX_SAFE, "output_tokens": 0})))
        raw = OpenAICompatibleProvider("https://fixture.invalid", "m")
        class Opaque:
            def next_turn(self, messages, tools):
                raw.next_turn(messages, tools)
                return raw.next_turn(messages, tools)
        sink = Sink()
        await next_model_turn(record_provider(Opaque(), sink, fault=ProviderReceiptFault(), engine="graph"), [], [])
        summary = receipts(sink, "provider.call_finished")[0]["transport"]
        assert summary["known_usage"] == 2 and summary["input_tokens"] is None
        assert summary["output_tokens"] == 0 and summary["total_tokens"] is None and summary["overflow"]
        assert all(item["usage"]["input_tokens"] == MAX_SAFE for item in receipts(sink, "provider.request_finished"))
    asyncio.run(scenario())


@pytest.mark.parametrize('unreadable', [False, True])
def test_original_http_error_without_body_preserves_classification_and_unknown_usage(monkeypatch, unreadable):
    async def scenario():
        original = HTTPError("https://private.invalid", 429, "PRIVATE", {}, None)
        if unreadable:
            def failed_read(*args, **kwargs):
                raise OSError('PRIVATE_BODY_READ')
            monkeypatch.setattr(original, 'read', failed_read)
        def urlopen(*args, **kwargs):
            raise original
        monkeypatch.setattr("doppel_agent.provider.urlopen", urlopen)
        sink, fault = Sink(), ProviderReceiptFault()
        with pytest.raises(ProviderRequestError) as error:
            await next_model_turn(record_provider(OpenAICompatibleProvider("https://fixture.invalid", "m"),
                sink, fault=fault, engine="graph"), [], [])
        assert error.value.kind == "rate_limited" and not fault.broken
        finish = receipts(sink, "provider.request_finished")[0]
        assert finish["usage"] is None and finish["http_status"] == 429
        assert finish["response_state"] == ("unavailable" if unreadable else "malformed_body")
        assert finish["failure"] == "rate_limited"
    asyncio.run(scenario())


@pytest.mark.parametrize("close_failed", [False, True])
def test_failed_original_body_read_does_not_hide_close_failure_or_invent_usage(monkeypatch, close_failed):
    async def scenario():
        class BadRead(Response):
            def read(self, *args):
                raise URLError("PRIVATE_READ")
            def __exit__(self, *args):
                if close_failed:
                    raise OSError("PRIVATE_CLOSE")
                return super().__exit__(*args)
        response = BadRead(b"")
        monkeypatch.setattr("doppel_agent.provider.urlopen", lambda *a, **kw: response)
        sink, fault = Sink(), ProviderReceiptFault()
        try:
            expected = ProviderReceiptError if close_failed else ProviderRequestError
            with pytest.raises(expected):
                await next_model_turn(record_provider(OpenAICompatibleProvider("https://fixture.invalid", "m"),
                    sink, fault=fault, engine="graph"), [], [])
            attempt = receipts(sink, "provider.request_finished")[0]
            assert attempt["usage"] is None and attempt["response_state"] == "unavailable"
            assert attempt["http_status"] == 200
            assert attempt["failure"] == ("response_cleanup" if close_failed else "connection")
            assert fault.broken is close_failed and response.closed is not close_failed
        finally:
            response.close()
    asyncio.run(scenario())
