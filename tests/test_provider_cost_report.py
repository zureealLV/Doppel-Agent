"""B2b5c cost definitions FIRST, ALL UNRUN. Offline declared rates, not invoices."""

from copy import deepcopy
from fractions import Fraction
import json

import pytest

from doppel_agent.reporting.provider_cost import build_provider_cost
from doppel_agent.reporting.provider_usage import build_provider_usage
from doppel_agent.billing_tariff import freeze_tariff
from doppel_agent.provider_usage import MAX_SAFE
from test_billing_tariff import PROFILE, tariff
from test_billing_tariff_recording import price, priced_rows
from test_provider_usage_report import RUN, call_frames, request_frames, row, usage


def project(rows=None, snapshot=None, **kwargs):
    units = []
    tokens = build_provider_usage(
        RUN,
        priced_rows() if rows is None else rows,
        pricing_units=units,
        truncated=kwargs.get("truncated", False),
        omitted_usage_rows=kwargs.get("omitted", 0),
    )
    original = {**PROFILE, "billing_price_receipt": price()} if snapshot is None else snapshot
    return tokens, build_provider_cost(
        units, original, tokens, source_omitted=kwargs.get("source_omitted", False)
    )


def test_same_full_selected_units_priced_once_against_original_run_snapshot_not_callback_or_sample():
    tokens, cost = project()
    assert tokens["selected"]["known_units"] == cost["selected_units"] == cost["known_units"] == 1
    assert cost["unknown_units"] == 0 and cost["state"] == "known_for_declared_observations"
    assert cost["currencies"] == [
        {
            "currency": "CNY",
            "amount": "0.00001625",
            "known_units": 1,
            "request_units": 1,
            "logical_call_units": 0,
            "tariff_sha256": price()["tariff_sha256"],
            "input_tokens": "7",
            "output_tokens": "3",
            "cached_input_tokens": None,
            "uncached_input_tokens": None,
        }
    ]
    assert (
        Fraction(cost["currencies"][0]["amount"]) == (7 * Fraction("1.25") + 3 * Fraction("2.5")) / 1_000_000
    )
    assert cost["billing_complete"] is cost["account_cap_guaranteed"] is cost["source_verified"] is False
    assert "source_reference" not in json.dumps(cost) and "fixture-model" not in json.dumps(cost)
    assert cost["price_snapshot"] == price()


def test_all_24_observations_count_even_though_report_samples_are_first16():
    rows = []
    for index in range(24):
        start, finish = call_frames(call_id=f"{index + 1:032x}")
        for frame in (start, finish):
            frame.update(version=3, price_receipt=price())
        rows.extend(
            [
                row(2 * index + 1, "provider.call_started", start),
                row(2 * index + 2, "provider.call_finished", finish),
            ]
        )
    tokens, cost = project(rows)
    assert tokens["samples"]["emitted"] == 16 and cost["selected_units"] == cost["known_units"] == 24
    assert cost["currencies"][0]["logical_call_units"] == 24
    assert Fraction(cost["currencies"][0]["amount"]) == 24 * Fraction("0.00001625")


