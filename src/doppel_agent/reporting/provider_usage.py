"""Bounded, pure original-journal projection; NOT billing or admission authority.

Request observations take precedence over logical-call observations. Callback
markers alone never remove compatibility evidence. Globally ambiguous identities
are reserved and refused, including malformed or foreign-scope frames. No IO,
live pricing, profile reconstruction, response bodies or private strings exported.
Explicit v3 tariff frames are validated and compared internally, not exposed as
cost/eligibility until the original closed cost projection is paired.
"""


from ..provider_observation import empty_usage_details
from ..billing_tariff import validate_price_receipt
from ..provider_receipts import _integer, _identifier, _enum, _equal, _frame, _group, _expected_summary
from ..provider_usage import MAX_SAFE, parse_usage


CALL_START = "provider.call_started"
CALL_FINISH = "provider.call_finished"
REQUEST_START = "provider.request_started"
REQUEST_FINISH = "provider.request_finished"
RECEIPT_KINDS = frozenset({CALL_START, CALL_FINISH, REQUEST_START, REQUEST_FINISH})
CALLBACK_KINDS = frozenset({"graph.model_finished", "deep.model_finished", "provider.model_finished"})
TRANSPORT_BOUNDARY = "admitted_transport_method_not_wire_request"
LIMIT = 16
MAX_EVENTS = 5000


def _subtotal(units):
    pairs = [unit["usage"] for unit in units if unit["usage"] is not None]
    inputs = sum(pair["input_tokens"] for pair in pairs)
    outputs = sum(pair["output_tokens"] for pair in pairs)
    overflow = inputs + outputs > MAX_SAFE
    return {"known_units": len(pairs), "unknown_units": len(units) - len(pairs),
            "request_units": sum(unit["kind"] == "request" for unit in units),
            "logical_call_units": sum(unit["kind"] == "logical_call" for unit in units),
            "input_tokens": inputs if pairs and not overflow else None,
            "output_tokens": outputs if pairs and not overflow else None,
            "total_tokens": inputs + outputs if pairs and not overflow else None,
            "subtotal_overflow": overflow}


def _sample(frame, kind):
    data = frame["data"]
    scope = frame["scope"]
    return {"kind": kind, "seq": frame["seq"], "receipt_version": data.get("version"),
            "call_id": data.get("call_id"), "attempt_id": data.get("attempt_id"),
            "attempt_index": data.get("attempt_index"), "engine": data.get("engine"),
            "actor": data.get("actor"),
            "scope": {"kind": "root"} if scope == () else {"kind": "child", "subagent_id": scope[0], "generation": scope[1]},
            "outcome": data.get("outcome"), "method_entered": data.get("method_entered"),
            "usage": data["usage"], "usage_details": data.get("usage_details", empty_usage_details()),
            "selected": False, "callback_linkage": None}


