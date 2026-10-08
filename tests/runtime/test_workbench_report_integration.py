"""S8 D FIRST full original workbench integration definitions, ALL UNRUN.

Real API lifespan/service/schedulers/runtimes/SQLite/report; only HTTP transport
is offline. No SDK/runtime/result substitute, paid call, native window or invoice
proof. Temporary workspace only. Watchdogs detect deadlock, not measured SLAs.
"""

import asyncio
from copy import deepcopy
from fractions import Fraction
import hashlib
import io
import json
import os
import sys
from threading import Event
from uuid import uuid4

import httpx
import pytest

from doppel_agent.api import create_app
from doppel_agent.provider import AsyncOpenAICompatibleProvider
import doppel_agent.provider as provider_module
import doppel_agent.runtime.service as service_module
import doppel_agent.settings as settings_module
from doppel_agent.runtime.provider_recording import ProviderReceiptError
from doppel_agent.reporting.evidence import report_digest


PRIVATE = "PRIVATE_D_FULL_PROMPT_ANSWER_MODEL_SOURCE"
WATCHDOG = 30


def original_dag(modes=("legacy", "graph", "deep"), *, write_first=False):
    return {
        "title": PRIVATE,
        "tasks": [
            {
                "id": f"step{index}",
                "title": PRIVATE,
                "prompt": PRIVATE,
                "mode": mode,
                "access": "write" if index == 1 or (write_first and index == 0) else "read",
                "dependencies": [f"step{index - 1}"] if index else [],
            }
            for index, mode in enumerate(modes)
        ],
    }


async def original_order(api, modes=("legacy", "graph", "deep"), *, permissions=None, write_first=False):
    created = await api.post(
        "/api/v1/work-orders",
        json={
            "plan": original_dag(modes, write_first=write_first),
            "idempotency_key": "offline-dag-first-create",
        },
    )
    assert created.status_code == 201, created.text
    identifier = created.json()["work_order_id"]
    replay = await api.post(
        "/api/v1/work-orders",
        json={
            "plan": original_dag(modes, write_first=write_first),
            "idempotency_key": "offline-dag-first-create",
        },
    )
    assert replay.status_code == 201 and replay.json()["work_order_id"] == identifier
    assert replay.json()["replayed"] is True
    activated = await api.post(
        f"/api/v1/work-orders/{identifier}/activate",
        json={
            "expected_revision": 1,
            "settings": {"permissions": permissions or {}, "deadline_seconds": WATCHDOG},
        },
    )
    assert activated.status_code == 200, activated.text
    return identifier, activated.json()


async def original_order_until(api, identifier, predicate):
    async with asyncio.timeout(WATCHDOG):
        while True:
            response = await api.get(f"/api/v1/work-orders/{identifier}")
            assert response.status_code == 200, response.text
            record = response.json()
            if predicate(record):
                return record
            await asyncio.sleep(0.01)  # Observe original pump; never step/replace it.


async def original_attempt_report(api, service, identifier, attempt, frozen, order_status):
    record = await service.get(attempt["run_id"])
    response = await api.get(f"/api/v1/reports/runs/{attempt['run_id']}")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["version"] == 4 and report["source"]["status"] == record["status"]
    assert report["cost"]["price_snapshot"] == frozen
    assert report["cost"]["billing_complete"] is report["physical_drain_verified"] is False
    assert report["cost"]["source_verified"] is report["cost"]["account_cap_guaranteed"] is False
    assert PRIVATE not in json.dumps(report) and "CHANGED_PRIVATE_MODEL" not in json.dumps(report)
    evidence = report["evidence"]["work_orders"]
    assert evidence["state"] == "known" and evidence["total"] == evidence["emitted"] == 1
    assert evidence["items"] == [
        {
            "attempt_id": attempt["attempt_id"],
            "work_order_id": identifier,
            "task_id_sha256": report_digest("task_id", attempt["task_id"]),
            "execution_revision": 1,
            "active_plan_revision": 1,
            "attempt_number": attempt["attempt_number"],
            "attempt_status": attempt["status"],
            "order_status": order_status,
            "dispatch_state": "admitted",
        }
    ]
    return record, report


def declaration(rate="1.25"):
    return {
        "version": 1,
        "currency": "CNY",
        "effective_date": "2026-10-01",
        "source_kind": "offline_fixture",
        "source_reference": PRIVATE,
        "unit_tokens": 1_000_000,
        "billing_basis": "input_output_inclusive",
        "reasoning_basis": "included_in_output",
        "rates": {"input": rate, "output": "2.5", "cached_input": None, "uncached_input": None},
    }


def config(rate="1.25", model=PRIVATE):
    return {
        "provider": "openai",
        "preset": "openai",
        "base_url": "https://fixture.invalid/v1",
        "model": model,
        "name": PRIVATE,
        "billing_tariff": declaration(rate),
        "billing_tariff_confirmed": True,
    }


def body(inputs=7, outputs=3, *, malformed=False):
    message = {"content": PRIVATE}
    if malformed:
        message["tool_calls"] = [
            {"id": PRIVATE, "type": "function", "function": {"name": PRIVATE, "arguments": "[]"}}
        ]
    return {
        "choices": [{"message": message}],
        "usage": {"prompt_tokens": inputs, "completion_tokens": outputs},
    }


class Response(io.BytesIO):
    status = 200


def offline_transports(monkeypatch, client, journal):
    """Preserve original provider methods/parsers/receipts; never replace runtime."""
    providers = []

    async def fixture_sleep(_delay):
        await asyncio.sleep(0)  # Deterministic original retry path, not actual delay evidence.

    def offline_async(*args, **kwargs):
        # Exact original class, not subclass/opaque provider that weakens its
        # direct-original transport coverage contract. Only inject HTTP client.
        provider = AsyncOpenAICompatibleProvider(*args, **kwargs, client=client, sleep=fixture_sleep)
        providers.append(provider)
        return provider

    def sync_open(request, *, timeout):
        journal.append(("sync", json.loads(request.data)))
        return Response(json.dumps(body()).encode())

    async def no_real_network(*_args, **_kwargs):
        raise AssertionError("D integration attempted unmocked HTTP transport")

    def no_credentials(*_args, **_kwargs):
        raise AssertionError("D integration entered credential protection/decryption")

    monkeypatch.setattr(service_module, "AsyncOpenAICompatibleProvider", offline_async)
    monkeypatch.setattr(provider_module, "urlopen", sync_open)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_real_network)
    monkeypatch.setattr(settings_module, "_protect", no_credentials)
    monkeypatch.setattr(settings_module, "_unprotect", no_credentials)
    return providers


