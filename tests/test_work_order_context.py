"""S5 fixed execution-context/DAG fixture definitions; unexecuted until S9."""

from uuid import uuid4
import json

import pytest

from doppel_agent.context.input import ContextInputResolver
from doppel_agent.context.manifest import ContextManifestError, ManifestReader, ManifestStore
from doppel_agent.context.notes import ProjectNoteStore
from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.work_orders import WorkOrderStore
from doppel_agent.tasks.work_orders import plan_from_payload


def plan(future="future prompt"):
    return plan_from_payload({"title": "fixture", "tasks": [
        {"id": "first", "title": "first", "prompt": "first prompt"},
        {"id": "second", "title": "second", "prompt": future, "dependencies": ["first"]},
    ]})


def fixture(tmp_path):
    database = tmp_path / "runtime.sqlite3"
    reader = ManifestReader(tmp_path)
    store = WorkOrderStore(database, input_resolver=ContextInputResolver(reader).resolve)
    order = store.create(plan())[0]
    notes = ProjectNoteStore(database)
    payload = {"kind": "constraint", "title": "fixture", "body": "original fixed assertion", "scope_work_order_id": order["work_order_id"],
        "sources": [{"kind": "user", "label": "human"}]}
    note = notes.create(payload, confirmed=True, idempotency_key="work-context-note")
    (tmp_path / "source.txt").write_text("original fixed file", encoding="utf-8")
    preview = reader.preview([{"path": "source.txt"}])
    manifest = ManifestStore(database).accept(reader, [{"path": "source.txt", "expected_file_sha256": preview["entries"][0]["file_sha256"]}],
        budget_bytes=65536, confirmed=True, idempotency_key="work-context-manifest")
    descriptor = {"manifest_id": manifest["manifest_id"], "notes": [{"note_id": note["note_id"], "revision": 1}]}
    return store, RuntimeRunStore(database), notes, order["work_order_id"], note, payload, descriptor


def accept(runs, intent):
    return runs.create(uuid4().hex, uuid4().hex, intent["request"]["mode"], intent["request"], intent["admission_key"],
        native=True, frozen_input_snapshot=intent["input_snapshot"])[0]


def finish(runs, run, answer="successful predecessor data", status="completed", release=True):
    runs.update(run["run_id"], status, answer=answer)
    if release:
        runs.release_conversation_turn(run["run_id"])


def test_activation_freezes_scoped_context_once_and_lost_reply_does_not_recheck_changed_sources(tmp_path):
    store, runs, notes, oid, note, payload, descriptor = fixture(tmp_path)
    settings = {"context": descriptor}
    activated = store.activate(oid, expected_revision=1, settings=settings)
    (tmp_path / "source.txt").write_text("later source", encoding="utf-8")
    notes.update(note["note_id"], {**payload, "body": "later assertion"}, expected_revision=1, confirmed=True, idempotency_key="work-context-edited")
    assert store.activate(oid, expected_revision=1, settings=settings) == activated
    intent = store.reserve_next(oid, expected_revision=1)
    assert "original fixed file" in intent["input_snapshot"]["rendered_context"]
    assert "original fixed assertion" in intent["input_snapshot"]["rendered_context"]
    assert "later source" not in intent["input_snapshot"]["rendered_context"]
    run = accept(runs, intent)
    assert run["input_snapshot"] == intent["input_snapshot"] and run["request"] == intent["request"]
    store.reconcile(oid)


def test_context_projection_keeps_original_resolution_revision_on_carry_but_not_fresh_rebind(tmp_path):
    store, _, _, oid, _, _, descriptor = fixture(tmp_path)
    resolver = store.input_resolver
    calls = []

    def resolve(db, spec, *, scope_work_order_id):
        snapshot = resolver(db, spec, scope_work_order_id=scope_work_order_id)
        calls.append(scope_work_order_id)
        snapshot["captured_at"] = f"fixture-capture-{len(calls)}"
        return snapshot

    store.input_resolver = resolve
    first = store.activate(oid, expected_revision=1, settings={"context": descriptor})
    assert first["context_bindings"][0]["bound_revision"] == 1
    carried = store.revise_plan(oid, plan(), expected_revision=1)
    assert carried["context_bindings"] == [{**first["context_bindings"][0], "revision": 2}]
    assert calls == [oid]  # Carry is not a fresh source check/confirmation.
    rebound = store.revise_plan(oid, plan(), expected_revision=2, context=descriptor)
    assert rebound["status"] == "paused" and rebound["active_revision"] == 3
    assert rebound["context_bindings"][0]["revision"] == rebound["context_bindings"][0]["bound_revision"] == 3
    assert rebound["context_bindings"][0]["context_sha256"] == first["context_bindings"][0]["context_sha256"]
    assert rebound["context_bindings"][0]["captured_at"] != first["context_bindings"][0]["captured_at"]
    assert calls == [oid, oid]
    assert WorkOrderStore(store.database).get(oid)["context_bindings"] == rebound["context_bindings"]


