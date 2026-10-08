"""Native service approval/rejection controls with external side-effect traces."""

from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from bench.runtime_fixtures import APPROVAL_COMMAND_ARGV, materialize_task_case
from bench.runtime_contract import NativeTaskContract, native_approval_decisions
from bench.runtime_tdd_harness import workspace_snapshot
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.service import RunService
from doppel_agent.workspace.patching import PatchService
from doppel_agent.workspace.process_supervisor import ProcessSupervisor


def command_argv():
    return [sys.executable if item == "{python}" else item for item in APPROVAL_COMMAND_ARGV]


class ApprovalSideEffectTrace:
    """Observe actual native operations; no replacement runtime/tool injected."""

    def __init__(self, workspace):
        self.workspace = workspace.resolve(strict=True)
        self.trace = {"patch_apply_count": 0, "applied_patches": [], "command_calls": []}
        self.stack = ExitStack()

    def __enter__(self):
        original_apply, original_run = PatchService.apply, ProcessSupervisor.run

        def apply(service, proposal, *, record_intent=None):
            if service.workspace.root != self.workspace:
                return original_apply(service, proposal, record_intent=record_intent)
            self.trace["patch_apply_count"] += 1
            result = original_apply(service, proposal, record_intent=record_intent)
            self.trace["applied_patches"].append({"patch_id": result.patch_id,
                                                  "snapshot": workspace_snapshot(self.workspace)})
            return result

        async def run(supervisor, argv, **kwargs):
            if Path(kwargs["cwd"]).resolve() != self.workspace:
                return await original_run(supervisor, argv, **kwargs)
            canonical = ["{python}" if index == 0 and item == sys.executable else item for index, item in enumerate(argv)]
            row = {"argv": canonical, "exit_code": None}
            self.trace["command_calls"].append(row)
            result = await original_run(supervisor, argv, **kwargs)
            row.update(exit_code=result.exit_code, snapshot=workspace_snapshot(self.workspace))
            return result

        self.stack.enter_context(patch.object(PatchService, "apply", new=apply))
        self.stack.enter_context(patch.object(ProcessSupervisor, "run", new=run))
        return self

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


class ScriptedApprovalProvider:
    def __init__(self, fixture, mode, workspace):
        self.command = fixture.case_id == "approval-02"
        self.primary = "command.py" if self.command else "note.txt"
        self.base = dict(fixture.public_files)
        self.changes = None if self.command else json.loads(dict(fixture.hidden_files)["reference_changes.json"])
        self.mode, self.workspace, self.turn = mode, workspace, 0
        self.receipts, self.pending, self.seen = [], {}, set()

    def next_turn(self, messages, tools):
        names = {tool["function"]["name"] for tool in tools}
        expected = "run_command" if self.command else "propose_patch"
        if not {"read_file", expected} <= names or "execute" in names or any(n.startswith("mcp__") for n in names):
            raise ValueError("approval fixture requires its bounded native tool surface")
        if ("propose_patch" in names) == self.command or (not self.command and "run_command" in names):
            raise ValueError("approval fixture received an unrelated side-effect grant")
        for message in messages:
            if message.role != "tool" or message.tool_call_id not in self.pending or message.tool_call_id in self.seen:
                continue
            self.seen.add(message.tool_call_id)
            call = self.pending[message.tool_call_id]
            row = {"tool": call.name, "tool_call_id": call.id,
                   "response_sha256": sha256(message.content.encode()).hexdigest(), "snapshot": workspace_snapshot(self.workspace)}
            if call.name == "read_file":
                row.update(path=self.primary, source_lines_present=all(
                    line in message.content for line in self.base[self.primary].decode().splitlines() if line),
                    success=not message.content.startswith(("Error:", "Tool error")))
            self.receipts.append(row)
        self.turn += 1
        if self.turn == 1:
            arguments = {"file_path" if self.mode == "deep" else "path": self.primary}
            if self.mode == "deep":
                arguments.update(offset=0, limit=100)
            call = ToolCall("approval-read", "read_file", arguments)
        elif self.turn == 2:
            arguments = {"argv": command_argv()} if self.command else {"changes": [
                {"path": path, "content": content} for path, content in self.changes.items()]}
            call = ToolCall("approval-effect", expected, arguments)
        else:
            return ModelTurn(content="Scripted native approval control complete; human verdict pending.")
        self.pending[call.id] = call
        return ModelTurn(tool_calls=(call,))


