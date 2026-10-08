"""Original provider-method receipts, bound to the original runtime event sink.

Logical calls plus original sync urlopen/async post method entries, NOT wire/
hidden client retry/provider billing proof.
No cached-provider mutation, separate executor/database, live price lookup or text scrubbing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import CancelledError as FutureCancelledError
from dataclasses import replace
from threading import Event, Lock
from typing import Any
from uuid import uuid4

from ..events import EventSink
from ..billing_tariff import validate_price_receipt
from ..owned_async import await_durable
from ..provider import (
    AsyncOpenAICompatibleProvider, ModelTurn, OpenAICompatibleProvider, ProviderCircuitOpen, ProviderRequestError,
)
from ..provider_observation import RequestAttempt, TRANSPORT_BOUNDARY, canonical_usage_details, transport_scope
from ..provider_usage import MAX_SAFE, parse_usage
from .provider_adapter import ProviderAdapter


class ProviderReceiptError(RuntimeError):
    """Sticky original-owner recording failure; never a model fallback signal."""

    def __init__(self):
        super().__init__("provider_receipt_unavailable")


class ProviderReceiptFault:
    """One original owner/runtime lifetime's monotonic in-memory safety latch.

    No reset/retry authority. This isn't durable startup reconciliation, a global
    account cap, or proof that concurrent already-started provider work drained.
    """

    def __init__(self):
        self._failed = Event()
        self._cleanup_uncertain = Event()
        self._cleanup_lock = Lock()
        self._cleanup_sources: dict[int, Any] = {}

    @property
    def broken(self) -> bool:
        return self._failed.is_set()

    def mark_failed(self) -> None:
        self._failed.set()

    @property
    def cleanup_uncertain(self) -> bool:
        return self._cleanup_uncertain.is_set()

    def retain_cleanup(self, source: Any) -> None:
        # Same original owner's references, including a constructor that never
        # returned its ledger. No IO, new connection, worker, retry or reset.
        self.mark_failed()
        self._cleanup_uncertain.set()
        with self._cleanup_lock:
            self._cleanup_sources[id(source)] = source

    def check_cleanup(self) -> None:
        if self.cleanup_uncertain:
            raise RuntimeError('owner_cleanup_unresolved')

    def check(self) -> None:
        if self.broken:
            raise ProviderReceiptError()


class _ProviderReceiptSink:
    """Retain original runtime callbacks too; callback failure isn't fallback.

    Writes still go to the exact original sink/child outer envelope/database.
    This wrapper is not a second log, executor, redactor or physical-drain proof.
    """

    def __init__(self, original: EventSink, fault: ProviderReceiptFault):
        self.original, self.fault = original, fault

    async def emit(self, kind: str, **payload: Any) -> None:
        async def original_emit():
            try:
                await self.original.emit(kind, **payload)
            except BaseException as exc:
                self.fault.mark_failed()
                if not isinstance(exc, Exception):
                    raise
                raise ProviderReceiptError() from None
        await await_durable(original_emit())


def receipt_sink(sink: EventSink, fault: ProviderReceiptFault) -> EventSink:
    if isinstance(sink, _ProviderReceiptSink):
        if sink.fault is fault:
            return sink
        fault.mark_failed()
        raise ProviderReceiptError()
    return _ProviderReceiptSink(sink, fault)


class _InvalidModelTurn(RuntimeError):
    def __init__(self):
        super().__init__("provider_model_turn_invalid")


def _failure(exc: BaseException) -> str:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, _InvalidModelTurn):
        return "invalid_model_turn"
    if isinstance(exc, ProviderCircuitOpen):
        return "circuit_open"
    if isinstance(exc, ProviderRequestError) and type(exc.kind) is str and exc.kind in {
        "timeout", "connection", "rate_limited", "http_status",
    }:
        return exc.kind
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionError):
        return "connection"
    return "provider_failure"


class _CallTransport:
    """Original context-local sync/async attempt receipts, not hidden client IO.

    No hidden executor, second store or cached-provider run mutation. Nested
    original hooks in opaque wrappers are only a partial observed subtotal.
    Original admitted urlopen method entries aren't physical wire/billing proof.
    """

    def __init__(self, recorder, start):
        self.recorder, self.start = recorder, start
        self._lock = Lock()
        self._closed = False
        self._native_entries = 0
        self._direct = False
        self._active: dict[str, RequestAttempt] = {}
        self._finishing: set[str] = set()
        self._intents = self._entered = self._sealed = 0
        self._returned = self._failed = self._cancelled = 0
        self._known = self._unknown = self._inputs = self._outputs = 0
        self._last_usage = None

    def _check_open(self):
        if self._closed:
            self.recorder.fault.mark_failed()
            raise ProviderReceiptError()

    def native_entered(self, provider):
        self.recorder.fault.check()
        if isinstance(provider, AsyncOpenAICompatibleProvider):
            try:
                owner_loop = asyncio.get_running_loop() is self.recorder.loop
            except RuntimeError:
                owner_loop = False
            if not owner_loop:
                # Copied ContextVars don't grant a second loop access to the
                # original cached client's circuit/transport ownership.
                self.recorder.fault.mark_failed()
                raise ProviderReceiptError()
        with self._lock:
            self._check_open()
            self._native_entries += 1
            self._direct = (self._native_entries == 1 and provider is self.recorder.provider
                            and type(provider) in {OpenAICompatibleProvider, AsyncOpenAICompatibleProvider})

    def _identity(self, attempt):
        frozen = self.recorder._price_snapshot()
        return {"version": 2 if frozen is None else 3, "call_id": self.start["call_id"], "attempt_id": attempt.attempt_id,
                "attempt_index": attempt.index, "engine": self.start["engine"], "actor": self.start["actor"],
                "transport": attempt.transport, "boundary": TRANSPORT_BOUNDARY,
                **({"price_receipt": frozen} if frozen is not None else {})}

    def _reserve(self, transport):
        self.recorder.fault.check()
        with self._lock:
            self._check_open()
            if transport not in {"urllib_urlopen", "httpx_post"} or len(self._active) >= 64 or self._intents >= MAX_SAFE:
                self.recorder.fault.mark_failed()
                raise ProviderReceiptError()
            self._intents += 1
            attempt = RequestAttempt(uuid4().hex, self._intents, transport)
            self._active[attempt.attempt_id] = attempt
        return attempt

    def begin_sync(self, transport):
        attempt = self._reserve(transport)
        # Intent is durable BEFORE entering original urlopen. A failed append
        # stays unresolved, never a fabricated cancelled/zero-charge receipt.
        self.recorder._append_sync("provider.request_started", self._identity(attempt))
        self.recorder.fault.check()
        return attempt

    async def begin_async(self, transport):
        if asyncio.get_running_loop() is not self.recorder.loop:
            self.recorder.fault.mark_failed()
            raise ProviderReceiptError()
        attempt = self._reserve(transport)
        try:
            await self.recorder._append("provider.request_started", self._identity(attempt))
        except asyncio.CancelledError:
            # _append joined original intent/notification BEFORE propagating
            # cancellation. No actual post entry; seal exactly this intent.
            # An append fault masked by cancel is latched inside retained task,
            # leaving unresolved evidence, not a fabricated successful seal.
            if not self.recorder.fault.broken:
                attempt.outcome, attempt.failure = "cancelled", "cancelled"
                await self.finish_async(attempt)
            raise
        self.recorder.fault.check()
        return attempt

    def _finish_payload(self, attempt):
        with self._lock:
            self._check_open()
            if self._active.get(attempt.attempt_id) is not attempt or attempt.attempt_id in self._finishing:
                self.recorder.fault.mark_failed()
                raise ProviderReceiptError()
            self._finishing.add(attempt.attempt_id)
        pair = parse_usage(attempt.usage)
        usage = None if pair is None else {"input_tokens": pair[0], "output_tokens": pair[1],
                                          "total_tokens": sum(pair)}
        outcome = attempt.outcome if attempt.outcome in {"returned", "failed", "cancelled"} else "failed"
        failure = attempt.failure if attempt.failure in {None, "rate_limited", "http_status", "timeout", "connection",
            "response_limit", "invalid_response", "cancelled", "transport_failure", "response_cleanup"} else "transport_failure"
        status = attempt.http_status if type(attempt.http_status) is int and 100 <= attempt.http_status <= 599 else None
        state = attempt.response_state if attempt.response_state in {
            "not_observed", "bounded_body", "oversized_body", "malformed_body", "unavailable",
        } else "unavailable"
        return {**self._identity(attempt), "outcome": outcome, "method_entered": attempt.entered is True,
                   "http_status": status, "response_state": state, "usage": usage,
                   "usage_details": canonical_usage_details(attempt.usage_details, pair),
                   "usage_state": "known" if pair is not None else "unknown", "failure": failure,
                   "price_receipt": self.recorder._price_snapshot(),
                   "billing_complete": False, "account_cap_guaranteed": False}

    def _settle(self, attempt, payload):
        pair = parse_usage(payload["usage"])
        with self._lock:
            self._check_open()
            if self._active.get(attempt.attempt_id) is not attempt or attempt.attempt_id not in self._finishing:
                self.recorder.fault.mark_failed()
                raise ProviderReceiptError()
            del self._active[attempt.attempt_id]
            self._finishing.remove(attempt.attempt_id)
            self._sealed += 1
            self._entered += int(payload["method_entered"] is True)
            self._returned += int(payload["outcome"] == "returned")
            self._failed += int(payload["outcome"] == "failed")
            self._cancelled += int(payload["outcome"] == "cancelled")
            self._last_usage = payload["usage"]
            if pair is None:
                self._unknown += 1
            else:
                self._known += 1
                self._inputs += pair[0]
                self._outputs += pair[1]
        if payload["failure"] == "response_cleanup":
            # The original response close failed: usage can be known while
            # physical ownership is unresolved. Seal numeric failed-attempt
            # evidence, then quarantine NEW calls; C must reconcile/drain.
            self.recorder.fault.mark_failed()
            raise ProviderReceiptError()

    def finish_sync(self, attempt):
        payload = self._finish_payload(attempt)
        # Sync original worker cannot return until SAME append+notify settles.
        self.recorder._append_sync("provider.request_finished", payload)
        self._settle(attempt, payload)

    async def finish_async(self, attempt):
        if asyncio.get_running_loop() is not self.recorder.loop:
            self.recorder.fault.mark_failed()
            raise ProviderReceiptError()
        payload = self._finish_payload(attempt)
        # Commit observation INSIDE the retained original sink task, before
        # caller cancellation can hide a successful append from this caller.
        await self.recorder._append("provider.request_finished", payload,
                                    on_settled=lambda: self._settle(attempt, payload))

    def seal(self):
        with self._lock:
            self._check_open()
            self._closed = True
            if self._active:
                # Opaque detached nested work isn't a completed logical call.
                # Fence NEW IO, preserve started/unfinished evidence for C;
                # no claim that this in-memory fence physically drained it.
                self.recorder.fault.mark_failed()
                raise ProviderReceiptError()
            coverage = "direct_original" if self._direct else "nested_original" if self._native_entries else "opaque"
            summary = {"coverage": coverage, "boundary": TRANSPORT_BOUNDARY,
                       "intents": self._intents, "entered": self._entered, "sealed": self._sealed,
                       "unsettled": len(self._active), "returned": self._returned, "failed": self._failed,
                       "cancelled": self._cancelled, "known_usage": self._known, "unknown_usage": self._unknown,
                       "input_tokens": self._inputs if self._known and self._inputs <= MAX_SAFE else None,
                       "output_tokens": self._outputs if self._known and self._outputs <= MAX_SAFE else None,
                       "total_tokens": self._inputs + self._outputs if self._known and self._inputs + self._outputs <= MAX_SAFE else None,
                       "subtotal_state": "unknown" if not self._known else "partial" if self._unknown else "known_for_observed_responses",
                       "overflow": self._inputs + self._outputs > MAX_SAFE}
            return summary, self._last_usage

    def retire(self):
        with self._lock:
            if self._active or not self._closed:
                # A hard BaseException cannot leave started/no-finish evidence
                # and silently enable another provider entry in this owner.
                self.recorder.fault.mark_failed()
            self._closed = True


class RecordedProvider:
    """Per-invocation facade, not a cached provider's mutable current-run state.

    Sync next_turn must execute in the original owned worker, not on the owner
    loop. Its original event append+notification settles before worker returns,
    even when its async caller is repeatedly cancelled and loses the return.
    """

    def __init__(self, provider: Any, sink: EventSink, *, fault: ProviderReceiptFault,
                 engine: str, actor: str = "runtime_model", billing_price_receipt: dict | None = None):
        if engine not in {"legacy", "graph", "deep"} or actor not in {
            "runtime_model", "deep_builtin_investigator", "deep_builtin_verifier",
        } or engine != "deep" and actor != "runtime_model":
            raise ValueError("provider_receipt_scope_invalid")
        self.provider, self.sink, self.fault = provider, sink, fault
        self.engine, self.actor = engine, actor
        self._billing_price_receipt = (None if billing_price_receipt is None
                                       else validate_price_receipt(billing_price_receipt))
        self.loop = asyncio.get_running_loop()

    def _price_snapshot(self):
        # Each event owns a detached contract. No mutable current-profile read,
        # amount calculation or claim the declaration applies to hidden traffic.
        return None if self._billing_price_receipt is None else validate_price_receipt(self._billing_price_receipt)

    def _start(self, path: str) -> dict[str, Any]:
        frozen = self._price_snapshot()
        return {"version": 2 if frozen is None else 3, "call_id": uuid4().hex, "engine": self.engine, "actor": self.actor,
                "path": path, "boundary": "provider_method_not_transport_attempt",
                **({"price_receipt": frozen} if frozen is not None else {})}

    @staticmethod
    def _finished(start: dict[str, Any], outcome: str, entered: bool, *,
                  turn: ModelTurn | None = None, error: BaseException | None = None,
                  observation: _CallTransport) -> dict[str, Any]:
        transport, response_usage = observation.seal()
        try:
            pair = parse_usage(turn.usage) if turn is not None else None
        except Exception:
            # An opaque provider's custom mapping isn't trusted counter framing.
            # Keep its result private and usage unknown, never invent a charge.
            pair = None
        usage = None if pair is None else {"input_tokens": pair[0], "output_tokens": pair[1],
                                           "total_tokens": sum(pair)}
        if transport["coverage"] == "direct_original":
            # Turn parser is a compatibility surface, NOT bounded raw framing.
            # Failed logical turns keep their usage solely in request receipts.
            usage = response_usage if turn is not None else None
            usage_basis = "original_bounded_response" if turn is not None else "none"
        else:
            usage_basis = "returned_model_turn" if turn is not None else "none"
        return {**start, "outcome": outcome, "method_entered": entered, "usage": usage,
                "usage_state": "known" if usage is not None else "unknown", "usage_basis": usage_basis,
                "failure": _failure(error) if error is not None else None,
                "network_attempts": transport["entered"] if transport["coverage"] == "direct_original" else None,
                "network_attempts_basis": TRANSPORT_BOUNDARY if transport["coverage"] == "direct_original" else None,
                "transport": transport, "price_receipt": observation.recorder._price_snapshot(),
                "billing_complete": False, "account_cap_guaranteed": False}

    async def _append(self, kind: str, payload: dict[str, Any], *,
                      on_settled: Callable[[], None] | None = None) -> None:
        async def original_append_and_notify():
            try:
                # Never lend our start-frame contract to mutable sink consumers.
                # Version2 unpriced framing remains EXACTLY its original shape.
                event = dict(payload)
                if event.get("price_receipt") is not None:
                    event["price_receipt"] = validate_price_receipt(event["price_receipt"])
                await self.sink.emit(kind, **event)
                if on_settled is not None:
                    on_settled()
            except BaseException as exc:
                # Set INSIDE the retained sink task, before await_durable may
                # propagate caller cancellation in place of an append failure.
                self.fault.mark_failed()
                if not isinstance(exc, Exception):
                    raise
                raise ProviderReceiptError() from None

        await await_durable(original_append_and_notify())

    def _append_sync(self, kind: str, payload: dict[str, Any]) -> None:
        # The original owned sync worker blocks on this SAME original loop/sink;
        # no fire-and-forget callback, another worker or timeout-based discard.
        try:
            owner_loop = asyncio.get_running_loop() is self.loop
        except RuntimeError:
            owner_loop = False
        if owner_loop or self.loop.is_closed() or not self.loop.is_running():
            self.fault.mark_failed()
            raise ProviderReceiptError()
        original = self._append(kind, payload)
        try:
            future = asyncio.run_coroutine_threadsafe(original, self.loop)
        except Exception:
            # Scheduling failure means this exact coroutine was never owned by
            # the loop. Close it, latch, do not create a retry/another loop.
            original.close()
            self.fault.mark_failed()
            raise ProviderReceiptError() from None
        try:
            future.result()
        except FutureCancelledError:
            self.fault.mark_failed()
            raise ProviderReceiptError() from None

    @staticmethod
    def _link(turn: Any, call_id: str) -> ModelTurn:
        if not isinstance(turn, ModelTurn):
            raise _InvalidModelTurn()
        # Generated identity is observational only; ignore any provider-supplied
        # forged identity. Preserve original content/tool calls/raw usage objects.
        return replace(turn, provider_call_id=call_id)

    def next_turn(self, messages, tools) -> ModelTurn:
        self.fault.check()
        try:
            owner_loop = asyncio.get_running_loop() is self.loop
        except RuntimeError:
            owner_loop = False
        if owner_loop or self.loop.is_closed() or not self.loop.is_running():
            self.fault.mark_failed()
            raise ProviderReceiptError()
        start, entered = self._start("sync_worker"), False
        observation = _CallTransport(self, start)
        with transport_scope(observation):
            try:
                self._append_sync("provider.call_started", start)
                self.fault.check()  # Another original call's failed append fences entry.
                try:
                    method = getattr(self.provider, "next_turn", None)
                    if not callable(method):
                        raise _InvalidModelTurn()
                    entered = True
                    turn = self._link(method(messages, tools), start["call_id"])
                except ProviderReceiptError:
                    self.fault.mark_failed()
                    raise
                except asyncio.CancelledError as exc:
                    self._append_sync("provider.call_finished", self._finished(start, "cancelled", entered,
                        error=exc, observation=observation))
                    raise
                except Exception as exc:
                    self._append_sync("provider.call_finished", self._finished(start, "failed", entered,
                        error=exc, observation=observation))
                    raise
                self._append_sync("provider.call_finished", self._finished(start, "returned", entered,
                    turn=turn, observation=observation))
                return turn
            finally:
                observation.retire()

    async def anext_turn(self, messages, tools) -> ModelTurn:
        self.fault.check()
        if asyncio.get_running_loop() is not self.loop:
            self.fault.mark_failed()
            raise ProviderReceiptError()
        method = getattr(self.provider, "anext_turn", None)
        if method is None:
            # Record completion INSIDE next_turn worker, not after this await:
            # await_durable intentionally discards a result on caller cancel.
            return await await_durable(asyncio.to_thread(self.next_turn, messages, tools))
        start, entered = self._start("native_async"), False
        observation = _CallTransport(self, start)
        with transport_scope(observation):
            try:
                try:
                    await self._append("provider.call_started", start)
                    self.fault.check()
                    if not callable(method):
                        raise _InvalidModelTurn()
                    entered = True
                    turn = self._link(await method(messages, tools), start["call_id"])
                except ProviderReceiptError:
                    self.fault.mark_failed()
                    raise
                except asyncio.CancelledError as exc:
                    if not self.fault.broken:
                        await self._append("provider.call_finished", self._finished(start, "cancelled", entered,
                            error=exc, observation=observation))
                    raise
                except Exception as exc:
                    await self._append("provider.call_finished", self._finished(start, "failed", entered,
                        error=exc, observation=observation))
                    raise
                # Outside the inner try: returned append cancellation cannot
                # synthesize a second cancelled receipt after a known result.
                await self._append("provider.call_finished", self._finished(start, "returned", entered,
                    turn=turn, observation=observation))
                return turn
            finally:
                observation.retire()


def record_provider(provider: Any, sink: EventSink, *, fault: ProviderReceiptFault,
                    engine: str, actor: str = "runtime_model", billing_price_receipt: dict | None = None) -> Any:
    """Preserve ORIGINAL resource admission OUTSIDE original raw call recording.

    Exact original ProviderAdapter is re-created, never mutated. Other adapters
    remain opaque method boundaries; their hidden internal retries/workers aren't
    claimed observed. An existing recorder cannot be rebound to another scope.
    """
    if type(provider) is ProviderAdapter:
        return ProviderAdapter(record_provider(provider.provider, sink, fault=fault, engine=engine, actor=actor,
                                               billing_price_receipt=billing_price_receipt),
                               profile_id=provider.profile_id, limits=provider.limits)
    if isinstance(provider, RecordedProvider):
        if (provider.sink is sink and provider.fault is fault and provider.engine == engine
                and provider.actor == actor and provider.loop is asyncio.get_running_loop()
                and provider._price_snapshot() == (None if billing_price_receipt is None
                                                   else validate_price_receipt(billing_price_receipt))):
            return provider
        fault.mark_failed()
        raise ProviderReceiptError()
    return RecordedProvider(provider, sink, fault=fault, engine=engine, actor=actor,
                            billing_price_receipt=billing_price_receipt)
