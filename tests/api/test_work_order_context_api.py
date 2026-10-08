"""Work-order HTTP/pump/context scripted definitions; execute only at S9."""

import asyncio
import threading
import time
from contextlib import ExitStack

from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import ModelTurn


class RecordingProvider:
    def __init__(self):
        self.observed = []

    def next_turn(self, messages, tools):
        self.observed.append([(message.role, message.content) for message in messages])
        return ModelTurn("PREDECESSOR_RESULT_SENTINEL")

    async def anext_turn(self, messages, tools):
        return self.next_turn(messages, tools)


def test_actual_owned_pump_admits_frozen_context_and_direct_predecessor_without_added_grants(tmp_path):
    provider = RecordingProvider()
    (tmp_path / "source.txt").write_text("ACTIVATION_FILE_SENTINEL", encoding="utf-8")
    app = create_app(tmp_path, provider=provider)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        definition = {"title": "fixture", "tasks": [{"id": "one", "title": "one", "prompt": "first raw prompt"},
            {"id": "two", "title": "two", "prompt": "second raw prompt", "dependencies": ["one"]}]}
        oid = client.post("/api/v1/work-orders", json={"plan": definition}).json()["work_order_id"]
        preview = client.post("/api/v1/context/preview", json={"files": [{"path": "source.txt"}]}).json()
        manifest = client.post("/api/v1/context/manifests", json={"files": [{"path": "source.txt", "expected_file_sha256": preview["entries"][0]["file_sha256"]}],
            "confirmed": True, "idempotency_key": "order-api-context-manifest"}).json()
        note = client.post("/api/v1/context/notes", json={"note": {"kind": "constraint", "title": "fixture", "body": "ACTIVATION_NOTE_SENTINEL",
            "scope_work_order_id": oid, "sources": [{"kind": "user", "label": "human"}]}, "confirmed": True, "idempotency_key": "order-api-context-note"}).json()
        context = {"manifest_id": manifest["manifest_id"], "notes": [{"note_id": note["note_id"], "revision": 1}]}
        body = {"expected_revision": 1, "settings": {"context": context}}
        activated = client.post(f"/api/v1/work-orders/{oid}/activate", json=body)
        assert activated.status_code == 200 and activated.headers["cache-control"] == "no-store"
        (tmp_path / "source.txt").write_text("changed after activation", encoding="utf-8")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            order = client.get(f"/api/v1/work-orders/{oid}").json()
            if order["status"] == "succeeded":
                break
            time.sleep(0.02)
        assert order["status"] == "succeeded", order
        runs = [client.get("/api/v1/runs/" + attempt["run_id"]).json() for attempt in order["attempts"]]
        assert len(runs) == 2 and len(provider.observed) == 2
        for run in runs:
            snapshot = run["input_snapshot"]
            assert "ACTIVATION_FILE_SENTINEL" in snapshot["rendered_context"] and "ACTIVATION_NOTE_SENTINEL" in snapshot["rendered_context"]
            assert "changed after activation" not in snapshot["rendered_context"]
            assert snapshot["scope_work_order_id"] == oid and not any(run["request"]["permissions"].values())
            assert not run["lease_active"] and snapshot["actual_tokens"] is None
        second = next(run for run in runs if run["request"]["prompt"] == "second raw prompt")
        first = next(run for run in runs if run["request"]["prompt"] == "first raw prompt")
        assert second["input_snapshot"]["predecessors"][0]["run_id"] == first["run_id"]
        assert second["input_snapshot"]["predecessors"][0]["text"] == "PREDECESSOR_RESULT_SENTINEL"
        assert client.post(f"/api/v1/work-orders/{oid}/activate", json=body).status_code == 200
        assert app.state.run_service.scheduler.accepted_count == 2
        rejected = client.post(f"/api/v1/work-orders/{oid}/activate", json={**body, "settings": {"context": context, "input_snapshot": {}}})
        assert rejected.status_code == 422