def validate_approval_evidence(fixture, workspace, evidence, *, _positive_command=False):
    command = fixture.case_id == "approval-02"
    if fixture.category != "approval_resume" or (_positive_command and not command):
        raise ValueError("unsupported approval validation profile")
    base = {p: sha256(payload).hexdigest() for p, payload in fixture.public_files}
    current, trace, approval = evidence.get("current", {}), evidence.get("native_trace", {}), evidence.get("approval", {})
    expected = dict(base)
    if command and _positive_command:
        outcome = json.loads(dict(fixture.hidden_files)["expected_outcome.json"])
        expected[outcome["path"]] = sha256(outcome["content"].encode()).hexdigest()
    elif not command:
        expected.update({p: sha256(content.encode()).hexdigest()
                         for p, content in json.loads(dict(fixture.hidden_files)["reference_changes.json"]).items()})
    rows = evidence.get("receipts", [])
    duplicates = evidence.get("duplicate_resumes", [])
    wrong = evidence.get("wrong_interrupt", {})
    checks = {
        "frozen_native_identity": evidence.get("case_id") == fixture.case_id and evidence.get("fixture_sha256") == fixture.sha256
        and evidence.get("fixture_version") == fixture.version and evidence.get("boundary") == "run_service"
        and evidence.get("actual_runtime") == evidence.get("runtime") and evidence.get("runtime") in ({"graph"} if command else {"graph", "deep"})
        and not evidence.get("paused_fallback_runtime") and evidence.get("status") == "completed",
        "exact_permissions_and_decision": evidence.get("permissions") == ({"command_execute": True} if command else {"workspace_write": True})
        and evidence.get("decision") == ("approve" if _positive_command else fixture.oracle["decision"]),
        "no_unapproved_or_reconstruction_effects": evidence.get("initial") == evidence.get("paused_snapshot") == evidence.get("reconstructed_snapshot") == base,
        "wrong_interrupt_preserves_pending_state": wrong.get("error") == "ValueError" and wrong.get("status") == "interrupted" and wrong.get("snapshot") == base,
        "one_durable_decision": type(evidence.get("approval_decision_count")) is int and evidence["approval_decision_count"] == 1,
        "duplicate_resume_rejected_without_effects": len(duplicates) == 2 and all(
            row.get("error") == "ValueError" and row.get("snapshot") == current and row.get("status") == "completed"
            for row in duplicates),
        "exact_side_effect_and_protected_inventory": current == expected,
        "executor_receipts": [row.get("tool") for row in rows] == ["read_file", "run_command" if command else "propose_patch"]
        and len({row.get("tool_call_id") for row in rows}) == 2 and all(
            isinstance(row.get("response_sha256"), str) and len(row["response_sha256"]) == 64 for row in rows)
        and rows[0].get("path") == ("command.py" if command else "note.txt") and rows[0].get("source_lines_present") is True
        and rows[0].get("success") is True and rows[0].get("snapshot") == base and rows[-1].get("snapshot") == current,
    }
    if workspace is not None:
        checks["snapshot_matches_disk"] = current == workspace_snapshot(workspace)
    if command:
        calls = trace.get("command_calls", [])
        checks["reviewed_command_argv"] = approval.get("tool") == "run_command" and approval.get("argv") == list(APPROVAL_COMMAND_ARGV)
        checks["native_command_execution_count"] = trace.get("patch_apply_count") == 0 and trace.get("applied_patches") == [] and (
            len(calls) == 1 and calls[0].get("argv") == list(APPROVAL_COMMAND_ARGV) and calls[0].get("exit_code") == 0
            and calls[0].get("snapshot") == current if _positive_command else calls == []
        )
        if not _positive_command:
            positive = evidence.get("command_positive_control", {})
            checks["rejection_is_not_an_unrunnable_command"] = bool(positive) and all(
                validate_approval_evidence(fixture, None, positive, _positive_command=True).values())
    else:
        applied = trace.get("applied_patches", [])
        checks["reviewed_patch_integrity"] = approval.get("tool") == "propose_patch" and approval.get("integrity") is True and approval.get("visible_match") is True and (
            approval.get("base_sha256") == {"note.txt": "sha256:" + base["note.txt"]}
            and approval.get("content_sha256") == {"note.txt": expected["note.txt"]}
        )
        checks["native_patch_applied_once"] = type(trace.get("patch_apply_count")) is int and trace["patch_apply_count"] == 1 and (
            trace.get("command_calls") == [] and len(applied) == 1 and applied[0].get("snapshot") == current
            and applied[0].get("patch_id") == approval.get("patch_id")
        )
    return checks


