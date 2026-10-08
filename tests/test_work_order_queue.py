"""Queue snapshot store oracles, not executed until S9."""

from uuid import uuid4

from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.work_orders import WorkOrderStore
from doppel_agent.tasks.work_orders import plan_from_payload


def create(runs, prompt="fixture", *, native=True):
    return runs.create(uuid4().hex, uuid4().hex, "graph", {"prompt": prompt, "mode": "graph", "permissions": {}},
                       None, native=native)[0]


def test_queue_retains_interrupted_and_terminal_draining_rows_then_removes_released(tmp_path):
    runs = RuntimeRunStore(tmp_path / "runtime.sqlite3")
    queued = create(runs)
    waiting = create(runs, "approval fixture")
    draining = create(runs, "drain fixture")
    released = create(runs, "released fixture")
    runs.update(waiting["run_id"], "interrupted")
    runs.update(draining["run_id"], "completed")
    runs.update(released["run_id"], "completed")
    runs.release_conversation_turn(released["run_id"])
    snapshot = runs.active_queue()
    assert snapshot["total"] == 3
    assert {row["run_id"] for row in snapshot["items"]} == {queued["run_id"], waiting["run_id"], draining["run_id"]}
    runs.release_conversation_turn(draining["run_id"])
    assert runs.active_queue()["total"] == 2


def test_queue_list_is_bounded_but_total_is_not_visible_count(tmp_path):
    runs = RuntimeRunStore(tmp_path / "runtime.sqlite3")
    for _ in range(105):
        create(runs, "x" * 200, native=False)
    snapshot = runs.active_queue()
    assert len(snapshot["items"]) == snapshot["limit"] == 100
    assert snapshot["total"] == 105
    assert all(len(row["title"]) == 120 for row in snapshot["items"])
    assert all("request" not in row and "profile_snapshot" not in row for row in snapshot["items"])


def test_queue_links_a_work_order_attempt_without_guessing_from_prompt(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store, runs = WorkOrderStore(path), RuntimeRunStore(path)
    order, _ = store.create(plan_from_payload({"title": "order", "tasks": [
        {"id": "read", "title": "read", "prompt": "same text is not an identity"},
    ]}))
    store.activate(order["work_order_id"], expected_revision=1, settings={})
    intent = store.reserve_next(order["work_order_id"], expected_revision=1)
    run, _ = runs.create(uuid4().hex, uuid4().hex, "graph", intent["request"], intent["admission_key"], native=True)
    store.reconcile(order["work_order_id"])
    ordinary = create(runs, intent["request"]["prompt"])
    rows = {row["run_id"]: row for row in runs.active_queue()["items"]}
    assert rows[run["run_id"]]["work_order_id"] == order["work_order_id"]
    assert rows[run["run_id"]]["task_id"] == "read"
    assert rows[ordinary["run_id"]]["work_order_id"] is None
