"""B2b4a definitions FIRST, ALL UNRUN; bounded recorded linkage, not billing."""

from copy import deepcopy
import json

import pytest

from doppel_agent.provider_observation import empty_usage_details
from doppel_agent.provider_usage import MAX_SAFE
from doppel_agent.reporting.provider_usage import build_provider_usage


RUN, CALL, ATTEMPT, CHILD = "a" * 32, "b" * 32, "c" * 32, "d" * 32
_DEFAULT_USAGE = object()


def usage(inputs=7, outputs=3):
    return {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs}


def row(seq, kind, payload, child=None):
    if child is not None:
        return {
            "seq": seq,
            "type": "subagent.runtime",
            "payload": {
                "parent_run_id": RUN,
                "subagent_id": child,
                "generation": 2,
                "runtime_kind": kind,
                "runtime_payload": payload,
            },
        }
    return {"seq": seq, "type": kind, "payload": payload}


def summary(requests, *, coverage="direct_original"):
    pairs = [item[1]["usage"] for item in requests if item[1]["usage"] is not None]
    inputs, outputs = sum(p["input_tokens"] for p in pairs), sum(p["output_tokens"] for p in pairs)
    unknown = len(requests) - len(pairs)
    return {
        "coverage": coverage,
        "boundary": "admitted_transport_method_not_wire_request",
        "intents": len(requests),
        "entered": sum(item[1]["method_entered"] for item in requests),
        "sealed": len(requests),
        "unsettled": 0,
        **{
            state: sum(item[1]["outcome"] == state for item in requests)
            for state in ("returned", "failed", "cancelled")
        },
        "known_usage": len(pairs),
        "unknown_usage": unknown,
        "input_tokens": inputs if pairs and inputs <= MAX_SAFE else None,
        "output_tokens": outputs if pairs and outputs <= MAX_SAFE else None,
        "total_tokens": inputs + outputs if pairs and inputs + outputs <= MAX_SAFE else None,
        "overflow": inputs + outputs > MAX_SAFE,
        "subtotal_state": "unknown"
        if not pairs
        else "partial"
        if unknown
        else "known_for_observed_responses",
    }


def call_frames(
    *, version=2, call_id=CALL, pair=_DEFAULT_USAGE, requests=(), coverage="opaque", outcome="returned"
):
    if pair is _DEFAULT_USAGE:
        pair = usage()
    start = {
        "version": version,
        "call_id": call_id,
        "engine": "graph",
        "actor": "runtime_model",
        "path": "native_async",
        "boundary": "provider_method_not_transport_attempt",
    }
    finish = {
        **start,
        "outcome": outcome,
        "method_entered": True,
        "usage": pair if outcome == "returned" else None,
        "usage_state": "known" if outcome == "returned" and pair is not None else "unknown",
        "failure": None
        if outcome == "returned"
        else "cancelled"
        if outcome == "cancelled"
        else "provider_failure",
        "network_attempts": None,
        "price_receipt": None,
        "billing_complete": False,
        "account_cap_guaranteed": False,
    }
    if version == 2:
        finished_requests = [entry for entry in requests]
        transport = summary(finished_requests, coverage=coverage)
        finish.update(
            transport=transport,
            usage_basis="none"
            if outcome != "returned"
            else "original_bounded_response"
            if coverage == "direct_original"
            else "returned_model_turn",
            network_attempts=transport["entered"] if coverage == "direct_original" else None,
            network_attempts_basis="admitted_transport_method_not_wire_request"
            if coverage == "direct_original"
            else None,
        )
    return start, finish


def request_frames(
    *, index=1, attempt_id=ATTEMPT, call_id=CALL, pair=_DEFAULT_USAGE, version=2, outcome="returned"
):
    if pair is _DEFAULT_USAGE:
        pair = usage()
    start = {
        "version": version,
        "call_id": call_id,
        "attempt_id": attempt_id,
        "attempt_index": index,
        "engine": "graph",
        "actor": "runtime_model",
        "transport": "httpx_post",
        "boundary": "admitted_transport_method_not_wire_request",
    }
    finish = {
        **start,
        "outcome": outcome,
        "method_entered": True,
        "http_status": 200 if outcome == "returned" else 503,
        "response_state": "bounded_body",
        "usage": pair,
        "usage_state": "known" if pair is not None else "unknown",
        "failure": None if outcome == "returned" else "http_status",
        "price_receipt": None,
        "billing_complete": False,
        "account_cap_guaranteed": False,
    }
    if version == 2:
        finish["usage_details"] = empty_usage_details()
    return start, finish