async def probe_approval(fixture, mode, workspace, *, _positive_command=False, _contract=None):
    command = fixture.case_id == "approval-02"
    if fixture.category != "approval_resume" or mode not in ({"graph"} if command else {"graph", "deep"}) or (_positive_command and not command):
        raise ValueError("unsupported approval service probe")
    contract = _contract if _contract is not None else NativeTaskContract.load()
    case = contract.case(fixture, mode)
    materialize_task_case(fixture, workspace)
    initial = workspace_snapshot(workspace)
    provider = ScriptedApprovalProvider(fixture, mode, workspace)
    service = RunService(workspace, provider=provider)
    await service.start()
    try:
        with ApprovalSideEffectTrace(workspace) as instrumentation:
            record, _ = await service.create({"mode": mode, "prompt": case.prompt, "effort": "deep",
                                              "permissions": dict(case.permissions), "deadline_seconds": 60})
            run_id = record["run_id"]
            await service.scheduler.wait(run_id)
            paused = await service.get(run_id)
            if paused["status"] != "interrupted" or len(paused["metadata"].get("interrupts", [])) != 1:
                raise ValueError("native approval did not produce one pending interrupt")
            interrupt = paused["metadata"]["interrupts"][0]
            actions = interrupt["value"]["action_requests" if mode == "deep" else "tool_calls"]
            if len(actions) != 1 or actions[0]["name"] != ("run_command" if command else "propose_patch"):
                raise ValueError("native approval requested an unexpected action")
            arguments = actions[0]["args" if mode == "deep" else "arguments"]
            approval = {"tool": actions[0]["name"]}
            if command:
                approval["argv"] = ["{python}" if i == 0 and value == sys.executable else value for i, value in enumerate(arguments["argv"])]
            else:
                proposal = arguments["_doppel_patch"]
                approval.update(patch_id=proposal["patch_id"],
                                base_sha256={p["path"]: p["base_hash"] for p in proposal["changes"]},
                                content_sha256={p["path"]: sha256(p["content"].encode()).hexdigest() for p in proposal["changes"]},
                                visible_match=[(p["path"], p["content"]) for p in proposal["changes"]] == [(p["path"], p["content"]) for p in arguments["changes"]],
                                integrity=proposal["patch_id"] == sha256(json.dumps({"changes": proposal["changes"], "unified_diff": proposal["unified_diff"]},
                                                                                    ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32])
            paused_snapshot = workspace_snapshot(workspace)
            await service.close()
            service = RunService(workspace, provider=provider)
            await service.start()
            reconstructed = workspace_snapshot(workspace)
            wrong_error = None
            try:
                await service.resume(run_id, "not-the-pending-interrupt", {"action": "approve"})
            except ValueError as exc:
                wrong_error = type(exc).__name__
            wrong = {"error": wrong_error, "snapshot": workspace_snapshot(workspace), "status": (await service.get(run_id))["status"]}
            decision = "approve" if _positive_command else fixture.oracle["decision"]
            await service.resume(run_id, interrupt["id"], {"action": decision})
            await service.scheduler.wait(run_id)
            completed = await service.get(run_id)
            current = workspace_snapshot(workspace)
            duplicates = []
            for restart in (False, True):
                if restart:
                    await service.close()
                    service = RunService(workspace, provider=provider)
                    await service.start()
                error = None
                try:
                    await service.resume(run_id, interrupt["id"], {"action": decision})
                except ValueError as exc:
                    error = type(exc).__name__
                duplicates.append({"reconstructed": restart, "error": error,
                                   "snapshot": workspace_snapshot(workspace), "status": (await service.get(run_id))["status"]})
            events = await service.list_events(run_id)
            evidence = {"case_id": fixture.case_id, "fixture_sha256": fixture.sha256, "fixture_version": fixture.version,
                        "runtime": mode, "actual_runtime": completed["metadata"].get("fallback_runtime") or mode,
                        "paused_fallback_runtime": paused["metadata"].get("fallback_runtime"), "boundary": "run_service", "status": completed["status"],
                        "permissions": dict(case.permissions), "decision": decision, "initial": initial, "current": current,
                        "paused_snapshot": paused_snapshot, "reconstructed_snapshot": reconstructed, "wrong_interrupt": wrong,
                        "duplicate_resumes": duplicates, "approval": approval, "native_trace": instrumentation.trace,
                        "receipts": provider.receipts, "approval_decision_count": sum(row["type"] == "approval.decided" for row in events),
                        "approval_decisions": native_approval_decisions(events),
                        "human_review": "pending", "task_quality_scored": False,
                        "scope_note": "Trusted scripted native service controls and external operation/snapshot evidence, not paid/model quality or strong OS isolation."}
        if command and not _positive_command:
            with tempfile.TemporaryDirectory(prefix="doppel-command-positive-") as temp:
                evidence["command_positive_control"] = await probe_approval(fixture, mode, Path(temp) / "agent", _positive_command=True, _contract=contract)
        checks = validate_approval_evidence(fixture, workspace, evidence, _positive_command=_positive_command)
        return {**evidence, "checks": checks, "deterministic_pass": all(checks.values())}
    finally:
        await service.close()
