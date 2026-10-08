"""Offline context admission/runtime definitions, not executed until S9.

These exercise actual runtime classes with scripted providers, not model quality,
native UI or packaging. No external HTTP/provider configuration is involved.
"""

import time

import pytest
from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import ModelTurn


class CapturingProvider:
    def __init__(self):
        self.observed = []

    def next_turn(self, messages, tools):
        self.observed.append([(message.role, message.content) for message in messages])
        return ModelTurn("fixture completed")

    async def anext_turn(self, messages, tools):
        return self.next_turn(messages, tools)


def settled(client, rid):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        record = client.get(f"/api/v1/runs/{rid}").json()
        if record["status"] in {"completed", "failed", "cancelled"} and not record["lease_active"]:
            return record
        time.sleep(0.01)
    raise AssertionError(record)


def reviewed_context(client):
    preview = client.post("/api/v1/context/preview", json={"files": [{"path": "source.txt"}]}).json()
    accepted = client.post("/api/v1/context/manifests", json={"files": [{"path": "source.txt", "expected_file_sha256": preview["entries"][0]["file_sha256"]}],
        "confirmed": True, "idempotency_key": "runtime-input-manifest"})
    assert accepted.status_code == 201
    body = {"kind": "constraint", "title": "fixture", "body": "NOTE_SENTINEL_831", "sources": [{"kind": "user", "label": "human fixture"}]}
    note = client.post("/api/v1/context/notes", json={"note": body, "confirmed": True, "idempotency_key": "runtime-input-note"}).json()
    return {"manifest_id": accepted.json()["manifest_id"], "notes": [{"note_id": note["note_id"], "revision": 1}]}, body


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_one_new_user_injection_and_frozen_history_after_restart_without_rechecking_old_sources(tmp_path, mode):
    source = tmp_path / "source.txt"
    source.write_text("ATTACHMENT_SENTINEL_831\n", encoding="utf-8")
    provider = CapturingProvider()
    body = {"mode": mode, "prompt": "first raw prompt", "idempotency_key": "runtime-input-first-turn"}
    with TestClient(create_app(tmp_path, provider=provider), base_url="http://127.0.0.1") as client:
        cid = client.post("/api/v1/conversations", json={"mode": mode}).json()["id"]
        context, note_body = reviewed_context(client)
        body["context"] = context
        accepted = client.post(f"/api/v1/conversations/{cid}/runs", json=body)
        assert accepted.status_code == 202, accepted.text
        rid = accepted.json()["run_id"]
        record = settled(client, rid)
        assert record["status"] == "completed" and "fallback_runtime" not in record["metadata"]
        observed = provider.observed[-1]
        for sentinel in ("ATTACHMENT_SENTINEL_831", "NOTE_SENTINEL_831"):
            assert sum(content.count(sentinel) for role, content in observed if role == "user") == 1
            assert all(sentinel not in content for role, content in observed if role == "system")
        assert record["request"]["prompt"] == "first raw prompt"
        receipt = record["input_snapshot"]
        assert receipt["actual_tokens"] is None
        detail = client.get(f"/api/v1/conversations/{cid}").json()
        assert detail["messages"][0]["content"] == "first raw prompt"  # UI/title are not expanded model input.
        assert client.put("/api/v1/context/notes/" + context["notes"][0]["note_id"], json={"note": {**note_body, "body": "changed assertion"},
            "expected_revision": 1, "confirmed": True, "idempotency_key": "runtime-input-note-edited"}).status_code == 200
    source.write_text("changed file", encoding="utf-8")
    provider2 = CapturingProvider()
    with TestClient(create_app(tmp_path, provider=provider2), base_url="http://127.0.0.1") as client:
        replay = client.post(f"/api/v1/conversations/{cid}/runs", json=body)
        assert replay.status_code == 202 and replay.json()["replayed"]
        assert client.get(f"/api/v1/runs/{rid}").json()["input_snapshot"] == receipt
        assert provider2.observed == []
        second = client.post(f"/api/v1/conversations/{cid}/runs", json={"mode": mode, "prompt": "second raw prompt", "idempotency_key": "runtime-input-second-turn"})
        assert second.status_code == 202, second.text
        result = settled(client, second.json()["run_id"])
        assert result["status"] == "completed" and result["input_snapshot"] is None
        observed = provider2.observed[-1]
        assert sum(content.count("ATTACHMENT_SENTINEL_831") for role, content in observed if role == "user") == 1
        assert sum(content.count("NOTE_SENTINEL_831") for role, content in observed if role == "user") == 1
        assert sum(content == "second raw prompt" for role, content in observed if role == "user") == 1
        refused = client.post(f"/api/v1/conversations/{cid}/runs", json={**body, "idempotency_key": "runtime-input-stale-turn"})
        assert refused.status_code == 409 and refused.json()["detail"] == "context_file_stale"
        assert refused.headers["cache-control"] == "no-store"


def test_strict_input_ids_origin_and_old_null_request_replay_do_not_submit_extra_work(tmp_path):
    provider = CapturingProvider()
    app = create_app(tmp_path, provider=provider)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for context in ({}, {"manifest_id": None, "notes": [], "rendered_context": "caller invented"},
                        {"manifest_id": None, "notes": [{"note_id": "a" * 32, "revision": True}]},
                        {"manifest_id": None, "notes": [], "scope_work_order_id": "a" * 32}):
            assert client.post("/api/v1/runs", json={"prompt": "fixture", "context": context}).status_code == 422
        assert app.state.run_service.scheduler.accepted_count == 0
        empty = {"manifest_id": None, "notes": []}
        assert client.post("/api/v1/runs", json={"prompt": "fixture", "context": empty}, headers={"Origin": "https://foreign.invalid"}).status_code == 403
        body = {"prompt": "old-style fixture", "idempotency_key": "runtime-input-old-request"}
        first = client.post("/api/v1/runs", json=body)
        rid = first.json()["run_id"]
        assert settled(client, rid)["status"] == "completed"
        assert client.post("/api/v1/runs", json={**body, "context": None}).json()["replayed"]
        assert app.state.run_service.scheduler.accepted_count == 1
