"""Original receipt schema/link codec shared by report and startup readers.

Pure framing only: not a journal scan, recovery/admission decision, cost estimator,
executor, replay authority or resource-drain proof. Preserve historical v1/v2/v3.
"""

import re

from .provider_observation import canonical_usage_details, empty_usage_details
from .billing_tariff import validate_price_receipt
from .provider_usage import MAX_SAFE, parse_usage


CALL_START = "provider.call_started"
CALL_FINISH = "provider.call_finished"
REQUEST_START = "provider.request_started"
REQUEST_FINISH = "provider.request_finished"
RECEIPT_KINDS = frozenset({CALL_START, CALL_FINISH, REQUEST_START, REQUEST_FINISH})

TRANSPORT_BOUNDARY = "admitted_transport_method_not_wire_request"


_MISSING = object()
_DETAIL_KEYS = tuple(empty_usage_details())
_SUMMARY_COUNTERS = ("intents", "entered", "sealed", "unsettled", "returned", "failed",
                     "cancelled", "known_usage", "unknown_usage")
_CALL_FAILURES = {"cancelled", "invalid_model_turn", "circuit_open", "timeout", "connection",
                  "rate_limited", "http_status", "provider_failure"}
_REQUEST_FAILURES = {"rate_limited", "http_status", "timeout", "connection", "response_limit",
                     "invalid_response", "cancelled", "transport_failure", "response_cleanup"}


def _integer(value, *, positive=False):
    return type(value) is int and int(positive) <= value <= MAX_SAFE


