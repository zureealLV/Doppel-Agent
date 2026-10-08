"""Context-local original transport hooks and bounded numeric response projection.

No provider/runtime imports, mutable cached-provider state, raw-body retention,
IO, tariff inference or claim of wire requests, billing or physical drain.
"""

from __future__ import annotations

import json
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

from .provider_usage import parse_usage


BODY_LIMIT = 4 * 1024 * 1024
TRANSPORT_BOUNDARY = "admitted_transport_method_not_wire_request"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous_response")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite_response")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite_response")
    return result


def empty_usage_details() -> dict[str, Any]:
    return {"version": 1, "cached_input_tokens": None, "uncached_input_tokens": None,
            "reasoning_output_tokens": None, "cache_state": "unknown", "reasoning_state": "unknown"}


def _detail_counter(values, base):
    if not values:
        return None, False
    if any(type(value) is not int or not 0 <= value <= base for value in values) or len(set(values)) != 1:
        return None, True
    return values[0], False


def _usage_details(raw: dict[str, Any], pair: tuple[int, int]) -> dict[str, Any]:
    details = empty_usage_details()
    hits = [raw["prompt_cache_hit_tokens"]] if "prompt_cache_hit_tokens" in raw else []
    misses = [raw["prompt_cache_miss_tokens"]] if "prompt_cache_miss_tokens" in raw else []
    reasoning = []
    bad_cache = bad_reasoning = False
    for name in ("input_tokens_details", "prompt_tokens_details"):
        if name in raw:
            value = raw[name]
            if type(value) is not dict:
                bad_cache = True
            elif "cached_tokens" in value:
                hits.append(value["cached_tokens"])
    for name in ("output_tokens_details", "completion_tokens_details"):
        if name in raw:
            value = raw[name]
            if type(value) is not dict:
                bad_reasoning = True
            elif "reasoning_tokens" in value:
                reasoning.append(value["reasoning_tokens"])
    hit, invalid_hit = _detail_counter(hits, pair[0])
    miss, invalid_miss = _detail_counter(misses, pair[0])
    reason, invalid_reason = _detail_counter(reasoning, pair[1])
    if bad_cache or invalid_hit or invalid_miss or hit is not None and miss is not None and hit + miss != pair[0]:
        details["cache_state"] = "invalid"
    else:
        details.update(cached_input_tokens=hit, uncached_input_tokens=miss,
                       cache_state="known" if hit is not None and miss is not None else
                       "partial" if hit is not None or miss is not None else "unknown")
    if bad_reasoning or invalid_reason:
        details["reasoning_state"] = "invalid"
    else:
        details.update(reasoning_output_tokens=reason, reasoning_state="known" if reason is not None else "unknown")
    return details


def canonical_usage_details(value: Any, pair: tuple[int, int] | None) -> dict[str, Any]:
    """Reallowlist a numeric snapshot; no private extras or malformed enums out.

    This validates observed dimensions only, NOT tariff eligibility or billing.
    Invalid details don't erase the independently valid base input/output pair.
    """
    result = empty_usage_details()
    if pair is None:
        return result
    if type(value) is not dict or type(value.get("version")) is not int or value["version"] != 1:
        return {**result, "cache_state": "invalid", "reasoning_state": "invalid"}
    cache_state, reason_state = value.get("cache_state"), value.get("reasoning_state")
    hit, miss, reason = (value.get(key) for key in ("cached_input_tokens", "uncached_input_tokens", "reasoning_output_tokens"))
    valid_cache = type(cache_state) is str and cache_state in {"known", "partial", "unknown", "invalid"}
    if valid_cache:
        if cache_state in {"unknown", "invalid"}:
            valid_cache = hit is None and miss is None
        else:
            _, invalid_hit = _detail_counter([] if hit is None else [hit], pair[0])
            _, invalid_miss = _detail_counter([] if miss is None else [miss], pair[0])
            if invalid_hit or invalid_miss:
                valid_cache = False
            elif cache_state == "known":
                valid_cache = hit is not None and miss is not None and hit + miss == pair[0]
            else:
                valid_cache = (hit is None) != (miss is None)
    if valid_cache:
        result.update(cache_state=cache_state, cached_input_tokens=hit, uncached_input_tokens=miss)
    else:
        result["cache_state"] = "invalid"
    valid_reason = type(reason_state) is str and reason_state in {"known", "unknown", "invalid"}
    if valid_reason:
        if reason_state == "known":
            _, invalid_reason = _detail_counter([reason], pair[1])
            valid_reason = not invalid_reason
        else:
            valid_reason = reason is None
    if valid_reason:
        result.update(reasoning_state=reason_state, reasoning_output_tokens=reason)
    else:
        result["reasoning_state"] = "invalid"
    return result