class FirstTurnGateProvider(RecordingProvider):
    """Scripted gate, not a network provider or paid model."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def next_turn(self, messages, tools):
        first = not self.observed
        result = super().next_turn(messages, tools)
        if first:
            self.entered.set()
            if not self.release.wait(15):
                raise RuntimeError("fixture gate timed out")
        return result

    async def anext_turn(self, messages, tools):
        first = not self.observed
        result = RecordingProvider.next_turn(self, messages, tools)
        if first:
            self.entered.set()
            deadline = time.monotonic() + 15
            while not self.release.is_set():
                if time.monotonic() >= deadline:
                    raise RuntimeError("fixture gate timed out")
                await asyncio.sleep(0.01)
        return result


def test_http_context_only_revision_preserves_begun_input_and_pauses_until_explicit_resume(tmp_path):
    provider = FirstTurnGateProvider()
    app = create_app(tmp_path, provider=provider)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client, ExitStack() as cleanup:
            cleanup.callback(provider.release.set)  # Release before TestClient shutdown on every failure.
            definition = {"title": "fixture", "tasks": [
                {"id": "first", "title": "first", "prompt": "first fixture"},
                {"id": "future", "title": "future", "prompt": "future fixture", "dependencies": ["first"]},
            ]}
            oid = client.post("/api/v1/work-orders", json={"plan": definition}).json()["work_order_id"]
            note = client.post("/api/v1/context/notes", json={"note": {"kind": "constraint", "title": "fixture", "body": "OLD_PINNED_NOTE_SENTINEL",
                "scope_work_order_id": oid, "sources": [{"kind": "user", "label": "explicit human fixture"}]},
                "confirmed": True, "idempotency_key": "context-only-revision-note"}).json()
            context = {"manifest_id": None, "notes": [{"note_id": note["note_id"], "revision": 1}]}
            activated = client.post(f"/api/v1/work-orders/{oid}/activate", json={"expected_revision": 1, "settings": {"context": context}})
            assert activated.status_code == 200
            assert provider.entered.wait(10), "first actual scripted provider turn did not enter"
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                before = client.get(f"/api/v1/work-orders/{oid}").json()
                if len(before["attempts"]) == 1 and before["attempts"][0]["run_id"]:
                    break
                time.sleep(0.02)
            assert len(before["attempts"]) == 1 and before["attempts"][0]["run_id"]
            first_run_id = before["attempts"][0]["run_id"]
            first_input = client.get(f"/api/v1/runs/{first_run_id}").json()["input_snapshot"]
            assert "OLD_PINNED_NOTE_SENTINEL" in first_input["rendered_context"]
            empty = {"manifest_id": None, "notes": []}
            revision_body = {"expected_revision": 1, "plan": definition, "context": empty}
            revised = client.put(f"/api/v1/work-orders/{oid}/plan", json=revision_body)
            assert revised.status_code == 200 and revised.headers["cache-control"] == "no-store"
            revised = revised.json()
            assert revised["status"] == "paused" and revised["active_revision"] == 2
            assert {task["task_id"]: task["execution_revision"] for task in revised["tasks"]} == {"first": 1, "future": 2}
            bindings = {binding["revision"]: binding for binding in revised["context_bindings"]}
            assert bindings[1]["descriptor"] == context and bindings[1]["bound_revision"] == 1
            assert bindings[2]["descriptor"] == empty and bindings[2]["bound_revision"] == 2
            assert client.get(f"/api/v1/runs/{first_run_id}").json()["input_snapshot"] == first_input
            assert client.put(f"/api/v1/work-orders/{oid}/plan", json=revision_body).status_code == 409
            assert app.state.run_service.scheduler.accepted_count == 1
            provider.release.set()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                first = client.get(f"/api/v1/runs/{first_run_id}").json()
                if first["status"] == "completed" and not first["lease_active"]:
                    break
                time.sleep(0.02)
            assert first["status"] == "completed" and not first["lease_active"]
            assert client.get(f"/api/v1/work-orders/{oid}").json()["status"] == "paused"
            assert app.state.run_service.scheduler.accepted_count == 1
            assert client.post(f"/api/v1/work-orders/{oid}/control", json={"expected_revision": 2, "action": "resume"}).status_code == 200
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                final = client.get(f"/api/v1/work-orders/{oid}").json()
                if final["status"] == "succeeded":
                    break
                time.sleep(0.02)
            assert final["status"] == "succeeded" and len(final["attempts"]) == 2
            future_id = next(a["run_id"] for a in final["attempts"] if a["task_id"] == "future")
            future = client.get(f"/api/v1/runs/{future_id}").json()
            assert future["input_snapshot"]["notes"] == []
            assert future["input_snapshot"]["predecessors"][0]["run_id"] == first_run_id
            assert "OLD_PINNED_NOTE_SENTINEL" not in future["input_snapshot"]["rendered_context"]
            assert not any(future["request"]["permissions"].values())
            assert app.state.run_service.scheduler.accepted_count == 2 and len(provider.observed) == 2
    finally:
        provider.release.set()  # Every assertion failure also releases the owned fixture.
