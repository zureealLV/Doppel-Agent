"""Authored-plan revisions versus immutable execution provenance (S9 definitions)."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.work_orders import WorkOrderStore
from doppel_agent.tasks.work_orders import plan_from_payload


def plan(*, future_prompt="prepare change", future_access="write", extra=False):
    tasks = [{"id": "read", "title": "read", "prompt": "read fixture"},
             {"id": "change", "title": "change", "prompt": future_prompt, "access": future_access, "dependencies": ["read"]}]
    if extra:
        tasks.append({"id": "check", "title": "check", "prompt": "review changes", "dependencies": ["change"]})
    return plan_from_payload({"title": "fixture", "tasks": tasks})


def fixture(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store = WorkOrderStore(path)
    original, _ = store.create(plan(), "fixture-original")
    identifier = original["work_order_id"]
    store.activate(identifier, expected_revision=1, settings={"profile_id": "mock-fixture"})
    intent = store.reserve_next(identifier, expected_revision=1)
    return store, RuntimeRunStore(path), identifier, intent


def accept(runs, intent):
    request = intent["request"]
    return runs.create(uuid4().hex, uuid4().hex, request["mode"], request, intent["admission_key"], native=True,
        frozen_input_snapshot=intent["input_snapshot"])[0]


def finish(runs, run, status="completed"):
    runs.update(run["run_id"], status)
    runs.release_conversation_turn(run["run_id"])


def test_reserved_definition_and_request_survive_future_node_edit(tmp_path):
    store, _, identifier, intent = fixture(tmp_path)
    scope = store.get(identifier)["execution"]
    changed = store.revise_plan(identifier, plan(future_prompt="revised future", extra=True), expected_revision=1)
    assert changed["active_revision"] == 2 and changed["status"] == "paused"
    assert changed["tasks"][0]["execution_revision"] == 1
    assert changed["tasks"][1]["execution_revision"] == 2
    assert changed["attempts"][0]["attempt_id"] == intent["attempt_id"]
    assert store.pending_intents(identifier)[0]["request"] == intent["request"]
    assert changed["execution"] == scope
    assert store.plan(identifier, 1)["tasks"][1]["prompt"] == "prepare change"
    assert store.plan(identifier, 2)["tasks"][1]["prompt"] == "revised future"
    assert store.reserve_next(identifier, expected_revision=2) is None


def test_active_run_is_mapped_into_new_plan_without_new_attempt_or_lease_release(tmp_path):
    store, runs, identifier, intent = fixture(tmp_path)
    run = accept(runs, intent)
    runs.update(run["run_id"], "running")
    changed = store.revise_plan(identifier, plan(future_prompt="new task"), expected_revision=1)
    assert changed["tasks"][0]["status"] == "running"
    assert changed["attempts"][0]["run_id"] == run["run_id"]
    assert changed["attempts"][0]["runtime_lease_active"] == 1
    store.control(identifier, "resume", expected_revision=2)
    assert store.reserve_next(identifier, expected_revision=2) is None
    finish(runs, run)
    future = store.reserve_next(identifier, expected_revision=2)
    assert future["task_id"] == "change" and future["revision"] == 2
    assert future["request"]["prompt"] == "new task"
    assert store.get(identifier)["tasks"][0]["status"] == "succeeded"


@pytest.mark.parametrize("mutation", ["prompt", "mode", "access", "profile_id", "remove"])
def test_started_node_cannot_change_or_disappear(tmp_path, mutation):
    store, _, identifier, _ = fixture(tmp_path)
    payload = plan().payload()
    payload["tasks"] = [dict(task) for task in payload["tasks"]]
    if mutation == "remove":
        payload["tasks"] = [payload["tasks"][1]]
        payload["tasks"][0]["dependencies"] = []
    else:
        payload["tasks"][0][mutation] = {"prompt": "different", "mode": "deep", "access": "write", "profile_id": "other"}[mutation]
    with pytest.raises(ValueError, match="started task"):
        store.revise_plan(identifier, plan_from_payload(payload), expected_revision=1)
    assert store.get(identifier)["active_revision"] == 1
    assert store.plan(identifier, 2) is None


def test_completed_execution_is_carried_through_multiple_revisions_not_replayed(tmp_path):
    store, runs, identifier, intent = fixture(tmp_path)
    run = accept(runs, intent)
    finish(runs, run)
    store.revise_plan(identifier, plan(future_prompt="rev2"), expected_revision=1)
    changed = store.revise_plan(identifier, plan(future_prompt="rev3", extra=True), expected_revision=2)
    assert changed["tasks"][0]["execution_revision"] == 1
    assert changed["tasks"][0]["status"] == "succeeded"
    assert len(changed["attempts"]) == 1
    store.control(identifier, "resume", expected_revision=3)
    future = store.reserve_next(identifier, expected_revision=3)
    assert future["task_id"] == "change" and future["revision"] == 3
    assert future["attempt_number"] == 1
    assert len(store.get(identifier)["attempts"]) == 2


def test_pending_approval_tracks_original_run_after_plan_edit(tmp_path):
    store, runs, identifier, intent = fixture(tmp_path)
    run = accept(runs, intent)
    runs.update(run["run_id"], "interrupted", metadata={"interrupts": [{"id": "fixture", "value": {}}]})
    changed = store.revise_plan(identifier, plan(extra=True), expected_revision=1)
    assert changed["tasks"][0]["status"] == "awaiting_approval"
    assert changed["attempts"][0]["run_id"] == run["run_id"]
    store.control(identifier, "resume", expected_revision=2)
    assert store.reserve_next(identifier, expected_revision=2) is None


def test_retry_after_revision_keeps_execution_provenance_and_attempt_number(tmp_path):
    store, runs, identifier, intent = fixture(tmp_path)
    finish(runs, accept(runs, intent), "failed")
    store.revise_plan(identifier, plan(future_prompt="rev2"), expected_revision=1)
    retry = store.retry_task(identifier, "read", expected_revision=2, expected_attempt_id=intent["attempt_id"])
    assert retry["tasks"][0]["retry_requested"] == 1
    store.revise_plan(identifier, plan(future_prompt="rev3"), expected_revision=2)
    store.control(identifier, "resume", expected_revision=3)
    second = store.reserve_next(identifier, expected_revision=3)
    assert second["task_id"] == "read" and second["revision"] == 1 and second["attempt_number"] == 2
    assert second["admission_key"] != intent["admission_key"]
    finish(runs, accept(runs, second))
    future = store.reserve_next(identifier, expected_revision=3)
    assert future["task_id"] == "change" and future["revision"] == 3


def test_terminal_lease_drain_is_preserved_across_revision(tmp_path):
    store, runs, identifier, intent = fixture(tmp_path)
    run = accept(runs, intent)
    runs.update(run["run_id"], "completed")
    changed = store.revise_plan(identifier, plan(extra=True), expected_revision=1)
    assert changed["tasks"][0]["status"] == "running"
    store.control(identifier, "resume", expected_revision=2)
    assert store.reserve_next(identifier, expected_revision=2) is None
    runs.release_conversation_turn(run["run_id"])
    assert store.reserve_next(identifier, expected_revision=2)["task_id"] == "change"


def test_stale_and_concurrent_plan_edits_commit_at_most_one_revision(tmp_path):
    store, _, identifier, _ = fixture(tmp_path)
    barrier = Barrier(2)

    def revise(text):
        barrier.wait(timeout=5)
        try:
            return store.revise_plan(identifier, plan(future_prompt=text), expected_revision=1)
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(revise, text) for text in ("one", "two")]
        results = [future.result(timeout=10) for future in futures]
    assert sum(isinstance(result, dict) for result in results) == 1
    assert store.get(identifier)["active_revision"] == 2
    assert store.plan(identifier, 3) is None


def test_failed_revision_transaction_does_not_move_current_plan_or_binding(tmp_path, monkeypatch):
    store, _, identifier, _ = fixture(tmp_path)
    original = store._insert_plan

    def fail(db, identifier, revision, plan, now):
        original(db, identifier, revision, plan, now)
        raise RuntimeError("fixture revision write failed")

    monkeypatch.setattr(store, "_insert_plan", fail)
    with pytest.raises(RuntimeError):
        store.revise_plan(identifier, plan(extra=True), expected_revision=1)
    assert store.get(identifier)["active_revision"] == 1
    assert store.plan(identifier, 2) is None
    with sqlite3.connect(store.database) as db:
        assert db.execute("SELECT COUNT(*) FROM work_order_task_bindings WHERE plan_revision=2").fetchone()[0] == 0


def test_additive_binding_migration_preserves_old_plan_and_attempt_ids(tmp_path):
    store, _, identifier, intent = fixture(tmp_path)
    with sqlite3.connect(store.database) as db:
        db.execute("DROP TABLE work_order_task_bindings")
        db.execute("DELETE FROM schema_migrations WHERE version=7")
    reopened = WorkOrderStore(store.database)
    current = reopened.get(identifier)
    assert current["tasks"][0]["execution_revision"] == 1
    assert current["attempts"][0]["attempt_id"] == intent["attempt_id"]
    assert reopened.pending_intents(identifier)[0]["request"] == intent["request"]
