"""FIRST original same-transaction pricing query definitions, ALL UNRUN."""

import json
import sqlite3

import pytest

from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.events import EventStore
from doppel_agent.reporting.queries import RunReportQueries
from doppel_agent.reporting import queries
from test_billing_tariff import PROFILE
from test_billing_tariff_recording import price, priced_rows
from test_provider_usage_report import RUN


def fixture(tmp_path):
    database = tmp_path / "runtime.sqlite3"
    store = RuntimeRunStore(database)
    store.create(
        RUN,
        "fixture-thread",
        "graph",
        {"prompt": "offline"},
        None,
        profile_snapshot={**PROFILE, "billing_price_receipt": price()},
    )
    store.update(RUN, "completed")
    events = EventStore(database)
    for row in priced_rows():
        events.append(RUN, "fixture-thread", row["type"], row["payload"])
    return database, RunReportQueries(database)


def test_pricing_internal_opt_in_v4_preserves_existing_v1_and_v3_readers(tmp_path):
    _, reader = fixture(tmp_path)
    assert reader.read(RUN)["version"] == 1
    assert reader.read(RUN, provider_usage=True)["version"] == 3
    report = reader.read(RUN, provider_usage=True, billing=True)
    assert report["version"] == 4 and report["cost"]["known_units"] == 1
    assert report["cost"]["currencies"][0]["amount"] == "0.00001625"
    assert report["query"]["decoded_bytes"] > 0
    assert "fixture-model" not in json.dumps(report) and "source_reference" not in json.dumps(report)


def test_full4_original_independent_evidence_phase_preserves_default2_and_source_scope(tmp_path):
    database, _ = fixture(tmp_path)
    missing = tmp_path / "missing-ledger.sqlite3"
    reader = RunReportQueries(database, missing)
    assert reader.read_full(RUN)["version"] == 2
    report = reader.read_full(RUN, provider_usage=True, billing=True)
    assert report["version"] == 4 and report["cost"]["scopes"]["root"]["known_units"] == 1
    assert report["cost"]["scopes"]["root"]["currencies"] == report["cost"]["currencies"]
    assert (
        report["evidence"]["snapshot_consistency"]
        == "independent_read_transactions_not_atomic_with_event_snapshot"
    )
    assert report["evidence"]["patches"]["state"] == "unknown" and not missing.exists()
    assert (
        report["redaction"]["policy"]
        == "allowlisted_identifiers_enums_numeric_counters_and_tariff_contract_only"
    )


def test_original_profile_and_events_are_same_read_transaction_not_latest_profile_reconstruction(
    tmp_path, monkeypatch
):
    database, reader = fixture(tmp_path)
    original = queries.build_provider_usage

    def project(*args, **kwargs):
        with sqlite3.connect(database) as writer:
            writer.execute(
                "UPDATE runtime_runs SET profile_json=? WHERE run_id=?",
                (json.dumps({**PROFILE, "billing_price_receipt": price("USD")}), RUN),
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(queries, "build_provider_usage", project)
    report = reader.read(RUN, provider_usage=True, billing=True)
    assert report["cost"]["currencies"][0]["currency"] == "CNY"
    assert report["cost"]["known_units"] == 1


def test_original_source_body_budget_reserved_before_loading_and_omission_never_free(tmp_path, monkeypatch):
    database, reader = fixture(tmp_path)
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE runtime_runs SET profile_json=? WHERE run_id=?",
            ('{"private":"' + "x" * 65536 + '"}', RUN),
        )
    report = reader.read(RUN, provider_usage=True, billing=True)
    assert report["provider_usage"]["selected"]["known_units"] == 1
    assert report["cost"]["source_state"] == "omitted" and report["cost"]["unknown_units"] == 1
    monkeypatch.setattr(queries, "MAX_DECODE", 1)
    empty = reader.read(RUN, provider_usage=True, billing=True)
    assert empty["query"]["decoded_bytes"] == 0 and empty["cost"]["source_state"] == "omitted"
    assert empty["cost"]["currencies"] == []


@pytest.mark.parametrize(
    "raw", ['{"billing_price_receipt":NaN}', '{"version":1,"version":2}', "[]", "{", '{"x":1e999}']
)
def test_invalid_source_json_is_cost_unknown_without_erasing_known_usage(tmp_path, raw):
    database, reader = fixture(tmp_path)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE runtime_runs SET profile_json=? WHERE run_id=?", (raw, RUN))
    report = reader.read(RUN, provider_usage=True, billing=True)
    assert report["cost"]["source_state"] == "invalid" and report["cost"]["known_units"] == 0
    assert report["provider_usage"]["selected"]["known_units"] == 1


def test_invalid_pricing_flags_refused_before_database_access(monkeypatch):
    def forbidden(*_args):
        pytest.fail("invalid flags reached database IO")

    monkeypatch.setattr(queries, "_safe_database", forbidden)
    reader = RunReportQueries("unused")
    for billing, provider_usage in [(True, False), (1, True), ([], True), (True, 1)]:
        with pytest.raises(ValueError, match="report_evidence_unavailable"):
            reader.read(RUN, provider_usage=provider_usage, billing=billing)
        with pytest.raises(ValueError, match="report_evidence_unavailable"):
            reader.read_full(RUN, provider_usage=provider_usage, billing=billing)