def test_activation_failure_is_atomic_and_public_frozen_snapshots_cannot_be_invented(tmp_path):
    store, _, _, oid, _, _, descriptor = fixture(tmp_path)
    (tmp_path / "source.txt").write_text("changed before activation", encoding="utf-8")
    with pytest.raises(ContextManifestError, match="context_file_stale"):
        store.activate(oid, expected_revision=1, settings={"context": descriptor})
    assert store.get(oid)["status"] == "draft"
    with pytest.raises(ValueError, match="execution settings"):
        store.activate(oid, expected_revision=1, settings={"input_snapshot": {"invented": True}})
    with sqlite_connection(store.database) as db:
        assert db.execute("SELECT COUNT(*) FROM work_order_context_bindings WHERE input_snapshot_json IS NOT NULL").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM work_order_attempts").fetchone()[0] == 0


def test_only_real_success_drained_result_is_bound_with_exact_ids_hashes_and_bounded_text(tmp_path):
    store, runs, _, oid, _, _, descriptor = fixture(tmp_path)
    store.activate(oid, expected_revision=1, settings={"context": descriptor})
    first = store.reserve_next(oid, expected_revision=1)
    run = accept(runs, first)
    finish(runs, run, "界" * 10000, release=False)
    assert store.reserve_next(oid, expected_revision=1) is None
    runs.release_conversation_turn(run["run_id"])
    second = store.reserve_next(oid, expected_revision=1)
    predecessor = second["input_snapshot"]["predecessors"][0]
    assert predecessor["run_id"] == run["run_id"] and predecessor["attempt_id"] == first["attempt_id"]
    assert predecessor["task_id"] == "first" and predecessor["execution_revision"] == 1
    assert predecessor["result_bytes"] == 30000 and predecessor["included_bytes"] <= 8192
    assert predecessor["included_bytes"] == len(predecessor["text"].encode("utf-8"))
    assert predecessor["truncation_reasons"] and second["input_snapshot"]["context_bytes"] <= 128 * 1024
    accepted = accept(runs, second)
    assert accepted["input_snapshot"] == second["input_snapshot"]


def test_retry_and_carried_begun_revision_keep_old_input_while_rebind_only_changes_future_nodes(tmp_path):
    store, runs, _, oid, _, _, descriptor = fixture(tmp_path)
    store.activate(oid, expected_revision=1, settings={"context": descriptor})
    first = store.reserve_next(oid, expected_revision=1)
    run = accept(runs, first)
    finish(runs, run, status="failed")
    store.reconcile(oid)
    changed = store.revise_plan(oid, plan("revised future"), expected_revision=1, context={"manifest_id": None, "notes": []})
    assert changed["tasks"][0]["execution_revision"] == 1
    store.retry_task(oid, "first", expected_revision=2, expected_attempt_id=first["attempt_id"])
    store.control(oid, "resume", expected_revision=2)
    retry = store.reserve_next(oid, expected_revision=2)
    assert retry["input_snapshot"] == first["input_snapshot"] and retry["request"]["context"] == descriptor
    retry_run = accept(runs, retry)
    finish(runs, retry_run)
    second = store.reserve_next(oid, expected_revision=2)
    assert second["revision"] == 2 and second["request"]["context"] == {"manifest_id": None, "notes": []}
    assert second["input_snapshot"]["manifest"] is None and second["input_snapshot"]["notes"] == []
    assert second["input_snapshot"]["predecessors"][0]["run_id"] == retry_run["run_id"]


def test_success_text_or_pending_lease_is_not_a_dependency_completion_oracle(tmp_path):
    store, runs, _, oid, _, _, _ = fixture(tmp_path)
    store.activate(oid, expected_revision=1, settings={})
    intent = store.reserve_next(oid, expected_revision=1)
    run = accept(runs, intent)
    finish(runs, run, answer="ALL TASKS PASSED", status="failed")
    assert store.reserve_next(oid, expected_revision=1) is None
    assert store.get(oid)["tasks"][1]["status"] == "blocked"


