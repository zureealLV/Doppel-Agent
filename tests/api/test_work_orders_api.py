"""Local work-order API definitions for unified S9 execution, not receipts."""

import time

from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider, ModelTurn, ToolCall


def body():
    return {"idempotency_key": "offline-order-key", "plan": {"title": "offline plan", "tasks": [
        {"id": "read", "title": "read", "prompt": "offline fixture read"},
        {"id": "next", "title": "next", "prompt": "offline fixture next", "dependencies": ["read"]},
    ]}}


def wait(client, identifier, predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/work-orders/{identifier}")
        assert response.status_code == 200, response.text
        record = response.json()
        if predicate(record):
            return record
        time.sleep(0.01)
    raise AssertionError(record)


def test_draft_is_explicit_and_idempotent_without_runtime_execution(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.post("/api/v1/work-orders", json=body())
        assert first.status_code == 201 and first.headers["cache-control"] == "no-store"
        draft = first.json()
        replay = client.post("/api/v1/work-orders", json=body())
        assert replay.json()["replayed"] is True
        assert replay.json()["work_order_id"] == draft["work_order_id"]
        assert draft["status"] == "draft" and draft["attempts"] == []
        assert client.get("/api/v1/work-orders").json()[0]["work_order_id"] == draft["work_order_id"]
        assert app.state.run_service.scheduler.accepted_count == 0


def test_list_endpoint_exposes_bounded_stable_creation_cursor(tmp_path):
    with TestClient(create_app(tmp_path, provider=MockProvider()), base_url="http://127.0.0.1") as client:
        identifiers = []
        for index in range(5):
            request = body()
            request["idempotency_key"] = f"offline-order-{index}"
            identifiers.append(client.post("/api/v1/work-orders", json=request).json()["work_order_id"])
        first = client.get("/api/v1/work-orders", params={"limit": 2}).json()
        second = client.get("/api/v1/work-orders", params={"limit": 2, "before_id": first[-1]["work_order_id"]}).json()
        last = client.get("/api/v1/work-orders", params={"limit": 2, "before_id": second[-1]["work_order_id"]}).json()
        assert [item["work_order_id"] for item in first + second + last] == list(reversed(identifiers))
        assert client.get("/api/v1/work-orders", params={"limit": 101}).status_code == 422
        assert client.get("/api/v1/work-orders", params={"before_id": "a" * 32}).status_code == 404


def test_queue_route_precedes_identifier_and_reports_real_native_occupancy(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/v1/work-orders/queue")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        snapshot = response.json()
        assert snapshot["items"] == [] and snapshot["total"] == 0
        assert snapshot["scheduler"] == {"active": 0, "queued": 0, "max_active": 4, "queue_capacity": 100}
        assert client.get("/api/v1/work-orders/queue", headers={"Origin": "https://attacker.invalid"}).status_code == 403


def test_work_order_search_is_server_owned_and_bounded(tmp_path):
    with TestClient(create_app(tmp_path, provider=MockProvider()), base_url="http://127.0.0.1") as client:
        request = body()
        request["plan"]["title"] = "查找 100%_fixture"
        record = client.post("/api/v1/work-orders", json=request).json()
        assert client.get("/api/v1/work-orders", params={"search": "100%_"}).json()[0]["work_order_id"] == record["work_order_id"]
        assert client.get("/api/v1/work-orders", params={"search": "no such title"}).json() == []
        assert client.get("/api/v1/work-orders", params={"search": "x" * 201}).status_code == 422


def test_selection_static_route_explicit_clear_cas_replay_and_no_implicit_activation(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        url = "/api/v1/work-orders/selection"
        response = client.get(url)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json() == {"saved": False, "work_order_id": None, "run_id": None, "revision": 0, "request_key": None}
        record = client.post("/api/v1/work-orders", json=body()).json()
        identifier = record["work_order_id"]
        selection_url = f"/api/v1/work-orders/{identifier}/selection"
        assert client.get(selection_url).json() == {"saved": False, "work_order_id": identifier, "run_id": None}
        payload = {"work_order_id": identifier, "run_id": None, "expected_revision": 0, "idempotency_key": "offline-selection-key"}
        accepted = client.post(url, json=payload)
        assert accepted.status_code == 200 and accepted.headers["cache-control"] == "no-store"
        assert accepted.json()["revision"] == 1 and accepted.json()["work_order_id"] == identifier
        assert client.post(url, json=payload).json() == accepted.json()
        assert client.get(selection_url).json()["saved"] is True
        assert client.post(url, json={**payload, "work_order_id": None}).status_code == 409
        stale = client.post(url, json={**payload, "idempotency_key": "other-selection-key"})
        assert stale.status_code == 409 and stale.headers["cache-control"] == "no-store"
        cleared = client.post(url, json={**payload, "work_order_id": None, "expected_revision": 1,
                                         "idempotency_key": "clear-selection-key"})
        assert cleared.status_code == 200 and cleared.json()["saved"] and cleared.json()["work_order_id"] is None
        assert client.get(selection_url).json()["saved"] is True
        # Creation's replay marker is an HTTP envelope, not part of the durable record.
        durable = client.get(f"/api/v1/work-orders/{identifier}").json()
        assert durable == {key: value for key, value in record.items() if key != "replayed"}
        assert durable["status"] == "draft" and durable["attempts"] == [] and durable["active_revision"] == 1
        assert app.state.run_service.scheduler.accepted_count == 0


def test_selection_api_schema_origin_and_wrong_run_fail_without_overwriting(tmp_path):
    with TestClient(create_app(tmp_path, provider=MockProvider()), base_url="http://127.0.0.1") as client:
        url = "/api/v1/work-orders/selection"
        identifier = client.post("/api/v1/work-orders", json=body()).json()["work_order_id"]
        payload = {"work_order_id": identifier, "run_id": None, "expected_revision": 0, "idempotency_key": "offline-selection-key"}
        for invalid in ({**payload, "expected_revision": True}, {**payload, "expected_revision": "0"},
                        {**payload, "run_id": "not-an-id"}, {**payload, "permissions": {}},
                        {key: value for key, value in payload.items() if key != "work_order_id"}):
            assert client.post(url, json=invalid).status_code == 422
        assert client.get(url, headers={"Origin": "https://attacker.invalid"}).status_code == 403
        assert client.post(url, json=payload, headers={"Origin": "https://attacker.invalid"}).status_code == 403
        assert client.get(f"/api/v1/work-orders/{identifier}/selection", headers={"Origin": "https://attacker.invalid"}).status_code == 403
        absent = client.post(url, json={**payload, "run_id": "f" * 32})
        assert absent.status_code == 404 and absent.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/work-orders/" + "f" * 32 + "/selection").status_code == 404
        assert client.get(url).json()["saved"] is False


def test_revision_cas_preserves_history_and_old_creation_replay(tmp_path):
    with TestClient(create_app(tmp_path, provider=MockProvider()), base_url="http://127.0.0.1") as client:
        original = body()
        identifier = client.post("/api/v1/work-orders", json=original).json()["work_order_id"]
        edited = body()["plan"]
        edited["title"] = "revision two"
        url = f"/api/v1/work-orders/{identifier}"
        response = client.put(url + "/plan", json={"expected_revision": 1, "plan": edited})
        assert response.status_code == 200 and response.json()["active_revision"] == 2
        assert client.put(url + "/plan", json={"expected_revision": 1, "plan": edited}).status_code == 409
        assert client.get(url + "/plans/1").json()["title"] == "offline plan"
        assert client.get(url + "/plans/2").json()["title"] == "revision two"
        assert client.post("/api/v1/work-orders", json=original).json()["active_revision"] == 2
        assert client.post(url + "/activate", json={"expected_revision": 1}).status_code == 409


def test_explicit_activation_uses_normal_mock_runtime_and_completed_lease_oracle(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        identifier = client.post("/api/v1/work-orders", json=body()).json()["work_order_id"]
        response = client.post(f"/api/v1/work-orders/{identifier}/activate", json={"expected_revision": 1})
        assert response.status_code == 200, response.text
        record = wait(client, identifier, lambda r: r["status"] == "succeeded")
        assert [t["status"] for t in record["tasks"]] == ["succeeded", "succeeded"]
        assert len(record["attempts"]) == app.state.run_service.scheduler.accepted_count == 2
        for attempt in record["attempts"]:
            run = client.get(f"/api/v1/runs/{attempt['run_id']}").json()
            assert run["status"] == "completed" and run["lease_active"] is False
            assert not any(run["request"]["permissions"].values())


def test_validation_rejects_cycles_unknown_grants_and_client_owned_snapshots(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        cyclic = body()
        cyclic["plan"]["tasks"][0]["dependencies"] = ["next"]
        assert client.post("/api/v1/work-orders", json=cyclic).status_code == 409
        identifier = client.post("/api/v1/work-orders", json=body()).json()["work_order_id"]
        url = f"/api/v1/work-orders/{identifier}/activate"
        for settings in ({"profiles": {}}, {"permissions": {"workspace_write": "false"}},
                         {"permissions": {"workspace_write": 1}}, {"permissions": {"admin": True}},
                         {"deadline_seconds": True}):
            response = client.post(url, json={"expected_revision": 1, "settings": settings})
            assert response.status_code == 422, response.text
        assert app.state.run_service.scheduler.accepted_count == 0


def test_pause_cancel_and_missing_revision_have_no_false_dispatch(tmp_path):
    with TestClient(create_app(tmp_path, provider=MockProvider()), base_url="http://127.0.0.1") as client:
        identifier = client.post("/api/v1/work-orders", json=body()).json()["work_order_id"]
        url = f"/api/v1/work-orders/{identifier}"
        assert client.post(url + "/control", json={"expected_revision": 1, "action": "pause"}).status_code == 409
        cancelled = client.post(url + "/control", json={"expected_revision": 1, "action": "cancel"})
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["attempts"] == []
        assert client.post(url + "/activate", json={"expected_revision": 1}).status_code == 409
        missing = client.get(url + "/plans/999")
        assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"
        missing = client.get("/api/v1/work-orders/" + "a" * 32)
        assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"


def test_same_origin_and_host_gate_and_private_admission_namespace(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        for headers in ({"Origin": "https://attacker.invalid"}, {"Host": "attacker.invalid"},
                        {"Origin": "http://127.0.0.1:9999"}):
            assert client.post("/api/v1/work-orders", json=body(), headers=headers).status_code == 403
        spoof = client.post("/api/v1/runs", json={"prompt": "spoof", "idempotency_key": "work-order:" + "a" * 32})
        assert spoof.status_code == 409
        assert client.get("/api/v1/work-orders").json() == []
        assert app.state.run_service.scheduler.accepted_count == 0


def test_work_order_approval_does_not_approve_its_patch_or_unlock_successor(tmp_path):
    class WriteProvider:
        calls = 0

        def next_turn(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return ModelTurn(tool_calls=(ToolCall("work-order-patch", "propose_patch", {
                    "changes": [{"path": "approved.txt", "content": "approved fixture"}],
                }),))
            return ModelTurn(content="finished offline fixture")

    with TestClient(create_app(tmp_path, provider=WriteProvider()), base_url="http://127.0.0.1") as client:
        write = body()
        write["plan"]["tasks"][0]["access"] = "write"
        identifier = client.post("/api/v1/work-orders", json=write).json()["work_order_id"]
        activated = client.post(f"/api/v1/work-orders/{identifier}/activate", json={"expected_revision": 1,
            "settings": {"permissions": {"workspace_write": True}}})
        assert activated.status_code == 200, activated.text
        record = wait(client, identifier, lambda r: r["tasks"][0]["status"] == "awaiting_approval")
        assert len(record["attempts"]) == 1 and record["tasks"][1]["status"] == "pending"
        assert not (tmp_path / "approved.txt").exists()
        run_id = record["attempts"][0]["run_id"]
        run = client.get(f"/api/v1/runs/{run_id}").json()
        interrupt = run["metadata"]["interrupts"][0]["id"]
        resumed = client.post(f"/api/v1/runs/{run_id}/interrupts/{interrupt}/resume", json={"action": "approve"})
        assert resumed.status_code == 202, resumed.text
        terminal = wait(client, identifier, lambda r: r["status"] == "succeeded")
        assert len(terminal["attempts"]) == 2
        assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "approved fixture"
