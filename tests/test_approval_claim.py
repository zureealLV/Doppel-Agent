"""Durable interrupt claims are atomic across independent SQLite store owners."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier

import pytest

from doppel_agent.persistence import EventStore, RuntimeRunStore


def interrupted(database):
    store = RuntimeRunStore(database)
    store.create("run", "thread", "graph", {"permissions": {"workspace_write": True}}, None)
    store.update("run", "interrupted", metadata={"interrupts": [{"id": "pending", "value": {"prepared": "kept"}}]})
    return store


def test_independent_store_claims_have_one_winner_and_one_transactional_event(tmp_path):
    database = tmp_path / "runtime.sqlite3"
    first = interrupted(database)
    stores = [first, RuntimeRunStore(database)]
    barrier = Barrier(2)

    def claim(index):
        barrier.wait(timeout=10)
        try:
            return stores[index].claim_interrupt("run", "pending", {"action": "approve"}, ttl_seconds=900)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(claim, (0, 1)))
    assert sum(row is not None for row in result) == 1
    record = first.get("run")
    assert record["status"] == "queued"
    assert record["metadata"]["interrupts"][0]["value"] == {"prepared": "kept"}
    events = EventStore(database).list("run")
    assert [row["type"] for row in events] == ["approval.decided"]
    assert events[0]["payload"] == {"interrupt_id": "pending", "decision": {"action": "approve"}}


def test_wrong_interrupt_cannot_consume_pending_state_or_publish_decision(tmp_path):
    database = tmp_path / "runtime.sqlite3"
    store = interrupted(database)
    with pytest.raises(ValueError):
        store.claim_interrupt("run", "wrong", {"action": "approve"}, ttl_seconds=900)
    assert store.get("run")["status"] == "interrupted"
    assert EventStore(database).list("run") == []


def test_expired_claim_persists_one_expiry_without_a_decision(tmp_path):
    database = tmp_path / "runtime.sqlite3"
    store = interrupted(database)
    with pytest.raises(TimeoutError):
        store.claim_interrupt("run", "pending", {"action": "approve"}, ttl_seconds=0)
    assert store.get("run")["status"] == "interrupted_expired"
    assert [row["type"] for row in EventStore(database).list("run")] == ["approval.expired"]


def test_event_write_failure_rolls_back_interrupt_claim(tmp_path):
    from doppel_agent.persistence.database import sqlite_connection
    database = tmp_path / "runtime.sqlite3"
    store = interrupted(database)
    with sqlite_connection(database) as connection:
        connection.execute("CREATE TRIGGER fail_decision BEFORE INSERT ON runtime_events "
                           "WHEN NEW.type='approval.decided' BEGIN SELECT RAISE(ABORT,'fixture event failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.claim_interrupt("run", "pending", {"action": "approve"}, ttl_seconds=900)
    assert store.get("run")["status"] == "interrupted"
    assert EventStore(database).list("run") == []
