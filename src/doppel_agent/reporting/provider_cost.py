"""Pure declared-price projection over SAME selected journal units, never invoices.

No settings/provider/FX/keys/IO/recovery/admission; hashes aren't authentication.
Original one-run model snapshot applies to inherited children, never today's
profile or another model's nested traffic. Source read budget is caller-owned.
"""

import re

from ..billing_tariff import _amount, estimate_observation, validate_price_receipt
from ..provider_observation import canonical_usage_details
from ..provider_usage import MAX_SAFE, parse_usage


REASONS = (
    "no_frozen_tariff",
    "receipt_mismatch",
    "source_not_eligible",
    "usage_unknown",
    "cache_partition_unknown",
)
UNIT_KEYS = {"kind", "scope", "usage", "usage_details", "price_receipt", "source_eligible"}
SCOPE_LIMIT = 16


def _scaled(value):
    # Production estimator already canonical. Preserve all14 decimal places,
    # bounded integer coefficient, no floats/Decimal ambient rounding/FX.
    if type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,14})?", value) is None:
        raise ValueError("report_cost_unavailable")
    whole, _, fraction = value.partition(".")
    return int(whole) * 10**14 + int(fraction.ljust(14, "0"))


def _scope(value):
    return type(value) is tuple and (
        value == ()
        or len(value) == 2
        and type(value[0]) is str
        and re.fullmatch(r"[0-9a-f]{32}", value[0]) is not None
        and type(value[1]) is int
        and 1 <= value[1] <= MAX_SAFE
    )


def _check_denominators(records, subtotal):
    if type(subtotal) is not dict:
        raise ValueError("report_cost_unavailable")
    for key in ("known_units", "unknown_units", "request_units", "logical_call_units"):
        if type(subtotal.get(key)) is not int or not 0 <= subtotal[key] <= MAX_SAFE:
            raise ValueError("report_cost_unavailable")
    requests = sum(unit["kind"] == "request" for unit, _, _ in records)
    priced = sum(reason is None for _, reason, _ in records)
    if (
        subtotal["known_units"] + subtotal["unknown_units"] != len(records)
        or subtotal["request_units"] != requests
        or subtotal["logical_call_units"] != len(records) - requests
        or priced > subtotal["known_units"]
    ):
        raise ValueError("report_cost_unavailable")


def _subtotal(records, frozen):
    # Classify/estimate ONCE in original selected order. Scope subtotals only
    # accumulate those detached classifications, never price samples anew.
    reasons, currencies, known, requests = dict.fromkeys(REASONS, 0), {}, 0, 0
    for unit, reason, amount in records:
        requests += unit["kind"] == "request"
        if reason is not None:
            if reason not in reasons:
                raise ValueError("report_cost_unavailable")
            reasons[reason] += 1
            continue
        subtotal = currencies.setdefault(
            frozen["currency"],
            {
                "scaled": 0,
                "known_units": 0,
                "request_units": 0,
                "logical_call_units": 0,
                "input": 0,
                "output": 0,
                "hit": 0,
                "miss": 0,
            },
        )
        subtotal["scaled"] += _scaled(amount)
        pair = parse_usage(unit["usage"])
        subtotal["input"] += pair[0]
        subtotal["output"] += pair[1]
        if frozen["billing_basis"] == "cache_partition_output_inclusive":
            details = canonical_usage_details(unit["usage_details"], pair)
            subtotal["hit"] += details["cached_input_tokens"]
            subtotal["miss"] += details["uncached_input_tokens"]
        subtotal["known_units"] += 1
        subtotal["request_units" if unit["kind"] == "request" else "logical_call_units"] += 1
        known += 1
    return {
        "selected_units": len(records),
        "request_units": requests,
        "logical_call_units": len(records) - requests,
        "known_units": known,
        "unknown_units": len(records) - known,
        "unknown_reasons": reasons,
        "currencies": [
            {
                "currency": currency,
                "amount": _amount(subtotal["scaled"]),
                "known_units": subtotal["known_units"],
                "request_units": subtotal["request_units"],
                "logical_call_units": subtotal["logical_call_units"],
                "tariff_sha256": frozen["tariff_sha256"],
                # Exact priced-SUBSET witnesses, not rounded JS-safe totals and not
                # all selected usage when some monetary eligibility is unknown.
                "input_tokens": str(subtotal["input"]),
                "output_tokens": str(subtotal["output"]),
                "cached_input_tokens": str(subtotal["hit"])
                if frozen["billing_basis"] == "cache_partition_output_inclusive"
                else None,
                "uncached_input_tokens": str(subtotal["miss"])
                if frozen["billing_basis"] == "cache_partition_output_inclusive"
                else None,
            }
            for currency, subtotal in sorted(currencies.items())
        ],
    }