def build_provider_usage(run_id, events, *, omitted_usage_rows=0, truncated=False, pricing_units=None):
    """Selected observed prefix + separately retained unlinked compatibility.

    Caller supplies one original runtime read transaction, never a second journal
    query. Limits/truncation cannot certify absence of later collisions/events.
    Invalid frames reserve identities; they cannot erase ambiguity by failing
    decoding. Outer child tags only, NOT independently reconstructed ownership.
    """
    if (not _identifier(run_id) or type(events) is not list or len(events) > MAX_EVENTS
            or not _integer(omitted_usage_rows) or omitted_usage_rows > len(events) or type(truncated) is not bool):
        raise ValueError("report_provider_usage_invalid")
    counts = {key: 0 for key in (
        "call_rows", "request_rows", "call_ids", "calls_matched", "calls_unsettled", "call_invalid_ids",
        "call_duplicate_ids", "request_ids", "requests_matched", "requests_unsettled", "request_invalid_ids",
        "request_duplicate_ids", "requests_linked", "request_orphans", "request_ordinal_collisions", "request_ordinal_gaps",
        "summary_mismatches", "historical_calls_with_requests", "call_returned", "call_failed", "call_cancelled", "call_method_entered",
        "request_returned", "request_failed", "request_cancelled", "request_method_entered",
        "callbacks", "callbacks_matched", "callbacks_unmatched", "callbacks_invalid_usage", "callbacks_invalid_scope",
        "invalid_scope_frames", "invalid_frames", "unclassified_rows", "unselected_known_receipts")}
    counts["unclassified_rows"] = omitted_usage_rows
    calls, requests, ordinals, request_calls, callbacks, samples = {}, {}, {}, {}, [], []
    previous = 0
    for event in events:
        if type(event) is not dict or not _integer(event.get("seq"), positive=True) or event["seq"] <= previous:
            raise ValueError("report_provider_usage_invalid")
        previous = event["seq"]
        kind, payload, scope = event.get("type"), event.get("payload"), ()
        if kind == "subagent.runtime":
            outer = payload
            if type(outer) is not dict:
                counts["invalid_frames"] += 1
                continue  # query omissions have type=None; do not invent child/model counts
            scope = ((outer["subagent_id"], outer["generation"]) if outer.get("parent_run_id") == run_id
                     and _identifier(outer.get("subagent_id")) and _integer(outer.get("generation"), positive=True) else None)
            kind, payload = outer.get("runtime_kind"), outer.get("runtime_payload")
            if type(kind) is not str:
                counts["invalid_frames"] += 1
                continue
        if not _enum(kind, RECEIPT_KINDS | CALLBACK_KINDS):
            continue
        if scope is None:
            counts["invalid_scope_frames"] += 1
        if kind in CALLBACK_KINDS:
            counts["callbacks"] += 1
            if scope is None:
                counts["callbacks_unmatched"] += 1
                counts["callbacks_invalid_scope"] += 1
                continue
            raw = payload if type(payload) is dict else {}
            pair = parse_usage(raw.get("usage"))
            usage = None if pair is None else {"input_tokens": pair[0], "output_tokens": pair[1], "total_tokens": sum(pair)}
            if usage is None:
                counts["callbacks_invalid_usage"] += 1
            frame = {"seq": previous, "scope": scope, "data": {"usage": usage}}
            sample = _sample(frame, "compatibility_callback")
            callbacks.append((kind, raw.get("provider_call_id"), frame, sample))
            samples.append(sample)
            continue
        request = kind in {REQUEST_START, REQUEST_FINISH}
        finish = kind in {CALL_FINISH, REQUEST_FINISH}
        counts["request_rows" if request else "call_rows"] += 1
        raw = payload if type(payload) is dict else {}
        data = _frame(payload, request=request, finish=finish)
        if data is None:
            counts["invalid_frames"] += 1
        frame = {"seq": previous, "scope": scope, "finish": finish, "data": data}
        identity = raw.get("attempt_id" if request else "call_id")
        if _identifier(identity):
            (requests if request else calls).setdefault(identity, []).append(frame)
        else:
            counts["invalid_frames"] += int(data is not None)
        if request and _identifier(raw.get("call_id")):
            # A malformed request still prevents fallback to its call-only pair.
            request_calls.setdefault(raw["call_id"], set()).add(identity if _identifier(identity) else None)
            if _integer(raw.get("attempt_index"), positive=True):
                ordinals.setdefault((raw["call_id"], raw["attempt_index"]), set()).add(
                    identity if _identifier(identity) else None)
        if finish and data is not None and scope is not None:
            frame["sample"] = _sample(frame, "request" if request else "logical_call")
            samples.append(frame["sample"])

    call_groups = {key: _group(frames) for key, frames in calls.items()}
    request_groups = {key: _group(frames) for key, frames in requests.items()}
    for groups, prefix in ((call_groups, "call"), (request_groups, "request")):
        counts[prefix + "_ids"] = len(groups)
        for group in groups.values():
            state = group["state"]
            key = ("calls" if prefix == "call" else "requests") + "_" + state if state in {"matched", "unsettled"} else prefix + "_" + state + "_ids"
            counts[key] += 1
            if state == "matched":
                data = group["finish"]["data"]
                counts[prefix + "_" + data["outcome"]] += 1
                counts[prefix + "_method_entered"] += int(data["method_entered"])
    collisions = {ordinal for ordinal, identities in ordinals.items() if len(identities) != 1 or None in identities}
    counts["request_ordinal_collisions"] = len(collisions)
    selected, linked = [], set()
    for identity, group in request_groups.items():
        start, finish = group["start"], group["finish"]
        if group["state"] not in {"matched", "unsettled"}:
            counts["request_orphans"] += 1
            continue
        data = start["data"]
        call = call_groups.get(data["call_id"])
        valid = call is not None and call["state"] in {"matched", "unsettled"}
        if valid:
            parent = call["start"]
            valid = (parent["scope"] == start["scope"] and parent["seq"] < start["seq"]
                     and parent["data"]["engine"] == data["engine"] and parent["data"]["actor"] == data["actor"])
            if valid and (data["version"] == 3 or parent["data"]["version"] == 3):
                valid = (data["version"] == parent["data"]["version"] == 3
                         and _equal(data["price_receipt"], parent["data"]["price_receipt"]))
            end = call["finish"]
            if valid and end is not None:
                valid = (finish or start)["seq"] < end["seq"]
        if not valid or (data["call_id"], data["attempt_index"]) in collisions:
            counts["request_orphans"] += 1
            continue
        linked.add(identity)
        counts["requests_linked"] += 1
        usage = finish["data"]["usage"] if finish is not None else None
        selected.append({"kind": "request", "scope": start["scope"], "usage": usage,
                         "call_id": data["call_id"], "attempt_id": identity})
        if finish is not None:
            finish["sample"]["selected"] = True

    suppressible, consistent_calls = {}, set()
    for identity, group in call_groups.items():
        if group["state"] not in {"matched", "unsettled"}:
            continue
        start, finish = group["start"], group["finish"]
        related = request_calls.get(identity, set())
        consistent = True
        if finish is not None:
            data = finish["data"]
            if data["version"] in (2, 3):
                transport = data["transport"]
                complete = all(item in linked and request_groups[item]["state"] == "matched" for item in related)
                observed = [request_groups[item] for item in related if item in linked and request_groups[item]["state"] == "matched"]
                indexes = sorted(item["start"]["data"]["attempt_index"] for item in observed)
                contiguous = all(index == offset for offset, index in enumerate(indexes, 1))
                if not contiguous:
                    counts["request_ordinal_gaps"] += 1
                consistent = (complete and contiguous and (not related or data["method_entered"])
                              and _equal(transport, _expected_summary(observed, transport["coverage"])))
                if not related and transport["coverage"] == "direct_original" and data["outcome"] == "returned":
                    consistent = False  # no bounded-response provenance, even if scalar intent is zero
                if consistent and related and data["outcome"] == "returned":
                    last = max(observed, key=lambda item: item["finish"]["seq"])["finish"]["data"]
                    consistent = _equal(data["usage"], last["usage"])
                if not consistent:
                    counts["summary_mismatches"] += 1
            elif related:
                consistent = False  # v1 has no independently comparable request summary
                counts["historical_calls_with_requests"] += 1
        if not related:
            # Scalar positive intent cannot manufacture absent request receipts.
            if finish is None or consistent:
                selected.append({"kind": "logical_call", "scope": start["scope"],
                                 "call_id": identity,
                                 "usage": finish["data"]["usage"] if finish is not None else None})
                if finish is not None:
                    finish["sample"]["selected"] = True
        if finish is not None and consistent:
            consistent_calls.add(identity)
        if finish is not None and consistent and finish["data"]["outcome"] == "returned" and finish["data"]["usage"] is not None:
            suppressible[identity] = finish

    if pricing_units is not None:
        # INTERNAL collector, SAME original selection/link/summary decisions.
        # Not samples or another reconstruction. Never part of numeric report
        # output, no raw response/profile/provider lookup or cost side effect.
        if type(pricing_units) is not list or pricing_units:
            raise ValueError("report_cost_unavailable")
        for unit in selected:
            call = call_groups[unit["call_id"]]
            start, finish = call["start"]["data"], call["finish"]
            tariff = start.get("price_receipt")
            sealed = finish["data"] if finish is not None else None
            eligible = unit["call_id"] in consistent_calls and start["version"] == 3
            details = empty_usage_details()
            if unit["kind"] == "request":
                response = request_groups[unit["attempt_id"]]["finish"]
                data = response["data"] if response is not None else None
                eligible = bool(eligible and sealed["transport"]["coverage"] == "direct_original"
                                and data is not None and data["method_entered"]
                                and data["response_state"] == "bounded_body")
                if data is not None:
                    details = data["usage_details"]
            else:
                eligible = bool(eligible and sealed["transport"]["coverage"] == "opaque"
                                and sealed["outcome"] == "returned" and sealed["method_entered"]
                                and sealed["usage_basis"] == "returned_model_turn")
            pricing_units.append({"kind": unit["kind"], "scope": unit["scope"],
                "usage": None if unit["usage"] is None else dict(unit["usage"]),
                "usage_details": dict(details), "source_eligible": eligible,
                "price_receipt": None if tariff is None else validate_price_receipt(tariff)})

    compatibility = []
    for kind, marker, frame, sample in callbacks:
        call = suppressible.get(marker) if _identifier(marker) else None
        matched = (call is not None and call["scope"] == frame["scope"] and call["seq"] < frame["seq"]
                   and kind == call["data"]["engine"] + ".model_finished"
                   and frame["data"]["usage"] is not None and _equal(call["data"]["usage"], frame["data"]["usage"]))
        counts["callbacks_matched" if matched else "callbacks_unmatched"] += 1
        if not matched:
            compatibility.append({"kind": "compatibility_callback", "scope": frame["scope"], "usage": frame["data"]["usage"]})
        # Callback IDs, including valid-looking forged markers, never exported.
        sample["selected"] = False
        sample["callback_linkage"] = "matched" if matched else "unmatched"
    counts["unselected_known_receipts"] = sum(sample["kind"] != "compatibility_callback" and not sample["selected"]
                                              and sample["usage"] is not None for sample in samples)
    root, children = [], {}
    for unit in selected:
        if unit["scope"] == ():
            root.append(unit)
        else:
            children.setdefault(unit["scope"], []).append(unit)
    child_scopes = sorted(children)
    other = [unit for scope in child_scopes[LIMIT:] for unit in children[scope]]
    samples.sort(key=lambda item: item["seq"])
    subtotal = _subtotal(selected)
    uncertain = (truncated or omitted_usage_rows or subtotal["unknown_units"] or subtotal["subtotal_overflow"]
                 or any(counts[key] for key in ("calls_unsettled", "call_invalid_ids", "call_duplicate_ids",
                     "requests_unsettled", "request_invalid_ids", "request_duplicate_ids", "request_orphans",
                     "request_ordinal_collisions", "request_ordinal_gaps", "summary_mismatches", "callbacks_unmatched", "invalid_scope_frames", "invalid_frames"))
                 or counts["historical_calls_with_requests"] > 0)
    return {"version": 1, "policy": "linked_requests_else_logical_calls_no_callback_addition",
            "state": "unknown" if not subtotal["known_units"] else "partial" if uncertain else "known_for_selected_receipts",
            "selected": subtotal, "compatibility": _subtotal(compatibility), "counts": counts,
            "scopes": {"basis": "recorded_outer_tags_not_reconstructed", "root": _subtotal(root),
                "children_total": len(children), "children": [{"subagent_id": scope[0], "generation": scope[1],
                    **_subtotal(children[scope])} for scope in child_scopes[:LIMIT]],
                "children_omitted": max(0, len(children) - LIMIT), "other_children": _subtotal(other),
                "limit": LIMIT, "truncated": len(children) > LIMIT},
            "samples": {"total": len(samples), "emitted": min(len(samples), LIMIT), "omitted": max(0, len(samples) - LIMIT),
                        "limit": LIMIT, "truncated": len(samples) > LIMIT, "items": samples[:LIMIT]},
            "billing_complete": False, "account_cap_guaranteed": False, "wire_request_coverage": "unknown"}