async def original_root(api, service, mode, *, delegate=True):
    response = await api.post(
        "/api/v1/runs",
        json={
            "prompt": PRIVATE,
            "mode": mode,
            "permissions": {"delegate": delegate},
            "deadline_seconds": WATCHDOG,
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    result = await asyncio.wait_for(service.scheduler.wait(run_id), WATCHDOG)
    record = await service.get(run_id)
    assert result.status == record["status"] == "completed" and not record["lease_active"]
    return run_id, record, result


async def original_child(api, service, run_id):
    response = await api.post(f"/api/v1/runs/{run_id}/subagents", json={"prompt": PRIVATE})
    assert response.status_code == 202, response.text
    child_id = response.json()["subagent_id"]
    first = await asyncio.wait_for(service.subagents.wait(child_id), WATCHDOG)
    assert first["status"] == "completed" and first["generation"] == 1
    await service.follow_up_subagent(run_id, child_id, PRIVATE, expected_generation=1)
    second = await asyncio.wait_for(service.subagents.wait(child_id), WATCHDOG)
    assert second["status"] == "completed" and second["generation"] == 2
    assert second["history"] == [{"prompt": first["prompt"], "answer": first["answer"]}]
    return child_id


async def original_applied_patch(api, service, mode, tool_call, target):
    """Observe original SDK policy/approval/effect; never synthesize a ledger row."""
    response = await api.post(
        "/api/v1/runs",
        json={
            "prompt": PRIVATE,
            "mode": mode,
            "permissions": {"workspace_write": True},
            "deadline_seconds": WATCHDOG,
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    first = await asyncio.wait_for(service.scheduler.wait(run_id), WATCHDOG)
    run = await service.get(run_id)
    assert first.status == run["status"] == "interrupted" and not target.exists()
    assert "fallback_runtime" not in run["metadata"]
    interrupt_id = run["metadata"]["interrupts"][0]["id"]
    approval = await api.post(
        f"/api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume", json={"action": "approve"}
    )
    assert approval.status_code == 202
    last = await asyncio.wait_for(service.scheduler.wait(run_id), WATCHDOG)
    record = await service.get(run_id)
    assert last.status == record["status"] == "completed" and not record["lease_active"]
    assert "fallback_runtime" not in record["metadata"] and target.read_bytes() == PRIVATE.encode()
    evidence = await api.get(
        f"/api/v1/changes/runs/{run_id}/patch-evidence", params={"tool_call_id": tool_call}
    )
    assert evidence.status_code == 200, evidence.text
    original = evidence.json()
    assert original["confirmed_applied"] and original["receipt"]["patch_id"]
    return run_id, record, original


def original_patch_response(journal, target, tool_call):
    async def handler(request):
        journal.append(("async", json.loads(request.content)))
        if len(journal) == 1:
            response = body(2, 1)
            response["choices"][0]["message"] = {
                "content": "",
                "tool_calls": [
                    {
                        "id": tool_call,
                        "type": "function",
                        "function": {
                            "name": "propose_patch",
                            "arguments": json.dumps({"changes": [{"path": target.name, "content": PRIVATE}]}),
                        },
                    }
                ],
            }
        else:
            assert len(journal) == 2  # No unexpected fallback/provider/verification prompt.
            response = body()
        return httpx.Response(200, request=request, json=response)

    return handler


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("outcome", ["success", "nonzero", "reject", "stale_config"])
def test_original_sdk_patch_manual_verification_pending_reopen_uses_current_workspace_and_numeric_report(
    tmp_path, monkeypatch, mode, outcome
):
    """D FIRST 2026-10-07, ALL UNRUN; actual Windows Job only at whole S9.

    Config pins argv/config, NOT original source bytes/executable/dependency state.
    Actual verification deliberately observes a later user-edited workspace, not
    original patch acceptance or model quality. Reject/stale cases launch nothing.
    """
    if outcome in {"success", "nonzero"} and os.name != "nt":
        pytest.skip("Actual original Windows strict Job gate is mandatory at S9")

    async def scenario():
        target, marker = tmp_path / "verified-fixture.txt", tmp_path / "verification-marker.txt"
        tool_call, name = PRIVATE + "_VERIFICATION_PATCH", PRIVATE + "_VERIFY_NAME"
        dirty = PRIVATE + "_LATER_USER_EDIT"
        config_path = tmp_path / ".doppel" / "verification.json"
        config_path.parent.mkdir()
        code = (
            f"import pathlib,sys; value=pathlib.Path({target.name!r}).read_text(encoding='utf-8'); "
            f"assert value == {dirty!r}; pathlib.Path({marker.name!r}).write_text(value,encoding='utf-8'); "
            f"print(value); sys.exit({7 if outcome == 'nonzero' else 0})"
        )
        config_data = {
            "commands": [{"name": name, "argv": [sys.executable, "-c", code], "timeout_seconds": 5}],
            "max_output_bytes": 4096,
            "stop_on_failure": True,
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")
        journal = []
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(original_patch_response(journal, target, tool_call))
        ) as upstream:
            offline_transports(monkeypatch, upstream, journal)
            app = create_app(tmp_path)
            service = app.state.run_service
            await asyncio.to_thread(service.settings.save_profile, config())
            review_id = uuid4().hex
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api:
                    run_id, original_run, patch = await original_applied_patch(
                        api, service, mode, tool_call, target
                    )
                    patch_id = patch["receipt"]["patch_id"]
                    assert not marker.exists()  # Patch approval is NOT manual command approval.
                    initial = (await api.get(f"/api/v1/reports/runs/{run_id}")).json()
                    request = {
                        "confirmed": True,
                        "source_tool_call_id": tool_call,
                        "source_patch_id": patch_id,
                        "operation_id": review_id,
                        "names": [name],
                        "command_execute": True,
                        "workspace_write": True,
                    }
                    refused = await api.post(
                        f"/api/v1/changes/runs/{run_id}/verification-reviews",
                        json={**request, "confirmed": False},
                    )
                    assert refused.status_code == 403 and not marker.exists()
                    prepared = await api.post(
                        f"/api/v1/changes/runs/{run_id}/verification-reviews", json=request
                    )
                    assert prepared.status_code == 200, prepared.text
                    pending = prepared.json()
                    assert (
                        pending["status"] == "pending"
                        and pending["steps"] == []
                        and pending["success"] is None
                    )
                    assert pending["source"] == {
                        "run_id": run_id,
                        "tool_call_id": tool_call,
                        "patch_id": patch_id,
                    }
                    assert pending["target"] == "current_workspace_not_original_patch_snapshot"
                    replay = await api.post(
                        f"/api/v1/changes/runs/{run_id}/verification-reviews", json=request
                    )
                    assert replay.status_code == 200 and replay.json()["plan"] == pending["plan"]
                    assert (
                        replay.json()["created_at"] == pending["created_at"]
                        and replay.json()["expires_at"] == pending["expires_at"]
                    )
                    assert (
                        not marker.exists()
                        and len(journal) == 2
                        and service.process_supervisor.active_count == 0
                    )
                    await asyncio.to_thread(
                        service.settings.save_profile, config("999", "CHANGED_PRIVATE_MODEL")
                    )
            assert service.cleanup_complete and not service._owner.held and not marker.exists()

            reopened = create_app(tmp_path)
            again = reopened.state.run_service
            async with reopened.router.lifespan_context(reopened):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=reopened), base_url="http://127.0.0.1"
                ) as api:
                    base = f"/api/v1/changes/runs/{run_id}/verification-reviews/{review_id}"
                    restored = await api.get(base)
                    assert restored.status_code == 200 and restored.json()["plan"] == pending["plan"]
                    assert restored.json()["status"] == "pending" and not marker.exists()
                    assert again.scheduler.accepted_count == 0 and not again._providers and len(journal) == 2
                    if outcome in {"success", "nonzero"}:
                        target.write_text(
                            dirty, encoding="utf-8"
                        )  # Disposable fixture user edit, not agent attribution.
                    if outcome == "stale_config":
                        changed = deepcopy(config_data)
                        changed["commands"][0]["argv"][-1] = (
                            "raise AssertionError('PRIVATE_STALE_COMMAND_MUST_NOT_RUN')"
                        )
                        config_path.write_text(json.dumps(changed), encoding="utf-8")
                    decision = {
                        "confirmed": True,
                        "plan_id": pending["plan"]["plan_id"],
                        "action": "reject" if outcome == "reject" else "approve",
                        "command_execute": outcome != "reject",
                        "workspace_write": outcome != "reject",
                    }
                    if outcome != "reject":
                        denied = await api.post(
                            base + "/decision", json={**decision, "command_execute": False}
                        )
                        assert denied.status_code == 403 and not marker.exists()
                    selected = await api.post(base + "/decision", json=decision)
                    assert selected.status_code == 200, selected.text
                    current = selected.json()
                    expected = (
                        "rejected"
                        if outcome == "reject"
                        else "failed"
                        if outcome == "stale_config"
                        else "completed"
                    )
                    assert current["status"] == expected and current["source"] == pending["source"]
                    assert current["plan"] == pending["plan"] and not current["has_unknown_command"]
                    if outcome in {"success", "nonzero"}:
                        assert (
                            marker.read_text(encoding="utf-8") == dirty
                            and target.read_text(encoding="utf-8") == dirty
                        )
                        (step,) = current["steps"]
                        assert step["status"] == "finished" and step["result"]["supervision"] == "job_object"
                        assert step["result"]["exit_code"] == (7 if outcome == "nonzero" else 0)
                        assert (
                            current["success"] is (outcome == "success") and dirty in step["result"]["stdout"]
                        )
                    else:
                        assert not marker.exists() and current["steps"] == [] and current["success"] is None
                        if outcome == "stale_config":
                            assert current["error_code"] == "verification_review_stale"
                    repeat = await api.post(base + "/decision", json=decision)
                    assert repeat.status_code == (409 if outcome == "stale_config" else 200)
                    if repeat.status_code == 200:
                        assert repeat.json()["decision_replayed"] is True
                    report_response = await api.get(f"/api/v1/reports/runs/{run_id}")
                    assert report_response.status_code == 200
                    report = report_response.json()
                    assert (
                        report["cost"] == initial["cost"]
                        and report["provider_usage"] == initial["provider_usage"]
                    )
                    assert PRIVATE not in json.dumps(report) and "CHANGED_PRIVATE_MODEL" not in json.dumps(
                        report
                    )
                    (item,) = report["evidence"]["verifications"]["items"]
                    assert item["review_id"] == review_id and item["source_patch_id"] == patch_id
                    assert item["source_tool_call_sha256"] == report_digest("tool_call", tool_call)
                    assert item["status"] == expected and item["source_receipt_confirmed"] is True
                    assert (
                        item["target"] == "current_workspace_not_original_patch_snapshot"
                        and item["project_acceptance"] is False
                    )
                    assert item["planned_commands"] == 1 and item["sealed_steps"] == (
                        1 if outcome in {"success", "nonzero"} else 0
                    )
                    assert item["failed_attempts"] == (1 if outcome == "nonzero" else 0)
                    if outcome in {"success", "nonzero"}:
                        assert item["attempts_success"] is (outcome == "success")
                    else:
                        assert item["attempts_success"] is None
                    assert (await again.get(run_id))["status"] == original_run["status"] == "completed"
                    detail = await api.get(
                        f"/api/v1/changes/runs/{run_id}/patch-evidence", params={"tool_call_id": tool_call}
                    )
                    assert (
                        detail.json() == patch
                    )  # Original ledger result is not rewritten by current-workspace verification.
                    assert (
                        again.process_supervisor.active_count == 0
                        and not again.process_supervisor.cleanup_failed
                    )
                    assert len(journal) == 2 and again.scheduler.accepted_count == 0 and not again._providers
            assert again.cleanup_complete and not again._owner.held

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("dirty_phase", ["before_prepare", "after_prepare"])
def test_original_sdk_patch_manual_inverse_never_erases_later_user_edit_or_original_receipt(
    tmp_path, monkeypatch, mode, dirty_phase
):
    """D FIRST ALL UNRUN: actual original patch, inverse API and ledger; no Git oracle."""

    async def scenario():
        target, tool_call = tmp_path / "inverse-fixture.txt", PRIVATE + "_INVERSE_PATCH"
        dirty = PRIVATE + "_PRESERVE_USER_EDIT"
        journal = []
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(original_patch_response(journal, target, tool_call))
        ) as upstream:
            offline_transports(monkeypatch, upstream, journal)
            app = create_app(tmp_path)
            service = app.state.run_service
            await asyncio.to_thread(service.settings.save_profile, config())
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api:
                    run_id, run, patch = await original_applied_patch(api, service, mode, tool_call, target)
                    before = (await api.get(f"/api/v1/reports/runs/{run_id}")).json()
                    review_id = uuid4().hex
                    base = f"/api/v1/changes/runs/{run_id}/inverse-reviews"
                    request = {
                        "confirmed": True,
                        "source_tool_call_id": tool_call,
                        "source_patch_id": patch["receipt"]["patch_id"],
                        "operation_id": review_id,
                        "workspace_write": True,
                    }
                    if dirty_phase == "before_prepare":
                        target.write_text(dirty, encoding="utf-8")
                    prepared = await api.post(base, json=request)
                    if dirty_phase == "before_prepare":
                        assert (
                            prepared.status_code == 409
                            and prepared.json()["detail"] == "changes_patch_conflict"
                        )
                    else:
                        assert prepared.status_code == 200 and prepared.json()["status"] == "pending"
                        inverse = prepared.json()
                        target.write_text(dirty, encoding="utf-8")
                        decision = {
                            "confirmed": True,
                            "patch_id": inverse["patch_id"],
                            "action": "approve",
                            "workspace_write": True,
                        }
                        refused = await api.post(base + f"/{review_id}/decision", json=decision)
                        assert (
                            refused.status_code == 409
                            and refused.json()["detail"] == "changes_patch_conflict"
                        )
                        # The reserved failed effect remains visible; restoring bytes would NOT authorize retry.
                        repeat = await api.post(base + f"/{review_id}/decision", json=decision)
                        assert repeat.status_code == 409
                    assert target.read_text(encoding="utf-8") == dirty
                    report = (await api.get(f"/api/v1/reports/runs/{run_id}")).json()
                    assert (
                        report["cost"] == before["cost"]
                        and report["provider_usage"] == before["provider_usage"]
                    )
                    assert PRIVATE not in json.dumps(report) and len(journal) == 2
                    applied = [
                        item for item in report["evidence"]["patches"]["items"] if item["confirmed_applied"]
                    ]
                    assert len(applied) == 1 and applied[0]["patch_id"] == patch["receipt"]["patch_id"]
                    if dirty_phase == "after_prepare":
                        (item,) = report["evidence"]["inverses"]["items"]
                        assert (
                            item["review_id"] == review_id
                            and item["status"] == "failed"
                            and item["outcome_unknown"] is True
                        )
                        assert (
                            item["source_receipt_confirmed"] is True
                            and item["effect_receipt_state"] != "confirmed"
                        )
                    detail = await api.get(
                        f"/api/v1/changes/runs/{run_id}/patch-evidence", params={"tool_call_id": tool_call}
                    )
                    assert (
                        detail.json() == patch
                        and (await service.get(run_id))["status"] == run["status"] == "completed"
                    )
                    assert (
                        not service._provider_receipt_fault.broken
                        and service.process_supervisor.active_count == 0
                    )
            assert service.cleanup_complete and not service._owner.held

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("remote_tool", ["echo", "structured", "failure"])
@pytest.mark.parametrize("second_decision", ["approve", "reject"])
def test_original_extension_discovery_stdio_checkpoint_reopen_distinct_same_argument_calls_and_report(
    tmp_path, monkeypatch, mode, remote_tool, second_decision
):
    """D FIRST ALL UNRUN; actual SDK/API/local stdio, only provider HTTP offline.

    Real process handles are bound before EACH orderly owner shutdown at S9. No
    remote-server safety/invoice/model-quality or process-crash recovery claim.
    Distinct original tool call IDs MUST NOT alias by equal tool name/arguments.
    """
    from bench.runtime_fixtures import load_task_fixture, materialize_task_case
    from bench.runtime_process_evidence import ProcessIdentity

    async def scenario():
        workspace, trace = tmp_path / "agent", tmp_path / "external-server-trace.jsonl"
        materialize_task_case(load_task_fixture("mcp-01"), workspace)
        config_path = workspace / ".doppel" / "mcp.json"
        config_path.parent.mkdir()
        config_path.write_text(
            json.dumps(
                {
                    "servers": {
                        "matrix": {
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": ["-B", "server.py", str(trace)],
                            "cwd": str(workspace.resolve()),
                            "max_concurrency": 1,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        def observed():
            return (
                [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
                if trace.exists()
                else []
            )

        def calls():
            return [row for row in observed() if row["kind"] == "call"]

        journal, identities = [], []
        logical_name = "mcp__matrix__" + remote_tool
        arguments = {"value": 17} if remote_tool == "structured" else {"text": PRIVATE}
        original_ids = [PRIVATE + "_MCP_CALL_ONE", PRIVATE + "_MCP_CALL_TWO"]

        async def handler(request):
            payload = json.loads(request.content)
            journal.append(("async", payload))
            response = body(2, 1) if len(journal) == 1 else body()
            if len(journal) <= 2:
                assert logical_name in {tool["function"]["name"] for tool in payload["tools"]}
                response["choices"][0]["message"] = {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": original_ids[len(journal) - 1],
                            "type": "function",
                            "function": {"name": logical_name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            else:
                assert len(journal) == 3
            return httpx.Response(200, request=request, json=response)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            offline_transports(monkeypatch, upstream, journal)
            app = create_app(workspace)
            service = app.state.run_service
            saved = await asyncio.to_thread(service.settings.save_profile, config())
            try:
                async with app.router.lifespan_context(app):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                    ) as api:
                        cached_url = "/api/v1/extensions/mcp/servers/matrix/tools/cached"
                        passive = await api.get("/api/v1/extensions/mcp/servers")
                        assert passive.status_code == 200 and passive.json()["servers"][0]["name"] == "matrix"
                        missing = await api.get(cached_url)
                        assert missing.status_code == 200 and missing.json()["cache_state"] == "missing"
                        assert observed() == [] and service.mcp_manager._connections == {}
                        discover_url = "/api/v1/extensions/mcp/servers/matrix/discovery"
                        denied = await api.post(discover_url, json={"action": "refresh", "confirmed": False})
                        assert denied.status_code == 422 and observed() == []
                        discovered = await api.post(
                            discover_url, json={"action": "refresh", "confirmed": True}
                        )
                        assert discovered.status_code == 200, discovered.text
                        assert (
                            discovered.json()["cache_state"] == "cached" and discovered.json()["total"] == 3
                        )
                        assert discovered.json()["tool_execution_verified"] is False and calls() == []
                        starts = [row for row in observed() if row["kind"] == "start"]
                        assert len(starts) == 1
                        first_identity = ProcessIdentity(starts[0]["pid"])
                        identities.append(first_identity)
                        assert not first_identity.exited()
                        before_passive = observed()
                        for _ in range(2):
                            cached = await api.get(cached_url)
                            assert cached.status_code == 200 and cached.json()["cache_state"] == "cached"
                            assert observed() == before_passive  # Cached GET does not refresh/probe/execute.
                        run_response = await api.post(
                            "/api/v1/runs",
                            json={
                                "mode": mode,
                                "prompt": PRIVATE,
                                "permissions": {"mcp_execute": True},
                                "deadline_seconds": WATCHDOG,
                            },
                        )
                        assert run_response.status_code == 202
                        run_id = run_response.json()["run_id"]
                        pending_result = await asyncio.wait_for(service.scheduler.wait(run_id), WATCHDOG)
                        pending = await service.get(run_id)
                        assert (
                            pending_result.status == pending["status"] == "interrupted"
                            and pending["lease_active"]
                        )
                        assert (
                            "fallback_runtime" not in pending["metadata"]
                            and calls() == []
                            and len(journal) == 1
                        )
                        interrupt_id = pending["metadata"]["interrupts"][0]["id"]
                        await asyncio.to_thread(
                            service.settings.save_profile,
                            config("999", "CHANGED_PRIVATE_MODEL"),
                            profile_id=saved["active_profile_id"],
                        )
                await first_identity.wait_exited(timeout=WATCHDOG)
                assert service.cleanup_complete and not service._owner.held and calls() == []
                first_shutdown_trace = observed()

                reopened = create_app(workspace)
                again = reopened.state.run_service
                async with reopened.router.lifespan_context(reopened):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=reopened), base_url="http://127.0.0.1"
                    ) as api:
                        missing = await api.get(cached_url)
                        assert missing.status_code == 200 and missing.json()["cache_state"] == "missing"
                        assert observed() == first_shutdown_trace and again.mcp_manager._connections == {}
                        restored = await again.get(run_id)
                        assert (
                            restored["thread_id"] == pending["thread_id"]
                            and restored["profile_snapshot"] == pending["profile_snapshot"]
                        )
                        assert (
                            restored["status"] == "interrupted"
                            and again.scheduler.accepted_count == 0
                            and not again._providers
                        )
                        first_approval = await api.post(
                            f"/api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume",
                            json={"action": "approve"},
                        )
                        assert first_approval.status_code == 202
                        first_resume = await asyncio.wait_for(again.scheduler.wait(run_id), WATCHDOG)
                        second_pending = await again.get(run_id)
                        assert first_resume.status == second_pending["status"] == "interrupted"
                        assert calls() == [{"kind": "call", "name": remote_tool, "arguments": arguments}]
                        assert len(journal) == 2 and not again._provider_receipt_fault.broken
                        starts = [row for row in observed() if row["kind"] == "start"]
                        assert len(starts) == 2
                        second_identity = ProcessIdentity(starts[1]["pid"])
                        identities.append(second_identity)
                        assert not second_identity.exited()
                        second_interrupt = second_pending["metadata"]["interrupts"][0]["id"]
                        assert second_interrupt != interrupt_id
                        decided = await api.post(
                            f"/api/v1/runs/{run_id}/interrupts/{second_interrupt}/resume",
                            json={"action": second_decision},
                        )
                        assert decided.status_code == 202
                        completed = await asyncio.wait_for(again.scheduler.wait(run_id), WATCHDOG)
                        final = await again.get(run_id)
                        assert (
                            completed.status == final["status"] == "completed" and not final["lease_active"]
                        )
                        assert (
                            "fallback_runtime" not in final["metadata"]
                            and final["profile_snapshot"] == pending["profile_snapshot"]
                        )
                        expected_calls = 2 if second_decision == "approve" else 1
                        assert (
                            calls()
                            == [{"kind": "call", "name": remote_tool, "arguments": arguments}]
                            * expected_calls
                        )
                        rows = await again.list_events(run_id)
                        audits = [row["payload"] for row in rows if row["type"] == "mcp.tool_executed"]
                        assert (
                            len(audits) == expected_calls
                            and len({row["audit_id"] for row in audits}) == expected_calls
                        )
                        assert [row["tool_call_id"] for row in audits] == original_ids[:expected_calls]
                        assert all(
                            row["success"] is (remote_tool != "failure")
                            and row["logical_name"] == logical_name
                            for row in audits
                        )
                        assert len([row for row in rows if row["type"] == "approval.decided"]) == 2
                        assert not any(row["type"] == "deep.fallback" for row in rows)
                        response = await api.get(f"/api/v1/reports/runs/{run_id}")
                        assert response.status_code == 200
                        report = response.json()
                        selected = report["provider_usage"]["selected"]
                        assert (
                            selected["request_units"] == selected["known_units"] == 3
                            and selected["unknown_units"] == 0
                        )
                        assert (selected["input_tokens"], selected["output_tokens"]) == (16, 7)
                        assert Fraction(report["cost"]["currencies"][0]["amount"]) == Fraction("0.0000375")
                        assert (
                            report["physical_drain_verified"] is report["cost"]["billing_complete"] is False
                        )
                        assert PRIVATE not in json.dumps(
                            report
                        ) and "CHANGED_PRIVATE_MODEL" not in json.dumps(report)
                        assert len(journal) == 3 and all(
                            payload["model"] == PRIVATE for _, payload in journal
                        )
                        assert (
                            not again._provider_receipt_fault.broken and not again.mcp_manager.cleanup_failed
                        )
                await second_identity.wait_exited(timeout=WATCHDOG)
                assert (
                    again.cleanup_complete and not again._owner.held and again.mcp_manager._connections == {}
                )
            finally:
                for identity in identities:
                    identity.close()  # Test observation handles, not remote cleanup authority.

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_original_api_root_child_generations_retry_frozen_price_report_and_reopen(
    tmp_path, monkeypatch, mode
):
    async def scenario():
        journal, async_calls = [], []

        async def handler(request):
            payload = json.loads(request.content)
            journal.append(("async", payload))
            async_calls.append(request)
            first = len(async_calls) == 1
            return httpx.Response(
                503 if first else 200,
                request=request,
                headers={"Retry-After": "0.01"},
                json=body(2, 1) if first else body(),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            providers = offline_transports(monkeypatch, upstream, journal)
            app = create_app(tmp_path)
            service = app.state.run_service
            # Original disposable settings, no key input/protection/decryption.
            saved = await asyncio.to_thread(service.settings.save_profile, config())
            profile_id = saved["active_profile_id"]
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api:
                    run_id, record, result = await original_root(api, service, mode)
                    assert "fallback_runtime" not in result.metadata  # Deep must really execute SDK path.
                    frozen = deepcopy(record["profile_snapshot"]["billing_price_receipt"])
                    assert frozen["rates"]["input"] == "1.25"
                    # Change current settings before actual child admission: old snapshot
                    # remains parent authority, including model binding and both generations.
                    await asyncio.to_thread(
                        service.settings.save_profile,
                        config("999", "CHANGED_PRIVATE_MODEL"),
                        profile_id=profile_id,
                    )
                    child_id = await original_child(api, service, run_id)
                    assert len(journal) == 4 and len(async_calls) == (3 if mode == "legacy" else 4)
                    assert all(payload["model"] == PRIVATE for _, payload in journal)

                    rows = await service.list_events(run_id)
                    originals = []
                    for row in rows:
                        if row["type"] == "subagent.runtime":
                            outer = row["payload"]
                            assert outer["parent_run_id"] == run_id and outer["subagent_id"] == child_id
                            assert outer["generation"] in {1, 2}
                            kind, payload = outer["runtime_kind"], outer["runtime_payload"]
                        else:
                            kind, payload = row["type"], row["payload"]
                        if kind.startswith(("provider.call_", "provider.request_")):
                            originals.append((kind, payload))
                            assert payload["version"] == 3 and payload["price_receipt"] == frozen
                    assert len([v for k, v in originals if k == "provider.call_started"]) == 3
                    assert len([v for k, v in originals if k == "provider.request_started"]) == 4
                    assert [v["outcome"] for k, v in originals if k == "provider.request_finished"].count(
                        "failed"
                    ) == 1

                    def forbidden_settings(*_args, **_kwargs):
                        raise AssertionError("report read latest profile/key instead of frozen source")

                    for name in ("public", "profile", "api_key"):
                        monkeypatch.setattr(service.settings, name, forbidden_settings)
                    viewed = await api.get(f"/api/v1/reports/runs/{run_id}")
                    downloaded = await api.get(f"/api/v1/reports/runs/{run_id}/download")
                    assert viewed.status_code == downloaded.status_code == 200
                    report = viewed.json()
                    assert downloaded.json() == report
                    assert (
                        viewed.headers["cache-control"] == downloaded.headers["cache-control"] == "no-store"
                    )
                    assert (
                        downloaded.headers["content-disposition"]
                        == f'attachment; filename="doppel-report-{run_id}.json"'
                    )
                    selected = report["provider_usage"]["selected"]
                    assert (
                        report["version"] == 4 and selected["known_units"] == selected["request_units"] == 4
                    )
                    assert selected["logical_call_units"] == selected["unknown_units"] == 0
                    assert (selected["input_tokens"], selected["output_tokens"]) == (23, 10)
                    scopes = report["provider_usage"]["scopes"]
                    assert [(v["subagent_id"], v["generation"]) for v in scopes["children"]] == [
                        (child_id, 1),
                        (child_id, 2),
                    ]
                    assert scopes["children_total"] == 2
                    cost = report["cost"]
                    assert (
                        cost["price_snapshot"] == frozen
                        and cost["known_units"] == cost["selected_units"] == 4
                    )
                    assert cost["unknown_units"] == 0 and cost["currencies"][0]["currency"] == "CNY"
                    assert Fraction(cost["currencies"][0]["amount"]) == Fraction("0.00005375")
                    assert (
                        cost["billing_complete"]
                        is cost["source_verified"]
                        is cost["account_cap_guaranteed"]
                        is False
                    )
                    assert report["physical_drain_verified"] is False
                    assert PRIVATE not in json.dumps(report) and "CHANGED_PRIVATE_MODEL" not in json.dumps(
                        report
                    )
                    assert len(journal) == 4  # GET/download cannot enter provider or retry.
                    assert service._owner.held and not service.cleanup_complete
            assert service.cleanup_complete and not service._owner.held
            assert all(not hasattr(provider, "_sink") for provider in providers)
            assert not service._providers and not service._operations

            # New ORIGINAL lifespan/owner reads settled journals, no replay or model lookup.
            reopened = create_app(tmp_path)
            again = reopened.state.run_service
            async with reopened.router.lifespan_context(reopened):
                assert again._started and not again._provider_receipt_fault.broken
                restored = await again.run_report(run_id)
                for name in ("provider_usage", "cost", "source", "snapshot"):
                    assert restored[name] == report[name]
                assert len(journal) == 4 and not again._providers
            assert again.cleanup_complete and not again._owner.held

    asyncio.run(scenario())


def test_actual_deep_sdk_parse_failure_then_original_graph_fallback_keeps_both_receipts_and_one_frozen_tariff(
    tmp_path, monkeypatch
):
    async def scenario():
        journal = []

        async def handler(request):
            journal.append(("async", json.loads(request.content)))
            first = len(journal) == 1
            return httpx.Response(200, request=request, json=body(2, 1, malformed=True) if first else body())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            offline_transports(monkeypatch, upstream, journal)
            app = create_app(tmp_path)
            service = app.state.run_service
            await asyncio.to_thread(service.settings.save_profile, config())
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api:
                    run_id, record, result = await original_root(api, service, "deep", delegate=False)
                    assert result.metadata["fallback_runtime"] == "graph"
                    assert len(journal) == 2  # Actual provider parse failure, not patched _invoke.
                    rows = await service.list_events(run_id)
                    calls = [v["payload"] for v in rows if v["type"] == "provider.call_finished"]
                    assert [(v["engine"], v["outcome"]) for v in calls] == [
                        ("deep", "failed"),
                        ("graph", "returned"),
                    ]
                    assert len({v["call_id"] for v in calls}) == 2
                    assert all(
                        v["price_receipt"] == record["profile_snapshot"]["billing_price_receipt"]
                        for v in calls
                    )
                    report = (await api.get(f"/api/v1/reports/runs/{run_id}")).json()
                    selected = report["provider_usage"]["selected"]
                    assert (
                        selected["request_units"] == selected["known_units"] == 2
                        and selected["logical_call_units"] == 0
                    )
                    assert (selected["input_tokens"], selected["output_tokens"]) == (9, 4)
                    assert Fraction(report["cost"]["currencies"][0]["amount"]) == Fraction("0.00002125")
                    assert report["cost"]["billing_complete"] is False and PRIVATE not in json.dumps(report)
                    assert not service._provider_receipt_fault.broken
            assert service.cleanup_complete and not service._owner.held

    asyncio.run(scenario())


def test_original_legacy_late_transport_result_survives_cancel_and_owner_waits_for_actual_worker(
    tmp_path, monkeypatch
):
    async def scenario():
        entered, release, journal = Event(), Event(), []

        # Upstream client is never entered by this sync Legacy scenario; any
        # unexpected async fallback is an explicit fixture failure.
        async def forbidden_async(_request):
            raise AssertionError("cancelled Legacy entered another async provider")

        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden_async)) as upstream:
            offline_transports(monkeypatch, upstream, journal)

            def original_gated_open(request, *, timeout):
                journal.append(("sync", json.loads(request.data)))
                entered.set()
                assert release.wait(WATCHDOG)
                return Response(json.dumps(body()).encode())

            monkeypatch.setattr(provider_module, "urlopen", original_gated_open)
            app = create_app(tmp_path)
            service = app.state.run_service
            await asyncio.to_thread(service.settings.save_profile, config())
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api:
                    response = await api.post(
                        "/api/v1/runs",
                        json={"prompt": PRIVATE, "mode": "legacy", "deadline_seconds": WATCHDOG},
                    )
                    assert response.status_code == 202
                    run_id = response.json()["run_id"]
                    closing = None
                    try:
                        assert await asyncio.to_thread(entered.wait, WATCHDOG)
                        original_task = service.scheduler._running[run_id]
                        assert await service.cancel(run_id)
                        await asyncio.sleep(0)
                        # Repeated cancellation targets SAME original scheduler task.
                        assert (
                            service.scheduler._running[run_id] is original_task and not original_task.done()
                        )
                        original_task.cancel()
                        closing = asyncio.create_task(service.close())
                        await asyncio.sleep(0)
                        assert service._owner.held and not service.cleanup_complete
                        assert not closing.done() and len(journal) == 1
                    finally:
                        release.set()
                        if closing is not None:
                            await asyncio.wait_for(closing, WATCHDOG)
                    assert service.cleanup_complete and not service._owner.held
            reopened = create_app(tmp_path)
            again = reopened.state.run_service
            async with reopened.router.lifespan_context(reopened):
                report = await again.run_report(run_id)
                assert report["source"]["status"] == "cancelled"
                selected = report["provider_usage"]["selected"]
                assert selected["known_units"] == selected["request_units"] == 1
                assert (selected["input_tokens"], selected["output_tokens"]) == (7, 3)
                assert Fraction(report["cost"]["currencies"][0]["amount"]) == Fraction("0.00001625")
                rows = await again.list_events(run_id)
                finish = [r["payload"] for r in rows if r["type"] == "provider.call_finished"]
                assert len(finish) == 1 and finish[0]["outcome"] == "returned"
                assert len([r for r in rows if r["type"] == "run.cancelled"]) == 1
                assert len(journal) == 1 and not again._provider_receipt_fault.broken

    asyncio.run(scenario())


def test_original_mixed_runtime_dag_pause_reopen_resume_keeps_attempts_inputs_and_tariff(
    tmp_path, monkeypatch
):
    """D FIRST 2026-10-07, UNRUN: real orderly owner reopen, NOT crash/EXE proof."""

    async def scenario():
        entered, release, journal = Event(), Event(), []
        answers = [PRIVATE + suffix for suffix in ("_LEGACY_RESULT", "_GRAPH_RESULT", "_DEEP_RESULT")]

        async def handler(request):
            journal.append(("async", json.loads(request.content)))
            response = body()
            response["choices"][0]["message"]["content"] = answers[len(journal) - 1]
            return httpx.Response(200, request=request, json=response)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            offline_transports(monkeypatch, upstream, journal)

            def gated_open(request, *, timeout):
                journal.append(("sync", json.loads(request.data)))
                entered.set()
                assert release.wait(WATCHDOG)
                response = body()
                response["choices"][0]["message"]["content"] = answers[0]
                return Response(json.dumps(response).encode())

            monkeypatch.setattr(provider_module, "urlopen", gated_open)
            app = create_app(tmp_path)
            service = app.state.run_service
            saved = await asyncio.to_thread(service.settings.save_profile, config())
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api:
                    try:
                        identifier, activated = await original_order(api)
                        frozen = deepcopy(
                            activated["execution"]["profiles"][saved["active_profile_id"]][
                                "billing_price_receipt"
                            ]
                        )
                        assert await asyncio.to_thread(entered.wait, WATCHDOG)
                        paused = await api.post(
                            f"/api/v1/work-orders/{identifier}/control",
                            json={"action": "pause", "expected_revision": 1},
                        )
                        assert paused.status_code == 200 and paused.json()["status"] == "paused"
                        await asyncio.to_thread(
                            service.settings.save_profile,
                            config("999", "CHANGED_PRIVATE_MODEL"),
                            profile_id=saved["active_profile_id"],
                        )
                    finally:
                        release.set()  # Always release original sync worker BEFORE original close.
                    first = await original_order_until(
                        api,
                        identifier,
                        lambda r: r["status"] == "paused" and r["tasks"][0]["status"] == "succeeded",
                    )
                    assert len(first["attempts"]) == len(journal) == service.scheduler.accepted_count == 1
                    first_attempt = deepcopy(first["attempts"][0])
                    first_run, first_report = await original_attempt_report(
                        api, service, identifier, first_attempt, frozen, "paused"
                    )
                    assert first_run["answer"] == answers[0] and not first_run["lease_active"]
                    assert first_run["input_snapshot"] is None
                    assert all(task["status"] == "pending" for task in first["tasks"][1:])
            assert service.cleanup_complete and not service._owner.held

            reopened = create_app(tmp_path)
            again = reopened.state.run_service
            async with reopened.router.lifespan_context(reopened):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=reopened), base_url="http://127.0.0.1"
                ) as api:
                    before = await original_order_until(api, identifier, lambda r: r["status"] == "paused")
                    assert (
                        before["attempts"] == first["attempts"]
                        and before["execution"] == activated["execution"]
                    )
                    assert again.scheduler.accepted_count == 0 and len(journal) == 1 and not again._providers
                    restored = await again.run_report(first_attempt["run_id"])
                    for name in ("provider_usage", "cost", "source", "snapshot"):
                        assert restored[name] == first_report[name]
                    resumed = await api.post(
                        f"/api/v1/work-orders/{identifier}/control",
                        json={"action": "resume", "expected_revision": 1},
                    )
                    assert resumed.status_code == 200
                    completed = await original_order_until(
                        api, identifier, lambda r: r["status"] == "succeeded"
                    )
                    assert len(completed["attempts"]) == len(journal) == 3
                    assert again.scheduler.accepted_count == 2
                    assert all(task["status"] == "succeeded" for task in completed["tasks"])
                    assert completed["execution"] == activated["execution"]
                    by_task = {a["task_id"]: a for a in completed["attempts"]}
                    assert by_task["step0"]["attempt_id"] == first_attempt["attempt_id"]
                    runs, reports = [], []
                    for index in range(3):
                        attempt = by_task[f"step{index}"]
                        run, report = await original_attempt_report(
                            api, again, identifier, attempt, frozen, "succeeded"
                        )
                        assert attempt["attempt_number"] == 1 and attempt["status"] == "succeeded"
                        assert (
                            run["answer"] == answers[index]
                            and run["mode"] == ("legacy", "graph", "deep")[index]
                        )
                        assert not run["lease_active"] and not any(run["request"]["permissions"].values())
                        assert run["write_scope"] is (index == 1)  # Classification never grants writes.
                        assert "fallback_runtime" not in run["metadata"]
                        selected = report["provider_usage"]["selected"]
                        assert selected["request_units"] == selected["known_units"] == 1
                        assert selected["unknown_units"] == selected["logical_call_units"] == 0
                        assert (selected["input_tokens"], selected["output_tokens"]) == (7, 3)
                        runs.append(run)
                        reports.append(report)
                    for index in (1, 2):
                        snapshot = runs[index]["input_snapshot"]
                        assert snapshot["scope_work_order_id"] == identifier
                        (predecessor,) = snapshot["predecessors"]
                        assert predecessor["attempt_id"] == by_task[f"step{index - 1}"]["attempt_id"]
                        assert (
                            predecessor["run_id"] == runs[index - 1]["run_id"]
                            and predecessor["lease_drained"] is True
                        )
                        assert predecessor["text"] == answers[index - 1]
                        assert (
                            predecessor["result_sha256"]
                            == hashlib.sha256(answers[index - 1].encode()).hexdigest()
                        )
                        assert answers[index - 1] in json.dumps(journal[index][1]["messages"])
                    assert all(payload["model"] == PRIVATE for _, payload in journal)
                    assert sum(Fraction(r["cost"]["currencies"][0]["amount"]) for r in reports) == Fraction(
                        "0.00004875"
                    )
                    assert reports[0]["provider_usage"] == first_report["provider_usage"]
                    assert reports[0]["cost"] == first_report["cost"]  # New order status isn't new billing.
                    assert len(journal) == 3
            assert again.cleanup_complete and not again._owner.held

    asyncio.run(scenario())


def test_original_dag_failed_numeric_prefix_explicit_retry_uses_new_attempt_and_same_frozen_scope(
    tmp_path, monkeypatch
):
    """D FIRST 2026-10-07, UNRUN: no runtime/result/callback substitution."""

    async def scenario():
        journal = []

        async def handler(request):
            journal.append(("async", json.loads(request.content)))
            first = len(journal) == 1
            return httpx.Response(
                400 if first else 200, request=request, json=body(2, 1) if first else body()
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            offline_transports(monkeypatch, upstream, journal)
            app = create_app(tmp_path)
            service = app.state.run_service
            saved = await asyncio.to_thread(service.settings.save_profile, config())
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api:
                    identifier, activated = await original_order(api, ("graph", "deep"))
                    frozen = deepcopy(
                        activated["execution"]["profiles"][saved["active_profile_id"]][
                            "billing_price_receipt"
                        ]
                    )
                    stopped = await original_order_until(api, identifier, lambda r: r["status"] == "failed")
                    assert [task["status"] for task in stopped["tasks"]] == ["failed", "blocked"]
                    assert len(stopped["attempts"]) == len(journal) == service.scheduler.accepted_count == 1
                    failed = stopped["attempts"][0]
                    old_run, old_report = await original_attempt_report(
                        api, service, identifier, failed, frozen, "failed"
                    )
                    assert old_run["status"] == "failed" and not old_run["lease_active"]
                    assert Fraction(old_report["cost"]["currencies"][0]["amount"]) == Fraction("0.000005")
                    assert (
                        not service._provider_receipt_fault.broken
                    )  # Known upstream rejection isn't receipt failure.
                    await asyncio.to_thread(
                        service.settings.save_profile,
                        config("999", "CHANGED_PRIVATE_MODEL"),
                        profile_id=saved["active_profile_id"],
                    )
                    stale = await api.post(
                        f"/api/v1/work-orders/{identifier}/tasks/step0/retry",
                        json={"expected_revision": 1, "expected_attempt_id": "f" * 32},
                    )
                    assert stale.status_code == 409 and len(journal) == 1
                    retried = await api.post(
                        f"/api/v1/work-orders/{identifier}/tasks/step0/retry",
                        json={"expected_revision": 1, "expected_attempt_id": failed["attempt_id"]},
                    )
                    assert retried.status_code == 200 and retried.json()["status"] == "paused"
                    assert (
                        len(retried.json()["attempts"])
                        == service.scheduler.accepted_count
                        == len(journal)
                        == 1
                    )
                    resumed = await api.post(
                        f"/api/v1/work-orders/{identifier}/control",
                        json={"expected_revision": 1, "action": "resume"},
                    )
                    assert resumed.status_code == 200
                    settled = await original_order_until(
                        api, identifier, lambda r: r["status"] == "succeeded"
                    )
                    assert len(settled["attempts"]) == service.scheduler.accepted_count == len(journal) == 3
                    assert settled["execution"] == activated["execution"]
                    first_attempts = sorted(
                        (a for a in settled["attempts"] if a["task_id"] == "step0"),
                        key=lambda a: a["attempt_number"],
                    )
                    assert [a["attempt_number"] for a in first_attempts] == [1, 2]
                    assert [a["status"] for a in first_attempts] == ["failed", "succeeded"]
                    assert first_attempts[0]["attempt_id"] == failed["attempt_id"]
                    assert (
                        len({a["attempt_id"] for a in settled["attempts"]})
                        == len({a["run_id"] for a in settled["attempts"]})
                        == 3
                    )
                    reports, runs = [], {}
                    for attempt in settled["attempts"]:
                        run, report = await original_attempt_report(
                            api, service, identifier, attempt, frozen, "succeeded"
                        )
                        assert (
                            run["profile_snapshot"]
                            == activated["execution"]["profiles"][saved["active_profile_id"]]
                        )
                        selected = report["provider_usage"]["selected"]
                        assert (
                            selected["known_units"] == selected["request_units"] == 1
                            and selected["unknown_units"] == 0
                        )
                        assert (selected["input_tokens"], selected["output_tokens"]) == (
                            (2, 1) if attempt["attempt_id"] == failed["attempt_id"] else (7, 3)
                        )
                        runs[attempt["attempt_id"]] = run
                        reports.append(report)
                        if attempt["attempt_id"] == failed["attempt_id"]:
                            assert (
                                report["provider_usage"] == old_report["provider_usage"]
                                and report["cost"] == old_report["cost"]
                            )
                    successor = next(a for a in settled["attempts"] if a["task_id"] == "step1")
                    (predecessor,) = runs[successor["attempt_id"]]["input_snapshot"]["predecessors"]
                    assert predecessor["attempt_id"] == first_attempts[1]["attempt_id"]
                    assert predecessor["run_id"] == first_attempts[1]["run_id"]
                    assert (
                        runs[first_attempts[1]["attempt_id"]]["input_snapshot"] == old_run["input_snapshot"]
                    )
                    assert sum(Fraction(r["cost"]["currencies"][0]["amount"]) for r in reports) == Fraction(
                        "0.0000375"
                    )
                    assert all(payload["model"] == PRIVATE for _, payload in journal) and len(journal) == 3
            assert service.cleanup_complete and not service._owner.held

    asyncio.run(scenario())


def test_original_work_order_cancel_keeps_active_legacy_lease_until_late_receipt_and_never_dispatches_descendant(
    tmp_path, monkeypatch
):
    """D FIRST 2026-10-07, UNRUN: entered sync worker, not fake run status."""

    async def scenario():
        entered, release, journal = Event(), Event(), []

        async def forbidden_async(_request):
            raise AssertionError("cancelled DAG admitted descendant/fallback transport")

        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden_async)) as upstream:
            offline_transports(monkeypatch, upstream, journal)

            def gated_open(request, *, timeout):
                journal.append(("sync", json.loads(request.data)))
                entered.set()
                assert release.wait(WATCHDOG)
                return Response(json.dumps(body()).encode())

            monkeypatch.setattr(provider_module, "urlopen", gated_open)
            app = create_app(tmp_path)
            service = app.state.run_service
            saved = await asyncio.to_thread(service.settings.save_profile, config())
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api:
                    closing = None
                    try:
                        identifier, activated = await original_order(api, ("legacy", "graph"))
                        frozen = deepcopy(
                            activated["execution"]["profiles"][saved["active_profile_id"]][
                                "billing_price_receipt"
                            ]
                        )
                        assert await asyncio.to_thread(entered.wait, WATCHDOG)
                        accepted = await original_order_until(
                            api, identifier, lambda r: bool(r["attempts"] and r["attempts"][0]["run_id"])
                        )
                        run_id = accepted["attempts"][0]["run_id"]
                        original_task = service.scheduler._running[run_id]
                        cancelled = await api.post(
                            f"/api/v1/work-orders/{identifier}/control",
                            json={"expected_revision": 1, "action": "cancel"},
                        )
                        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
                        pending = await service.get(run_id)
                        assert pending["lease_active"] and len(cancelled.json()["attempts"]) == 1
                        assert cancelled.json()["tasks"][1]["status"] == "cancelled"
                        assert (
                            service.scheduler._running[run_id] is original_task and not original_task.done()
                        )
                        original_task.cancel()  # SAME original task, no second admission/cancel oracle.
                        closing = asyncio.create_task(service.close())
                        await asyncio.sleep(0)
                        assert not closing.done() and service._owner.held and len(journal) == 1
                    finally:
                        release.set()
                        if closing is not None:
                            await asyncio.wait_for(closing, WATCHDOG)
            assert service.cleanup_complete and not service._owner.held
            reopened = create_app(tmp_path)
            again = reopened.state.run_service
            async with reopened.router.lifespan_context(reopened):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=reopened), base_url="http://127.0.0.1"
                ) as api:
                    settled = await original_order_until(
                        api, identifier, lambda r: r["status"] == "cancelled"
                    )
                    assert len(settled["attempts"]) == 1 and settled["attempts"][0]["status"] == "cancelled"
                    run, report = await original_attempt_report(
                        api, again, identifier, settled["attempts"][0], frozen, "cancelled"
                    )
                    assert run["status"] == "cancelled" and not run["lease_active"]
                    assert report["provider_usage"]["selected"]["known_units"] == 1
                    assert (
                        report["provider_usage"]["selected"]["input_tokens"],
                        report["provider_usage"]["selected"]["output_tokens"],
                    ) == (7, 3)
                    assert Fraction(report["cost"]["currencies"][0]["amount"]) == Fraction("0.00001625")
                    finishes = [
                        r["payload"]
                        for r in await again.list_events(run_id)
                        if r["type"] == "provider.call_finished"
                    ]
                    assert len(finishes) == 1 and finishes[0]["outcome"] == "returned"
                    assert again.scheduler.accepted_count == 0 and not again._providers and len(journal) == 1
            assert again.cleanup_complete and not again._owner.held

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_original_dag_checkpoint_reopen_sdk_approval_resume_preserves_one_attempt_and_both_calls(
    tmp_path, monkeypatch, mode, decision
):
    """D FIRST 2026-10-07, UNRUN: actual Graph/Deep SDK, patch gateway and ledger.

    Orderly original owner close/reopen is NOT a measured process crash or native
    EXE proof. Workspace write occurs ONLY in this disposable fixture at S9.
    """

    async def scenario():
        journal = []
        tool_call = PRIVATE + "_PATCH_CALL"
        target = tmp_path / "approved-fixture.txt"

        async def handler(request):
            journal.append(("async", json.loads(request.content)))
            if len(journal) == 1:
                response = body(2, 1)
                response["choices"][0]["message"] = {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": tool_call,
                            "type": "function",
                            "function": {
                                "name": "propose_patch",
                                "arguments": json.dumps(
                                    {"changes": [{"path": target.name, "content": PRIVATE}]}
                                ),
                            },
                        }
                    ],
                }
            else:
                assert len(journal) == 2  # Resume executes pending tool, NEVER re-enters old model turn.
                response = body()
            return httpx.Response(200, request=request, json=response)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            offline_transports(monkeypatch, upstream, journal)
            app = create_app(tmp_path)
            service = app.state.run_service
            saved = await asyncio.to_thread(service.settings.save_profile, config())
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api:
                    # This fixture actually requests a writer task. Global user
                    # permission alone must not escalate the original read task.
                    identifier, activated = await original_order(
                        api, (mode,), permissions={"workspace_write": True}, write_first=True
                    )
                    frozen = deepcopy(
                        activated["execution"]["profiles"][saved["active_profile_id"]][
                            "billing_price_receipt"
                        ]
                    )
                    pending = await original_order_until(
                        api, identifier, lambda r: r["tasks"][0]["status"] == "awaiting_approval"
                    )
                    (attempt,) = pending["attempts"]
                    run_id = attempt["run_id"]
                    result = await asyncio.wait_for(service.scheduler.wait(run_id), WATCHDOG)
                    assert result.status == "interrupted" and "fallback_runtime" not in result.metadata
                    run, pending_report = await original_attempt_report(
                        api, service, identifier, attempt, frozen, "running"
                    )
                    assert run["status"] == "interrupted" and run["lease_active"]
                    interrupt_id = run["metadata"]["interrupts"][0]["id"]
                    assert interrupt_id and not target.exists() and len(journal) == 1
                    assert Fraction(pending_report["cost"]["currencies"][0]["amount"]) == Fraction("0.000005")
                    await asyncio.to_thread(
                        service.settings.save_profile,
                        config("999", "CHANGED_PRIVATE_MODEL"),
                        profile_id=saved["active_profile_id"],
                    )
            assert service.cleanup_complete and not service._owner.held
            assert not target.exists()  # Close does not silently approve or execute pending effect.

            reopened = create_app(tmp_path)
            again = reopened.state.run_service
            async with reopened.router.lifespan_context(reopened):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=reopened), base_url="http://127.0.0.1"
                ) as api:
                    restored = await again.get(run_id)
                    assert restored["thread_id"] == run["thread_id"] and restored["status"] == "interrupted"
                    assert (
                        restored["profile_snapshot"] == run["profile_snapshot"] and restored["lease_active"]
                    )
                    assert again.scheduler.accepted_count == 0 and not again._providers and len(journal) == 1
                    response = await api.post(
                        f"/api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume", json={"action": decision}
                    )
                    assert response.status_code == 202 and response.json()["run_id"] == run_id
                    duplicate = await api.post(
                        f"/api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume", json={"action": decision}
                    )
                    assert duplicate.status_code == 409
                    resumed = await asyncio.wait_for(again.scheduler.wait(run_id), WATCHDOG)
                    assert resumed.status == "completed" and "fallback_runtime" not in resumed.metadata
                    settled = await original_order_until(
                        api, identifier, lambda r: r["status"] == "succeeded"
                    )
                    (current,) = settled["attempts"]
                    assert current["attempt_id"] == attempt["attempt_id"] and current["run_id"] == run_id
                    assert current["attempt_number"] == 1 and current["status"] == "succeeded"
                    assert again.scheduler.accepted_count == 1 and len(journal) == 2
                    final, report = await original_attempt_report(
                        api, again, identifier, current, frozen, "succeeded"
                    )
                    assert not final["lease_active"] and final["profile_snapshot"] == run["profile_snapshot"]
                    selected = report["provider_usage"]["selected"]
                    assert (
                        selected["known_units"] == selected["request_units"] == 2
                        and selected["unknown_units"] == 0
                    )
                    assert (selected["input_tokens"], selected["output_tokens"]) == (9, 4)
                    assert Fraction(report["cost"]["currencies"][0]["amount"]) == Fraction("0.00002125")
                    patches = report["evidence"]["patches"]
                    assert patches["state"] == "known" and patches["total"] == (
                        1 if decision == "approve" else 0
                    )
                    if decision == "approve":
                        assert target.read_bytes() == PRIVATE.encode()
                        (patch,) = patches["items"]
                        assert patch["confirmed_applied"] is True and patch["files"] == 1
                        assert patch["tool_call_sha256"] == report_digest("tool_call", tool_call)
                    else:
                        assert not target.exists() and patches["items"] == []
                    rows = await again.list_events(run_id)
                    calls = [r["payload"] for r in rows if r["type"] == "provider.call_started"]
                    assert len(calls) == 2 and len({r["call_id"] for r in calls}) == 2
                    assert all(r["price_receipt"] == frozen for r in calls)
                    assert len([r for r in rows if r["type"] == "approval.decided"]) == 1
                    assert all(payload["model"] == PRIVATE for _, payload in journal) and len(journal) == 2
            assert again.cleanup_complete and not again._owner.held

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("after_effect", [False, True])
@pytest.mark.parametrize("cleanup_resource", ["connection", "cursor_after_response"])
def test_original_sdk_checkpoint_failed_close_fences_fallback_and_keeps_same_service_owner(
    tmp_path, monkeypatch, mode, after_effect, cleanup_resource
):
    """FIRST 2026-10-07, UNRUN; actual SDK + original close, no fake result."""
    from doppel_agent.persistence import checkpoints

    async def scenario():
        journal, sources, close_calls = [], [], []

        async def handler(request):
            journal.append(("async", json.loads(request.content)))
            return httpx.Response(200, request=request, json=body())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            offline_transports(monkeypatch, upstream, journal)
            original_connection_close = checkpoints.aiosqlite.Connection.close
            target_class = (
                checkpoints.aiosqlite.Connection
                if cleanup_resource == "connection"
                else checkpoints.aiosqlite.Cursor
            )
            original_close = target_class.close

            def retained(source):
                sources.append(source)

            async def close(resource):
                if cleanup_resource == "cursor_after_response" and (not journal or close_calls):
                    return await original_close(resource)
                close_calls.append(resource)
                if after_effect:
                    await original_close(resource)
                raise OSError("PRIVATE_ORIGINAL_CHECKPOINT_CLOSE")

            monkeypatch.setattr(target_class, "close", close)
            service = create_app(tmp_path).state.run_service
            await asyncio.to_thread(service.settings.save_profile, config())
            await service.start()
            original_retain = service._provider_receipt_fault.retain_cleanup

            def root_retain(source):
                retained(source)
                original_retain(source)

            monkeypatch.setattr(service._provider_receipt_fault, "retain_cleanup", root_retain)
            try:
                record, _ = await service.create(
                    {"prompt": PRIVATE, "mode": mode, "permissions": {}, "deadline_seconds": WATCHDOG}
                )
                with pytest.raises((checkpoints.CheckpointCleanupError, ProviderReceiptError)):
                    await asyncio.wait_for(service.scheduler.wait(record["run_id"]), WATCHDOG)
                assert len(sources) == len(close_calls) == len(journal) == 1
                source = sources[0]
                assert source.proxy is source.connection
                if cleanup_resource == "connection":
                    assert source.proxy is close_calls[0]
                else:
                    failed = [frame for frame in source.sdk_cursors if frame["cursor"] is close_calls[0]]
                    assert (
                        len(failed) == 1 and failed[0]["close_attempted"] and not failed[0]["close_returned"]
                    )
                    assert failed[0]["native_cursor"] is close_calls[0]._cursor and source.close_returned
                assert source.native_connection is not None and source.closing.done()
                assert source.cleanup_uncertain and service._provider_receipt_fault.broken
                assert not any(
                    row["type"] == "deep.fallback" for row in await service.list_events(record["run_id"])
                )
                report = await service.run_report(record["run_id"])
                assert (
                    report["source"]["status"] == "failed"
                    and report["service"]["execution_admission"] == "quarantined"
                )
                assert report["provider_usage"]["selected"]["known_units"] == 1
                assert Fraction(report["cost"]["currencies"][0]["amount"]) == Fraction("0.00001625")
                assert PRIVATE not in json.dumps(report) and len(journal) == 1
                with pytest.raises(ProviderReceiptError):
                    await service.create({"prompt": PRIVATE, "mode": mode, "permissions": {}})
                for _ in range(2):
                    with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
                        await service.close()
                assert service._owner.held and not service.cleanup_complete
                assert service._close_task.done() and len(close_calls) == 1 and len(journal) == 1
            finally:
                # Fixture ONLY, after actual original close task joined SDK work.
                for source in sources:
                    if source.proxy._connection is not None:
                        await original_connection_close(source.proxy)
                service._owner.release()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["append", "notification"])
