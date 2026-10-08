"""D1 transport definitions for S9; no provider/native/Git calls here."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.workspace.patching import PatchService
from doppel_agent.workspace.patching import PatchConflictError


RUN, REVIEW, PATCH, PLAN = "a" * 32, "b" * 32, "c" * 32, "d" * 64
BASE = f"/api/v1/changes/runs/{RUN}"


def client_fixture(tmp_path, monkeypatch, method, *, value=None, error=None):
    app = create_app(tmp_path, provider=MockProvider())
    calls = []

    async def operation(*args, **kwargs):
        calls.append((args, kwargs))
        if error is not None:
            raise error
        return value if value is not None else {"fixture": "transport_not_execution_proof"}

    monkeypatch.setattr(app.state.run_service, method, operation)
    return app, calls


@pytest.mark.parametrize("verb,path,method,body,args,kwargs", [
    ("get", "/api/v1/changes/status", "git_status", None, (), {}),
    ("post", "/api/v1/changes/diff", "git_diff", {"path": "a.txt", "plane": "staged", "expected_fingerprint": PLAN},
     ("a.txt",), {"plane": "staged", "expected_fingerprint": PLAN, "conflict_stage": None}),
    ("get", BASE + "/patches?limit=2&offset=1", "list_patch_effects", None, (RUN,), {"limit": 2, "offset": 1}),
    ("get", BASE + "/patch-evidence?tool_call_id=actual-source", "get_patch_evidence", None, (RUN, "actual-source"), {}),
    ("get", BASE + "/inverse-reviews/" + REVIEW, "get_inverse_patch", None, (RUN, REVIEW), {}),
    ("get", BASE + "/verification-reviews?limit=2&after_id=" + REVIEW + "&tool_call_id=actual-source&patch_id=" + PATCH,
     "list_verifications", None, (RUN,), {"limit": 2, "after_id": REVIEW, "tool_call_id": "actual-source", "patch_id": PATCH}),
    ("get", BASE + "/verification-reviews/" + REVIEW, "get_verification", None, (RUN, REVIEW), {}),
    ("post", BASE + "/inverse-reviews", "prepare_inverse_patch",
     {"operation_id": REVIEW, "source_tool_call_id": "actual-source", "source_patch_id": PATCH, "workspace_write": True, "confirmed": True},
     (RUN, "actual-source", PATCH), {"operation_id": REVIEW, "workspace_write": True}),
    ("post", BASE + "/inverse-reviews/" + REVIEW + "/decision", "decide_inverse_patch",
     {"patch_id": PATCH, "action": "approve", "workspace_write": True, "confirmed": True},
     (RUN, REVIEW, PATCH), {"action": "approve", "workspace_write": True}),
    ("post", BASE + "/inverse-reviews/" + REVIEW + "/reconcile", "reconcile_inverse_patch",
     {"confirmed": True}, (RUN, REVIEW), {}),
    ("post", BASE + "/verification-reviews", "prepare_verification",
     {"operation_id": REVIEW, "source_tool_call_id": "actual-source", "source_patch_id": PATCH, "names": ["unit"],
      "command_execute": True, "workspace_write": True, "confirmed": True},
     (RUN, "actual-source", PATCH), {"operation_id": REVIEW, "names": ["unit"], "command_execute": True, "workspace_write": True}),
    ("post", BASE + "/verification-reviews/" + REVIEW + "/decision", "decide_verification",
     {"plan_id": PLAN, "action": "approve", "command_execute": True, "workspace_write": True, "confirmed": True},
     (RUN, REVIEW, PLAN), {"action": "approve", "command_execute": True, "workspace_write": True}),
    ("post", BASE + "/verification-reviews/" + REVIEW + "/cancel", "cancel_verification",
     {"plan_id": PLAN, "confirmed": True}, (RUN, REVIEW, PLAN), {}),
    ("post", BASE + "/verification-reviews/" + REVIEW + "/reconcile", "reconcile_verification",
     {"confirmed": True}, (RUN, REVIEW), {}),
])
def test_fixed_route_forwarding_source_and_original_review_ids(tmp_path, monkeypatch, verb, path, method, body, args, kwargs):
    app, calls = client_fixture(tmp_path, monkeypatch, method)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.request(verb, path, json=body) if body is not None else client.request(verb, path)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert calls == [(args, kwargs)]
        assert app.state.run_service.scheduler.accepted_count == 0


@pytest.mark.parametrize("changes", [
    {"confirmed": False}, {"command_execute": "true"}, {"workspace_write": 1}, {"plan_id": "bad"},
    {"argv": ["private-request-argv-must-not-echo"]}, {"action": "retry"},
])
def test_invalid_or_unconfirmed_review_never_reaches_service_or_echoes_input(tmp_path, monkeypatch, changes):
    app, calls = client_fixture(tmp_path, monkeypatch, "decide_verification")
    body = {"plan_id": PLAN, "action": "approve", "command_execute": True, "workspace_write": True, "confirmed": True}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(BASE + "/verification-reviews/" + REVIEW + "/decision", json={**body, **changes})
        assert response.status_code in {403, 422} and calls == []
        assert response.headers["cache-control"] == "no-store"
        assert "private-request-argv" not in response.text and "input" not in response.json()


@pytest.mark.parametrize("error,code,detail", [
    (KeyError("private error fixture"), 404, "changes_source_not_found"),
    (PermissionError("private denial fixture"), 403, "changes_permission_required"),
    (ValueError("verification_review_stale"), 409, "verification_review_stale"),
    (ValueError("verification_review_scope_unavailable"), 404, "verification_review_scope_unavailable"),
    (RuntimeError("verification_process_cleanup_quarantine"), 503, "verification_process_cleanup_quarantine"),
    (RuntimeError("verification_operation_active"), 409, "verification_operation_active"),
    (OSError("private OS path fixture"), 503, "changes_service_unavailable"),
    (ValueError("private parser fixture"), 503, "changes_service_unavailable"),
    (PatchConflictError("stale patch base for private fixture path"), 409, "changes_patch_conflict"),
])
def test_fixed_error_codes_no_raw_sensitive_exception(tmp_path, monkeypatch, error, code, detail):
    app, _ = client_fixture(tmp_path, monkeypatch, "get_verification", error=error)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get(BASE + "/verification-reviews/" + REVIEW)
        assert response.status_code == code and response.json() == {"detail": detail}
        assert response.headers["cache-control"] == "no-store"
        assert "private" not in response.text


def test_request_budget_duplicates_unknown_queries_host_origin_and_no_path_authority(tmp_path, monkeypatch):
    app, calls = client_fixture(tmp_path, monkeypatch, "git_status", value={"available": False, "reason": "not_git_repository", "repository_clean": None})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        path = "/api/v1/changes/status"
        assert client.get(path).json()["repository_clean"] is None
        count = len(calls)
        for query in ("authorized_metadata_roots=D:/not-authorized", "executable=evil", "argv=anything"):
            response = client.get(path + "?" + query)
            assert response.status_code == 422 and response.headers["cache-control"] == "no-store"
        for headers in ({"Origin": "https://evil.invalid"}, {"Host": "evil.invalid"}):
            response = client.get(path, headers=headers)
            assert response.status_code == 403 and response.headers["cache-control"] == "no-store"
        assert len(calls) == count
        missing = client.post("/api/v1/changes/metadata-authorizations", json={"path": "D:/not-authorized"})
        assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"
        wrong_method = client.post(path, json={"confirmed": True})
        assert wrong_method.status_code == 405 and wrong_method.headers["cache-control"] == "no-store"
        repeated = client.get(BASE + "/patches?limit=1&limit=2")
        assert repeated.status_code == 422 and repeated.json() == {"detail": "invalid_changes_request"}
        endpoint = BASE + "/verification-reviews/" + REVIEW + "/decision"
        duplicate = '{"confirmed":false,"confirmed":true,"plan_id":"' + PLAN + '","action":"approve"}'
        invalid = client.post(endpoint, content=duplicate, headers={"Content-Type": "application/json"})
        assert invalid.status_code == 422 and invalid.json() == {"detail": "invalid_changes_request"}
        nonfinite = client.post(endpoint, content='{"confirmed":true,"plan_id":NaN,"action":"approve"}',
                                headers={"Content-Type": "application/json"})
        assert nonfinite.status_code == 422 and nonfinite.json() == {"detail": "invalid_changes_request"}
        too_large = client.post(endpoint, content=b" " * (16384 + 1), headers={"Content-Type": "application/json"})
        assert too_large.status_code == 413 and too_large.headers["cache-control"] == "no-store"
        assert client.get(path + "?" + "q=" + "x" * 8192).status_code == 413


def test_verification_diagnostic_get_never_calls_start_or_recovery_under_quarantine(tmp_path, monkeypatch):
    # Transport's diagnostic path calls the read service only; held owner/failure
    # behavior is defined separately against actual RunService in service tests.
    app, calls = client_fixture(tmp_path, monkeypatch, "get_verification", value={"status": "indeterminate", "service": {"execution_admission": "quarantined"}})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        async def forbidden():
            pytest.fail("query transport restarted service")

        monkeypatch.setattr(app.state.run_service, "start", forbidden)
        response = client.get(BASE + "/verification-reviews/" + REVIEW)
        assert response.status_code == 200 and response.json()["status"] == "indeterminate" and len(calls) == 1


def test_actual_patch_and_inverse_transport_sticky_review_and_no_get_mutation(tmp_path):
    # Actual app/owned service/ledger, no runtime/provider or command execution.
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        service = app.state.run_service
        run = uuid4().hex
        service.runs.create(run, uuid4().hex, "graph", {"prompt": "fixture", "mode": "graph"}, None)
        (tmp_path / "file.txt").write_text("old fixture", encoding="utf-8")
        patcher = PatchService(tmp_path)
        proposal = patcher.prepare([{"path": "file.txt", "content": "new fixture"}])
        ledger = ToolExecutionLedger(service.state_root / "tool-executions.sqlite3")
        ledger.execute_patch_once(run, "source-call", proposal.as_dict(), patcher, proposal)
        service.runs.update(run, "completed")
        service.runs.release_conversation_turn(run)
        original = service.runs.get(run)
        base = f"/api/v1/changes/runs/{run}"
        listed = client.get(base + "/patches")
        assert listed.status_code == 200 and listed.json()[0]["confirmed_applied"]
        assert "before_content" not in listed.text and service._inverse_reviews is None
        assert client.get(base + "/patch-evidence?tool_call_id=source-call").json()["result"]["patch_id"] == proposal.patch_id
        body = {"operation_id": uuid4().hex, "source_tool_call_id": "source-call", "source_patch_id": proposal.patch_id,
                "workspace_write": True, "confirmed": True}
        prepared = client.post(base + "/inverse-reviews", json=body)
        assert prepared.status_code == 200 and prepared.json()["status"] == "pending"
        read = client.get(base + "/inverse-reviews/" + body["operation_id"])
        assert read.json()["query"]["sql_read_only"] and read.json()["review"] == prepared.json()["review"]
        # Original exact operation ID/payload replay, never a new proposal/TTL.
        assert client.post(base + "/inverse-reviews", json=body).json() == prepared.json()
        rejected = client.post(base + "/inverse-reviews/" + body["operation_id"] + "/decision",
            json={"patch_id": prepared.json()["patch_id"], "action": "reject", "confirmed": True})
        assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
        assert (tmp_path / "file.txt").read_text(encoding="utf-8") == "new fixture"
        assert service.runs.get(run) == original and service.scheduler.accepted_count == 0