def test_input_mismatch_reconciliation_fails_and_oversize_result_creates_no_partial_attempt(tmp_path):
    store, runs, _, oid, _, _, descriptor = fixture(tmp_path)
    store.activate(oid, expected_revision=1, settings={"context": descriptor})
    first = store.reserve_next(oid, expected_revision=1)
    run = accept(runs, first)
    with sqlite_connection(store.database) as db:
        db.execute("UPDATE runtime_runs SET input_snapshot_json=NULL WHERE run_id=?", (run["run_id"],))
    with pytest.raises(ValueError, match="input snapshot changed"):
        store.reconcile(oid)
    with sqlite_connection(store.database) as db:
        db.execute("UPDATE runtime_runs SET input_snapshot_json=? WHERE run_id=?", (json.dumps(first["input_snapshot"]), run["run_id"]))
    finish(runs, run, answer="x" * (1024 * 1024 + 1))
    with pytest.raises(ContextManifestError, match="predecessor_result_too_large"):
        store.reserve_next(oid, expected_revision=1)
    assert len(store.get(oid)["attempts"]) == 1


def test_serialized_total_truncates_only_predecessor_text_preserving_all_identities_and_hashes(tmp_path):
    reader = ManifestReader(tmp_path)
    path = tmp_path / "runtime.sqlite3"
    store, notes, runs = WorkOrderStore(path, input_resolver=ContextInputResolver(reader).resolve), ProjectNoteStore(path), RuntimeRunStore(path)
    nodes = [{"id": key, "title": key, "prompt": key} for key in ("left", "right")]
    nodes.append({"id": "join", "title": "join", "prompt": "join", "dependencies": ["left", "right"]})
    oid = store.create(plan_from_payload({"title": "fixture", "tasks": nodes}))[0]["work_order_id"]
    refs = []
    for index in range(8):
        note = notes.create({"kind": "fact", "title": str(index), "body": "x" * 15000,
            "sources": [{"kind": "user", "label": "human"}]}, confirmed=True, idempotency_key=f"serialized-context-note-{index}")
        refs.append({"note_id": note["note_id"], "revision": 1})
    store.activate(oid, expected_revision=1, settings={"context": {"manifest_id": None, "notes": refs}})
    for _ in range(2):
        intent = store.reserve_next(oid, expected_revision=1)
        run = accept(runs, intent)
        finish(runs, run, answer="result data " * 2000)
    join = store.reserve_next(oid, expected_revision=1)["input_snapshot"]
    assert join["context_bytes"] <= 128 * 1024 and len(join["notes"]) == 8
    assert {entry["task_id"] for entry in join["predecessors"]} == {"left", "right"}
    assert any("input_budget" in entry["truncation_reasons"] for entry in join["predecessors"])
    assert all(len(entry["result_sha256"]) == len(entry["included_sha256"]) == 64 for entry in join["predecessors"])


def test_old_nullable_intent_and_execution_rows_survive_migration_without_rebinding(tmp_path, monkeypatch):
    from doppel_agent.persistence import migrations
    from doppel_agent.tasks.work_orders import dispatch_request

    path = tmp_path / "runtime.sqlite3"
    aid = uuid4().hex
    key = f"work-order:{aid}"
    options = {"permissions": {}, "profile_id": None, "effort": "balanced", "deadline_seconds": 600}
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", {v: sql for v, sql in migrations.MIGRATIONS.items() if v < 14})
        store = WorkOrderStore(path)
        old = store.create(plan())[0]
        oid = old["work_order_id"]
        payload = dispatch_request(old["tasks"][0], options, key)
        before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        # Prefix fixture uses old SQL for state changed before this new contract.
        with sqlite_connection(path) as db:
            db.execute("UPDATE work_orders SET status='running',execution_json=? WHERE work_order_id=?", (json.dumps(options), oid))
            db.execute("INSERT INTO work_order_attempts(attempt_id,work_order_id,revision,task_id,attempt_number,status,created_at,updated_at) VALUES(?,?,1,'first',1,'reserved','fixture','fixture')", (aid, oid))
            db.execute("INSERT INTO work_order_dispatch_intents(attempt_id,admission_key,request_json,state,created_at,updated_at) VALUES(?,?,?,'pending','fixture','fixture')", (aid, key, before))
            db.execute("UPDATE work_order_tasks SET status='dispatching' WHERE work_order_id=? AND task_id='first'", (oid,))
    store = WorkOrderStore(path)
    intent = store.pending_intents(oid)[0]
    assert intent["input_snapshot"] is None and "context" not in intent["request"]
    assert store.reserve_next(oid, expected_revision=1) is None
    assert WorkOrderStore(path).pending_intents(oid)[0] == intent
    with sqlite_connection(path) as db:
        assert db.execute("SELECT request_json FROM work_order_dispatch_intents WHERE attempt_id=?", (intent["attempt_id"],)).fetchone()[0] == before
        assert db.execute("SELECT input_snapshot_json FROM work_order_context_bindings WHERE work_order_id=?", (oid,)).fetchone()[0] is None
