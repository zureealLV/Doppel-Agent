"""Durable DAG/dispatch store oracles, defined now and run at S9.

Native run records below are fixture SQLite rows, not provider executions.
"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.work_orders import WorkOrderStore
from doppel_agent.tasks.work_orders import execution_settings, plan_from_payload


def setup_order(tmp_path, *, tasks=None, permissions=None):
    path = tmp_path / "runtime.sqlite3"
    store = WorkOrderStore(path)
    tasks = tasks or [
        {"id": "read", "title": "read", "prompt": "read fixture"},
        {"id": "change", "title": "change", "prompt": "prepare fixture change", "access": "write", "dependencies": ["read"]},
        {"id": "verify", "title": "verify", "prompt": "review fixture", "dependencies": ["change"]},
    ]
    order, _ = store.create(plan_from_payload({"title": "fixture", "tasks": tasks}))
    settings = {"profile_id": "mock-fixture", "permissions": permissions or {}}
    store.activate(order["work_order_id"], expected_revision=1, settings=settings)
    return store, RuntimeRunStore(path), order["work_order_id"]


def accept(runs, intent):
    request = intent["request"]
    return runs.create(uuid4().hex, uuid4().hex, request["mode"], request, intent["admission_key"], native=True,
                       profile_snapshot={"id": "mock-fixture", "provider": "explicit_override"}, frozen_input_snapshot=intent["input_snapshot"])[0]


def finish(runs, run, outcome="completed", *, release=True):
    runs.update(run["run_id"], outcome)
    if release:
        runs.release_conversation_turn(run["run_id"])


def test_frozen_intent_rejects_matching_request_with_wrong_profile_or_write_scope(tmp_path):
    for index, mismatch in enumerate(("profile", "write")):
        path = tmp_path / f"frozen-{index}.sqlite3"
        store = WorkOrderStore(path)
        node = {"id": "fix", "title": "fix", "prompt": "fixture", "access": "write"}
        order, _ = store.create(plan_from_payload({"title": "frozen", "tasks": [node]}))
        snapshot = {"id": "offline", "provider": "mock", "model": "original", "base_url": ""}
        store.activate(order["work_order_id"], expected_revision=1,
                       settings={"profile_id": "offline", "profiles": {"offline": snapshot}})
        intent = store.reserve_next(order["work_order_id"], expected_revision=1)
        assert store.intent_by_key(intent["admission_key"])["profile_snapshot"] == snapshot
        wrong = {**snapshot, "model": "changed"} if mismatch == "profile" else snapshot
        runs = RuntimeRunStore(path)
        runs.create(uuid4().hex, uuid4().hex, "graph", intent["request"], intent["admission_key"],
                    native=True, profile_snapshot=wrong, write_scope=mismatch != "write")
        with pytest.raises(ValueError, match="profile or write scope"):
            store.reconcile(order["work_order_id"])
        assert store.get(order["work_order_id"])["attempts"][0]["run_id"] is None


def test_frozen_profile_fields_exclude_credentials_and_revisions_cannot_replace_them(tmp_path):
    snapshot = {"id": "offline", "provider": "mock", "model": "original", "base_url": ""}
    with pytest.raises(ValueError, match="fields"):
        execution_settings({"profiles": {"offline": {**snapshot, "api_key": "NOT_A_REAL_KEY"}}})
    store, _, identifier = setup_order(tmp_path)
    # The revision operation can append server-resolved profiles, not modify a saved one.
    current = plan_from_payload(store.get(identifier)["plan"])
    revision = store.revise_plan(identifier, current, expected_revision=1, profiles={"offline": snapshot})
    with pytest.raises(ValueError, match="cannot change"):
        store.revise_plan(identifier, current, expected_revision=2,
                          profiles={"offline": {**snapshot, "model": "changed"}})
    assert store.get(identifier)["execution"] == revision["execution"]


def test_migration_nine_preserves_preexisting_runtime_and_plan_rows(tmp_path, monkeypatch):
    from doppel_agent.persistence import migrations

    path = tmp_path / "migration.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", {k: v for k, v in migrations.MIGRATIONS.items() if k < 9})
        old = WorkOrderStore(path)
        order, _ = old.create(plan_from_payload({"title": "existing", "tasks": [
            {"id": "read", "title": "read", "prompt": "existing fixture"},
        ]}))
        with sqlite3.connect(path) as db:
            db.execute("""INSERT INTO runtime_runs(run_id,thread_id,status,mode,request_json,created_at,updated_at)
                          VALUES ('old-run','old-thread','completed','graph','{}','old','old')""")
    reopened = WorkOrderStore(path)
    assert reopened.get(order["work_order_id"])["plan"] == order["plan"]
    assert RuntimeRunStore(path).get("old-run")["write_scope"] is False
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=9").fetchone()[0] == 1


def test_reserved_dispatch_survives_reopen_without_creating_a_second_attempt(tmp_path):
    store, _, identifier = setup_order(tmp_path)
    first = store.reserve_next(identifier, expected_revision=1)
    assert first["task_id"] == "read" and first["attempt_status"] == "reserved"
    reopened = WorkOrderStore(store.database)
    assert reopened.reserve_next(identifier, expected_revision=1) is None
    assert reopened.pending_intents(identifier) == [first]
    assert len(reopened.get(identifier)["attempts"]) == 1


def test_readiness_query_accepts_no_work_and_uses_current_revision(tmp_path):
    store, _, identifier = setup_order(tmp_path)
    assert [task["task_id"] for task in store.ready_tasks(identifier, expected_revision=1)] == ["read"]
    assert store.get(identifier)["attempts"] == []
    with pytest.raises(ValueError, match="revision changed"):
        store.reserve_next(identifier, expected_revision=2)
    assert store.pending_intents(identifier) == []


def test_acceptance_before_mapping_is_reconciled_by_exact_admission_key(tmp_path):
    store, runs, identifier = setup_order(tmp_path)
    first = store.reserve_next(identifier, expected_revision=1)
    run = accept(runs, first)
    # Simulate process loss after runtime durable acceptance, before mapping.
    reopened = WorkOrderStore(store.database)
    record = reopened.reconcile(identifier)
    assert record["attempts"][0]["run_id"] == run["run_id"]
    assert record["attempts"][0]["status"] == "accepted"
    assert reopened.pending_intents(identifier) == []
    assert reopened.reserve_next(identifier, expected_revision=1) is None
    assert reopened.reconcile(identifier)["attempts"][0]["run_id"] == run["run_id"]


def test_terminal_status_does_not_unlock_dependencies_before_runtime_drain(tmp_path):
    store, runs, identifier = setup_order(tmp_path)
    first = store.reserve_next(identifier, expected_revision=1)
    run = accept(runs, first)
    finish(runs, run, release=False)
    assert store.reserve_next(identifier, expected_revision=1) is None
    record = store.get(identifier)
    assert record["tasks"][0]["status"] == "running"
    assert record["attempts"][0]["runtime_status"] == "completed"
    assert record["attempts"][0]["runtime_lease_active"] == 1
    runs.release_conversation_turn(run["run_id"])
    next_intent = store.reserve_next(identifier, expected_revision=1)
    assert next_intent["task_id"] == "change"


def test_approval_wait_is_live_and_never_replayed_or_marked_succeeded(tmp_path):
    store, runs, identifier = setup_order(tmp_path)
    first = store.reserve_next(identifier, expected_revision=1)
    run = accept(runs, first)
    runs.update(run["run_id"], "interrupted", metadata={"interrupts": [{"id": "fixture", "value": {}}]})
    record = store.reconcile(identifier)
    assert record["tasks"][0]["status"] == "awaiting_approval"
    assert store.reserve_next(identifier, expected_revision=1) is None
    with pytest.raises(ValueError):
        store.retry_task(identifier, "read", expected_revision=1, expected_attempt_id=first["attempt_id"])


def test_failed_ancestry_blocks_descendants_even_in_reverse_presentation_order(tmp_path):
    tasks = [{"id": "last", "title": "last", "prompt": "last", "dependencies": ["middle"]},
             {"id": "middle", "title": "middle", "prompt": "middle", "dependencies": ["first"]},
             {"id": "first", "title": "first", "prompt": "first"}]
    store, runs, identifier = setup_order(tmp_path, tasks=tasks)
    first = store.reserve_next(identifier, expected_revision=1)
    finish(runs, accept(runs, first), "failed")
    record = store.reconcile(identifier)
    assert record["status"] == "failed"
    assert [task["status"] for task in record["tasks"]] == ["blocked", "blocked", "failed"]
    assert store.reserve_next(identifier, expected_revision=1) is None


def test_explicit_retry_preserves_prior_success_and_uses_a_new_bounded_attempt(tmp_path):
    store, runs, identifier = setup_order(tmp_path)
    first = store.reserve_next(identifier, expected_revision=1)
    first_run = accept(runs, first)
    finish(runs, first_run)
    failed = store.reserve_next(identifier, expected_revision=1)
    finish(runs, accept(runs, failed), "failed")
    store.reconcile(identifier)
    record = store.retry_task(identifier, "change", expected_revision=1, expected_attempt_id=failed["attempt_id"])
    assert record["status"] == "paused" and record["tasks"][0]["status"] == "succeeded"
    assert store.reserve_next(identifier, expected_revision=1) is None
    store.control(identifier, "resume", expected_revision=1)
    second = store.reserve_next(identifier, expected_revision=1)
    assert second["task_id"] == "change" and second["attempt_number"] == 2
    assert second["admission_key"] != failed["admission_key"]
    assert store.get(identifier)["attempts"][0]["run_id"] == first_run["run_id"]
    with pytest.raises(ValueError):
        store.retry_task(identifier, "read", expected_revision=1, expected_attempt_id=first["attempt_id"])


def test_completed_task_is_not_replayed_when_retrying_a_downstream_failure(tmp_path):
    store, runs, identifier = setup_order(tmp_path)
    first = store.reserve_next(identifier, expected_revision=1)
    finish(runs, accept(runs, first))
    second = store.reserve_next(identifier, expected_revision=1)
    finish(runs, accept(runs, second), "failed")
    store.reconcile(identifier)
    with pytest.raises(ValueError, match="latest failed"):
        store.retry_task(identifier, "read", expected_revision=1, expected_attempt_id=first["attempt_id"])
    assert store.get(identifier)["tasks"][0]["status"] == "succeeded"


def test_attempt_limit_is_explicit_and_never_auto_retries_a_failed_run(tmp_path):
    store, runs, identifier = setup_order(tmp_path)
    for number in (1, 2, 3):
        intent = store.reserve_next(identifier, expected_revision=1)
        assert intent["attempt_number"] == number
        finish(runs, accept(runs, intent), "failed")
        assert store.reconcile(identifier)["status"] == "failed"
        assert store.reserve_next(identifier, expected_revision=1) is None
        if number < 3:
            store.retry_task(identifier, "read", expected_revision=1, expected_attempt_id=intent["attempt_id"])
            store.control(identifier, "resume", expected_revision=1)
    with pytest.raises(ValueError, match="attempt limit"):
        store.retry_task(identifier, "read", expected_revision=1, expected_attempt_id=intent["attempt_id"])
    assert len(store.get(identifier)["attempts"]) == 3


def test_pause_and_cancel_keep_admission_and_io_truthful(tmp_path):
    store, runs, identifier = setup_order(tmp_path)
    reserved = store.reserve_next(identifier, expected_revision=1)
    store.control(identifier, "pause", expected_revision=1)
    assert store.pending_intents(identifier) == [reserved]
    assert store.reserve_next(identifier, expected_revision=1) is None
    accepted = accept(runs, reserved)
    cancelled = store.control(identifier, "cancel", expected_revision=1)
    assert cancelled["status"] == "cancelled"
    assert cancelled["tasks"][0]["status"] == "dispatching"
    assert [task["status"] for task in cancelled["tasks"][1:]] == ["cancelled", "cancelled"]
    finish(runs, accepted, "cancelled", release=False)
    assert store.reconcile(identifier)["tasks"][0]["status"] == "running"
    runs.release_conversation_turn(accepted["run_id"])
    assert store.reconcile(identifier)["tasks"][0]["status"] == "cancelled"
    assert store.reserve_next(identifier, expected_revision=1) is None


def test_unaccepted_intent_cannot_be_abandoned_once_matching_run_exists(tmp_path):
    store, runs, identifier = setup_order(tmp_path)
    reserved = store.reserve_next(identifier, expected_revision=1)
    accept(runs, reserved)
    with pytest.raises(ValueError, match="already accepted"):
        store.abandon_unaccepted(reserved["attempt_id"], admission_settled=True)
    assert store.get(identifier)["attempts"][0]["status"] == "reserved"  # transaction rolled back
    assert store.reconcile(identifier)["attempts"][0]["status"] == "accepted"


def test_safe_unaccepted_cancellation_abandons_without_fake_runtime(tmp_path):
    store, _, identifier = setup_order(tmp_path)
    reserved = store.reserve_next(identifier, expected_revision=1)
    store.control(identifier, "cancel", expected_revision=1)
    record = store.abandon_unaccepted(reserved["attempt_id"], admission_settled=True)
    assert record["attempts"][0]["status"] == "cancelled"
    assert record["attempts"][0]["run_id"] is None
    assert store.pending_intents(identifier) == []


def test_missing_runtime_row_is_not_proof_of_settled_admission(tmp_path):
    store, _, identifier = setup_order(tmp_path)
    intent = store.reserve_next(identifier, expected_revision=1)
    with pytest.raises(ValueError, match="admission must be settled"):
        store.abandon_unaccepted(intent["attempt_id"], admission_settled=False)
    assert store.pending_intents(identifier) == [intent]


def test_read_tasks_cannot_inherit_write_command_mcp_or_delegate_grants(tmp_path):
    permissions = {name: True for name in ("workspace_write", "command_execute", "mcp_execute", "delegate")}
    store, runs, identifier = setup_order(tmp_path, permissions=permissions)
    first = store.reserve_next(identifier, expected_revision=1)
    assert first["request"]["permissions"] == {name: False for name in permissions}
    finish(runs, accept(runs, first))
    writer = store.reserve_next(identifier, expected_revision=1)
    assert writer["request"]["permissions"] == permissions


def test_concurrent_reservers_commit_one_attempt_and_one_dispatch_intent(tmp_path):
    store, _, identifier = setup_order(tmp_path)
    barrier = Barrier(2)

    def reserve():
        barrier.wait(timeout=5)
        return store.reserve_next(identifier, expected_revision=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(result is not None for result in results) == 1
    assert len(store.get(identifier)["attempts"]) == len(store.pending_intents(identifier)) == 1


@pytest.mark.parametrize("settings", [{"permissions": {"workspace_write": 1}},
                                      {"api_key": "NOT-A-REAL-KEY"}, {"deadline_seconds": True}])
def test_execution_settings_reject_implicit_grants_and_credentials(settings):
    with pytest.raises(ValueError):
        execution_settings(settings)


def test_schema_active_attempt_index_defends_against_bypassed_repository(tmp_path):
    store, _, identifier = setup_order(tmp_path)
    first = store.reserve_next(identifier, expected_revision=1)
    with sqlite3.connect(store.database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("""
                INSERT INTO work_order_attempts VALUES (?, ?, 1, 'change', 1, NULL, 'reserved', 'fixture', 'fixture')
            """, (uuid4().hex, identifier))
    assert store.pending_intents(identifier) == [first]


def test_changed_request_under_a_dispatch_key_fails_closed_without_mapping(tmp_path):
    store, runs, identifier = setup_order(tmp_path)
    reserved = store.reserve_next(identifier, expected_revision=1)
    changed = {**reserved["request"], "prompt": "different request"}
    runs.create(uuid4().hex, uuid4().hex, changed["mode"], changed, reserved["admission_key"], native=True)
    with pytest.raises(ValueError, match="different runtime request"):
        store.reconcile(identifier)
    assert store.get(identifier)["attempts"][0]["run_id"] is None
    assert store.pending_intents(identifier)[0]["state"] == "pending"


def test_reservation_transaction_rolls_back_both_attempt_and_intent_on_failure(tmp_path, monkeypatch):
    store, _, identifier = setup_order(tmp_path)
    original = store._intent

    def fail_after_insert(db, attempt_id):
        original(db, attempt_id)
        raise RuntimeError("fixture after intent insertion")

    monkeypatch.setattr(store, "_intent", fail_after_insert)
    with pytest.raises(RuntimeError, match="fixture after intent"):
        store.reserve_next(identifier, expected_revision=1)
    assert store.get(identifier)["attempts"] == []
    assert store.get(identifier)["status"] == "queued"
    monkeypatch.setattr(store, "_intent", original)
    assert store.pending_intents(identifier) == []
    assert store.reserve_next(identifier, expected_revision=1)["attempt_number"] == 1


def test_all_drained_nodes_complete_order_without_synthesizing_verification(tmp_path):
    store, runs, identifier = setup_order(tmp_path)
    for task_id in ("read", "change", "verify"):
        intent = store.reserve_next(identifier, expected_revision=1)
        assert intent["task_id"] == task_id
        finish(runs, accept(runs, intent))
    record = store.reconcile(identifier)
    assert record["status"] == "succeeded"
    assert len(record["attempts"]) == 3
    assert store.reserve_next(identifier, expected_revision=1) is None
    assert "verification_passed" not in record


def test_work_order_control_schema_rejects_implicit_grants_and_stale_scope_edits(tmp_path):
    from pydantic import ValidationError
    from doppel_agent.api.schemas import WorkOrderActivate

    with pytest.raises(ValidationError):
        WorkOrderActivate.model_validate({"expected_revision": 1, "settings": {"permissions": {"workspace_write": 1}}})
    store, _, identifier = setup_order(tmp_path)
    with pytest.raises(ValueError, match="draft"):
        store.activate(identifier, expected_revision=1, settings={"permissions": {"workspace_write": True}})
    assert not store.get(identifier)["execution"]["permissions"]["workspace_write"]
