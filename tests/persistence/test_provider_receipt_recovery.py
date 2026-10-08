"""C2a FIRST definitions, ALL UNRUN. Disposable original SQL, no native proof.

These define an ORIGINAL journal recovery check, not report/sample admission,
automatic replay, provider IO, invoice reconstruction or physical resource drain.
"""

import pytest

from doppel_agent.billing_tariff import freeze_tariff

from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.events import EventStore
from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.provider_recovery import ProviderReceiptRecoveryQueries
from doppel_agent.runtime.async_subagents import AsyncSubagentStore

RUN, THREAD, CHILD, CALL, ATTEMPT = ("a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32)


def call(version=2):
    return dict(
        version=version,
        call_id=CALL,
        engine="graph",
        actor="runtime_model",
        path="native_async",
        boundary="provider_method_not_transport_attempt",
    )


def finished(start=None, *, requests=False):
    value = dict(
        start or call(),
        outcome="returned",
        method_entered=True,
        usage=None,
        usage_state="unknown",
        failure=None,
        billing_complete=False,
        account_cap_guaranteed=False,
        network_attempts=None,
        price_receipt=None,
    )
    if value["version"] != 1:
        value.update(
            usage_basis="original_bounded_response" if requests else "returned_model_turn",
            network_attempts=1 if requests else None,
            network_attempts_basis="admitted_transport_method_not_wire_request" if requests else None,
            transport=dict(
                coverage="direct_original" if requests else "opaque",
                boundary="admitted_transport_method_not_wire_request",
                intents=int(requests),
                entered=int(requests),
                sealed=int(requests),
                unsettled=0,
                returned=int(requests),
                failed=0,
                cancelled=0,
                known_usage=0,
                unknown_usage=int(requests),
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                overflow=False,
                subtotal_state="unknown",
            ),
        )
    return value


def request():
    return dict(
        version=2,
        call_id=CALL,
        attempt_id=ATTEMPT,
        attempt_index=1,
        engine="graph",
        actor="runtime_model",
        transport="httpx_post",
        boundary="admitted_transport_method_not_wire_request",
    )


def request_finished():
    return dict(
        request(),
        outcome="returned",
        method_entered=True,
        failure=None,
        usage=None,
        usage_state="unknown",
        usage_details=dict(
            version=1,
            cached_input_tokens=None,
            uncached_input_tokens=None,
            reasoning_output_tokens=None,
            cache_state="unknown",
            reasoning_state="unknown",
        ),
        http_status=200,
        response_state="bounded_body",
        price_receipt=None,
        billing_complete=False,
        account_cap_guaranteed=False,
    )


@pytest.fixture
def journal(tmp_path):
    database = tmp_path / "runtime.sqlite3"
    store = RuntimeRunStore(database)
    store.create(RUN, THREAD, "graph", dict(prompt="private fixture", mode="graph"), None)
    events = EventStore(database)
    children = AsyncSubagentStore(database)
    children.create_bounded(CHILD, RUN, "private child prompt", 4)
    return database, events


def append(events, kind, payload, *, child=False, generation=1):
    if child:
        return events.append(
            RUN,
            THREAD,
            "subagent.runtime",
            dict(
                parent_run_id=RUN,
                subagent_id=CHILD,
                generation=generation,
                runtime_kind=kind,
                runtime_payload=payload,
            ),
        )
    return events.append(RUN, THREAD, kind, payload)


def read(journal):
    return ProviderReceiptRecoveryQueries(journal[0]).read()


def test_empty_journal_is_no_unresolved_receipt_found_not_zero_effect_or_billing_proof(journal):
    result = read(journal)
    assert not result.quarantined and result.reason == "no_unresolved_receipts_found"
    assert result.receipt_rows == 0
    assert set(result.__dict__) == {"quarantined", "reason", "receipt_rows", "last_seq"}


@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("version", [1, 2])
def test_original_root_or_registered_child_complete_pair_is_not_replayed(journal, child, version):
    append(journal[1], "provider.call_started", call(version), child=child)
    append(journal[1], "provider.call_finished", finished(call(version)), child=child)
    assert not read(journal).quarantined
    assert read(journal).receipt_rows == 2


@pytest.mark.parametrize("child", [False, True])
def test_started_no_finish_stays_unknown_across_recreated_reader_and_terminal_run_metadata(journal, child):
    append(journal[1], "provider.call_started", call(), child=child)
    with sqlite_connection(journal[0]) as connection:
        connection.execute("UPDATE runtime_runs SET status='completed',lease_active=0 WHERE run_id=?", (RUN,))
    assert read(journal).quarantined
    assert ProviderReceiptRecoveryQueries(journal[0]).read().reason == "unresolved_receipts"


@pytest.mark.parametrize(
    "shape", ["finish_only", "duplicate", "reversed", "scope_mismatch", "generation_unknown"]
)
def test_ambiguous_identity_order_and_registered_scope_never_authorize_restart(journal, shape):
    e = journal[1]
    if shape == "finish_only":
        append(e, "provider.call_finished", finished())
    elif shape == "reversed":
        append(e, "provider.call_finished", finished())
        append(e, "provider.call_started", call())
    else:
        append(e, "provider.call_started", call())
        if shape == "duplicate":
            append(e, "provider.call_started", call())
        append(
            e,
            "provider.call_finished",
            finished(),
            child=shape != "duplicate",
            generation=2 if shape == "generation_unknown" else 1,
        )
    assert read(journal).quarantined


def test_prior_registered_child_generation_is_kept_not_rebound_to_current_generation(journal):
    append(journal[1], "provider.call_started", call(), child=True)
    append(journal[1], "provider.call_finished", finished(), child=True)
    with sqlite_connection(journal[0]) as connection:
        connection.execute("UPDATE async_subagents SET generation=2 WHERE subagent_id=?", (CHILD,))
    assert not read(journal).quarantined


def test_original_complete_request_pair_requires_parent_pair_and_exact_transport_summary(journal):
    e = journal[1]
    append(e, "provider.call_started", call())
    append(e, "provider.request_started", request())
    append(e, "provider.request_finished", request_finished())
    append(e, "provider.call_finished", finished(requests=True))
    assert not read(journal).quarantined


@pytest.mark.parametrize("fault", ["no_request_finish", "summary_mismatch", "orphan", "cleanup_failure"])
def test_unclosed_attempt_or_false_parent_summary_is_not_logical_success(journal, fault):
    e = journal[1]
    if fault != "orphan":
        append(e, "provider.call_started", call())
    append(e, "provider.request_started", request())
    if fault != "no_request_finish":
        value = request_finished()
        if fault == "cleanup_failure":
            value.update(outcome="failed", failure="response_cleanup")
        append(e, "provider.request_finished", value)
    value = finished(requests=True)
    if fault == "summary_mismatch":
        value["transport"]["entered"] = 0
    append(e, "provider.call_finished", value)
    assert read(journal).quarantined


@pytest.mark.parametrize(
    "payload",
    ['{"version":2,"version":1}', '{"x":NaN}', "[]", "x" * 65537],
    ids=["duplicate-version", "nonfinite", "nonobject", "oversized"],
)
def test_malformed_duplicate_nonfinite_or_oversized_original_carrier_is_unknown(journal, payload):
    with sqlite_connection(journal[0]) as connection:
        connection.execute(
            "INSERT INTO runtime_events(run_id,thread_id,type,timestamp,payload_json) VALUES(?,?,?,?,?)",
            (RUN, THREAD, "provider.call_started", "fixture", payload),
        )
    assert read(journal).quarantined


def test_unrelated_private_root_events_are_not_decoded_or_returned(journal):
    journal[1].append(RUN, THREAD, "tool.finished", {"private": "SECRET" * 20000})
    result = read(journal)
    assert not result.quarantined and result.receipt_rows == 0
    assert "SECRET" not in str(result)


def test_closed_pair_identity_including_tariff_cannot_be_mutated_into_a_match(journal):
    start = call()
    end = finished()
    end["engine"] = "deep"
    append(journal[1], "provider.call_started", start)
    append(journal[1], "provider.call_finished", end)
    assert read(journal).quarantined


def test_matched_receipt_engine_must_bind_original_registered_root_mode(journal):
    start = call()
    start["engine"] = "deep"
    append(journal[1], "provider.call_started", start)
    append(journal[1], "provider.call_finished", finished(start))
    assert read(journal).reason == "scope_unavailable"


@pytest.mark.parametrize("fault", [
    "none", "missing-marker", "late-marker", "foreign-thread", "wrong-registered-mode",
    "wrong-runtime", "malformed-marker", "child-marker", "unclosed-call", "unclosed-attempt",
    "false-summary",
])
def test_deep_fallback_requires_prior_original_scope_and_still_closed_transport(journal, fault):
    database, events = journal
    with sqlite_connection(database) as db:
        db.execute("UPDATE runtime_runs SET mode='deep' WHERE run_id=?", (RUN,))
    marker = dict(runtime="graph", error="ValueError: deep execution failed")
    if fault == "wrong-registered-mode":
        with sqlite_connection(database) as db:
            db.execute("UPDATE runtime_runs SET mode='graph' WHERE run_id=?", (RUN,))
    if fault == "wrong-runtime":
        marker["runtime"] = "deep"
    if fault == "malformed-marker":
        marker["error"] = "private arbitrary diagnostic"
    if fault == "foreign-thread":
        events.append(RUN, "f" * 32, "deep.fallback", marker)
    elif fault not in {"missing-marker", "late-marker"}:
        append(events, "deep.fallback", marker, child=fault == "child-marker")
    append(events, "provider.call_started", call())
    append(events, "provider.request_started", request())
    if fault != "unclosed-attempt":
        append(events, "provider.request_finished", request_finished())
    if fault != "unclosed-call":
        end = finished(requests=True)
        if fault == "false-summary":
            end["transport"]["entered"] = 0
        append(events, "provider.call_finished", end)
    if fault == "late-marker":
        append(events, "deep.fallback", marker)
    result = read(journal)
    assert result.quarantined is (fault != "none")
    if fault == "none":
        assert result.receipt_rows == 4  # Marker is provenance, not metering.
    assert "private" not in str(result)


def test_unknown_after_more_than_sixteen_complete_pairs_is_not_hidden_by_report_sample_limits(journal):
    for index in range(1, 25):
        start = call()
        start["call_id"] = f"{index:032x}"
        append(journal[1], "provider.call_started", start)
        append(journal[1], "provider.call_finished", finished(start))
    start = call()
    start["call_id"] = "f" * 32
    append(journal[1], "provider.call_started", start)
    result = read(journal)
    assert result.quarantined and result.receipt_rows == 49


def test_v3_exact_original_frozen_contract_is_preserved_by_shared_codec_without_current_price_lookup(journal):
    profile = dict(id="fixture", provider="openai", model="fixture-model", base_url="http://127.0.0.1/v1")
    price = freeze_tariff(
        dict(
            version=1,
            currency="CNY",
            effective_date="2026-10-01",
            source_kind="offline_fixture",
            source_reference="PRIVATE_ONLY",
            unit_tokens=1000000,
            billing_basis="input_output_inclusive",
            reasoning_basis="included_in_output",
            rates=dict(input="1.25", output="2.5", cached_input=None, uncached_input=None),
        ),
        profile,
        freeze_date="2026-10-06",
    )
    start = dict(call(3), price_receipt=price)
    end = finished(start)
    end["price_receipt"] = price
    append(journal[1], "provider.call_started", start)
    append(journal[1], "provider.call_finished", end)
    result = read(journal)
    assert not result.quarantined and "PRIVATE_ONLY" not in str(result)


def test_shared_decode_budget_failure_is_not_empty_evidence(journal, monkeypatch):
    import doppel_agent.persistence.provider_recovery as recovery

    append(journal[1], "provider.call_started", call())
    append(journal[1], "provider.call_finished", finished())
    monkeypatch.setattr(recovery, "MAX_DECODE", 1)
    assert read(journal).reason == "evidence_limit"


def test_truncated_scan_never_treats_prefix_as_full_and_reader_does_not_modify_sql(journal, monkeypatch):
    import doppel_agent.persistence.provider_recovery as recovery

    append(journal[1], "provider.call_started", call())
    append(journal[1], "provider.call_finished", finished())
    monkeypatch.setattr(recovery, "MAX_ROWS", 1)
    assert read(journal).reason == "evidence_limit"
    with sqlite_connection(journal[0]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0] == 2


def test_missing_database_is_unknown_and_never_created(tmp_path):
    path = tmp_path / "absent" / "runtime.sqlite3"
    assert ProviderReceiptRecoveryQueries(path).read().reason == "evidence_unavailable"
    assert not path.exists() and not path.parent.exists()