def projection(rows, **kwargs):
    return build_provider_usage(
        RUN,
        rows,
        omitted_usage_rows=kwargs.get("omitted_usage_rows", 0),
        truncated=kwargs.get("truncated", False),
    )


def transport_rows(*, child=None, call_version=2, request_version=2):
    request = request_frames(version=request_version)
    call = call_frames(version=call_version, requests=[request], coverage="direct_original")
    return [
        row(1, "provider.call_started", call[0], child),
        row(2, "provider.request_started", request[0], child),
        row(3, "provider.request_finished", request[1], child),
        row(4, "provider.call_finished", call[1], child),
        row(5, "graph.model_finished", {"provider_call_id": CALL, "usage": usage()}, child),
    ]


def test_request_call_callback_one_observed_subtotal_not_three_charges_and_no_private_payload():
    rows = transport_rows()
    for entry in rows:
        entry["payload"]["private"] = "PRIVATE_KEY_REASONING_PATH"
    report = projection(rows)
    assert report["selected"]["known_units"] == report["selected"]["request_units"] == 1
    assert report["selected"]["input_tokens"] == 7 and report["selected"]["total_tokens"] == 10
    assert report["selected"]["logical_call_units"] == 0
    assert report["counts"]["callbacks_matched"] == 1 and report["compatibility"]["known_units"] == 0
    assert report["counts"]["calls_matched"] == 1 and report["counts"]["requests_linked"] == 1
    assert report["state"] == "known_for_selected_receipts" and report["billing_complete"] is False
    assert report["wire_request_coverage"] == "unknown" and "PRIVATE_" not in json.dumps(report)


def test_failed_retry_response_is_counted_even_when_logical_call_later_returns():
    failed = request_frames(pair=usage(2, 1), outcome="failed")
    returned = request_frames(index=2, attempt_id="e" * 32)
    call = call_frames(requests=[failed, returned], coverage="direct_original")
    rows = [
        row(1, "provider.call_started", call[0]),
        row(2, "provider.request_started", failed[0]),
        row(3, "provider.request_finished", failed[1]),
        row(4, "provider.request_started", returned[0]),
        row(5, "provider.request_finished", returned[1]),
        row(6, "provider.call_finished", call[1]),
        row(7, "graph.model_finished", {"provider_call_id": CALL, "usage": usage()}),
    ]
    report = projection(rows)
    assert report["selected"]["known_units"] == 2 and report["selected"]["total_tokens"] == 13
    assert report["counts"]["callbacks_matched"] == 1


def test_known_request_survives_started_no_finish_logical_call_and_unknown_pending_request():
    rows = transport_rows()[:3]
    report = projection(rows)
    assert report["selected"]["total_tokens"] == 10 and report["counts"]["calls_unsettled"] == 1
    assert report["state"] == "partial" and report["billing_complete"] is False
    pending = request_frames(index=2, attempt_id="e" * 32)
    rows.append(row(4, "provider.request_started", pending[0]))
    report = projection(rows)
    assert report["selected"]["total_tokens"] == 10 and report["selected"]["unknown_units"] == 1
    assert report["counts"]["requests_unsettled"] == 1


@pytest.mark.parametrize("version", [1, 2])
def test_historical_or_opaque_logical_only_receipt_is_separate_from_wire_coverage(version):
    call = call_frames(version=version)
    report = projection(
        [
            row(1, "provider.call_started", call[0]),
            row(2, "provider.call_finished", call[1]),
            row(3, "graph.model_finished", {"provider_call_id": CALL, "usage": usage()}),
        ]
    )
    assert report["selected"]["logical_call_units"] == 1 and report["selected"]["request_units"] == 0
    assert report["selected"]["total_tokens"] == 10 and report["counts"]["callbacks_matched"] == 1
    assert report["wire_request_coverage"] == "unknown"


@pytest.mark.parametrize("marker", [None, "f" * 32, "PRIVATE_FORGED_ID", [], {}])
def test_callback_marker_alone_never_suppresses_valid_compatibility_observation(marker):
    report = projection([row(1, "graph.model_finished", {"usage": usage(), "provider_call_id": marker})])
    assert report["selected"]["known_units"] == 0 and report["selected"]["input_tokens"] is None
    assert report["compatibility"]["known_units"] == 1 and report["compatibility"]["total_tokens"] == 10
    assert report["counts"]["callbacks_matched"] == 0 and report["counts"]["callbacks_unmatched"] == 1
    assert report["state"] == "unknown" and "PRIVATE_" not in json.dumps(report)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows[3]["payload"]["transport"].update(intents=2),
        lambda rows: rows[3]["payload"].update(usage=usage(8, 3)),
        lambda rows: rows[4]["payload"].update(usage=usage(8, 3)),
        lambda rows: rows[4].update(type="deep.model_finished"),
    ],
)
def test_summary_usage_or_engine_mismatch_cannot_suppress_callback_or_add_logical_charge(mutate):
    rows = transport_rows()
    mutate(rows)
    report = projection(rows)
    assert report["selected"]["request_units"] == 1 and report["selected"]["total_tokens"] == 10
    assert report["selected"]["logical_call_units"] == 0 and report["counts"]["callbacks_matched"] == 0
    assert report["compatibility"]["known_units"] == 1 and report["state"] == "partial"