def test_actual_deep_finish_publication_fault_fences_fallback_children_and_audits_new_owner(
    tmp_path, monkeypatch, phase
):
    async def scenario():
        journal = []

        async def handler(request):
            journal.append(("async", json.loads(request.content)))
            return httpx.Response(200, request=request, json=body())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            offline_transports(monkeypatch, upstream, journal)
            app = create_app(tmp_path)
            service = app.state.run_service
            await asyncio.to_thread(service.settings.save_profile, config())
            original_append = service.events.append
            original_notify = service.notifier.notify
            finish_appended = False

            def fail_finish(run_id, thread_id, kind, payload):
                nonlocal finish_appended
                if kind == "provider.call_finished" and phase == "append":
                    raise OSError("PRIVATE_D_ORIGINAL_FINISH_APPEND_FAILURE")
                result = original_append(run_id, thread_id, kind, payload)
                if kind == "provider.call_finished":
                    finish_appended = True
                return result

            async def fail_notification(run_id):
                nonlocal finish_appended
                if phase == "notification" and finish_appended:
                    finish_appended = False  # Fail only SAME original finish notification.
                    raise OSError("PRIVATE_D_ORIGINAL_FINISH_NOTIFICATION_FAILURE")
                await original_notify(run_id)

            monkeypatch.setattr(service.events, "append", fail_finish)
            monkeypatch.setattr(service.notifier, "notify", fail_notification)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api:
                    response = await api.post(
                        "/api/v1/runs",
                        json={
                            "prompt": PRIVATE,
                            "mode": "deep",
                            "permissions": {"delegate": True},
                            "deadline_seconds": WATCHDOG,
                        },
                    )
                    assert response.status_code == 202
                    run_id = response.json()["run_id"]
                    with pytest.raises(ProviderReceiptError):
                        await asyncio.wait_for(service.scheduler.wait(run_id), WATCHDOG)
                    rows = await service.list_events(run_id)
                    assert len(journal) == 1 and service._provider_receipt_fault.broken
                    assert not any(r["type"] == "deep.fallback" for r in rows)
                    assert len([r for r in rows if r["type"] == "provider.call_finished"]) == (
                        0 if phase == "append" else 1
                    )
                    assert (await service.get(run_id))["status"] == "failed"
                    child = await api.post(f"/api/v1/runs/{run_id}/subagents", json={"prompt": PRIVATE})
                    fresh = await api.post("/api/v1/runs", json={"prompt": PRIVATE, "mode": "graph"})
                    assert child.status_code == fresh.status_code == 503
                    report = (await api.get(f"/api/v1/reports/runs/{run_id}")).json()
                    assert report["provider_usage"]["counts"]["calls_unsettled"] == (
                        1 if phase == "append" else 0
                    )
                    if phase == "append":
                        assert report["provider_usage"]["state"] in {"unknown", "partial"}
                    assert report["cost"]["billing_complete"] is False
                    assert report["service"]["execution_admission"] == "quarantined"
                    assert PRIVATE not in json.dumps(report) and len(journal) == 1
            assert service.cleanup_complete and not service._owner.held

            # Persisted unpaired append failure stays quarantined. A known closed
            # journal after notification failure is NOT rewritten as unpaired;
            # original fault remains sticky only in its original owner lifetime.
            reopened = create_app(tmp_path)
            again = reopened.state.run_service
            async with reopened.router.lifespan_context(reopened):
                assert again._metadata_ready
                assert again._started is (phase == "notification")
                assert again._provider_receipt_fault.broken is (phase == "append")
                if phase == "append":
                    assert not again.scheduler._workers and not again.subagents.scheduler._workers
                restored = await again.run_report(run_id)
                assert restored["provider_usage"]["counts"]["calls_unsettled"] == (
                    1 if phase == "append" else 0
                )
                if phase == "append":
                    with pytest.raises(ProviderReceiptError):
                        await again.create({"prompt": PRIVATE, "mode": "graph", "permissions": {}})
                assert len(journal) == 1 and not again._providers
            assert again.cleanup_complete and not again._owner.held

    asyncio.run(scenario())