def test_monetary_outer_scopes_conserve_all_units_amounts_witnesses_and_unknowns_beyond_first16(monkeypatch):
    # FIRST definition, UNRUN. Reverse source order must not change sorted slots.
    import doppel_agent.reporting.provider_cost as projection

    original, calls = projection.estimate_observation, []

    def counted(*args, **kwargs):
        calls.append(None)
        return original(*args, **kwargs)

    monkeypatch.setattr(projection, "estimate_observation", counted)
    rows = []
    for index in reversed(range(18)):
        start, finish = call_frames(call_id=f"{index + 100:032x}", pair=usage(index + 1, 1))
        for frame in (start, finish):
            frame.update(version=3, price_receipt=price(), subagent_id="f" * 32, generation=99)
        child = f"{index + 1:032x}"
        rows.extend(
            [
                row(len(rows) + 1, "provider.call_started", start, child),
                row(len(rows) + 2, "provider.call_finished", finish, child),
            ]
        )
    # Missing frozen receipt stays an unknown first child; other children survive.
    rows[-2]["payload"]["runtime_payload"].pop("price_receipt")
    rows[-2]["payload"]["runtime_payload"]["version"] = 2
    rows[-1]["payload"]["runtime_payload"].update(version=2, price_receipt=None)
    root_start, root_finish = call_frames(call_id="e" * 32, pair=usage(4, 2))
    for frame in (root_start, root_finish):
        frame.update(version=3, price_receipt=price())
    rows.extend(
        [
            row(len(rows) + 1, "provider.call_started", root_start),
            row(len(rows) + 2, "provider.call_finished", root_finish),
        ]
    )
    tokens, cost = project(rows)
    scopes = cost["scopes"]
    assert len(calls) == 18  # 17 priced children + root, never re-estimate each scope.
    assert scopes["basis"] == "recorded_outer_tags_not_reconstructed"
    assert scopes["children_total"] == tokens["scopes"]["children_total"] == 18
    assert scopes["children_omitted"] == 2 and scopes["limit"] == 16 and scopes["truncated"] is True
    assert [(v["subagent_id"], v["generation"]) for v in scopes["children"]] == [
        (f"{i + 1:032x}", 2) for i in range(16)
    ]
    assert scopes["children"][0]["unknown_reasons"]["no_frozen_tariff"] == 1
    assert scopes["other_children"]["known_units"] == 2
    parts = [scopes["root"], *scopes["children"], scopes["other_children"]]
    for key in ("selected_units", "request_units", "logical_call_units", "known_units", "unknown_units"):
        assert sum(v[key] for v in parts) == cost[key]
    for reason in cost["unknown_reasons"]:
        assert sum(v["unknown_reasons"][reason] for v in parts) == cost["unknown_reasons"][reason]
    currencies = [v["currencies"][0] for v in parts if v["currencies"]]
    assert sum(Fraction(v["amount"]) for v in currencies) == Fraction(cost["currencies"][0]["amount"])
    for key in ("input_tokens", "output_tokens"):
        assert sum(int(v[key]) for v in currencies) == int(cost["currencies"][0][key])
    assert cost["state"] == "partial" and scopes["root"]["known_units"] == 1


def test_same_child_id_generations_are_distinct_and_unknown_omitted_child_not_root_or_free():
    rows = []
    for index in range(18):
        start, finish = call_frames(call_id=f"{index + 100:032x}")
        for frame in (start, finish):
            frame.update(version=3, price_receipt=price())
        entries = [
            row(len(rows) + 1, "provider.call_started", start, "d" * 32),
            row(len(rows) + 2, "provider.call_finished", finish, "d" * 32),
        ]
        for entry in entries:
            entry["payload"]["generation"] = index + 1
        rows.extend(entries if index < 17 else entries[:1])
    _, cost = project(rows)
    scopes = cost["scopes"]
    assert scopes["root"]["selected_units"] == 0 and scopes["root"]["currencies"] == []
    assert [v["generation"] for v in scopes["children"]] == list(range(1, 17))
    assert scopes["other_children"]["selected_units"] == 2
    assert scopes["other_children"]["known_units"] == scopes["other_children"]["unknown_units"] == 1
    assert scopes["other_children"]["unknown_reasons"]["source_not_eligible"] == 1
    assert Fraction(scopes["other_children"]["currencies"][0]["amount"]) == Fraction("0.00001625")


@pytest.mark.parametrize(
    "scope", [None, [], ("bad", 2), ("d" * 32, True), ("d" * 32, 0), ("d" * 32, MAX_SAFE + 1)]
)
def test_internal_malformed_scope_never_silently_rebound_to_root(scope):
    units = []
    tokens = build_provider_usage(RUN, priced_rows(), pricing_units=units)
    units[0]["scope"] = scope
    with pytest.raises(ValueError, match="^report_cost_unavailable$"):
        build_provider_cost(units, {**PROFILE, "billing_price_receipt": price()}, tokens)


