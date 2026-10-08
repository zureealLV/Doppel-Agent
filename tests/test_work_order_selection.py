"""S4 selection persistence definitions; execute together at S9, not receipts.

Only temporary fixture databases/ledger rows; no provider or user store access.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from doppel_agent.persistence import migrations
from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.work_orders import WorkOrderStore
from doppel_agent.tasks.work_orders import plan_from_payload


def draft(store, title="fixture"):
    return store.create(plan_from_payload({"title": title, "tasks": [
        {"id": "read", "title": "read", "prompt": "offline fixture"},
    ]}))[0]["work_order_id"]


def associated_run(store, runs, identifier):
    store.activate(identifier, expected_revision=1, settings={})
    intent = store.reserve_next(identifier, expected_revision=1)
    run = runs.create(uuid4().hex, uuid4().hex, "graph", intent["request"], intent["admission_key"], native=True)[0]
    store.reconcile(identifier)
    return run["run_id"]


def save(store, identifier, run=None, *, revision=None, key=None):
    return store.set_selection(identifier, run,
        expected_revision=store.selection()["revision"] if revision is None else revision,
        idempotency_key=key or uuid4().hex)


def test_never_saved_differs_from_explicit_null_and_reads_do_not_seed_rows(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store = WorkOrderStore(path)
    identifier = draft(store)
    assert store.selection() == {"saved": False, "work_order_id": None, "run_id": None, "revision": 0, "request_key": None}
    assert store.run_selection(identifier) == {"saved": False, "work_order_id": identifier, "run_id": None}
    with sqlite_connection(path) as db:
        assert db.execute("SELECT COUNT(*) FROM work_order_workspace_selection").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM work_order_run_selection").fetchone()[0] == 0
    cleared = save(store, None)
    assert cleared["saved"] and cleared["revision"] == 1 and cleared["work_order_id"] is None
    assert WorkOrderStore(path).selection() == cleared


def test_reopen_and_global_clear_preserve_per_order_associated_run_not_recent_list(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store, runs = WorkOrderStore(path), RuntimeRunStore(path)
    first, second = draft(store, "older"), draft(store, "newer")
    run = associated_run(store, runs, first)
    selected = save(store, first, run)
    assert WorkOrderStore(path).selection() == selected
    assert store.list(1)[0]["work_order_id"] == second
    save(store, second)
    assert store.run_selection(first)["run_id"] == run
    save(store, None)
    assert store.selection()["work_order_id"] is None
    assert store.run_selection(first)["run_id"] == run
    save(store, first, None)
    assert store.run_selection(first) == {"saved": True, "work_order_id": first, "run_id": None}


def test_selection_does_not_rewrite_plan_scope_attempt_or_runtime_audit(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store, runs = WorkOrderStore(path), RuntimeRunStore(path)
    identifier = draft(store)
    run = associated_run(store, runs, identifier)
    before_order, before_run = store.get(identifier), runs.get(run)
    save(store, identifier, run)
    save(store, None)
    assert store.get(identifier) == before_order
    assert runs.get(run) == before_run
    assert store.plan(identifier, 1) == before_order["plan"]


def test_cross_order_standalone_missing_and_orphan_runs_fail_without_metadata_mutation(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store, runs = WorkOrderStore(path), RuntimeRunStore(path)
    first, second = draft(store), draft(store)
    run = associated_run(store, runs, first)
    standalone = runs.create(uuid4().hex, uuid4().hex, "graph", {"prompt": "fixture", "mode": "graph"}, None, native=True)[0]
    before = save(store, first, run)
    for identifier, run_id in ((second, run), (first, standalone["run_id"]), (first, uuid4().hex), (None, run)):
        with pytest.raises(ValueError):
            save(store, identifier, run_id)
        assert store.selection() == before
    with pytest.raises(ValueError, match="not found"):
        store.run_selection(uuid4().hex)
    assert store.run_selection(second)["saved"] is False


@pytest.mark.parametrize("revision,key,identifier", [
    (True, "valid-key", None), (-1, "valid-key", None), ("0", "valid-key", None),
    (0, "short", None), (0, "x" * 129, None), (0, "nul\x00key!", None), (0, "valid-key", "not-a-hex-id"),
])
def test_selection_input_boundaries_do_not_create_state(tmp_path, revision, key, identifier):
    store = WorkOrderStore(tmp_path / "runtime.sqlite3")
    with pytest.raises(ValueError):
        save(store, identifier, revision=revision, key=key)
    assert store.selection()["saved"] is False


def test_lost_reply_replays_exact_metadata_key_and_stale_write_cannot_overwrite_newer_selection(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store = WorkOrderStore(path)
    first, second = draft(store), draft(store)
    key = "lost-selection-reply"
    accepted = save(store, first, revision=0, key=key)
    assert save(WorkOrderStore(path), first, revision=0, key=key) == accepted
    with pytest.raises(ValueError, match="different request"):
        save(store, second, revision=0, key=key)
    current = save(store, second, revision=1)
    with pytest.raises(ValueError, match="revision changed"):
        save(store, first, revision=0, key=key)
    assert store.selection() == current
    assert store.run_selection(first)["saved"] is True


def test_concurrent_selection_cas_has_exactly_one_winner(tmp_path):
    store = WorkOrderStore(tmp_path / "runtime.sqlite3")
    identifiers = [draft(store), draft(store)]
    barrier = Barrier(2)

    def write(identifier):
        barrier.wait(timeout=5)
        try:
            return save(store, identifier, revision=0)
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, identifier) for identifier in identifiers]
        results = [future.result(timeout=10) for future in futures]
    winner = [result for result in results if isinstance(result, dict)]
    assert len(winner) == 1 and store.selection() == winner[0]
    assert store.selection()["revision"] == 1
    loser = next(identifier for identifier in identifiers if identifier != winner[0]["work_order_id"])
    assert store.run_selection(loser)["saved"] is False


def test_failed_transaction_rolls_back_both_global_and_per_order_selection(tmp_path, monkeypatch):
    store = WorkOrderStore(tmp_path / "runtime.sqlite3")
    first, second = draft(store), draft(store)
    before = save(store, first)

    def fail_projection(db):
        raise RuntimeError("fixture projection failure after metadata writes")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_selection", fail_projection)
        with pytest.raises(RuntimeError, match="projection failure"):
            store.set_selection(second, None, expected_revision=before["revision"], idempotency_key="rollback-selection-key")
    assert store.selection() == before
    assert store.run_selection(second)["saved"] is False


def test_additive_selection_migration_leaves_existing_plans_and_runtime_rows_untouched(tmp_path, monkeypatch):
    path = tmp_path / "runtime.sqlite3"
    tables = ('work_orders', 'work_order_plans', 'work_order_tasks', 'work_order_dependencies',
              'work_order_task_bindings', 'work_order_attempts', 'work_order_dispatch_intents', 'runtime_runs')
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", {version: sql for version, sql in migrations.MIGRATIONS.items() if version < 10})
        store = WorkOrderStore(path)
        identifier = draft(store)
        before_plan = store.plan(identifier, 1)
        run, attempt = uuid4().hex, uuid4().hex
        # Seed real v9 columns, rather than calling a current mutator requiring
        # context binding/snapshot tables that did not exist in that version.
        with sqlite_connection(path) as db:
            db.execute("INSERT INTO runtime_runs(run_id,thread_id,status,mode,request_json,created_at,updated_at) "
                       "VALUES(?,?,'completed','graph','{}','old','old')", (run, uuid4().hex))
            db.execute("INSERT INTO work_order_attempts VALUES (?,?,1,'read',1,?,'succeeded','old','old')",
                       (attempt, identifier, run))
            db.execute("INSERT INTO work_order_dispatch_intents "
                       "(attempt_id,admission_key,request_json,state,run_id,created_at,updated_at) "
                       "VALUES (?,?,'{}','admitted',?,'old','old')", (attempt, 'old-admission-key', run))
            before = {table: [dict(row) for row in db.execute(f'SELECT * FROM {table}')] for table in tables}
    reopened = WorkOrderStore(path)
    assert reopened.plan(identifier, 1) == before_plan
    assert reopened.selection()["saved"] is False
    assert reopened.run_selection(identifier)["saved"] is False
    with sqlite_connection(path) as db:
        for table in tables:
            after = [dict(row) for row in db.execute(f'SELECT * FROM {table}')]
            assert len(after) == len(before[table])
            for old, current in zip(before[table], after, strict=True):
                assert {key: current[key] for key in old} == old
                assert {key: value for key, value in current.items() if key not in old} == (
                    {'input_snapshot_json': None} if table in ('runtime_runs', 'work_order_dispatch_intents') else {})
        assert db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=10").fetchone()[0] == 1
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
