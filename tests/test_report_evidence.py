"""S8 B2a regression definitions FIRST, ALL UNRUN; disposable original stores.

SQL/frame/lineage fixtures are not native/project verification or model quality.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.inverse_reviews import InverseReviewStore
from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.persistence.verification_reviews import VerificationReviewStore
from doppel_agent.reporting import evidence
from doppel_agent.reporting.evidence import ReportEvidenceQueries, report_digest
from doppel_agent.workspace.patching import PatchService
from doppel_agent.workspace.verification import VerificationPipeline, VerificationResult


RUN = "a" * 32
CALL = "UNRECOGNIZABLE_PRIVATE_CREDENTIAL"


def fixture(tmp_path):
    root = tmp_path / "private"
    runs = RuntimeRunStore(root / "runtime.sqlite3")
    runs.create(RUN, "PRIVATE_THREAD", "graph", {"prompt": "PRIVATE_PROMPT"}, None)
    runs.update(RUN, "completed")
    ledger = ToolExecutionLedger(root / "tool-executions.sqlite3")
    file = tmp_path / "PRIVATE_PATH.txt"
    file.write_text("PRIVATE_BEFORE", encoding="utf-8")
    patch = PatchService(tmp_path)
    proposal = patch.prepare([{"path": file.name, "content": "PRIVATE_AFTER"}])
    ledger.execute_patch_once(RUN, CALL, proposal.as_dict(), patch, proposal)
    reader = ReportEvidenceQueries(runs.database, ledger.database)
    return runs, ledger, patch, proposal, reader


def verification(tmp_path, ledger, proposal, *, commands=2):
    config = tmp_path / ".doppel" / "verification.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "max_output_bytes": 1024,
                "commands": [
                    {
                        "name": f"PRIVATE_UNIT_{i}",
                        "argv": ["PRIVATE_EXECUTABLE", "PRIVATE_ARG"],
                        "timeout_seconds": 5,
                    }
                    for i in range(commands)
                ],
            }
        ),
        encoding="utf-8",
    )
    plan = VerificationPipeline(tmp_path).prepare().as_dict()
    store = VerificationReviewStore(ledger.database)
    review = uuid4().hex
    store.save(RUN, CALL, proposal.patch_id, review, None, plan)
    return store, review, plan


def test_original_patch_receipt_and_pending_inverse_are_numeric_not_current_diff(tmp_path, monkeypatch):
    runs, ledger, patch, proposal, reader = fixture(tmp_path)
    inverse = InverseReviewStore(ledger.database)
    review = uuid4().hex
    inverse.save(
        RUN, CALL, proposal.patch_id, review, patch.prepare_inverse(ledger.read_applied_patch(RUN, CALL))
    )
    (tmp_path / "PRIVATE_PATH.txt").write_text("UNRELATED_LATER_EDIT", encoding="utf-8")
    monkeypatch.setattr(
        ToolExecutionLedger, "__init__", lambda *_a, **_kw: pytest.fail("new writable ledger")
    )
    monkeypatch.setattr(InverseReviewStore, "__init__", lambda *_a, **_kw: pytest.fail("new inverse store"))
    result = reader.read(RUN)
    assert result["patches"]["items"][0]["confirmed_applied"] is True
    assert result["patches"]["items"][0]["tool_call_sha256"] == report_digest("tool_call", CALL)
    assert result["patches"]["items"][0]["files"] == 1
    inv = result["inverses"]["items"][0]
    assert inv["review_id"] == review and inv["status"] == "pending"
    assert inv["source_receipt_confirmed"] is True and inv["effect_receipt_state"] == "not_confirmed"
    assert "PRIVATE_" not in json.dumps(result) and CALL not in json.dumps(result)
    assert "UNRELATED_LATER_EDIT" not in json.dumps(result)
    assert result["current_git_ownership_proven"] is False and result["project_acceptance_proven"] is False
    with sqlite3.connect(ledger.database) as db:
        assert db.execute("SELECT status FROM inverse_patch_reviews").fetchone()[0] == "pending"


def test_read_projects_pending_expiry_without_mutation_or_recovery(tmp_path):
    _runs, ledger, patch, proposal, reader = fixture(tmp_path)
    inverse = InverseReviewStore(ledger.database)
    review = uuid4().hex
    inverse.save(
        RUN, CALL, proposal.patch_id, review, patch.prepare_inverse(ledger.read_applied_patch(RUN, CALL))
    )
    _store, _verification_id, _plan = verification(tmp_path, ledger, proposal)
    now = datetime.now(UTC) - timedelta(seconds=10)
    with sqlite_connection(ledger.database) as db:
        for table in ("inverse_patch_reviews", "verification_reviews"):
            db.execute(
                f"UPDATE {table} SET created_at=?,expires_at=?",
                ((now - timedelta(seconds=10)).isoformat(), now.isoformat()),
            )
    report = reader.read(RUN)
    assert (
        report["inverses"]["items"][0]["status"] == "pending"
        and report["inverses"]["items"][0]["pending_expired"] is True
    )
    assert (
        report["verifications"]["items"][0]["status"] == "pending"
        and report["verifications"]["items"][0]["pending_expired"] is True
    )
    with sqlite3.connect(ledger.database) as db:
        assert db.execute("SELECT status,proposal_json FROM inverse_patch_reviews").fetchone()[0] == "pending"
        assert db.execute("SELECT status FROM verification_reviews").fetchone()[0] == "pending"


def test_applied_inverse_review_requires_its_independent_original_effect_ledger(tmp_path):
    _runs, ledger, patch, proposal, reader = fixture(tmp_path)
    store = InverseReviewStore(ledger.database)
    review = uuid4().hex
    inverse = patch.prepare_inverse(ledger.read_applied_patch(RUN, CALL))
    store.save(RUN, CALL, proposal.patch_id, review, inverse)
    _view, accepted = store.claim(RUN, review, inverse.patch_id, "approve")
    assert accepted is not None
    effect = store.effect_id(review)
    output, replayed = ledger.execute_patch_once(
        RUN, effect, accepted.as_dict(), patch, accepted, tool_name="inverse_patch"
    )
    assert replayed is False
    result = json.loads(output)
    result["receipt_source"] = {
        "run_id": RUN,
        "tool_call_id": effect,
        "origin": "manual_inverse",
        "durability": "sealed_tool_ledger",
        "source_tool_call_id": CALL,
        "source_patch_id": proposal.patch_id,
    }
    store.finish(RUN, review, result=result)
    actual = reader.read(RUN)["inverses"]["items"][0]
    assert actual["status"] == "applied" and actual["effect_receipt_state"] == "confirmed"
    with sqlite_connection(ledger.database) as db:
        db.execute("DELETE FROM patch_effect_receipts WHERE tool_call_id=?", (effect,))
        db.execute("DELETE FROM tool_executions WHERE tool_call_id=?", (effect,))
    missing = reader.read(RUN)["inverses"]["items"][0]
    assert missing["status"] == "applied" and missing["effect_receipt_state"] == "not_confirmed"
    assert missing["source_receipt_confirmed"] is True
    assert "PRIVATE_" not in json.dumps(missing)


def test_expired_original_preimages_keep_verified_historical_summary_without_invention(tmp_path):
    _runs, ledger, _patch, _proposal, reader = fixture(tmp_path)
    with sqlite_connection(ledger.database) as db:
        db.execute(
            "UPDATE patch_effect_receipts SET receipt_json='',preimage_expired_at=?",
            (datetime.now(UTC).isoformat(),),
        )
    item = reader.read(RUN)["patches"]["items"][0]
    assert item["confirmed_applied"] is True and item["preimage_state"] == "expired"
    assert item["files"] == 1 and "PRIVATE_" not in json.dumps(item)


def test_sealed_failure_prefix_unknown_running_step_and_patch_provenance_are_separate(tmp_path):
    _runs, ledger, _patch, proposal, reader = fixture(tmp_path)
    store, review, plan = verification(tmp_path, ledger, proposal)
    store.claim(RUN, review, plan["plan_id"], "approve")
    command = plan["commands"][0]
    store.start_step(RUN, review, 0, command)
    store.seal_step(
        RUN,
        review,
        0,
        VerificationResult(
            command["name"],
            tuple(command["argv"]),
            1,
            False,
            "PRIVATE_STDOUT",
            "PRIVATE_STDERR",
            1,
            "fixture_not_native",
        ).as_dict(),
    )
    store.finish(RUN, review, "completed")
    result = reader.read(RUN)["verifications"]["items"][0]
    assert result["status"] == "completed" and result["attempts_success"] is False
    assert result["sealed_steps"] == result["exited_commands"] == result["failed_attempts"] == 1
    assert result["remaining_commands"] == 1 and result["all_commands_exited"] is False
    assert result["source_receipt_confirmed"] is True and result["project_acceptance"] is False
    assert result["steps"][0]["exit_code"] == 1 and "PRIVATE_" not in json.dumps(result)
    unknown = uuid4().hex
    store.save(RUN, CALL, proposal.patch_id, unknown, None, plan)
    store.claim(RUN, unknown, plan["plan_id"], "approve")
    store.start_step(RUN, unknown, 0, command)
    entries = reader.read(RUN)["verifications"]["items"]
    actual = next(item for item in entries if item["review_id"] == unknown)
    assert actual["has_unknown_command"] is True and actual["steps"][0]["success"] is None
    assert actual["steps"][0]["exit_code"] is None and actual["sealed_steps"] == 0


def test_missing_ledger_tables_are_unknown_not_zero_or_migrations(tmp_path):
    runs = RuntimeRunStore(tmp_path / "private" / "runtime.sqlite3")
    runs.create(RUN, "thread", "graph", {"prompt": "fixture"}, None)
    ledger = runs.database.with_name("absent.sqlite3")
    report = ReportEvidenceQueries(runs.database, ledger).read(RUN)
    for name in ("patches", "inverses", "verifications"):
        assert report[name]["state"] == "unknown" and report[name]["total"] is None
    assert report["work_orders"]["state"] == "known" and report["work_orders"]["total"] == 0
    assert not ledger.exists()
    with sqlite_connection(ledger) as db:
        db.execute("CREATE TABLE fixture_only(value TEXT)")
    report = ReportEvidenceQueries(runs.database, ledger).read(RUN)
    assert report["patches"]["reason"] == "missing_tables"
    with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='tool_executions'").fetchone()[0] == 0
        )


def test_corrupt_duplicate_receipt_and_predecode_total_budget_remain_partial_without_private_echo(
    tmp_path, monkeypatch
):
    _runs, ledger, _patch, _proposal, reader = fixture(tmp_path)
    with sqlite_connection(ledger.database) as db:
        raw = db.execute("SELECT result FROM tool_executions").fetchone()[0]
        db.execute("UPDATE tool_executions SET result=?", ('{"patch_id":"PRIVATE_DUPLICATE",' + raw[1:],))
    bad = reader.read(RUN)["patches"]
    assert bad["state"] == "partial" and bad["omitted"] == 1 and not bad["items"]
    assert "PRIVATE_" not in json.dumps(bad)
    monkeypatch.setattr(evidence, "MAX_DECODE", 1)
    monkeypatch.setattr(
        evidence.PatchEvidenceQueries, "_public", lambda *_a: pytest.fail("payload read before budget")
    )
    budget = reader.read(RUN)
    assert budget["patches"]["omitted"] == 1 and budget["reserved_body_bytes"] == 0


def test_scoped_history_cap_keeps_actual_total_and_other_run_private(tmp_path):
    _runs, ledger, _patch, _proposal, reader = fixture(tmp_path)
    with sqlite_connection(ledger.database) as db:
        for number in range(evidence.MAX_ROWS + 2):
            db.execute(
                "INSERT INTO tool_executions VALUES(?,?,'propose_patch',?,'running','','')",
                (RUN, f"PRIVATE_CALL_{number}", "hash"),
            )
        db.execute(
            "INSERT INTO tool_executions VALUES(?,?,'propose_patch',?,'running','','')",
            ("c" * 32, "PRIVATE_FOREIGN", "hash"),
        )
    section = reader.read(RUN)["patches"]
    assert section["total"] == evidence.MAX_ROWS + 3 and section["scanned"] == evidence.MAX_ROWS
    assert section["truncated"] is True and section["state"] == "partial"
    assert all(
        item["tool_call_sha256"] != report_digest("tool_call", "PRIVATE_FOREIGN") for item in section["items"]
    )
    assert all(item["preimage_state"] == "unknown" for item in section["items"] if item["patch_id"] is None)


def test_orphan_or_misnamed_receipt_is_counted_and_omitted_not_hidden_as_empty(tmp_path):
    _runs, ledger, _patch, _proposal, reader = fixture(tmp_path)
    # Corrupt isolated fixture bypasses FK; no app write/recovery is permitted.
    with sqlite3.connect(ledger.database) as db:
        db.execute(
            "INSERT INTO patch_effect_receipts(run_id,tool_call_id,patch_id,receipt_json,created_at) VALUES(?,'ORPHAN_PRIVATE',?,'PRIVATE_BAD','PRIVATE_DATE')",
            (RUN, "b" * 32),
        )
        db.execute("UPDATE tool_executions SET tool_name='PRIVATE_WRONG_KIND' WHERE run_id=?", (RUN,))
    section = reader.read(RUN)["patches"]
    assert section["total"] == 2 and section["omitted"] == 2 and section["state"] == "partial"
    assert not section["items"] and "PRIVATE_" not in json.dumps(section)


def test_work_order_attempt_uses_original_execution_revision_and_hashed_task_not_current_plan(tmp_path):
    runs, _ledger, _patch, _proposal, reader = fixture(tmp_path)
    order, attempt = "d" * 32, "e" * 32
    with sqlite_connection(runs.database) as db:
        now = "2026-10-05T00:00:00+00:00"
        db.execute(
            "INSERT INTO work_orders(work_order_id,title,status,active_revision,request_json,created_at,updated_at) VALUES(?,'PRIVATE_TITLE','paused',2,'{}',?,?)",
            (order, now, now),
        )
        for revision in (1, 2):
            db.execute("INSERT INTO work_order_plans VALUES(?,?,?,?)", (order, revision, "{}", now))
            db.execute(
                "INSERT INTO work_order_tasks(work_order_id,revision,task_id,position,title,prompt,access,mode) VALUES(?,?,'PRIVATE_TASK',0,'PRIVATE_TITLE','PRIVATE_PROMPT','read','graph')",
                (order, revision),
            )
        db.execute(
            "INSERT INTO work_order_attempts VALUES(?,?,1,'PRIVATE_TASK',1,?,'succeeded',?,?)",
            (attempt, order, RUN, now, now),
        )
        db.execute(
            "INSERT INTO work_order_dispatch_intents(attempt_id,admission_key,request_json,state,run_id,created_at,updated_at) VALUES(?,'PRIVATE_KEY','{}','admitted',?,?,?)",
            (attempt, RUN, now, now),
        )
    item = reader.read(RUN)["work_orders"]["items"][0]
    assert item["execution_revision"] == 1 and item["active_plan_revision"] == 2
    assert item["task_id_sha256"] == report_digest("task_id", "PRIVATE_TASK")
    assert item["dispatch_state"] == "admitted" and item["attempt_status"] == "succeeded"
    assert "PRIVATE_" not in json.dumps(item)
    with sqlite_connection(runs.database) as db:
        db.execute("DELETE FROM work_order_dispatch_intents WHERE attempt_id=?", (attempt,))
    missing = reader.read(RUN)["work_orders"]
    assert missing["items"][0]["dispatch_state"] is None and missing["state"] == "partial"


def test_connections_are_read_only_and_no_original_writer_or_filesystem_validation_is_invoked(
    tmp_path, monkeypatch
):
    _runs, ledger, _patch, _proposal, reader = fixture(tmp_path)
    connect = sqlite3.connect
    statements = []

    def traced(*args, **kwargs):
        assert "?mode=ro" in str(args[0]) and kwargs["uri"] is True
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(evidence.sqlite3, "connect", traced)
    result = reader.read(RUN)
    assert result["patches"]["state"] == "known" and result["sql_read_only"] is True
    assert not any(
        sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"))
        for sql in statements
    )
    assert result["filesystem_zero_write_guarantee"] is False
