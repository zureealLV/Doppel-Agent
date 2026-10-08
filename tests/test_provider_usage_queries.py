"""B2b4a query definitions FIRST, ALL UNRUN; original disposable stores only."""

import json
import sqlite3

import pytest

from doppel_agent.persistence.events import EventStore
from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.reporting import queries
from doppel_agent.reporting.queries import RunReportQueries
from test_provider_usage_report import RUN, CHILD, transport_rows


def fixture(tmp_path, rows=None):
    database = tmp_path / "runtime.sqlite3"
    runs = RuntimeRunStore(database)
    runs.create(RUN, "PRIVATE_THREAD", "graph", {"prompt": "PRIVATE_PROMPT"}, None)
    runs.update(RUN, "completed")
    store = EventStore(database)
    for event in transport_rows() if rows is None else rows:
        store.append(RUN, "PRIVATE_THREAD", event["type"], event["payload"])
    return database, RunReportQueries(database, tmp_path / "missing-ledger.sqlite3")


def test_default_v1_v2_compatibility_unchanged_draft_v3_explicit_only(tmp_path, monkeypatch):
    _database, reader = fixture(tmp_path)
    from doppel_agent.reporting.evidence import ReportEvidenceQueries
    monkeypatch.setattr(ReportEvidenceQueries, "read", lambda *_: {"PRIVATE_PHASE_FIXTURE": True})
    old = reader.read(RUN)
    assert old["version"] == 1 and "provider_usage" not in old
    assert old["usage"]["observed"]["input_tokens"] == 7
    assert reader.read_full(RUN)["version"] == 2
    draft = reader.read_full(RUN, provider_usage=True)
    assert draft["version"] == 3 and draft["usage_basis"] == "provider_usage_only_not_compat_model_events"
    assert draft["provider_usage"]["selected"]["total_tokens"] == 10
    assert draft["provider_usage"]["counts"]["callbacks_matched"] == 1
    # Existing compatibility event subtotal retained, not summed into selected.
    assert draft["usage"]["observed"]["input_tokens"] == 7


def test_new_receipt_rows_are_selected_inside_same_original_read_transaction(tmp_path, monkeypatch):
    database, reader = fixture(tmp_path)
    original = queries.build_provider_usage
    def project(run_id, rows, **kwargs):
        # Happens after query collected rows, while original read snapshot held.
        with sqlite3.connect(database) as writer:
            writer.execute("DELETE FROM runtime_events WHERE run_id=?", (RUN,))
        assert len(rows) == 5
        return original(run_id, rows, **kwargs)
    monkeypatch.setattr(queries, "build_provider_usage", project)
    report = reader.read(RUN, provider_usage=True)
    assert report["snapshot"]["event_total"] == report["snapshot"]["scanned_events"] == 5
    assert report["provider_usage"]["selected"]["total_tokens"] == 10
    assert report["snapshot"]["consistency"] == "same_runtime_database_read_transaction"


@pytest.mark.parametrize("bad", [
    '', '{"version":2,"version":1}', '{"private":NaN}', '{"private":1e999}', '[1,2]',
    '{"private":"' + "x" * 65536 + '"}',
], ids=["empty", "duplicate", "nan", "infinity", "nonobject", "oversized"])
def test_invalid_oversized_or_nonobject_frame_retains_known_prefix_and_omission(tmp_path, bad):
    rows = transport_rows()
    rows.append({"seq": 6, "type": "provider.request_finished", "payload": {}})
    database, reader = fixture(tmp_path, rows)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE runtime_events SET payload_json=? WHERE run_id=? AND seq=(SELECT MAX(seq) FROM runtime_events WHERE run_id=?)",
                   (bad, RUN, RUN))
    report = reader.read(RUN, provider_usage=True)
    assert report["provider_usage"]["selected"]["total_tokens"] == 10
    assert report["snapshot"]["omitted_usage_rows"] == 1
    assert report["provider_usage"]["counts"]["unclassified_rows"] == 1
    assert report["provider_usage"]["state"] == "partial" and "private" not in json.dumps(report)