def test_raw_unknown_attempt_cannot_be_upgraded_by_known_logical_or_callback_pair():
    rows = transport_rows()
    rows[2]["payload"].update(usage=None, usage_state="unknown", response_state="malformed_body")
    # Make scalar prefix internally consistent, but forged logical raw pair must
    # still not upgrade independently refused response framing.
    rows[3]["payload"]["transport"].update(
        known_usage=0,
        unknown_usage=1,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        subtotal_state="unknown",
    )
    report = projection(rows)
    assert report["selected"]["known_units"] == 0 and report["selected"]["unknown_units"] == 1
    assert report["selected"]["input_tokens"] is None and report["counts"]["summary_mismatches"] == 1
    assert report["compatibility"]["known_units"] == 1


def test_outer_child_scope_authority_and_global_id_collision_do_not_rebind_or_charge_twice():
    rows = transport_rows(child=CHILD)
    for entry in rows:
        entry["payload"]["runtime_payload"].update(subagent_id="e" * 32, generation=99)
    report = projection(rows)
    child = report["scopes"]["children"][0]
    assert child["subagent_id"] == CHILD and child["generation"] == 2 and child["input_tokens"] == 7
    assert report["scopes"]["root"]["input_tokens"] is None
    duplicate = deepcopy(rows[0])
    duplicate["seq"] = 6
    duplicate["payload"]["subagent_id"] = "e" * 32
    report = projection([*rows, duplicate])
    assert report["counts"]["call_duplicate_ids"] == 1 and report["selected"]["known_units"] == 0
    assert report["counts"]["request_orphans"] == 1 and report["compatibility"]["known_units"] == 1


def test_duplicate_request_identity_or_ordinal_is_ambiguous_not_a_second_charge():
    rows = transport_rows()
    duplicate = deepcopy(rows[2])
    duplicate["seq"] = 6
    report = projection([*rows, duplicate])
    assert report["counts"]["request_duplicate_ids"] == 1 and report["selected"]["known_units"] == 0
    another = request_frames(attempt_id="e" * 32)  # Same ordinal, distinct identity.
    report = projection(
        [
            *rows[:3],
            row(4, "provider.request_started", another[0]),
            row(5, "provider.request_finished", another[1]),
            row(6, "provider.call_finished", rows[3]["payload"]),
        ]
    )
    assert report["counts"]["request_ordinal_collisions"] == 1 and report["selected"]["known_units"] == 0


@pytest.mark.parametrize("version", [1, 2])
def test_request_version_history_zero_and_invalid_raw_detail_state_are_not_upgraded(version):
    request = request_frames(pair=usage(0, 0), version=version)
    if version == 2:
        request[1]["usage_details"] = {**empty_usage_details(), "cache_state": "invalid"}
    call = call_frames(pair=usage(0, 0), requests=[request], coverage="direct_original")
    report = projection(
        [
            row(1, "provider.call_started", call[0]),
            row(2, "provider.request_started", request[0]),
            row(3, "provider.request_finished", request[1]),
            row(4, "provider.call_finished", call[1]),
        ]
    )
    assert report["selected"]["known_units"] == 1 and report["selected"]["total_tokens"] == 0
    sample = next(item for item in report["samples"]["items"] if item["kind"] == "request")
    assert sample["receipt_version"] == version
    assert sample["usage_details"]["cache_state"] == ("invalid" if version == 2 else "unknown")