def _identifier(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _enum(value, values):
    return type(value) is str and value in values


def _equal(left, right):
    """Exact canonical JSON types: bool never impersonates version/counter zero."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    return left == right


def _pair(value):
    if type(value) is not dict:
        return None
    pair = parse_usage(value)
    if pair is None:
        return None
    canonical = {"input_tokens": pair[0], "output_tokens": pair[1], "total_tokens": sum(pair)}
    if any(not _equal(value.get(key, _MISSING), item) for key, item in canonical.items()):
        return None
    return canonical


def _summary(value):
    if type(value) is not dict or not _enum(value.get("coverage"), {"direct_original", "nested_original", "opaque"}):
        return None
    if value.get("boundary") != TRANSPORT_BOUNDARY:
        return None
    if any(not _integer(value.get(key)) for key in _SUMMARY_COUNTERS):
        return None
    if any(value.get(key, _MISSING) is not None and not _integer(value.get(key))
           for key in ("input_tokens", "output_tokens", "total_tokens")):
        return None
    if type(value.get("overflow")) is not bool or not _enum(
        value.get("subtotal_state"), {"unknown", "partial", "known_for_observed_responses"}
    ):
        return None
    keys = (*_SUMMARY_COUNTERS, "coverage", "boundary", "input_tokens", "output_tokens",
            "total_tokens", "overflow", "subtotal_state")
    return {key: value[key] for key in keys}


def _frame(payload, *, request, finish):
    """Reallowlist v1/v2 history and explicit frozen-price v3, never repricing."""
    if type(payload) is not dict or type(payload.get("version")) is not int or payload["version"] not in (1, 2, 3):
        return None
    if not _identifier(payload.get("call_id")) or not _enum(payload.get("engine"), {"legacy", "graph", "deep"}):
        return None
    actors = {"runtime_model"}
    if payload["engine"] == "deep":
        actors.update({"deep_builtin_investigator", "deep_builtin_verifier"})
    if not _enum(payload.get("actor"), actors):
        return None
    keys = ["version", "call_id", "engine", "actor", "boundary"]
    if request:
        if (not _identifier(payload.get("attempt_id")) or not _integer(payload.get("attempt_index"), positive=True)
                or not _enum(payload.get("transport"), {"urllib_urlopen", "httpx_post"})
                or payload.get("boundary") != TRANSPORT_BOUNDARY):
            return None
        keys += ["attempt_id", "attempt_index", "transport"]
    elif (not _enum(payload.get("path"), {"sync_worker", "native_async"})
          or payload.get("boundary") != "provider_method_not_transport_attempt"):
        return None
    else:
        keys += ["path"]
    result = {key: payload[key] for key in keys}
    if result["version"] == 3:
        try:
            # v3 contract present BEFORE method entry and exact start/finish
            # equality enforced by _group. No current settings/provider lookup.
            result["price_receipt"] = validate_price_receipt(payload.get("price_receipt"))
        except ValueError:
            return None
    elif payload.get("price_receipt") is not None:
        return None  # Historical framing cannot silently become priced v3.
    if not finish:
        return result
    outcome, entered = payload.get("outcome"), payload.get("method_entered")
    if not _enum(outcome, {"returned", "failed", "cancelled"}) or type(entered) is not bool:
        return None
    if outcome == "returned" and not entered:
        return None
    failure = payload.get("failure", _MISSING)
    if (outcome == "returned" and failure is not None) or (
        outcome != "returned" and not _enum(failure, _REQUEST_FAILURES if request else _CALL_FAILURES)
    ):
        return None
    if ((result["version"] != 3 and payload.get("price_receipt", _MISSING) is not None) or payload.get("billing_complete") is not False
            or payload.get("account_cap_guaranteed") is not False):
        return None
    raw_usage = payload.get("usage", _MISSING)
    usage = None if raw_usage is None else _pair(raw_usage)
    if raw_usage is not None and usage is None:
        return None
    if payload.get("usage_state") != ("known" if usage is not None else "unknown"):
        return None
    result.update(outcome=outcome, method_entered=entered, usage=usage,
                  usage_state=payload["usage_state"], failure=failure)
    if request:
        status, state = payload.get("http_status", _MISSING), payload.get("response_state")
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            return None
        if not _enum(state, {"not_observed", "bounded_body", "oversized_body", "malformed_body", "unavailable"}):
            return None
        if usage is not None and state != "bounded_body":
            return None
        if not entered and (outcome != "cancelled" or state != "not_observed" or usage is not None or status is not None):
            return None
        details = empty_usage_details()
        if result["version"] in (2, 3):
            raw_details = payload.get("usage_details")
            pair = None if usage is None else (usage["input_tokens"], usage["output_tokens"])
            details = canonical_usage_details(raw_details, pair)
            if type(raw_details) is not dict or any(
                not _equal(raw_details.get(key, _MISSING), details[key]) for key in _DETAIL_KEYS
            ):
                return None
        result.update(http_status=status, response_state=state, usage_details=details)
    else:
        if outcome != "returned" and usage is not None:
            return None
        network = payload.get("network_attempts", _MISSING)
        if result["version"] == 1:
            if network is not None:
                return None
        else:
            transport = _summary(payload.get("transport"))
            if transport is None:
                return None
            direct = transport["coverage"] == "direct_original"
            basis = "none" if outcome != "returned" else "original_bounded_response" if direct else "returned_model_turn"
            if payload.get("usage_basis") != basis:
                return None
            if not _equal(network, transport["entered"] if direct else None):
                return None
            if payload.get("network_attempts_basis", _MISSING) != (TRANSPORT_BOUNDARY if direct else None):
                return None
            result.update(transport=transport, usage_basis=basis)
    return result


def _group(frames):
    starts = [frame for frame in frames if not frame["finish"]]
    finishes = [frame for frame in frames if frame["finish"]]
    if len(starts) > 1 or len(finishes) > 1:
        return {"state": "duplicate", "start": None, "finish": None}
    start = starts[0] if starts else None
    finish = finishes[0] if finishes else None
    if start is None or start["data"] is None or start["scope"] is None or (
        finish is not None and (finish["data"] is None or finish["scope"] != start["scope"]
            or finish["seq"] <= start["seq"]
            or any(not _equal(finish["data"].get(key), value) for key, value in start["data"].items()))
    ):
        return {"state": "invalid", "start": start, "finish": finish}
    return {"state": "matched" if finish is not None else "unsettled", "start": start, "finish": finish}


def _expected_summary(requests, coverage):
    frames = [group["finish"]["data"] for group in requests]
    pairs = [frame["usage"] for frame in frames if frame["usage"] is not None]
    inputs = sum(pair["input_tokens"] for pair in pairs)
    outputs = sum(pair["output_tokens"] for pair in pairs)
    unknown = len(frames) - len(pairs)
    return {"coverage": coverage, "boundary": TRANSPORT_BOUNDARY, "intents": len(frames),
            "entered": sum(frame["method_entered"] for frame in frames), "sealed": len(frames), "unsettled": 0,
            **{key: sum(frame["outcome"] == key for frame in frames) for key in ("returned", "failed", "cancelled")},
            "known_usage": len(pairs), "unknown_usage": unknown,
            "input_tokens": inputs if pairs and inputs <= MAX_SAFE else None,
            "output_tokens": outputs if pairs and outputs <= MAX_SAFE else None,
            "total_tokens": inputs + outputs if pairs and inputs + outputs <= MAX_SAFE else None,
            "overflow": inputs + outputs > MAX_SAFE,
            "subtotal_state": "unknown" if not pairs else "partial" if unknown else "known_for_observed_responses"}
