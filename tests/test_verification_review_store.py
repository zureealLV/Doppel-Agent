"""S9 durable manual verification definitions; no actual command execution."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.verification_reviews import VerificationReviewStore
from doppel_agent.workspace.verification import VerificationPipeline, VerificationResult


def fixture(tmp_path):
    path = tmp_path / ".doppel" / "verification.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"commands": [{"name": "unit", "argv": ["fixture-not-launched"], "timeout_seconds": 5}]}), encoding="utf-8")
    plan = VerificationPipeline(tmp_path).prepare().as_dict()
    store = VerificationReviewStore(tmp_path / "private" / "ledger.sqlite3")
    run, review = uuid4().hex, uuid4().hex
    saved = store.save(run, "actual-source", "a" * 32, review, None, plan)
    return store, run, review, plan, saved


def result(plan, *, exit_code=0):
    command = plan["commands"][0]
    return VerificationResult(command["name"], tuple(command["argv"]), exit_code, exit_code == 0,
                              "private fixture stdout", "", 1, "fixture_not_native").as_dict()


def test_exact_review_cas_durable_step_prefix_and_final_replay(tmp_path):
    store, run, review, plan, saved = fixture(tmp_path)
    assert saved["status"] == "pending" and saved["steps"] == []
    assert saved["plan"]["operation_timeout_seconds"] == 600.0
    with pytest.raises(ValueError, match="scope"):
        store.claim(run, review, "0" * 64, "approve")
    view, claimed = store.claim(run, review, plan["plan_id"], "approve")
    assert view["status"] == "running" and claimed == plan
    store.start_step(run, review, 0, plan["commands"][0])
    assert store.get(run, review)["has_unknown_command"]
    store.seal_step(run, review, 0, result(plan))
    done = store.finish(run, review, "completed")
    assert done["success"] and done["steps"][0]["result"]["stdout"] == "private fixture stdout"
    replay, claimed = store.claim(run, review, plan["plan_id"], "approve")
    assert replay["decision_replayed"] and claimed is None
    assert done["operation_kind"] == "manual_verification"
    assert done["target"] == "current_workspace_not_original_patch_snapshot"


def test_missing_or_partial_result_never_claims_success_or_permits_repeat(tmp_path):
    store, run, review, plan, _ = fixture(tmp_path)
    store.claim(run, review, plan["plan_id"], "approve")
    store.start_step(run, review, 0, plan["commands"][0])
    with pytest.raises(ValueError):
        store.finish(run, review, "completed")
    cancelled = store.finish(run, review, "cancelled", error_code="verification_cancelled")
    assert cancelled["success"] is None and cancelled["has_unknown_command"]
    with pytest.raises(RuntimeError, match="indeterminate"):
        store.claim(run, review, plan["plan_id"], "approve")
    with pytest.raises(RuntimeError):
        store.start_step(run, review, 0, plan["commands"][0])


def test_sticky_review_id_source_selection_and_ttl_do_not_refresh(tmp_path):
    store, run, review, plan, saved = fixture(tmp_path)
    assert store.prepare_replay(run, "actual-source", "a" * 32, review, None) == saved
    for call, patch, names in (("foreign", "a" * 32, None), ("actual-source", "b" * 32, None),
                              ("actual-source", "a" * 32, ["unit"])):
        with pytest.raises(ValueError, match="scope"):
            store.prepare_replay(run, call, patch, review, names)
    old = datetime.now(UTC) - timedelta(seconds=10)
    with sqlite_connection(store.database) as db:
        db.execute("UPDATE verification_reviews SET created_at=?,expires_at=? WHERE review_id=?",
                   ((old - timedelta(seconds=1)).isoformat(), old.isoformat(), review))
    with pytest.raises(RuntimeError):
        store.claim(run, review, plan["plan_id"], "approve")
    assert store.get(run, review)["status"] == "expired"


def test_corrupt_plan_or_forged_result_cannot_be_sealed_as_actual_evidence(tmp_path):
    store, run, review, plan, _ = fixture(tmp_path)
    store.claim(run, review, plan["plan_id"], "approve")
    store.start_step(run, review, 0, plan["commands"][0])
    forged = result(plan)
    forged["argv"] = ["not-reviewed"]
    with pytest.raises(ValueError):
        store.seal_step(run, review, 0, forged)
    assert store.get(run, review)["has_unknown_command"]
    with sqlite_connection(store.database) as db:
        modified = dict(plan)
        modified["plan_id"] = "0" * 64
        db.execute("UPDATE verification_reviews SET plan_json=? WHERE review_id=?", (json.dumps(modified), review))
    with pytest.raises(ValueError):
        store.get(run, review)


@pytest.mark.parametrize("seal", [False, True])
def test_reconstruction_reconciles_only_actual_full_sealed_prefix(tmp_path, seal):
    store, run, review, plan, _ = fixture(tmp_path)
    store.claim(run, review, plan["plan_id"], "approve")
    store.start_step(run, review, 0, plan["commands"][0])
    if seal:
        store.seal_step(run, review, 0, result(plan, exit_code=1))
    reconstructed = VerificationReviewStore(store.database)
    view = reconstructed.reconcile(run, review)
    assert view["status"] == ("completed" if seal else "indeterminate")
    assert view["success"] is (False if seal else None)
    assert view["has_unknown_command"] is (not seal)
    assert reconstructed.reconcile(run, review) == view
    if seal:
        replay, selected = reconstructed.claim(run, review, plan["plan_id"], "approve")
        assert replay["decision_replayed"] and selected is None
    else:
        with pytest.raises(RuntimeError):
            reconstructed.claim(run, review, plan["plan_id"], "approve")


def test_claim_without_step_stays_unknown_and_pending_cancel_does_not_forge_approval(tmp_path):
    store, run, review, plan, _ = fixture(tmp_path)
    cancelled = store.cancel_pending(run, review, plan["plan_id"])
    assert cancelled["status"] == "cancelled" and cancelled["steps"] == [] and cancelled["success"] is None
    assert store.cancel_pending(run, review, plan["plan_id"]) == cancelled
    with pytest.raises(RuntimeError):
        store.claim(run, review, plan["plan_id"], "approve")
    other = uuid4().hex
    store.save(run, "actual-source", "a" * 32, other, None, plan)
    with pytest.raises(ValueError, match="scope"):
        store.cancel_pending(run, other, "0" * 64)
    store.claim(run, other, plan["plan_id"], "approve")
    assert store.cancel_pending(run, other, plan["plan_id"])["status"] == "running"
    unknown = store.reconcile(run, other)
    assert unknown["status"] == "indeterminate" and unknown["steps"] == [] and unknown["success"] is None
    assert unknown["error_code"] == "verification_outcome_indeterminate"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_reconcile_never_rewrites_known_terminal_cause_despite_sealed_results(tmp_path, status):
    store, run, review, plan, _ = fixture(tmp_path)
    store.claim(run, review, plan["plan_id"], "approve")
    store.start_step(run, review, 0, plan["commands"][0])
    store.seal_step(run, review, 0, result(plan))
    final = store.finish(run, review, status)
    assert store.reconcile(run, review) == final and final["success"] is None


@pytest.mark.parametrize("stop_on_failure", [True, False])
def test_reconcile_partial_sealed_prefix_is_complete_only_for_reviewed_stop_rule(tmp_path, stop_on_failure):
    store, run, _, _, _ = fixture(tmp_path)
    config = tmp_path / ".doppel" / "verification.json"
    config.write_text(json.dumps({"commands": [{"name": name, "argv": ["fixture-only", name], "timeout_seconds": 5} for name in ("one", "two")],
                                  "stop_on_failure": stop_on_failure}), encoding="utf-8")
    plan = VerificationPipeline(tmp_path).prepare().as_dict()
    review = uuid4().hex
    store.save(run, "actual-source", "a" * 32, review, None, plan)
    store.claim(run, review, plan["plan_id"], "approve")
    store.start_step(run, review, 0, plan["commands"][0])
    store.seal_step(run, review, 0, result(plan, exit_code=1))
    view = store.reconcile(run, review)
    assert view["status"] == ("completed" if stop_on_failure else "indeterminate")
    assert view["success"] is (False if stop_on_failure else None)
    assert len(view["steps"]) == 1 and not view["has_unknown_command"]


def test_expired_pending_does_not_exhaust_live_capacity_before_page_sweep(tmp_path, monkeypatch):
    store, run, review, plan, _ = fixture(tmp_path)
    monkeypatch.setattr("doppel_agent.persistence.verification_reviews.MAX_PENDING", 1)
    old = datetime.now(UTC) - timedelta(seconds=2)
    with sqlite_connection(store.database) as db:
        db.execute("UPDATE verification_reviews SET created_at=?,expires_at=? WHERE review_id=?",
                   ((old - timedelta(seconds=5)).isoformat(), old.isoformat(), review))
    assert store.save(run, "actual-source", "a" * 32, uuid4().hex, None, plan)["status"] == "pending"
    with pytest.raises(ValueError, match="pending_budget"):
        store.save(run, "actual-source", "a" * 32, uuid4().hex, None, plan)


@pytest.mark.parametrize("error,supervision", [("verification_timeout", "terminated"),
    ("verification_output_limit", "terminated"), ("verification_supervision_unavailable", "unavailable")])
def test_settled_strict_failed_attempt_has_explicit_code_never_exit_zero_or_output(tmp_path, error, supervision):
    store, run, review, plan, _ = fixture(tmp_path)
    store.claim(run, review, plan["plan_id"], "approve")
    store.start_step(run, review, 0, plan["commands"][0])
    actual = result(plan)
    actual.update(exit_code=None, success=False, stdout="", stderr="", error=error, supervision=supervision)
    for change in ({"exit_code": 0, "success": True}, {"stdout": "claimed partial output"}, {"supervision": "unproved"}):
        with pytest.raises(ValueError, match="result_unavailable"):
            store.seal_step(run, review, 0, {**actual, **change})
    store.seal_step(run, review, 0, actual)
    done = store.finish(run, review, "completed")
    assert not done["success"] and done["steps"][0]["result"]["error"] == error