def response_details_projection(raw: Any) -> tuple[str, dict[str, int] | None, dict[str, Any]]:
    """Independently extract BOTH actual counters before logical turn parsing.

    Duplicate/nonfinite/malformed/oversized bodies can't be upgraded by a later
    permissive parser. A valid pair survives invalid messages/tool arguments.
    Not a hard socket/RAM bound: an existing client can buffer before this check.
    Explicit cache partitions/reasoning details are numeric observations only;
    missing values aren't derived, charged additively or granted tariff authority.
    """
    if type(raw) is not bytes:
        return "unavailable", None, empty_usage_details()
    if len(raw) > BODY_LIMIT:
        return "oversized_body", None, empty_usage_details()
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
                             parse_float=_finite_float)
        raw_usage = payload.get("usage") if type(payload) is dict else None
        pair = parse_usage(raw_usage)
    except (ValueError, TypeError, RecursionError, OverflowError):
        return "malformed_body", None, empty_usage_details()
    usage = None if pair is None else {"input_tokens": pair[0], "output_tokens": pair[1],
                                      "total_tokens": sum(pair)}
    details = empty_usage_details() if pair is None else _usage_details(raw_usage, pair)
    return "bounded_body", usage, details


def response_projection(raw: Any) -> tuple[str, dict[str, int] | None]:
    """Original pair-only projection API retained; no body or unknown-as-zero."""
    state, usage, _details = response_details_projection(raw)
    return state, usage


@dataclass
class RequestAttempt:
    """Original call's numeric carrier; no request/response/error text stored."""

    attempt_id: str
    index: int
    transport: str
    entered: bool = False
    outcome: str = "failed"
    failure: str | None = "transport_failure"
    http_status: int | None = None
    response_state: str = "not_observed"
    usage: dict[str, int] | None = None
    usage_details: dict[str, Any] = field(default_factory=empty_usage_details)

    def observe(self, raw: bytes, status: Any = None) -> None:
        self.http_status = status if type(status) is int and 100 <= status <= 599 else None
        self.response_state, self.usage, self.usage_details = response_details_projection(raw)


class TransportObserver(Protocol):
    def native_entered(self, provider: Any) -> None: ...
    def begin_sync(self, transport: str) -> RequestAttempt: ...
    def finish_sync(self, attempt: RequestAttempt) -> None: ...
    async def begin_async(self, transport: str) -> RequestAttempt: ...
    async def finish_async(self, attempt: RequestAttempt) -> None: ...


_observer: ContextVar[TransportObserver | None] = ContextVar("doppel_provider_transport_observer", default=None)


@contextmanager
def transport_scope(observer: TransportObserver):
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


def native_entered(provider: Any) -> None:
    observer = _observer.get()
    if observer is not None:
        observer.native_entered(provider)


def begin_sync(transport: str) -> RequestAttempt | None:
    observer = _observer.get()
    return None if observer is None else observer.begin_sync(transport)


def finish_sync(attempt: RequestAttempt | None) -> None:
    observer = _observer.get()
    if observer is not None and attempt is not None:
        observer.finish_sync(attempt)


async def begin_async(transport: str) -> RequestAttempt | None:
    observer = _observer.get()
    return None if observer is None else await observer.begin_async(transport)


async def finish_async(attempt: RequestAttempt | None) -> None:
    observer = _observer.get()
    if observer is not None and attempt is not None:
        await observer.finish_async(attempt)