def test_internal_scope_denominator_mismatch_refused_before_publishing_monetary_allocation():
    units = []
    tokens = build_provider_usage(RUN, priced_rows(), pricing_units=units)
    units[0]["scope"] = ("d" * 32, 2)
    with pytest.raises(ValueError, match="^report_cost_unavailable$"):
        build_provider_cost(units, {**PROFILE, "billing_price_receipt": price()}, tokens)


@pytest.mark.parametrize(
    "snapshot,source_omitted,state",
    [
        ({}, False, "missing"),
        ({**PROFILE, "billing_price_receipt": {"currency": "CNY"}}, False, "invalid"),
        ({**PROFILE, "model": "changed", "billing_price_receipt": price()}, False, "invalid"),
        ({**PROFILE, "billing_price_receipt": price()}, True, "omitted"),
    ],
)
def test_missing_corrupt_omitted_original_snapshot_never_uses_receipt_alone_or_default_free(
    snapshot, source_omitted, state
):
    tokens, cost = project(snapshot=snapshot, source_omitted=source_omitted)
    assert tokens["selected"]["known_units"] == 1
    assert cost["source_state"] == state and cost["state"] == "unknown"
    assert cost["known_units"] == 0 and cost["unknown_units"] == 1
    assert cost["price_snapshot"] is None and cost["currencies"] == []


def test_changed_snapshot_tariff_or_currency_cannot_reprice_history_or_fx_convert():
    _, cost = project(snapshot={**PROFILE, "billing_price_receipt": price("USD")})
    assert cost["known_units"] == 0 and cost["unknown_reasons"]["receipt_mismatch"] == 1
    rows = priced_rows()
    for event in rows[:4]:
        event["payload"]["price_receipt"] = price("USD")
    _, usd = project(rows, snapshot={**PROFILE, "billing_price_receipt": price("USD")})
    assert usd["currencies"][0]["currency"] == "USD" and usd["currencies"][0]["amount"] == "0.00001625"
    assert "CNY" not in json.dumps(usd)


def test_known_zero_distinct_from_unfinished_source_and_truncated_prefix_not_full_billing():
    start, finish = call_frames(pair=usage(0, 0))
    for frame in (start, finish):
        frame.update(version=3, price_receipt=price())
    rows = [row(1, "provider.call_started", start), row(2, "provider.call_finished", finish)]
    _, zero = project(rows)
    assert zero["known_units"] == 1 and zero["currencies"][0]["amount"] == "0"
    _, pending = project(rows[:1])
    assert pending["known_units"] == 0 and pending["unknown_units"] == 1 and pending["currencies"] == []
    _, prefix = project(rows, truncated=True)
    assert (
        prefix["known_units"] == 1
        and prefix["state"] == "partial"
        and prefix["coverage"] == "partial_recorded_prefix"
    )


def test_nested_requests_summary_mismatch_and_historical_frames_not_fabricated_priced_eligibility():
    nested = deepcopy(priced_rows())
    nested[3]["payload"]["transport"]["coverage"] = "nested_original"
    nested[3]["payload"].update(
        usage_basis="returned_model_turn", network_attempts=None, network_attempts_basis=None
    )
    _, cost = project(nested)
    assert cost["unknown_reasons"]["source_not_eligible"] == 1 and cost["known_units"] == 0
    broken = deepcopy(priced_rows())
    broken[3]["payload"]["transport"]["intents"] = 2
    _, mismatch = project(broken)
    assert mismatch["known_units"] == 0 and mismatch["unknown_units"] == 1
    old = deepcopy(priced_rows())
    for event in old[:4]:
        event["payload"].update(version=2)
        if event["type"].endswith("started"):
            event["payload"].pop("price_receipt")
        else:
            event["payload"]["price_receipt"] = None
    _, historical = project(old)
    assert historical["known_units"] == 0 and historical["unknown_reasons"]["no_frozen_tariff"] == 1


