"""Native product API and real runtimes, strictly scripted/Mock providers."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider, ModelTurn


def client_for(root, provider=None, **kwargs):
    return TestClient(create_app(root, provider=provider, **kwargs), base_url="http://127.0.0.1")


def test_workspace_selection_api_roundtrip_ownership_and_clear(tmp_path):
    with client_for(tmp_path, MockProvider()) as client:
        assert client.get("/api/v1/workspace-selection").json() == {
            "saved": False, "conversation_id": None, "run_id": None,
        }
        a = client.post("/api/v1/conversations", json={}).json()["id"]
        b = client.post("/api/v1/conversations", json={}).json()["id"]
        rid = client.post(f"/api/v1/conversations/{a}/runs", json={"prompt": "local fixture"}).json()["run_id"]
        settled(client, rid)
        choice = {"conversation_id": a, "run_id": rid}
        assert client.put("/api/v1/workspace-selection", json=choice).json() == {"saved": True, **choice}
        before = client.get(f"/api/v1/runs/{rid}").json()
        assert client.get(f"/api/v1/conversations/{a}").json()["selected_run_id"] == rid
        assert client.put("/api/v1/workspace-selection", json={"conversation_id": b, "run_id": rid}).status_code == 404
        assert client.get("/api/v1/workspace-selection").json() == {"saved": True, **choice}
        for invalid in ({}, {"conversation_id": "bad"}, {**choice, "permissions": {"workspace_write": True}}):
            assert client.put("/api/v1/workspace-selection", json=invalid).status_code == 422
        assert client.put("/api/v1/workspace-selection", json={"conversation_id": None, "run_id": rid}).status_code == 409
        assert client.get(f"/api/v1/runs/{rid}").json() == before
        assert client.delete(f"/api/v1/conversations/{a}").status_code == 200
        assert client.get("/api/v1/workspace-selection").json() == {"saved": True, "conversation_id": None, "run_id": None}
        assert client.get(f"/api/v1/runs/{rid}").json() == before


def test_workspace_selection_routes_do_not_submit_probe_or_bypass_same_origin(tmp_path, monkeypatch):
    app = create_app(tmp_path, provider=MockProvider())
    service = app.state.run_service

    def forbidden(*_args):
        raise AssertionError("navigation must not read credentials")

    monkeypatch.setattr(service.settings, "api_key", forbidden)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        cid = client.post("/api/v1/conversations", json={}).json()["id"]
        assert client.put("/api/v1/workspace-selection", json={"conversation_id": cid}).status_code == 200
        for method in ("get", "put"):
            kwargs = {"json": {"conversation_id": cid}} if method == "put" else {}
            for headers in ({"Host": "foreign.example"}, {"Origin": "https://foreign.example"}):
                assert getattr(client, method)("/api/v1/workspace-selection", headers=headers, **kwargs).status_code == 403
        assert service.conversations.get(cid)["runs"] == []
        assert service.conversations.get(cid)["messages"] == []


def settled(client, rid, statuses=("completed", "failed", "cancelled", "interrupted")):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/runs/{rid}").json()
        # Terminal status/projection may commit before owned cancellation IO and
        # lease cleanup drain. Interrupts intentionally retain their turn lease.
        if r["status"] in statuses and (r["status"] == "interrupted" or not r["lease_active"]):
            return r
        time.sleep(0.01)
    raise AssertionError(r)


class MemoryProvider:
    def __init__(self):
        self.observed = []

    def next_turn(self, messages, tools):
        self.observed.append([(m.role, m.content) for m in messages])
        last = [m.content for m in messages if m.role == "user"][-1]
        if last == "remember violet42":
            return ModelTurn("stored violet42")
        first = [m for m in messages if m.role == "user" and m.content == "remember violet42"]
        answer = [m for m in messages if m.role == "assistant" and m.content == "stored violet42"]
        return ModelTurn("recall violet42 exactly once" if len(first) == len(answer) == 1 else "context broken")

    async def anext_turn(self, messages, tools):
        return self.next_turn(messages, tools)


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_two_turn_context_survives_reconstruction_without_history_duplication(tmp_path, mode):
    provider = MemoryProvider()
    with client_for(tmp_path, provider) as client:
        conv = client.post("/api/v1/conversations", json={"mode": mode}).json()
        cid = conv["id"]
        first = client.post(f"/api/v1/conversations/{cid}/runs", json={"mode": mode, "prompt": "remember violet42",
                            "idempotency_key": "first-turn-key"}).json()
        result = settled(client, first["run_id"])
        assert result["status"] == "completed", result
        assert "fallback_runtime" not in result["metadata"]
    # No in-memory runtime/provider context is carried to the reconstructed service.
    provider2 = MemoryProvider()
    with client_for(tmp_path, provider2) as client:
        body = {"mode": mode, "prompt": "recall", "idempotency_key": "second-turn-key"}
        accepted = client.post(f"/api/v1/conversations/{cid}/runs", json=body)
        assert accepted.status_code == 202, accepted.text
        rid = accepted.json()["run_id"]
        result = settled(client, rid)
        assert result["answer"] == "recall violet42 exactly once", result
        assert "fallback_runtime" not in result["metadata"]
        assert client.post(f"/api/v1/conversations/{cid}/runs", json=body).json()["replayed"]
        assert len(provider2.observed) == 1
        detail = client.get(f"/api/v1/conversations/{cid}").json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
        assert detail["active_run_id"] is None
        assert result["conversation_id"] == cid
        assert result["thread_id"] == first["thread_id"]


def test_native_crud_groups_archived_search_and_origin(tmp_path):
    with client_for(tmp_path, MockProvider()) as client:
        cid = client.post("/api/v1/conversations", json={"draft": True}).json()["id"]
        assert client.post("/api/v1/conversations", json={"draft": True}).json()["id"] == cid
        gid = client.post("/api/v1/conversation-groups", json={"name": "native"}).json()["id"]
        assert client.patch(f"/api/v1/conversations/{cid}", json={"title": "sentinel", "group_id": gid, "archived": True}).status_code == 200
        assert client.get("/api/v1/conversations?q=sentinel").json()[0]["archived"] == 1
        assert client.post(f"/api/v1/conversations/{cid}/runs", json={"prompt": "blocked"}).status_code == 409
        assert client.patch(f"/api/v1/conversations/{cid}", json={"profile_id": "unknown"}).status_code == 409
        assert client.get("/api/v1/conversations", headers={"Host": "attacker.invalid"}).status_code == 403
        assert client.post("/api/v1/conversations", json={}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
        assert client.post("/api/v1/runs", json={"prompt": "x", "conversation_id": "bad"}).status_code == 422
        assert client.patch(f"/api/v1/conversations/{cid}", json={"mode": "deep"}).status_code == 422
        assert client.delete(f"/api/v1/conversation-groups/{gid}").status_code == 200
        assert client.get(f"/api/v1/conversations/{cid}").json()["group_id"] is None
        assert client.delete(f"/api/v1/conversations/{cid}").json()["audit_retained"]
        assert client.get(f"/api/v1/conversations/{cid}").status_code == 404


def test_archived_profile_patch_cannot_bypass_readonly_or_mutate_accepted_audit(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    alternate = app.state.run_service.settings.save_profile(
        {"provider": "mock", "model": "offline-other", "name": "offline alternate"},
    )["active_profile_id"]
    with TestClient(app, base_url="http://127.0.0.1") as client:
        cid = client.post("/api/v1/conversations", json={"profile_id": "default"}).json()["id"]
        rid = client.post(f"/api/v1/conversations/{cid}/runs", json={"prompt": "read only fixture", "effort": "quick"}).json()["run_id"]
        accepted = settled(client, rid)
        assert accepted["status"] == "completed"
        assert client.patch(f"/api/v1/conversations/{cid}", json={"archived": True}).status_code == 200
        before = client.get(f"/api/v1/conversations/{cid}").json()
        for profile in (alternate, None):
            for extra in ({}, {"title": "must not apply"}, {"archived": False}):
                denied = client.patch(f"/api/v1/conversations/{cid}", json={"profile_id": profile, **extra})
                assert denied.status_code == 409
                assert denied.json()["detail"] == "archived conversation profile is read-only; unarchive first"
                assert client.get(f"/api/v1/conversations/{cid}").json() == before
                assert client.get(f"/api/v1/runs/{rid}").json() == accepted
        assert client.patch(f"/api/v1/conversations/{cid}", json={"archived": False}).status_code == 200
        changed = client.patch(f"/api/v1/conversations/{cid}", json={"profile_id": alternate})
        assert changed.status_code == 200 and changed.json()["profile_id"] == alternate
        assert changed.json()["messages"] == before["messages"]
        assert changed.json()["runs"] == before["runs"]
        assert client.get(f"/api/v1/runs/{rid}").json() == accepted


def test_native_ownership_concurrency_and_interrupted_cancellation(tmp_path):
    class Slow:
        async def anext_turn(self, *_args):
            await asyncio.sleep(60)

    with client_for(tmp_path, Slow()) as client:
        a = client.post("/api/v1/conversations", json={}).json()["id"]
        b = client.post("/api/v1/conversations", json={}).json()["id"]
        run = client.post(f"/api/v1/conversations/{a}/runs", json={"prompt": "slow"}).json()
        rid = run["run_id"]
        assert client.post(f"/api/v1/conversations/{a}/runs", json={"prompt": "conflict"}).status_code == 409
        assert client.delete(f"/api/v1/conversations/{a}").status_code == 409
        for suffix in ("", "/events"):
            assert client.get(f"/api/v1/conversations/{b}/runs/{rid}{suffix}").status_code == 404
        for suffix in ("/cancel", "/interrupts/fake/resume"):
            assert client.post(f"/api/v1/conversations/{b}/runs/{rid}{suffix}", json={"action": "approve"}).status_code == 404
        assert client.post(f"/api/v1/conversations/{a}/runs/{rid}/cancel").status_code == 200
        assert settled(client, rid)["status"] == "cancelled"
        detail = client.get(f"/api/v1/conversations/{a}").json()
        assert [m["role"] for m in detail["messages"]] == ["user"]
        assert detail["messages"][0]["status"] == "cancelled"


def test_public_profile_snapshot_without_probe_or_key_decryption(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    service = app.state.run_service
    service.settings.save_profile({"provider": "mock", "model": "offline-one", "name": "one"}, profile_id="default")

    def forbidden(*_args):
        raise AssertionError("key/probe access is forbidden")

    monkeypatch.setattr(service.settings, "api_key", forbidden)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        cid = client.post("/api/v1/conversations", json={"profile_id": "default"}).json()["id"]
        rid = client.post(f"/api/v1/conversations/{cid}/runs", json={"prompt": "snapshot"}).json()["run_id"]
        result = settled(client, rid)
        assert result["status"] == "completed"
        assert result["profile_snapshot"]["model"] == "offline-one"
        service.settings.save_profile({"provider": "mock", "model": "offline-two", "name": "two"}, profile_id="default")
        assert client.get(f"/api/v1/runs/{rid}").json()["profile_snapshot"]["model"] == "offline-one"
        assert "api_key" not in str(result["profile_snapshot"])


def test_last_event_id_replays_durable_native_stream(tmp_path):
    with client_for(tmp_path, MockProvider()) as client:
        accepted = client.post("/api/v1/runs", json={"prompt": "auto conversation"}).json()
        rid, cid = accepted["run_id"], accepted["conversation_id"]
        assert settled(client, rid)["status"] == "completed"
        assert len(client.get(f"/api/v1/conversations/{cid}").json()["messages"]) == 2
        events = client.get(f"/api/v1/runs/{rid}/events").json()
        pivot = events[1]["seq"]
        stream = client.get(f"/api/v1/runs/{rid}/events?stream=true", headers={"Last-Event-ID": str(pivot)})
        ids = [int(line[4:]) for line in stream.text.splitlines() if line.startswith("id: ")]
        assert ids == [e["seq"] for e in events if e["seq"] > pivot]
        assert client.get(f"/api/v1/runs/{rid}/events?stream=true", headers={"Last-Event-ID": "bad"}).status_code == 422
