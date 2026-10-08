"""C2c metadata lifecycle definitions, no commands; execution is S9."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.verification_maintenance import VerificationMaintenance
from test_verification_review_store import fixture, result


def test_missing_ledger_and_missing_tables_do_not_create_verification_schema(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    missing = root / "tool-executions.sqlite3"
    report = VerificationMaintenance().apply(missing, recover_running=True)
    assert report["deferred"] and not missing.exists()
    with sqlite_connection(missing) as db:
        db.execute("CREATE TABLE unrelated(value)")
    assert VerificationMaintenance().apply(missing, recover_running=True)["deferred"]
    with sqlite_connection(missing) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'verification_%'").fetchall() == []


def test_keyset_page_expires_pending_and_recovers_without_repeat_or_payload_deletion(tmp_path):
    store, run, review, plan, _ = fixture(tmp_path)
    ids = sorted([review, uuid4().hex, uuid4().hex])
    for key in ids:
        if key != review:
            store.save(run, "actual-source", "a" * 32, key, None, plan)
    old = datetime.now(UTC) - timedelta(seconds=2)
    with sqlite_connection(store.database) as db:
        db.execute("UPDATE verification_reviews SET created_at=?,expires_at=? WHERE review_id=?",
                   ((old - timedelta(seconds=5)).isoformat(), old.isoformat(), ids[0]))
    store.claim(run, ids[1], plan["plan_id"], "approve")
    store.start_step(run, ids[1], 0, plan["commands"][0])
    store.claim(run, ids[2], plan["plan_id"], "approve")
    store.start_step(run, ids[2], 0, plan["commands"][0])
    store.seal_step(run, ids[2], 0, result(plan))
    maintenance = VerificationMaintenance(max_rows=1)
    cursor = ""
    for index in range(3):
        report = maintenance.apply(store.database, after_id=cursor, recover_running=True)
        assert report["scanned"] == 1 and report["decode_bytes"] <= maintenance.max_decode_bytes
        cursor = report["next_after_id"]
        if index < 2:
            assert cursor == ids[index]
    assert cursor == ""
    assert store.get(run, ids[0])["status"] == "expired"
    assert store.get(run, ids[1])["status"] == "indeterminate"
    final = store.get(run, ids[2])
    assert final["status"] == "completed" and final["steps"][0]["result"]["stdout"] == "private fixture stdout"


def test_ordinary_maintenance_never_reconciles_live_running_operation(tmp_path):
    store, run, review, plan, _ = fixture(tmp_path)
    store.claim(run, review, plan["plan_id"], "approve")
    store.start_step(run, review, 0, plan["commands"][0])
    store.seal_step(run, review, 0, result(plan))
    report = VerificationMaintenance().apply(store.database)
    assert report["protected"] == 1 and store.get(run, review)["status"] == "running"
    store.finish(run, review, "indeterminate", error_code="verification_outcome_indeterminate")
    report = VerificationMaintenance().apply(store.database)
    assert report["protected"] == 1 and store.get(run, review)["status"] == "indeterminate"
    assert VerificationMaintenance().apply(store.database, recover_running=True)["completed"] == 1


@pytest.mark.parametrize("kwargs", [{"max_rows": 0}, {"max_rows": 65}, {"max_decode_bytes": True}, {"max_decode_bytes": 1}])
def test_invalid_bounded_maintenance_limits_rejected(kwargs):
    with pytest.raises(ValueError):
        VerificationMaintenance(**kwargs)


def test_owner_maintenance_protects_unregistered_or_leased_source(tmp_path):
    store, run, review, plan, _ = fixture(tmp_path)
    store.claim(run, review, plan["plan_id"], "approve")
    runtime = store.database.with_name("runtime.sqlite3")
    with sqlite_connection(runtime) as db:
        db.execute("CREATE TABLE runtime_runs(run_id TEXT PRIMARY KEY,status TEXT,lease_active INTEGER)")
    maintenance = VerificationMaintenance()
    for row in (None, (run, "completed", 1), (run, "interrupted", 0)):
        with sqlite_connection(runtime) as db:
            db.execute("DELETE FROM runtime_runs")
            if row is not None:
                db.execute("INSERT INTO runtime_runs VALUES(?,?,?)", row)
        report = maintenance.apply(store.database, runtime_database=runtime, recover_running=True)
        assert report["protected"] == 1 and store.get(run, review)["status"] == "running"
    with sqlite_connection(runtime) as db:
        db.execute("UPDATE runtime_runs SET status='completed',lease_active=0")
    assert maintenance.apply(store.database, runtime_database=runtime, recover_running=True)["indeterminate"] == 1
