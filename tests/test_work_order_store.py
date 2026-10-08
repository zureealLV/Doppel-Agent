import sqlite3

import pytest

from doppel_agent.persistence.work_orders import WorkOrderStore
from doppel_agent.tasks.work_orders import plan_from_payload


def plan(title="fixture"):
    return plan_from_payload({"title": title, "tasks": [
        {"id": "read", "title": "read", "prompt": "read source"},
        {"id": "fix", "title": "fix", "prompt": "prepare minimal change", "dependencies": ["read"], "access": "write"},
    ]})


def test_draft_creation_persists_plan_without_accepting_runtime_runs(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store = WorkOrderStore(path)
    created, fresh = store.create(plan(), "fixture-key")
    assert fresh and created["status"] == "draft" and created["active_revision"] == 1
    reopened = WorkOrderStore(path).get(created["work_order_id"])
    assert reopened == created
    assert reopened["tasks"][1]["dependencies"] == ["read"]
    assert all(item["status"] == "pending" for item in reopened["tasks"])
    with sqlite3.connect(path) as db:
        for table in ("runtime_runs", "work_order_attempts", "work_order_dispatch_intents"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_draft_idempotency_replay_does_not_duplicate_or_change_plan(tmp_path):
    store = WorkOrderStore(tmp_path / "runtime.sqlite3")
    first, _ = store.create(plan(), "same-key")
    second, fresh = store.create(plan(), "same-key")
    assert not fresh and second == first
    with pytest.raises(ValueError, match="different request"):
        store.create(plan("different"), "same-key")
    assert len(store.list()) == 1


def test_invalid_input_creates_no_partial_work_order(tmp_path):
    store = WorkOrderStore(tmp_path / "runtime.sqlite3")
    with pytest.raises(ValueError):
        store.create(plan(), " ")
    assert store.list() == []
    with pytest.raises(ValueError):
        store.get("' OR 1=1 --")
    assert store.get("a" * 32) is None


def test_plan_edges_cannot_reference_tasks_in_another_revision(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    created, _ = WorkOrderStore(path).create(plan())
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO work_order_dependencies VALUES (?, 2, 'fix', 'read')", (created["work_order_id"],))


def test_draft_plan_replacement_preserves_history_and_requires_matching_revision(tmp_path):
    store = WorkOrderStore(tmp_path / "runtime.sqlite3")
    original, _ = store.create(plan(), "initial-key")
    identifier = original["work_order_id"]
    changed = store.replace_draft_plan(identifier, plan("revised"), expected_revision=1)
    assert changed["active_revision"] == 2 and changed["title"] == "revised"
    assert store.plan(identifier, 1)["title"] == "fixture"
    assert store.plan(identifier, 2)["title"] == "revised"
    with pytest.raises(ValueError, match="revision changed"):
        store.replace_draft_plan(identifier, plan("stale"), expected_revision=1)
    assert store.get(identifier) == changed
    replay, fresh = store.create(plan(), "initial-key")
    assert not fresh and replay["active_revision"] == 2


def test_draft_plan_replacement_never_mutates_started_work(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store = WorkOrderStore(path)
    original, _ = store.create(plan())
    identifier = original["work_order_id"]
    with sqlite3.connect(path) as db:
        db.execute("UPDATE work_orders SET status='running' WHERE work_order_id=?", (identifier,))
    with pytest.raises(ValueError, match="unstarted draft"):
        store.replace_draft_plan(identifier, plan("rewrite"), expected_revision=1)
    assert store.plan(identifier, 2) is None
    assert store.get(identifier)["tasks"] == original["tasks"]


def test_partial_draft_write_rolls_back_before_retry(tmp_path, monkeypatch):
    path = tmp_path / "runtime.sqlite3"
    store = WorkOrderStore(path)
    original = store._insert_plan

    def fail_after_plan(db, identifier, revision, plan, now):
        original(db, identifier, revision, plan, now)
        raise RuntimeError("fixture failure after plan insertion")

    monkeypatch.setattr(store, "_insert_plan", fail_after_plan)
    with pytest.raises(RuntimeError, match="fixture failure"):
        store.create(plan(), "retry-key")
    with sqlite3.connect(path) as db:
        for table in ("work_orders", "work_order_plans", "work_order_tasks", "work_order_dependencies"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    monkeypatch.setattr(store, "_insert_plan", original)
    created, fresh = store.create(plan(), "retry-key")
    assert fresh and len(created["tasks"]) == 2


def test_concurrent_same_key_creators_share_one_draft(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "runtime.sqlite3"
    store = WorkOrderStore(path)
    barrier = Barrier(2)

    def create():
        barrier.wait(timeout=5)
        return store.create(plan(), "same-concurrent-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0][0]["work_order_id"] == results[1][0]["work_order_id"]
    assert sum(int(fresh) for _, fresh in results) == 1
    assert len(store.list()) == 1


def test_list_cursor_is_stable_when_old_orders_are_updated(tmp_path):
    store = WorkOrderStore(tmp_path / "runtime.sqlite3")
    identifiers = [store.create(plan(str(i)))[0]["work_order_id"] for i in range(5)]
    first = store.list(2)
    store.replace_draft_plan(identifiers[0], plan("updated older draft"), expected_revision=1)
    second = store.list(2, before_id=first[-1]["work_order_id"])
    last = store.list(2, before_id=second[-1]["work_order_id"])
    combined = first + second + last
    assert [item["work_order_id"] for item in combined] == list(reversed(identifiers))
    assert len({item["work_order_id"] for item in combined}) == 5
    with pytest.raises(ValueError, match="cursor not found"):
        store.list(before_id="a" * 32)


def test_server_search_covers_older_titles_and_literal_wildcard_characters(tmp_path):
    store = WorkOrderStore(tmp_path / "runtime.sqlite3")
    older, _ = store.create(plan("original 100%_fixture"))
    for index in range(52):
        store.create(plan(f"other {index}"))
    assert older["work_order_id"] not in {row["work_order_id"] for row in store.list()}
    assert [row["work_order_id"] for row in store.list(search="100%_")] == [older["work_order_id"]]
    assert store.list(search=older["work_order_id"])[0]["title"] == "original 100%_fixture"
    assert store.list(search="' OR 1=1 --") == []
    with pytest.raises(ValueError, match="search"):
        store.list(search="\x00")
