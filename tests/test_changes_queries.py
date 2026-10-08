"""D1a S9 definitions: existing-only patch/inverse queries, no command launch."""

import json
import sqlite3
from uuid import uuid4

import pytest

from doppel_agent.persistence import changes_queries
from doppel_agent.persistence.changes_queries import InverseEvidenceQueries, PatchEvidenceQueries
from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.inverse_reviews import InverseReviewStore
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.workspace.patching import PatchService


def fixture(tmp_path):
    root = tmp_path / "private"
    ledger = ToolExecutionLedger(root / "tool-executions.sqlite3")
    runtime = root / "runtime.sqlite3"
    run, other = uuid4().hex, uuid4().hex
    with sqlite_connection(runtime) as db:
        db.execute("CREATE TABLE runtime_runs(run_id TEXT PRIMARY KEY,status TEXT,lease_active INTEGER)")
        db.executemany("INSERT INTO runtime_runs VALUES(?,'completed',0)", [(run,), (other,)])
    (tmp_path / "file.txt").write_text("private before fixture", encoding="utf-8")
    patch = PatchService(tmp_path)
    proposal = patch.prepare([{"path": "file.txt", "content": "fixture after"}])
    ledger.execute_patch_once(run, "source", proposal.as_dict(), patch, proposal)
    return ledger, PatchEvidenceQueries(ledger.database, runtime), run, other, patch


@pytest.mark.parametrize("reader_type", [PatchEvidenceQueries, InverseEvidenceQueries])
def test_missing_existing_query_never_constructs_metadata(tmp_path, reader_type):
    root = tmp_path / "absent"
    reader = reader_type(root / "tool-executions.sqlite3", root / "runtime.sqlite3")
    with pytest.raises(ValueError, match="query_unavailable"):
        reader.detail(uuid4().hex, uuid4().hex)
    assert not root.exists()