def test_decode_budget_before_fetching_body_and_future_event_limit_are_not_zero(tmp_path, monkeypatch):
    _database, reader = fixture(tmp_path)
    monkeypatch.setattr(queries, "MAX_DECODE", 1)
    report = reader.read(RUN, provider_usage=True)
    assert report["query"]["decoded_bytes"] == 0
    assert report["snapshot"]["omitted_usage_rows"] == 5
    assert report["provider_usage"]["selected"]["input_tokens"] is None
    assert report["provider_usage"]["state"] == "unknown"


def test_unhashable_nested_kind_does_not_discard_other_known_original_rows(tmp_path):
    rows = transport_rows()
    rows.append({"seq": 6, "type": "subagent.runtime", "payload": {"parent_run_id": RUN,
        "subagent_id": CHILD, "generation": 2, "runtime_kind": [], "runtime_payload": {}}})
    _database, reader = fixture(tmp_path, rows)
    report = reader.read(RUN, provider_usage=True)
    assert report["provider_usage"]["selected"]["total_tokens"] == 10
    assert report["provider_usage"]["counts"]["invalid_frames"] == 1


def test_missing_database_scope_or_ledger_never_creates_or_admits(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(ValueError, match="report_evidence_unavailable"):
        RunReportQueries(missing).read(RUN, provider_usage=True)
    assert not missing.exists()
    _database, reader = fixture(tmp_path)
    with pytest.raises(KeyError, match="report_scope_not_found"):
        reader.read("f" * 32, provider_usage=True)
    report = reader.read_full(RUN, provider_usage=True)
    assert report["evidence"]["patches"]["state"] == "unknown"
    assert not reader.ledger_database.exists()
    with pytest.raises(ValueError, match="report_evidence_unavailable"):
        RunReportQueries(reader.database).read_full(RUN, provider_usage=True)


@pytest.mark.parametrize("bad", [None, 1, 0, "PRIVATE_FLAG", [], {}])
def test_internal_flag_is_strict_boolean_before_any_original_sql_io(tmp_path, monkeypatch, bad):
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid draft flag reached SQL/path read")
    monkeypatch.setattr(queries, "_safe_database", forbidden)
    reader = RunReportQueries(tmp_path / "missing.sqlite3")
    for method in (reader.read, reader.read_full):
        with pytest.raises(ValueError, match="report_evidence_unavailable"):
            method(RUN, provider_usage=bad)


def test_original_body_reads_remain_frozen_after_independent_writer_updates(tmp_path, monkeypatch):
    database, reader = fixture(tmp_path)
    connect = sqlite3.connect
    statements = []
    class SnapshotConnection:
        def __init__(self, inner):
            self.inner = inner
        @property
        def row_factory(self):
            return self.inner.row_factory
        @row_factory.setter
        def row_factory(self, value):
            self.inner.row_factory = value
        def set_progress_handler(self, *args):
            return self.inner.set_progress_handler(*args)
        def execute(self, sql, *args):
            statements.append(sql)
            cursor = self.inner.execute(sql, *args)
            if sql.startswith("SELECT COUNT(*)"):
                with connect(database) as writer:
                    writer.execute("UPDATE runtime_events SET payload_json='{}' WHERE run_id=?", (RUN,))
            return cursor
        def close(self):
            self.inner.close()
    def readonly(*args, **kwargs):
        assert "mode=ro" in args[0] and kwargs["uri"] is True
        return SnapshotConnection(connect(*args, **kwargs))
    monkeypatch.setattr(queries.sqlite3, "connect", readonly)
    report = reader.read(RUN, provider_usage=True)
    assert report["provider_usage"]["selected"]["total_tokens"] == 10
    assert sum(sql.startswith("SELECT payload_json FROM") for sql in statements) == 5
    assert statements.count("BEGIN") == 1


def test_metadata_budget_omission_never_fetches_selected_bodies(tmp_path, monkeypatch):
    _database, reader = fixture(tmp_path)
    connect = sqlite3.connect
    statements = []
    def traced(*args, **kwargs):
        inner = connect(*args, **kwargs)
        inner.set_trace_callback(statements.append)
        return inner
    monkeypatch.setattr(queries.sqlite3, "connect", traced)
    monkeypatch.setattr(queries, "MAX_DECODE", 1)
    report = reader.read(RUN, provider_usage=True)
    assert report["snapshot"]["omitted_usage_rows"] == 5
    assert not any(sql.startswith("SELECT payload_json FROM") for sql in statements)