def _scopes(records, frozen, provider_report):
    root, children = [], {}
    for record in records:
        scope = record[0]["scope"]
        if scope == ():
            root.append(record)
        else:
            children.setdefault(scope, []).append(record)
    identities = sorted(children)
    omitted = max(0, len(identities) - SCOPE_LIMIT)
    other = [record for scope in identities[SCOPE_LIMIT:] for record in children[scope]]
    original = provider_report.get("scopes")
    if (
        type(original) is not dict
        or set(original)
        != {
            "basis",
            "root",
            "children_total",
            "children",
            "children_omitted",
            "other_children",
            "limit",
            "truncated",
        }
        or original["basis"] != "recorded_outer_tags_not_reconstructed"
        or type(original["children_total"]) is not int
        or original["children_total"] != len(identities)
        or type(original["children_omitted"]) is not int
        or original["children_omitted"] != omitted
        or type(original["limit"]) is not int
        or original["limit"] != SCOPE_LIMIT
        or type(original["truncated"]) is not bool
        or original["truncated"] != bool(omitted)
        or type(original["children"]) is not list
        or len(original["children"]) != min(len(identities), SCOPE_LIMIT)
    ):
        raise ValueError("report_cost_unavailable")
    _check_denominators(root, original["root"])
    _check_denominators(other, original["other_children"])
    for scope, subtotal in zip(identities[:SCOPE_LIMIT], original["children"], strict=True):
        if (
            type(subtotal) is not dict
            or subtotal.get("subagent_id") != scope[0]
            or type(subtotal.get("generation")) is not int
            or subtotal["generation"] != scope[1]
        ):
            raise ValueError("report_cost_unavailable")
        _check_denominators(children[scope], subtotal)
    return {
        "basis": "recorded_outer_tags_not_reconstructed",
        "root": _subtotal(root, frozen),
        "children_total": len(identities),
        "children": [
            {"subagent_id": scope[0], "generation": scope[1], **_subtotal(children[scope], frozen)}
            for scope in identities[:SCOPE_LIMIT]
        ],
        "children_omitted": omitted,
        "other_children": _subtotal(other, frozen),
        "limit": SCOPE_LIMIT,
        "truncated": bool(omitted),
    }


def build_provider_cost(units, profile_snapshot, provider_report, *, source_omitted=False):
    """Full selected prefix, not first16 samples; original saved tariff ONLY.

    Unknown/ineligible units remain in exact denominators. Known failed bounded
    response observations may estimate even failed calls, not missing=free.
    Declared opaque logical observations are labelled estimates, not wire costs.
    A different tariff/currency than original run is unknown, not FX or repricing.
    """
    if (
        type(source_omitted) is not bool
        or type(units) is not list
        or len(units) > 5000
        or type(provider_report) is not dict
    ):
        raise ValueError("report_cost_unavailable")
    selected = provider_report.get("selected")
    if (
        type(selected) is not dict
        or type(provider_report.get("state")) is not str
        or provider_report["state"] not in {"unknown", "partial", "known_for_selected_receipts"}
    ):
        raise ValueError("report_cost_unavailable")
    for key in ("known_units", "unknown_units", "request_units", "logical_call_units"):
        if type(selected.get(key)) is not int or not 0 <= selected[key] <= MAX_SAFE:
            raise ValueError("report_cost_unavailable")
    if selected["known_units"] + selected["unknown_units"] != len(units):
        raise ValueError("report_cost_unavailable")
    if any(
        type(unit) is not dict
        or set(unit) != UNIT_KEYS
        or type(unit["kind"]) is not str
        or unit["kind"] not in {"request", "logical_call"}
        or type(unit["source_eligible"]) is not bool
        or not _scope(unit["scope"])
        for unit in units
    ):
        raise ValueError("report_cost_unavailable")
    request_units = sum(unit["kind"] == "request" for unit in units)
    if (
        request_units != selected["request_units"]
        or len(units) - request_units != selected["logical_call_units"]
    ):
        raise ValueError("report_cost_unavailable")
    frozen, source_state = None, "missing"
    if source_omitted:
        source_state = "omitted"
    elif type(profile_snapshot) is not dict:
        source_state = "invalid"
    elif profile_snapshot.get("billing_price_receipt") is not None:
        try:
            frozen = validate_price_receipt(
                profile_snapshot["billing_price_receipt"], profile=profile_snapshot
            )
            source_state = "known"
        except ValueError:
            source_state = "invalid"
    records = []
    for unit in units:
        reason, amount = None, None
        if frozen is None or unit["price_receipt"] is None:
            reason = "no_frozen_tariff"
        elif unit["price_receipt"] != frozen:
            reason = "receipt_mismatch"
        elif not unit["source_eligible"]:
            reason = "source_not_eligible"
        else:
            result = estimate_observation(frozen, unit["usage"], unit["usage_details"])
            if result["amount"] is None:
                reason = result["reason"]
            else:
                amount = result["amount"]
        records.append((unit, reason, amount))
    _check_denominators(records, selected)
    subtotal = _subtotal(records, frozen)
    scopes = _scopes(records, frozen, provider_report)
    known = subtotal["known_units"]
    uncertain = provider_report["state"] != "known_for_selected_receipts"
    return {
        "version": 1,
        "policy": "selected_units_original_frozen_tariff_no_fx",
        "state": "unknown"
        if not known
        else "partial"
        if known != len(units) or uncertain
        else "known_for_declared_observations",
        "coverage": "partial_recorded_prefix" if uncertain else "selected_recorded_prefix",
        "source_state": source_state,
        **subtotal,
        "scopes": scopes,
        "price_snapshot": frozen,
        "basis": "declared_tariff_observed_usage_not_invoice",
        "source_verified": False,
        "billing_complete": False,
        "account_cap_guaranteed": False,
    }