def test_scoped_patch_queries_frame_actual_result_without_preimage_export_or_constructors(tmp_path, monkeypatch):
    ledger, reader, run, other, _ = fixture(tmp_path)
    monkeypatch.setattr(ToolExecutionLedger, "__init__", lambda *a, **k: pytest.fail("writable constructor"))
    monkeypatch.setattr(InverseReviewStore, "__init__", lambda *a, **k: pytest.fail("inverse constructor"))
    detail = reader.detail(run, "source")
    page = reader.list(run)
    assert detail["confirmed_applied"] and page["items"][0]["confirmed_applied"]
    assert detail["result"]["patch_receipt"] == page["items"][0]["receipt"]
    assert detail["output"]["sensitive"] and not detail["output"]["globally_redacted"]
    assert not detail["output"]["accepted_preimage_blobs_exported"]
    assert "content" not in json.dumps(detail["receipt"])
    assert "result" not in page["items"][0] and "unified_diff" not in page["items"][0]
    assert reader.list(other)["items"] == []
    with pytest.raises(KeyError):
        reader.detail(other, "source")
    with pytest.raises(KeyError):
        reader.list(uuid4().hex)
    with sqlite3.connect(ledger.database.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='inverse_patch_reviews'").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["row", "page"])
def test_patch_payload_budget_is_checked_before_content_fetch(tmp_path, monkeypatch, mode):
    ledger, reader, run, _, _ = fixture(tmp_path)
    if mode == "row":
        with sqlite_connection(ledger.database) as db:
            db.execute("UPDATE tool_executions SET result=?", ("x" * (changes_queries.MAX_BLOB + 1),))
    else:
        monkeypatch.setattr(changes_queries, "MAX_PAGE_DECODE", 0)
    monkeypatch.setattr(PatchEvidenceQueries, "_row", staticmethod(lambda *a: pytest.fail("payload fetched before budget")))
    with pytest.raises(ValueError, match="unavailable|budget"):
        reader.list(run)


@pytest.mark.parametrize("column", ["receipt_json", "summary_json", "result"])
@pytest.mark.parametrize("corruption", ["duplicate", "nonfinite"])
def test_patch_stored_ambiguous_json_is_not_confirmed_effect(tmp_path, column, corruption):
    ledger, reader, run, _, _ = fixture(tmp_path)
    table = "tool_executions" if column == "result" else "patch_effect_receipts"
    with sqlite_connection(ledger.database) as db:
        raw = db.execute(f"SELECT {column} FROM {table}").fetchone()[0]
        if column == "summary_json":
            # Historical summaries are consumed once preimages have expired.
            from datetime import UTC, datetime

            db.execute("UPDATE patch_effect_receipts SET receipt_json='',preimage_expired_at=?", (datetime.now(UTC).isoformat(),))
        member = ('"patch_id":"ignored duplicate",' if corruption == "duplicate" else '"invalid_fixture":NaN,')
        db.execute(f"UPDATE {table} SET {column}=?", ("{" + member + raw[1:],))
    with pytest.raises(ValueError, match="unavailable"):
        reader.detail(run, "source")
    with pytest.raises(ValueError, match="unavailable"):
        reader.list(run)


def test_inverse_query_missing_table_does_not_create_it_and_preserves_other_evidence(tmp_path):
    ledger, reader, run, _, _ = fixture(tmp_path)
    inverse = InverseEvidenceQueries(ledger.database, reader.runtime_database)
    with pytest.raises(ValueError, match="query_unavailable"):
        inverse.detail(run, uuid4().hex)
    assert reader.detail(run, "source")["confirmed_applied"]
    with sqlite3.connect(ledger.database.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='inverse_patch_reviews'").fetchone()[0] == 0


@pytest.mark.parametrize("column", ["proposal_json", "review_json"])
def test_inverse_pending_duplicate_json_is_unavailable_without_expiry_mutation(tmp_path, column):
    ledger, patch_reader, run, other, patch = fixture(tmp_path)
    receipt = ledger.read_applied_patch(run, "source")
    proposal = patch.prepare_inverse(receipt)
    store = InverseReviewStore(ledger.database)
    review = uuid4().hex
    store.save(run, "source", receipt.patch_id, review, proposal)
    with sqlite_connection(ledger.database) as db:
        raw = db.execute(f"SELECT {column} FROM inverse_patch_reviews").fetchone()[0]
        member = '"patch_id":"ignored",' if column == "proposal_json" else '"unified_diff":"ignored",'
        db.execute(f"UPDATE inverse_patch_reviews SET {column}=?", ("{" + member + raw[1:],))
    reader = InverseEvidenceQueries(ledger.database, patch_reader.runtime_database)
    with pytest.raises(ValueError, match="unavailable"):
        reader.detail(run, review)
    with pytest.raises(ValueError, match="scope"):
        reader.detail(other, review)
    with sqlite3.connect(ledger.database.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("SELECT status FROM inverse_patch_reviews").fetchone()[0] == "pending"


def test_patch_existing_read_uses_readonly_connection_with_live_wal_writer(tmp_path, monkeypatch):
    ledger, reader, run, _, _ = fixture(tmp_path)
    connect = sqlite3.connect
    statements = []

    def traced(*args, **kwargs):
        assert "mode=ro" in str(args[0]) and kwargs["uri"] is True
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    # Isolated fixture only. The writer deliberately stays open with WAL state;
    # no immutable/main-only copy and no assertion of zero physical sidecar IO.
    with connect(ledger.database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE tool_executions SET error='private fixture not exported'")
        writer.commit()
        monkeypatch.setattr(sqlite3, "connect", traced)
        assert reader.detail(run, "source")["confirmed_applied"]
        assert reader.list(run)["query"]["sql_read_only"]
    assert any("ATTACH DATABASE" in sql and "mode=ro" in sql for sql in statements)
    assert any("query_only=ON" in sql for sql in statements)
    assert not any(sql.lstrip().upper().startswith(("UPDATE", "DELETE", "INSERT", "CREATE", "ALTER")) for sql in statements)