def test_failed_retry_response_and_returned_response_are_two_declared_observations_not_three_charges():
    failed, returned = (
        request_frames(pair=usage(2, 1), outcome="failed"),
        request_frames(index=2, attempt_id="e" * 32),
    )
    call = call_frames(requests=[failed, returned], coverage="direct_original")
    for frame in (*call, *failed, *returned):
        frame.update(version=3, price_receipt=price())
    rows = [
        row(1, "provider.call_started", call[0]),
        row(2, "provider.request_started", failed[0]),
        row(3, "provider.request_finished", failed[1]),
        row(4, "provider.request_started", returned[0]),
        row(5, "provider.request_finished", returned[1]),
        row(6, "provider.call_finished", call[1]),
        row(7, "graph.model_finished", {"provider_call_id": call[0]["call_id"], "usage": usage()}),
    ]
    tokens, cost = project(rows)
    assert tokens["counts"]["request_failed"] == 1 and cost["known_units"] == 2
    assert cost["currencies"][0]["request_units"] == 2 and cost["currencies"][0]["logical_call_units"] == 0
    assert cost["currencies"][0]["input_tokens"] == "9" and cost["currencies"][0]["output_tokens"] == "4"
    assert (
        Fraction(cost["currencies"][0]["amount"]) == (9 * Fraction("1.25") + 4 * Fraction("2.5")) / 1_000_000
    )


def test_actual_cache_partition_witness_and_reasoning_inclusive_rate_or_unknown_cache():
    receipt = freeze_tariff(
        tariff(
            billing_basis="cache_partition_output_inclusive",
            rates={"input": None, "cached_input": "0.5", "uncached_input": "2", "output": "3"},
        ),
        PROFILE,
        freeze_date="2026-10-06",
    )
    rows = deepcopy(priced_rows())
    for entry in rows[:4]:
        entry["payload"]["price_receipt"] = receipt
    rows[2]["payload"]["usage_details"].update(
        cache_state="known",
        cached_input_tokens=2,
        uncached_input_tokens=5,
        reasoning_state="known",
        reasoning_output_tokens=2,
    )
    _, cost = project(rows, snapshot={**PROFILE, "billing_price_receipt": receipt})
    priced = cost["currencies"][0]
    assert priced["cached_input_tokens"] == "2" and priced["uncached_input_tokens"] == "5"
    assert priced["amount"] == "0.00002"  # 2*.5+5*2+3*3; reasoning already part of output3.
    rows[2]["payload"]["usage_details"].update(cache_state="partial", uncached_input_tokens=None)
    tokens, unknown = project(rows, snapshot={**PROFILE, "billing_price_receipt": receipt})
    assert (
        tokens["selected"]["known_units"] == 1 and unknown["unknown_reasons"]["cache_partition_unknown"] == 1
    )
    assert unknown["currencies"] == []


def test_priced_subset_large_exact_witness_is_decimal_string_not_unsafe_json_number():
    rows = []
    for index in range(2):
        start, finish = call_frames(call_id=f"{index + 1:032x}", pair=usage(MAX_SAFE, 0))
        for frame in (start, finish):
            frame.update(version=3, price_receipt=price())
        rows.extend(
            [
                row(2 * index + 1, "provider.call_started", start),
                row(2 * index + 2, "provider.call_finished", finish),
            ]
        )
    tokens, cost = project(rows)
    assert tokens["selected"]["subtotal_overflow"] is True
    assert cost["currencies"][0]["input_tokens"] == str(2 * MAX_SAFE)
    assert Fraction(cost["currencies"][0]["amount"]) == 2 * MAX_SAFE * Fraction("1.25") / 1_000_000
    assert cost["state"] == "partial" and cost["billing_complete"] is False
