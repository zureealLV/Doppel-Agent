"""S5 local context HTTP fixture definitions, unexecuted until S9."""

from uuid import uuid4

from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider


def note_body():
    return {"confirmed": True, "idempotency_key": "offline-confirm-note", "note": {
        "kind": "decision", "title": "fixture decision", "body": "Preserve existing files.",
        "sources": [{"kind": "user", "label": "human confirmation"}],
    }}


def test_preview_is_read_only_accept_is_explicit_stale_is_not_silently_updated(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("first\nsecond\n", encoding="utf-8", newline="\n")
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        preview = client.post("/api/v1/context/preview", json={"files": [{"path": "source.txt", "start_line": 2, "end_line": 2}]})
        assert preview.status_code == 200 and preview.headers["cache-control"] == "no-store"
        snapshot = preview.json()
        assert snapshot["entries"][0]["text"] == "second\n" and snapshot["actual_tokens"] is None
        assert "manifest_id" not in snapshot and app.state.run_service.scheduler.accepted_count == 0
        payload = {"files": [{"path": "source.txt", "start_line": 2, "end_line": 2,
            "expected_file_sha256": snapshot["entries"][0]["file_sha256"]}],
            "confirmed": True, "idempotency_key": "offline-accept-context"}
        refused = client.post("/api/v1/context/manifests", json={**payload, "confirmed": False})
        assert refused.status_code == 400 and refused.json()["detail"] == "confirmation_required"
        accepted = client.post("/api/v1/context/manifests", json=payload)
        assert accepted.status_code == 201 and accepted.headers["cache-control"] == "no-store"
        manifest = accepted.json()
        url = "/api/v1/context/manifests/" + manifest["manifest_id"]
        assert client.get(url).json() == manifest
        assert client.post(url + "/check").json()["entries"][0]["status"] == "current"
        source.write_text("replacement\n", encoding="utf-8", newline="\n")
        assert client.post(url + "/check").json()["entries"][0]["status"] == "stale"
        assert client.post("/api/v1/context/manifests", json=payload).json() == manifest
        stale = client.post("/api/v1/context/manifests", json={**payload, "idempotency_key": "new-context-key"})
        assert stale.status_code == 409 and stale.json()["detail"] == "stale_file"
        assert app.state.run_service.scheduler.accepted_count == 0


def test_note_create_cas_delete_history_source_metadata_and_explicit_confirmation(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = note_body()
        refused = client.post("/api/v1/context/notes", json={**payload, "confirmed": False})
        assert refused.status_code == 400 and client.get("/api/v1/context/notes").json() == []
        accepted = client.post("/api/v1/context/notes", json=payload)
        assert accepted.status_code == 201 and accepted.headers["cache-control"] == "no-store"
        note = accepted.json()
        assert note["sources"][0]["origin"] == "user_declared" and note["actual_tokens"] is None
        assert client.post("/api/v1/context/notes", json=payload).json() == note
        url = "/api/v1/context/notes/" + note["note_id"]
        edit = {**payload, "expected_revision": 1, "idempotency_key": "offline-note-edit",
            "note": {**payload["note"], "body": "Preserve files and database."}}
        changed = client.put(url, json=edit)
        assert changed.status_code == 200 and changed.json()["revision"] == 2
        assert client.put(url, json=edit).json() == changed.json()
        stale = client.put(url, json={**edit, "idempotency_key": "stale-note-edit"})
        assert stale.status_code == 409 and stale.headers["cache-control"] == "no-store"
        deletion = {"expected_revision": 2, "confirmed": True, "idempotency_key": "offline-note-delete"}
        assert client.post(url + "/delete", json={**deletion, "confirmed": False}).status_code == 400
        tombstone = client.post(url + "/delete", json=deletion)
        assert tombstone.status_code == 200 and tombstone.json()["deleted"]
        assert client.get(url).status_code == 404 and client.get("/api/v1/context/notes").json() == []
        assert client.get(url, params={"revision": 1}).json()["body"] == payload["note"]["body"]
        assert app.state.run_service.scheduler.accepted_count == 0


def test_context_strict_schemas_origin_and_stable_path_failure_do_not_read_real_secrets(tmp_path):
    with TestClient(create_app(tmp_path, provider=MockProvider()), base_url="http://127.0.0.1") as client:
        url = "/api/v1/context/preview"
        for request in ({"files": [], "budget_bytes": 256}, {"files": [{"path": "source.txt", "start_line": True}]},
                        {"files": [{"path": "source.txt"}], "budget_bytes": "65536"},
                        {"files": [{"path": "source.txt", "permissions": {}}]}):
            assert client.post(url, json=request).status_code == 422
        for path in (".env", "../outside", ".doppel-agent/runtime.sqlite3", "node_modules/x.js"):
            response = client.post(url, json={"files": [{"path": path}]})
            assert response.status_code == 400 and response.headers["cache-control"] == "no-store"
            assert str(tmp_path) not in response.text and path not in response.text
        assert client.post(url, json={"files": [{"path": "source.txt"}]}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
        assert client.post("/api/v1/context/notes", json=note_body(), headers={"Origin": "https://attacker.invalid"}).status_code == 403
        assert client.get("/api/v1/context/notes", headers={"Origin": "https://attacker.invalid"}).status_code == 403
        for confirmed in (1, "true"):
            assert client.post("/api/v1/context/notes", json={**note_body(), "confirmed": confirmed}).status_code == 422
        assert client.get("/api/v1/context/notes", params={"limit": 101}).status_code == 422
        assert client.get("/api/v1/context/manifests/" + uuid4().hex).status_code == 404
        assert client.post("/api/v1/context/manifests/" + uuid4().hex + "/check").status_code == 404


def test_selected_context_static_route_cas_empty_replay_stale_notes_and_no_admission(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        url = "/api/v1/context/selection"
        before = client.get(url)
        assert before.status_code == 200 and before.headers["cache-control"] == "no-store"
        assert before.json()["saved"] is False and before.json()["revision"] == 0
        note = client.post("/api/v1/context/notes", json=note_body()).json()
        ref = {"note_id": note["note_id"], "revision": 1}
        payload = {"manifest_id": None, "notes": [ref], "expected_revision": 0, "idempotency_key": "context-choice-first"}
        accepted = client.post(url, json=payload)
        assert accepted.status_code == 200 and accepted.headers["cache-control"] == "no-store"
        assert accepted.json()["revision"] == 1 and accepted.json()["notes"] == [ref]
        assert client.post(url, json=payload).json() == accepted.json()
        edit = {**note_body(), "expected_revision": 1, "idempotency_key": "context-note-edited"}
        assert client.put("/api/v1/context/notes/" + note["note_id"], json=edit).status_code == 200
        stale = client.post(url, json={**payload, "expected_revision": 1, "idempotency_key": "context-choice-stale"})
        assert stale.status_code == 409 and stale.json()["detail"] == "selected_note_revision_changed"
        assert client.get(url).json() == accepted.json()  # Retain historical choice, not latest note.
        deletion = {"confirmed": True, "expected_revision": 2, "idempotency_key": "context-note-deleted"}
        assert client.post("/api/v1/context/notes/" + note["note_id"] + "/delete", json=deletion).status_code == 200
        assert client.post(url, json={**payload, "expected_revision": 1, "idempotency_key": "context-choice-deleted"}).status_code == 404
        cleared = client.post(url, json={**payload, "notes": [], "expected_revision": 1, "idempotency_key": "context-choice-clear"})
        assert cleared.status_code == 200 and cleared.json()["saved"] and cleared.json()["notes"] == []
        assert client.post(url, json=payload).status_code == 409
        assert client.get(url).json() == cleared.json() and app.state.run_service.scheduler.accepted_count == 0


def test_selection_schema_origin_and_order_scope_are_isolated(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        url = "/api/v1/context/selection"
        empty = {"manifest_id": None, "notes": [], "expected_revision": 0, "idempotency_key": "fixture-empty-selection"}
        for body in ({**empty, "expected_revision": True}, {**empty, "implicit_grants": True},
                     {**empty, "notes": [{"note_id": uuid4().hex, "revision": True}]},
                     {key: value for key, value in empty.items() if key != "manifest_id"}):
            assert client.post(url, json=body).status_code == 422
        for method in ("get", "post"):
            response = getattr(client, method)(url, headers={"Origin": "https://attacker.invalid"}, **({"json": empty} if method == "post" else {}))
            assert response.status_code == 403
        assert client.get(url, params={"scope_work_order_id": uuid4().hex}).status_code == 404
        order = client.post("/api/v1/work-orders", json={"plan": {"title": "fixture", "tasks": [{"id": "one", "title": "one", "prompt": "fixture"}]},
            "idempotency_key": "fixture-order-selection"}).json()
        oid = order["work_order_id"]
        scoped_note = note_body()
        scoped_note["note"]["scope_work_order_id"] = oid
        note = client.post("/api/v1/context/notes", json=scoped_note).json()
        selected = {**empty, "notes": [{"note_id": note["note_id"], "revision": 1}]}
        assert client.post(url, json=selected).status_code == 400
        assert client.get(url).json()["saved"] is False
        assert client.post(url, json={**selected, "scope_work_order_id": oid}).status_code == 200
        assert client.get(url).json()["saved"] is False
        assert client.get(url, params={"scope_work_order_id": oid}).json()["notes"] == selected["notes"]
        assert app.state.run_service.scheduler.accepted_count == 0
