"""S9 isolated private-lifecycle definitions; not executed during construction."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doppel_agent.loop import LoopContinuation
from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.inverse_reviews import InverseReviewStore
from doppel_agent.persistence.legacy_review import LegacyReviewStore
from doppel_agent.persistence.patch_retention import PrivatePatchRetention
from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.provider import Message, ToolCall
from doppel_agent.workspace.patch_receipts import receipt_summary
from doppel_agent.workspace.patching import PatchService


def source(tmp_path, *, call="source-call", run=None, age=40):
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    state = tmp_path / "state"
    runs = RuntimeRunStore(state / "runtime.sqlite3")
    ledger = ToolExecutionLedger(state / "tool-executions.sqlite3")
    run, thread = run or uuid4().hex, uuid4().hex
    runs.create(run, thread, "graph", {"prompt": "fixture", "mode": "graph",
                                      "permissions": {"workspace_write": True}}, None)
    (root / "file.py").write_bytes(b"old private preimage\n")
    patch = PatchService(root)
    proposal = patch.prepare([{"path": "file.py", "content": "agent\n"}])
    result, _ = ledger.execute_patch_once(run, call, proposal.as_dict(), patch, proposal)
    old = (datetime.now(UTC) - timedelta(days=age)).isoformat()
    with sqlite_connection(state / "runtime.sqlite3") as db:
        db.execute("UPDATE runtime_runs SET status='completed',lease_active=0,updated_at=? WHERE run_id=?", (old, run))
    with sqlite_connection(ledger.database) as db:
        db.execute("UPDATE patch_effect_receipts SET created_at=? WHERE run_id=?", (old, run))
    return root, state, runs, ledger, run, thread, patch, proposal, result


def receipt_pass(state, **kwargs):
    return PrivatePatchRetention().apply(state, cursor={"phase": "receipts", "after": ""}, **kwargs)


def private_row(ledger, run, call="source-call"):
    with sqlite_connection(ledger.database) as db:
        return dict(db.execute("SELECT * FROM patch_effect_receipts WHERE run_id=? AND tool_call_id=?", (run, call)).fetchone())


def test_expired_preimage_preserves_sealed_history_exact_replay_and_later_user_file(tmp_path, monkeypatch):
    root, state, runs, ledger, run, _, patch, proposal, result = source(tmp_path)
    original_run = runs.get(run)
    with sqlite_connection(ledger.database) as db:
        operation = dict(db.execute("SELECT * FROM tool_executions WHERE run_id=?", (run,)).fetchone())
    (root / "file.py").write_bytes(b"user owns this later\n")
    report = receipt_pass(state)
    assert report["preimages_expired"] == 1
    row = private_row(ledger, run)
    assert row["receipt_json"] == "" and row["preimage_expired_at"]
    assert "before_content" not in json.loads(row["summary_json"])["files"][0]
    assert ledger.list_patch_receipts(run)[0]["confirmed_applied"]
    assert ledger.list_patch_receipts(run)[0]["preimage_state"] == "expired"
    assert ledger.read_patch_evidence(run, "source-call")["confirmed_applied"]
    with pytest.raises(ValueError, match="patch_preimage_expired"):
        ledger.read_applied_patch(run, "source-call")

    def no_apply(*args, **kwargs):
        pytest.fail("expired historical replay is not an effect")

    monkeypatch.setattr(PatchService, "apply", no_apply)
    assert ledger.execute_patch_once(run, "source-call", proposal.as_dict(), patch, proposal) == (result, True)
    assert (root / "file.py").read_bytes() == b"user owns this later\n"
    assert runs.get(run) == original_run
    with sqlite_connection(ledger.database) as db:
        assert dict(db.execute("SELECT * FROM tool_executions WHERE run_id=?", (run,)).fetchone()) == operation


@pytest.mark.parametrize("protection", ["interrupted", "active_lease", "running_tool", "unregistered", "recent_root", "recent_receipt"])
def test_pending_unknown_live_and_unregistered_roots_are_not_expiry_authority(tmp_path, protection):
    _, state, _, ledger, run, *_ = source(tmp_path)
    before = private_row(ledger, run)
    with sqlite_connection(state / "runtime.sqlite3") as db:
        if protection == "interrupted":
            db.execute("UPDATE runtime_runs SET status='interrupted' WHERE run_id=?", (run,))
        elif protection == "active_lease":
            db.execute("UPDATE runtime_runs SET lease_active=1 WHERE run_id=?", (run,))
        elif protection == "unregistered":
            db.execute("DELETE FROM runtime_runs WHERE run_id=?", (run,))
        elif protection == "recent_root":
            db.execute("UPDATE runtime_runs SET updated_at=? WHERE run_id=?", (datetime.now(UTC).isoformat(), run))
    with sqlite_connection(ledger.database) as db:
        if protection == "running_tool":
            db.execute("UPDATE tool_executions SET status='running' WHERE run_id=?", (run,))
        elif protection == "recent_receipt":
            db.execute("UPDATE patch_effect_receipts SET created_at=? WHERE run_id=?", (datetime.now(UTC).isoformat(), run))
    protected = private_row(ledger, run)
    assert protected["receipt_json"] == before["receipt_json"]
    report = receipt_pass(state)
    assert report["preimages_expired"] == 0 and report["protected"] == 1
    assert private_row(ledger, run) == protected


@pytest.mark.parametrize("status", ["pending", "applying", "indeterminate"])
def test_live_manual_review_protects_original_receipt_and_manual_effect(tmp_path, status):
    _, state, _, ledger, run, _, patch, proposal, _ = source(tmp_path)
    store = InverseReviewStore(ledger.database)
    inverse = patch.prepare_inverse(ledger.read_applied_patch(run, "source-call"))
    review = store.save(run, "source-call", proposal.patch_id, uuid4().hex, inverse)
    # Fixture actual inverse seal; review intentionally remains unknown.
    ledger.execute_patch_once(run, review["effect_tool_call_id"], inverse.as_dict(), patch, inverse, tool_name="inverse_patch")
    with sqlite_connection(ledger.database) as db:
        db.execute("UPDATE inverse_patch_reviews SET status=? WHERE review_id=?", (status, review["review_id"]))
        db.execute("UPDATE patch_effect_receipts SET created_at=? WHERE run_id=?",
                   ((datetime.now(UTC) - timedelta(days=40)).isoformat(), run))
    assert receipt_pass(state)["preimages_expired"] == 0
    assert private_row(ledger, run)["preimage_expired_at"] is None
    assert private_row(ledger, run, review["effect_tool_call_id"])["preimage_expired_at"] is None


def test_owner_ttl_expiry_keeps_review_identity_and_malformed_time_never_clears_payload(tmp_path):
    _, state, _, ledger, run, _, patch, proposal, _ = source(tmp_path)
    store = InverseReviewStore(ledger.database)
    inverse = patch.prepare_inverse(ledger.read_applied_patch(run, "source-call"))
    review = store.save(run, "source-call", proposal.patch_id, uuid4().hex, inverse)
    old = datetime.now(UTC) - timedelta(seconds=10)
    with sqlite_connection(ledger.database) as db:
        db.execute("UPDATE inverse_patch_reviews SET created_at=?,expires_at=? WHERE review_id=?",
                   ((old - timedelta(seconds=1)).isoformat(), old.isoformat(), review["review_id"]))
    report = PrivatePatchRetention().apply(state)
    assert report["reviews_expired"] == 1
    assert store.get(run, review["review_id"])["status"] == "expired"
    with sqlite_connection(ledger.database) as db:
        row = db.execute("SELECT proposal_json,review_json FROM inverse_patch_reviews WHERE review_id=?", (review["review_id"],)).fetchone()
        assert row["proposal_json"] == "" and row["review_json"]
    second = store.save(run, "source-call", proposal.patch_id, uuid4().hex, inverse)
    with sqlite_connection(ledger.database) as db:
        db.execute("UPDATE inverse_patch_reviews SET expires_at='0000-not-a-date' WHERE review_id=?", (second["review_id"],))
    assert PrivatePatchRetention().apply(state)["reviews_expired"] == 0
    with pytest.raises(ValueError):
        store.get(run, second["review_id"])
    with sqlite_connection(ledger.database) as db:
        assert db.execute("SELECT proposal_json FROM inverse_patch_reviews WHERE review_id=?", (second["review_id"],)).fetchone()[0]


@pytest.mark.parametrize("status", ["pending", "consumed"])
def test_expired_legacy_continuation_keeps_old_identity_and_cannot_resume_or_reuse_run(tmp_path, status):
    _, state, _, ledger, run, thread, *_ = source(tmp_path)
    store = LegacyReviewStore(ledger.database)
    call = ToolCall("pending", "propose_patch", {})
    frame = LoopContinuation(1, (Message("user", "private prompt"), Message("assistant", "", tool_calls=(call,))), (call,))
    interrupt = store.save(run, thread, "fixture", "private context", frame)
    with sqlite_connection(ledger.database) as db:
        db.execute("UPDATE legacy_review_continuations SET status=?,decision_json=? WHERE run_id=?",
                   (status, "{}" if status == "consumed" else "", run))
    report = PrivatePatchRetention().apply(state, cursor={"phase": "legacy", "after": ""})
    assert report["legacy_expired"] == 1
    with sqlite_connection(ledger.database) as db:
        row = db.execute("SELECT * FROM legacy_review_continuations WHERE run_id=?", (run,)).fetchone()
        assert row["status"] == status and row["interrupt_id"] == interrupt
        assert row["snapshot_json"] == row["decision_json"] == "" and row["snapshot_expired_at"]
    with pytest.raises(ValueError, match="scope"):
        store.load(run, thread, interrupt)
    with pytest.raises(ValueError, match="already_exists"):
        store.ensure_new(run)
    with pytest.raises(ValueError, match="scope"):
        store.save(run, thread, "fixture", "replacement must not revive expired identity", frame)


def test_dry_run_does_not_clear_payload_or_change_audit_and_keyset_budget_progresses(tmp_path):
    _, state, _, ledger, first, *_ = source(tmp_path, run="1" * 32)
    source(tmp_path, run="2" * 32)
    before = private_row(ledger, first)
    cursor = {"phase": "receipts", "after": ""}
    policy = PrivatePatchRetention(max_rows=1)
    dry = policy.apply(state, cursor=cursor, dry_run=True)
    assert dry["scanned"] == dry["preimages_expired"] == 1
    assert private_row(ledger, first) == before
    first_pass = policy.apply(state, cursor=cursor)
    assert first_pass["next_cursor"]["after"] == (first, "source-call")
    second = policy.apply(state, cursor=first_pass["next_cursor"])
    assert second["scanned"] == second["preimages_expired"] == 1
    end = policy.apply(state, cursor=second["next_cursor"])
    assert end["next_cursor"] == {"phase": "legacy", "after": ""}
    assert all(p["decode_bytes"] <= policy.max_decode_bytes for p in (dry, first_pass, second, end))


@pytest.mark.parametrize("corruption", ["receipt", "date", "oversize"])
def test_malformed_or_oversize_preimages_stay_private_not_replaced_by_fake_summary(tmp_path, corruption):
    _, state, _, ledger, run, *_ = source(tmp_path)
    with sqlite_connection(ledger.database) as db:
        if corruption == "date":
            db.execute("UPDATE patch_effect_receipts SET created_at='0000-corrupt' WHERE run_id=?", (run,))
        else:
            db.execute("UPDATE patch_effect_receipts SET receipt_json=? WHERE run_id=?",
                       ("{}" if corruption == "receipt" else "x" * (4 * 1024 * 1024 + 1), run))
    before = private_row(ledger, run)
    report = receipt_pass(state)
    assert report["preimages_expired"] == 0 and report["protected"] == 1
    assert private_row(ledger, run) == before


def test_expiry_flag_alone_or_summary_with_private_fields_is_not_historical_proof(tmp_path):
    _, _, _, ledger, run, *_ = source(tmp_path)
    with sqlite_connection(ledger.database) as db:
        db.execute("UPDATE patch_effect_receipts SET preimage_expired_at=? WHERE run_id=?", (datetime.now(UTC).isoformat(), run))
    with pytest.raises(ValueError, match="receipt_unavailable"):
        ledger.list_patch_receipts(run)
    with sqlite_connection(ledger.database) as db:
        raw = json.loads(private_row(ledger, run)["receipt_json"])
        db.execute("UPDATE patch_effect_receipts SET receipt_json='',summary_json=? WHERE run_id=?", (json.dumps(raw), run))
    with pytest.raises(ValueError, match="receipt_unavailable"):
        ledger.read_patch_evidence(run, "source-call")
    with pytest.raises(ValueError, match="summary"):
        receipt_summary(raw)


def test_retention_database_hardlink_rejected_before_connection(tmp_path):
    _, state, _, ledger, *_ = source(tmp_path)
    alias = tmp_path / "ledger-hardlink"
    try:
        alias.hardlink_to(ledger.database)
    except OSError:
        pytest.skip("fixture volume does not support hardlinks")
    with pytest.raises(ValueError, match="database_path"):
        receipt_pass(state)
