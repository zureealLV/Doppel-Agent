"""Real local MCP stdio transport, native service approvals and gateway evidence."""

import asyncio
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from bench.runtime_fixtures import materialize_task_case
from bench.runtime_contract import NativeTaskContract, native_approval_decisions
from bench.runtime_process_evidence import ProcessIdentity
from bench.runtime_tdd_harness import workspace_snapshot
from doppel_agent.mcp.executor import MCPToolExecutor
from doppel_agent.permissions import PermissionManager
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.service import RunService


def read_trace(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def remote_calls(path):
    return [row for row in read_trace(path) if row["kind"] == "call"]


class ScriptedMCPProvider:
    def __init__(self, plan, workspace):
        # Only call names/arguments, never hidden expected outputs, are passed
        # to this explicitly trusted control provider.
        self.plan, self.workspace, self.turn = plan, workspace, 0
        self.receipts, self.pending, self.seen, self.tool_surface = [], {}, set(), []

    def next_turn(self, messages, tools):
        names = {tool["function"]["name"] for tool in tools}
        self.tool_surface = sorted(name for name in names if name.startswith("mcp__"))
        if any(name in names for name in ("run_command", "execute", "propose_patch", "write_file", "edit_file")):
            raise ValueError("MCP fixture received unrelated write/command permission")
        for message in messages:
            if message.role != "tool" or message.tool_call_id not in self.pending or message.tool_call_id in self.seen:
                continue
            self.seen.add(message.tool_call_id)
            call = self.pending[message.tool_call_id]
            self.receipts.append({"tool_call_id": call.id, "tool": call.name, "response": message.content,
                                  "response_sha256": sha256(message.content.encode()).hexdigest(),
                                  "snapshot": workspace_snapshot(self.workspace)})
        if self.turn < len(self.plan):
            row = self.plan[self.turn]
            self.turn += 1
            name = "mcp__matrix__" + row["name"]
            if name not in names:
                raise ValueError("native MCP catalog did not expose the planned tool")
            call = ToolCall(f"mcp-control-{self.turn}", name, row["arguments"])
            self.pending[call.id] = call
            return ModelTurn(tool_calls=(call,))
        return ModelTurn(content="Local MCP control complete; no model-quality verdict.")


def validate_mcp_evidence(fixture, workspace, evidence):
    if fixture.category != "mcp_workflow":
        raise ValueError("MCP evidence requires its frozen fixture")
    expected = json.loads(dict(fixture.hidden_files)["expected.json"])
    base = {p: sha256(payload).hexdigest() for p, payload in fixture.public_files}
    initial = evidence.get("initial", {})
    results, receipts, approvals = (evidence.get(key, []) for key in ("gateway_results", "receipts", "approvals"))
    deny = evidence.get("policy_denial", {})
    process = evidence.get("server_process", {})
    checks = {
        "native_identity_and_exact_grant": evidence.get("case_id") == fixture.case_id and evidence.get("fixture_sha256") == fixture.sha256
        and evidence.get("fixture_version") == fixture.version and evidence.get("permissions") == {"mcp_execute": True}
        and evidence.get("runtime") in {"graph", "deep"} and evidence.get("actual_runtime") == evidence.get("runtime")
        and evidence.get("boundary") == "run_service" and evidence.get("status") == "completed" and not evidence.get("fallback_runtime"),
        "public_and_transport_inventory_unchanged": set(initial) == set(base) | {".doppel/mcp.json"}
        and all(initial.get(p) == digest for p, digest in base.items()) and evidence.get("current") == initial == workspace_snapshot(workspace),
        "no_ungranted_tools_or_remote_effects": evidence.get("ungranted_surface") == [] and deny.get("error") == "PermissionError"
        and deny.get("remote_calls_before") == deny.get("remote_calls_after") == 0,
        "real_catalog_discovery": evidence.get("catalog_count", 0) >= 1
        and evidence.get("tool_surface") == ["mcp__matrix__echo", "mcp__matrix__failure", "mcp__matrix__structured"],
        "native_approval_before_each_remote_call": len(approvals) == len(expected) and all(
            row.get("tool") == "mcp__matrix__" + control["name"] and row.get("arguments") == control["arguments"]
            and row.get("prior_remote_calls") == index and row.get("snapshot") == initial and not row.get("fallback_runtime")
            for index, (row, control) in enumerate(zip(approvals, expected, strict=True))),
        "remote_calls_exactly_once": evidence.get("remote_calls") == [{"kind": "call", "name": row["name"], "arguments": row["arguments"]} for row in expected],
        "actual_gateway_results": len(results) == len(expected) and all(
            row.get("logical_name") == "mcp__matrix__" + control["name"] and row.get("arguments") == control["arguments"]
            and row.get("success") is (not control["is_error"]) and row.get("model_view") == control["model_view"]
            and row.get("application_view") == {key: control[key] for key in ("content", "structured_content", "is_error")}
            and row.get("replayed") is False and isinstance(row.get("audit_id"), str) and bool(row["audit_id"])
            for row, control in zip(results, expected, strict=True)),
        "model_receipts_preserve_error_and_structured_fallback": len(receipts) == len(expected) and all(
            row.get("tool") == "mcp__matrix__" + control["name"]
            and row.get("response") == ("MCP tool error: " if control["is_error"] else "") + control["model_view"]
            and row.get("response_sha256") == sha256(row.get("response", "").encode()).hexdigest()
            and row.get("snapshot") == initial for row, control in zip(receipts, expected, strict=True)),
        "owned_server_identity_exited": type(process.get("pid")) is int and process["pid"] > 0
        and process.get("identity_backend") in {"windows-process-handle", "linux-pidfd"}
        and process.get("bound_before_shutdown") is True and process.get("exited") is True
        and evidence.get("manager_connections_after_close") == 0,
    }
    events = evidence.get("gateway_audits", [])
    checks["durable_audit_matches_actual_gateway"] = len(events) == len(results) and all(
        event.get("audit_id") == result.get("audit_id") and event.get("logical_name") == result.get("logical_name")
        and event.get("arguments") == result.get("arguments") and event.get("success") is result.get("success")
        for event, result in zip(events, results, strict=True))
    return checks


async def probe_mcp(fixture, mode, workspace, *, _contract=None):
    if fixture.category != "mcp_workflow" or mode not in {"graph", "deep"}:
        raise ValueError("unsupported native MCP probe")
    contract = _contract if _contract is not None else NativeTaskContract.load()
    case = contract.case(fixture, mode)
    materialize_task_case(fixture, workspace)
    expected = json.loads(dict(fixture.hidden_files)["expected.json"])
    plan = [{"name": row["name"], "arguments": row["arguments"]} for row in expected]
    with tempfile.TemporaryDirectory(prefix="doppel-mcp-external-trace-") as temp:
        trace = Path(temp) / "server.jsonl"
        config = workspace / ".doppel/mcp.json"
        config.parent.mkdir()
        config.write_bytes((json.dumps({"servers": {"matrix": {"transport": "stdio", "command": sys.executable,
                                                               "args": ["-B", "server.py", str(trace)], "cwd": str(workspace.resolve()),
                                                               "max_concurrency": 1}}}, indent=2) + "\n").encode())
        initial = workspace_snapshot(workspace)
        provider = ScriptedMCPProvider(plan, workspace)
        service, identity = RunService(workspace, provider=provider), None
        await service.start()
        try:
            ungranted = await service._runtime({"mode": mode, "request": {"permissions": {}, "effort": "balanced"}})
            surface = list(ungranted.tools.tools) if mode == "graph" else [tool.name for tool in ungranted.additional_tools]
            ungranted_surface = sorted(name for name in surface if name.startswith("mcp__"))
            if mode == "deep":
                await ungranted.process_supervisor.close()
            async with asyncio.timeout(15):
                await service.mcp_catalog.list_all()
            starts = [row for row in read_trace(trace) if row["kind"] == "start"]
            if len(starts) != 1:
                raise ValueError("expected one isolated server process")
            identity = ProcessIdentity(starts[0]["pid"])
            if identity.exited():
                raise ValueError("MCP identity was not alive before shutdown")
            denied_error, before = None, len(remote_calls(trace))
            denied = MCPToolExecutor(service.mcp_manager, service.mcp_catalog, PermissionManager(frozenset()))
            try:
                await denied.execute("mcp__matrix__" + plan[0]["name"], plan[0]["arguments"], run_id="external-policy-control", tool_call_id="denied")
            except PermissionError as exc:
                denied_error = type(exc).__name__
            policy_denial = {"error": denied_error, "remote_calls_before": before, "remote_calls_after": len(remote_calls(trace))}
            results = []
            original_execute = MCPToolExecutor.execute

            async def execute(executor, logical_name, arguments, **kwargs):
                result = await original_execute(executor, logical_name, arguments, **kwargs)
                if executor.manager is service.mcp_manager:
                    results.append({"logical_name": result.logical_name, "arguments": arguments,
                                    "success": result.success, "application_view": result.application_view,
                                    "model_view": result.model_view, "audit_id": result.audit_id, "replayed": result.replayed})
                return result

            approvals = []
            with patch.object(MCPToolExecutor, "execute", new=execute):
                record, _ = await service.create({"mode": mode, "prompt": case.prompt, "effort": "deep",
                                                  "permissions": dict(case.permissions), "deadline_seconds": 60})
                run_id = record["run_id"]
                for planned in plan:
                    await service.scheduler.wait(run_id)
                    paused = await service.get(run_id)
                    if paused["status"] != "interrupted" or len(paused["metadata"].get("interrupts", [])) != 1:
                        raise ValueError("MCP call did not pause at its native policy boundary")
                    interrupt = paused["metadata"]["interrupts"][0]
                    actions = interrupt["value"]["action_requests" if mode == "deep" else "tool_calls"]
                    if len(actions) != 1:
                        raise ValueError("unexpected MCP approval inventory")
                    action = actions[0]
                    approvals.append({"tool": action["name"], "arguments": action["args" if mode == "deep" else "arguments"],
                                      "prior_remote_calls": len(remote_calls(trace)), "snapshot": workspace_snapshot(workspace),
                                      "fallback_runtime": paused["metadata"].get("fallback_runtime")})
                    if action["name"] != "mcp__matrix__" + planned["name"]:
                        raise ValueError("native MCP approval action drifted from the frozen control")
                    await service.resume(run_id, interrupt["id"], {"action": "approve"})
                await service.scheduler.wait(run_id)
                completed = await service.get(run_id)
            events = await service.list_events(run_id)
            await service.close()
            process = await identity.wait_exited()
            journal = read_trace(trace)
            evidence = {"case_id": fixture.case_id, "fixture_version": fixture.version, "fixture_sha256": fixture.sha256,
                        "runtime": mode, "actual_runtime": completed["metadata"].get("fallback_runtime") or mode,
                        "fallback_runtime": completed["metadata"].get("fallback_runtime"), "status": completed["status"],
                        "boundary": "run_service", "permissions": dict(case.permissions), "initial": initial,
                        "current": workspace_snapshot(workspace), "ungranted_surface": ungranted_surface,
                        "policy_denial": policy_denial, "tool_surface": provider.tool_surface, "approvals": approvals,
                        "catalog_count": sum(row["kind"] == "catalog" for row in journal), "remote_calls": remote_calls(trace),
                        "gateway_results": results, "receipts": provider.receipts,
                        "approval_decisions": native_approval_decisions(events), "approval_count": len(approvals),
                        "gateway_audits": [row["payload"] for row in events if row["type"] == "mcp.tool_executed"],
                        "server_process": process, "manager_connections_after_close": len(service.mcp_manager._connections),
                        "human_review": "pending", "task_quality_scored": False,
                        "scope_note": "Trusted scripted real local stdio controls through native policy gateway; external expected outputs/trace and OS identity handles. Not external-server or paid/model quality."}
            checks = validate_mcp_evidence(fixture, workspace, evidence)
            return {**evidence, "checks": checks, "deterministic_pass": all(checks.values())}
        finally:
            try:
                await service.close()
            finally:
                if identity is not None:
                    identity.close()
