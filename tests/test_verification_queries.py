"""C3 S9 definitions: scoped SQL-read-only existing evidence, no commands."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.verification_queries import VerificationQueries
from doppel_agent.persistence.verification_reviews import VerificationReviewStore
from doppel_agent.workspace.verification import VerificationPipeline, VerificationResult


def fixture(tmp_path, *, commands=1):
    config = tmp_path / ".doppel" / "verification.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"max_output_bytes": 1048576, "commands": [
        {"name": f"unit-{i}", "argv": ["fixture-never-launched", "private-argument"], "timeout_seconds": 5}
        for i in range(commands)]}), encoding="utf-8")
    plan = VerificationPipeline(tmp_path).prepare().as_dict()
    root = tmp_path / "private"
    store = VerificationReviewStore(root / "tool-executions.sqlite3")
    runtime = root / "runtime.sqlite3"
    run, other = uuid4().hex, uuid4().hex
    with sqlite_connection(runtime) as db:
        db.execute("CREATE TABLE runtime_runs(run_id TEXT PRIMARY KEY,status TEXT,lease_active INTEGER)")
        db.executemany("INSERT INTO runtime_runs VALUES(?,'completed',0)", [(run,), (other,)])
    reader = VerificationQueries(store.database, runtime)
    return store, reader, run, other, plan, config


def save(store, run, plan, *, review=None, call="source", patch="a" * 32):
    review = review or uuid4().hex
    store.save(run, call, patch, review, None, plan)
    return review


def seal(store, run, review, plan, index=0, *, exit_code=0, output="private fixture stdout"):
    command = plan["commands"][index]
    store.start_step(run, review, index, command)
    store.seal_step(run, review, index, VerificationResult(command["name"], tuple(command["argv"]),
        exit_code, exit_code == 0, output, "", 1, "fixture_not_native").as_dict())


def test_queries_do_not_construct_missing_stores_or_create_directories(tmp_path):
    root = tmp_path / "absent"
    reader = VerificationQueries(root / "tool-executions.sqlite3", root / "runtime.sqlite3")
    with pytest.raises(ValueError, match="query_unavailable"):
        reader.list(uuid4().hex)
    assert not root.exists()


def test_expired_pending_read_is_not_expiration_or_recovery(tmp_path, monkeypatch):
    store, reader, run, _, plan, config = fixture(tmp_path)
    review = save(store, run, plan)
    old = datetime.now(UTC) - timedelta(seconds=10)
    with sqlite_connection(store.database) as db:
        db.execute("UPDATE verification_reviews SET created_at=?,expires_at=? WHERE review_id=?",
            ((old - timedelta(seconds=1)).isoformat(), old.isoformat(), review))
    # Query must not reread changed config or enter a mutating store constructor.
    config.write_text("not config", encoding="utf-8")
    monkeypatch.setattr(VerificationReviewStore, "__init__", lambda *a, **k: pytest.fail("constructor"))
    monkeypatch.setattr(VerificationReviewStore, "_expire", lambda *a, **k: pytest.fail("expiration"))
    before = store.database.read_bytes(), reader.runtime_database.read_bytes()
    view = reader.detail(run, review)
    assert view["status"] == "pending" and view["lifecycle"]["effective_status"] == "expired"
    assert view["lifecycle"]["pending_expired"] and not view["lifecycle"]["approval_available"]
    assert view["success"] is None and view["evidence"] == "not_completion_proof"
    assert view["provenance"]["source_registration"] == "registered_run"
    assert (store.database.read_bytes(), reader.runtime_database.read_bytes()) == before
    # These are business/main-DB invariants, not a sidecar/OS zero-write claim.
    with sqlite3.connect(store.database.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("SELECT status FROM verification_reviews WHERE review_id=?", (review,)).fetchone()[0] == "pending"


def test_keyset_pages_source_filters_and_summaries_do_not_expose_payload(tmp_path):
    store, reader, run, other, plan, _ = fixture(tmp_path)
    reviews = [save(store, run, plan, review=f"{i:032x}") for i in range(1, 4)]
    save(store, other, plan, review="0" * 32)
    first = reader.list(run, limit=2)
    assert [r["review_id"] for r in first["items"]] == reviews[:2]
    assert first["has_more"] and first["next_after_id"] == reviews[1]
    second = reader.list(run, limit=2, after_id=first["next_after_id"])
    assert [r["review_id"] for r in second["items"]] == reviews[2:]
    assert not second["has_more"] and second["next_after_id"] is None
    for row in first["items"]:
        assert "plan" not in row and "steps" not in row
        assert "private-argument" not in json.dumps(row)
        assert row["evidence"] == "not_completion_proof" and row["success"] is None
    assert reader.list(run, tool_call_id="foreign")["items"] == []
    assert reader.list(run, patch_id="b" * 32)["items"] == []
    with pytest.raises(ValueError, match="scope"):
        reader.detail(other, reviews[0])
    with pytest.raises(KeyError):
        reader.list("f" * 32)
    for kwargs in ({"limit": 17}, {"limit": True}, {"after_id": "not-a-uuid"}, {"patch_id": "bad"}):
        with pytest.raises(ValueError):
            reader.list(run, **kwargs)


def test_sealed_failure_partial_unknown_and_source_patch_are_distinct(tmp_path):
    store, reader, run, _, plan, _ = fixture(tmp_path, commands=2)
    review = save(store, run, plan)
    store.claim(run, review, plan["plan_id"], "approve")
    seal(store, run, review, plan, exit_code=1)
    store.finish(run, review, "completed")  # Actual reviewed stop-on-failure prefix.
    detail = reader.detail(run, review)
    assert detail["status"] == "completed" and detail["success"] is False
    assert detail["lifecycle"]["sealed_steps"] == 1 and detail["lifecycle"]["remaining_commands"] == 1
    assert detail["lifecycle"]["commands_completed"] is False
    assert detail["provenance"]["patch_success"] == "not_inferred_from_verification"
    assert detail["output"]["sensitive"] and not detail["output"]["globally_redacted"]
    assert detail["output"]["encoding"] == "utf8_replacement_presentation_not_lossless_bytes"
    assert reader.list(run)["items"][0]["success"] is False

    partial = save(store, run, plan)
    store.claim(run, partial, plan["plan_id"], "approve")
    seal(store, run, partial, plan)
    store.start_step(run, partial, 1, plan["commands"][1])
    store.finish(run, partial, "cancelled", error_code="verification_cancelled")
    view = reader.detail(run, partial)
    assert view["status"] == "cancelled" and view["success"] is None and view["has_unknown_command"]
    assert view["lifecycle"]["sealed_steps"] == 1 and view["lifecycle"]["remaining_commands"] == 1
    assert not view["lifecycle"]["retry_available"] and view["evidence"] == "not_completion_proof"


def test_page_decode_budget_before_second_payload_and_no_unbounded_scalars(tmp_path, monkeypatch):
    store, reader, run, _, plan, _ = fixture(tmp_path)
    for i in (1, 2, 3):
        review = save(store, run, plan, review=f"{i:032x}")
        store.claim(run, review, plan["plan_id"], "approve")
        # Replacement text can encode to 3 MiB from a 1 MiB invalid-UTF8 stream;
        # persisted presentation bytes are not the original pipe byte count.
        seal(store, run, review, plan, output="\ufffd" * (1024 * 1024))
        store.finish(run, review, "completed")
    original = VerificationReviewStore._view
    decoded = []

    def observed(db, row, **kwargs):
        decoded.append(row["review_id"])
        return original(db, row, **kwargs)

    monkeypatch.setattr(VerificationReviewStore, "_view", observed)
    page = reader.list(run)
    assert decoded == [f"{i:032x}" for i in (1, 2)]
    assert page["has_more"] and page["budget_limited"] and page["decode_bytes"] <= 8 * 1024 * 1024
    assert page["next_after_id"] == f"{2:032x}"
    with sqlite_connection(store.database) as db:
        db.execute("UPDATE verification_reviews SET source_tool_call_id=? WHERE review_id=?", ("z" * 200000, "0" * 31 + "1"))
    with pytest.raises(ValueError):
        reader.detail(run, "0" * 31 + "1")


def test_live_wal_query_observes_committed_evidence_and_only_read_sql(tmp_path, monkeypatch):
    store, reader, run, _, plan, _ = fixture(tmp_path)
    connect = sqlite3.connect
    statements = []
    # Keep a real original WAL writer alive; no immutable bypass/stale main copy.
    writer = connect(store.database)
    writer.execute("PRAGMA journal_mode=WAL")
    try:
        review = save(store, run, plan)

        def traced(*args, **kwargs):
            assert args[0].endswith("?mode=ro") and kwargs["uri"]
            db = connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        monkeypatch.setattr(sqlite3, "connect", traced)
        assert reader.detail(run, review)["status"] == "pending"
        assert all(not sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE", "CREATE", "REPLACE", "BEGIN IMMEDIATE"))
                   for sql in statements)
        assert not any("immutable" in sql.lower() or "journal_mode=" in sql.lower() for sql in statements)
    finally:
        writer.close()


def test_oversize_result_and_duplicate_json_fail_closed(tmp_path):
    store, reader, run, _, plan, _ = fixture(tmp_path)
    review = save(store, run, plan)
    with sqlite_connection(store.database) as db:
        db.execute("UPDATE verification_reviews SET plan_json=? WHERE review_id=?",
                   ('{"schema":1,' + json.dumps(plan)[1:], review))
    with pytest.raises(ValueError):
        reader.detail(run, review)
    with sqlite_connection(store.database) as db:
        db.execute("UPDATE verification_reviews SET plan_json=?,status='running',decision='approve' WHERE review_id=?",
                   (json.dumps(plan), review))
        db.execute("INSERT INTO verification_command_steps VALUES(?,0,'finished',?)", (review, "x" * (4 * 1024 * 1024 + 1)))
    with pytest.raises(ValueError, match="budget"):
        reader.detail(run, review)


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_hardlinked_database_and_sidecar_are_rejected_before_sqlite_open(tmp_path, monkeypatch, suffix):
    import os

    store, reader, run, _, _, _ = fixture(tmp_path)
    unsafe = store.database.with_name(store.database.name + suffix)
    if suffix:
        unsafe.write_bytes(b"unsafe fixture not opened")
    os.link(unsafe, tmp_path / "alias")
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("unsafe open"))
    with pytest.raises(ValueError, match="query_unavailable"):
        reader.list(run)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("transition", ["absent", "regular", "hardlink", "persistent_zero"])
def test_delete_pending_sidecar_requires_one_fresh_safe_path_observation(
    tmp_path, monkeypatch, suffix, transition
):
    """Windows observed zero-link deletion window, not permission to open aliases."""
    import os
    from pathlib import Path
    from time import monotonic
    from types import SimpleNamespace

    from doppel_agent.persistence.verification_queries import QUERY_SECONDS, _safe_database

    database = tmp_path / "original.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE original(value INTEGER)")
    sidecar = database.with_name(database.name + suffix)
    sidecar.write_bytes(b"disposable filesystem admission fixture")
    original_lstat = Path.lstat
    observations = []

    def lstat(path):
        if path != sidecar:
            return original_lstat(path)
        observations.append(path)
        if len(observations) == 1 or transition == "persistent_zero":
            info = original_lstat(path)
            if len(observations) == 1:
                if transition == "absent":
                    sidecar.unlink()
                elif transition == "hardlink":
                    os.link(sidecar, tmp_path / "foreign-alias")
            return SimpleNamespace(st_mode=info.st_mode, st_nlink=0,
                                   st_file_attributes=getattr(info, "st_file_attributes", 0))
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    if transition in {"absent", "regular"}:
        assert _safe_database(database, monotonic() + QUERY_SECONDS) == database
        assert len(observations) >= 2
    else:
        with pytest.raises(ValueError, match="^verification_query_unavailable$"):
            _safe_database(database, monotonic() + QUERY_SECONDS)


def test_missing_evidence_tables_are_not_created_and_source_status_is_only_registration(tmp_path):
    store, reader, run, _, plan, _ = fixture(tmp_path)
    review = save(store, run, plan)
    with sqlite_connection(reader.runtime_database) as db:
        db.execute("UPDATE runtime_runs SET status='interrupted',lease_active=1 WHERE run_id=?", (run,))
    view = reader.detail(run, review)
    assert view["provenance"]["source_run"] == {"status": "interrupted", "lease_active": True}
    assert not view["lifecycle"]["approval_available"] and view["lifecycle"]["pending_unexpired"]
    with sqlite_connection(store.database) as db:
        db.execute("DROP TABLE verification_command_steps")
    with pytest.raises(ValueError, match="query_unavailable"):
        reader.list(run)
    with sqlite3.connect(store.database.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='verification_command_steps'").fetchone() is None


def test_malformed_surrogate_output_never_echoes_private_data_in_query_error(tmp_path):
    store, reader, run, _, plan, _ = fixture(tmp_path)
    review = save(store, run, plan)
    bad = json.loads(json.dumps(plan))
    bad["commands"][0]["argv"] = ["private-fixture-\ud800"]
    with sqlite_connection(store.database) as db:
        db.execute("UPDATE verification_reviews SET plan_json=? WHERE review_id=?", (json.dumps(bad), review))
    with pytest.raises(ValueError) as exc:
        reader.detail(run, review)
    assert str(exc.value) == "verification_query_unavailable"


@pytest.mark.parametrize("error", ["verification_timeout", "verification_output_limit", "verification_supervision_unavailable"])
def test_sealed_failed_attempt_is_not_command_completion_or_project_success(tmp_path, error):
    store, reader, run, _, plan, _ = fixture(tmp_path, commands=2)
    review = save(store, run, plan)
    store.claim(run, review, plan["plan_id"], "approve")
    command = plan["commands"][0]
    store.start_step(run, review, 0, command)
    store.seal_step(run, review, 0, VerificationResult(command["name"], tuple(command["argv"]),
        None, False, "", "", 1, "unavailable" if error == "verification_supervision_unavailable" else "terminated", error).as_dict())
    store.finish(run, review, "completed")
    view = reader.detail(run, review)
    assert view["status"] == "completed" and view["success"] is False
    assert view["steps"][0]["result"]["error"] == error
    assert not view["lifecycle"]["commands_completed"] and view["lifecycle"]["remaining_commands"] == 1
    assert not view["lifecycle"]["retry_available"]


def test_claim_without_step_is_unknown_while_known_deadline_cause_is_preserved(tmp_path):
    store, reader, run, _, plan, _ = fixture(tmp_path)
    review = save(store, run, plan)
    store.claim(run, review, plan["plan_id"], "approve")
    unknown = reader.detail(run, review)
    assert unknown["status"] == "running" and unknown["steps"] == []
    assert unknown["lifecycle"]["outcome_unknown"] and unknown["success"] is None
    store.finish(run, review, "failed", error_code="verification_deadline_exceeded")
    failed = reader.detail(run, review)
    assert failed["status"] == "failed" and failed["error_code"] == "verification_deadline_exceeded"
    assert failed["steps"] == [] and failed["evidence"] == "not_completion_proof"
    assert not failed["lifecycle"]["retry_available"] and failed["success"] is None


def test_oversize_blob_step_index_is_not_fetched_as_evidence_scalar(tmp_path):
    store, reader, run, _, plan, _ = fixture(tmp_path)
    review = save(store, run, plan)
    store.claim(run, review, plan["plan_id"], "approve")
    with sqlite_connection(store.database) as db:
        db.execute("INSERT INTO verification_command_steps VALUES(?,?,'running','')", (review, b"private" * 100000))
    with pytest.raises(ValueError, match="verification_evidence_unavailable"):
        reader.detail(run, review)