def test_bounded_samples_children_and_overflow_do_not_hide_denominators_or_guess_zero():
    rows = []
    for index in range(18):
        child, call_id = f"{index + 1:032x}", f"{index + 100:032x}"
        call = call_frames(call_id=call_id, pair=usage(MAX_SAFE, 0))
        rows.extend(
            [
                row(len(rows) + 1, "provider.call_started", call[0], child),
                row(len(rows) + 2, "provider.call_finished", call[1], child),
            ]
        )
    report = projection(rows, omitted_usage_rows=1, truncated=True)
    assert report["selected"]["known_units"] == 18 and report["selected"]["subtotal_overflow"]
    assert report["selected"]["input_tokens"] is None and report["state"] == "partial"
    assert (
        report["samples"]["total"] == 18
        and report["samples"]["emitted"] == 16
        and report["samples"]["omitted"] == 2
    )
    assert report["scopes"]["children_total"] == 18 and report["scopes"]["children_omitted"] == 2
    assert report["scopes"]["other_children"]["known_units"] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("attempt_index", True),
        ("transport", []),
        ("engine", {}),
        ("usage", {}),
        ("usage_details", None),
    ],
)
def test_malformed_request_reserves_identity_without_upgrading_or_hiding_unknown(field, value):
    rows = transport_rows()
    rows[2]["payload"][field] = value
    report = projection(rows)
    assert report["selected"]["known_units"] == 0
    assert report["counts"]["request_invalid_ids"] == 1
    assert report["compatibility"]["known_units"] == 1


def test_direct_raw_usage_claim_without_request_frames_cannot_become_logical_fallback():
    call = call_frames(coverage="direct_original")
    report = projection([row(1, "provider.call_started", call[0]), row(2, "provider.call_finished", call[1])])
    assert report["selected"]["known_units"] == 0
    assert report["counts"]["summary_mismatches"] == 1


def test_invalid_child_kind_is_unknown_not_rebound_to_root_or_allowed_to_crash_projection():
    rows = transport_rows()
    rows.append(row(6, [], {"usage": usage()}, CHILD))
    report = projection(rows)
    assert report["selected"]["total_tokens"] == 10
    assert report["counts"]["invalid_frames"] == 1 and report["state"] == "partial"


def test_detached_projection_does_not_return_mutable_source_usage_or_details():
    rows = transport_rows()
    report = projection(rows)
    sample = next(item for item in report["samples"]["items"] if item["kind"] == "request")
    sample["usage"]["input_tokens"] = 99
    sample["usage_details"]["cache_state"] = "invalid"
    assert rows[2]["payload"]["usage"] == usage()
    assert rows[2]["payload"]["usage_details"] == empty_usage_details()


def test_missing_first_ordinal_preserves_observation_but_cannot_prove_complete_scalar_prefix():
    rows = transport_rows()
    rows[1]["payload"]["attempt_index"] = rows[2]["payload"]["attempt_index"] = 2
    report = projection(rows)
    assert report["selected"]["total_tokens"] == 10
    assert report["counts"]["request_ordinal_gaps"] == 1
    assert report["counts"]["summary_mismatches"] == 1 and report["counts"]["callbacks_matched"] == 0
    assert report["compatibility"]["known_units"] == 1 and report["state"] == "partial"


@pytest.mark.parametrize("outer", [None, [], "PRIVATE_ENVELOPE"])
def test_invalid_outer_envelope_cannot_silently_certify_known_prefix(outer):
    rows = transport_rows()
    rows.append({"seq": 6, "type": "subagent.runtime", "payload": outer})
    report = projection(rows)
    assert report["selected"]["total_tokens"] == 10 and report["state"] == "partial"
    assert report["counts"]["invalid_frames"] == 1 and "PRIVATE_" not in json.dumps(report)


def test_full_denominators_and_sample_callback_linkage_are_not_inferred_from_first16():
    rows = transport_rows()
    report = projection(rows)
    assert report["counts"]["call_returned"] == report["counts"]["request_returned"] == 1
    assert report["counts"]["call_method_entered"] == report["counts"]["request_method_entered"] == 1
    assert report["samples"]["items"][-1]["callback_linkage"] == "matched"
    assert report["samples"]["items"][0]["callback_linkage"] is None
    rows[-1]["payload"]["provider_call_id"] = "f" * 32
    assert projection(rows)["samples"]["items"][-1]["callback_linkage"] == "unmatched"
    report = projection(transport_rows(call_version=1))
    assert report["counts"]["historical_calls_with_requests"] == 1 and report["state"] == "partial"


def test_invalid_callback_scope_is_counted_separately_not_fake_root_compatibility():
    rows = transport_rows()
    rows.append(row(6, "graph.model_finished", {"usage": usage()}, CHILD))
    rows[-1]["payload"]["generation"] = False
    report = projection(rows)
    assert report["counts"]["callbacks_invalid_scope"] == 1 and report["counts"]["callbacks_unmatched"] == 1
    assert report["compatibility"]["known_units"] == report["compatibility"]["unknown_units"] == 0
