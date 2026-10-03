"""Actual reviewed multi-file service controls; no native command grant added."""

from hashlib import sha256
import json
from pathlib import Path
import tempfile

from bench.runtime_fixtures import materialize_task_case
from bench.runtime_contract import NativeTaskContract, native_approval_decisions
from bench.runtime_patch_controls import ScopedPatchFault, validate_atomic_control
from bench.runtime_tdd_harness import external_oracle, workspace_snapshot
from bench.runtime_validators import validate_patch_evidence
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.service import RunService


class ScriptedPatchProvider:
    def __init__(self, fixture, mode, workspace, changes):
        self.paths = [p for p, _ in fixture.public_files]
        self.base = dict(fixture.public_files)
        self.mode, self.workspace, self.changes = mode, workspace, changes
        self.turn = 0
        self.receipts, self.pending, self.seen = [], {}, set()

    def next_turn(self, messages, tools):
        names = {tool["function"]["name"] for tool in tools}
        if not {"read_file", "propose_patch"} <= names or "run_command" in names or "execute" in names:
            raise ValueError("patch case must not acquire an execution grant")
        for message in messages:
            if message.role != "tool" or message.tool_call_id not in self.pending or message.tool_call_id in self.seen:
                continue
            self.seen.add(message.tool_call_id)
            call = self.pending[message.tool_call_id]
            row = {"tool": call.name, "tool_call_id": call.id, "response_sha256": sha256(message.content.encode()).hexdigest(),
                   "snapshot": workspace_snapshot(self.workspace)}
            if call.name == "read_file":
                path = call.arguments.get("path", call.arguments.get("file_path"))
                row.update(path=path, success=not message.content.startswith(("Error:", "Tool error")),
                           source_lines_present=all(line in message.content for line in self.base[path].decode().splitlines() if line))
            else:
                try:
                    output = json.loads(message.content[message.content.rindex('{"patch_id"'):])
                except (ValueError, TypeError):
                    output = {}
                row.update(requested_paths=list(self.changes), changed_paths=output.get("changed_paths", []),
                           patch_id=output.get("patch_id"), native_verification_present="verification" in output)
            self.receipts.append(row)
        self.turn += 1
        if self.turn <= len(self.paths):
            args = {"file_path" if self.mode == "deep" else "path": self.paths[self.turn - 1]}
            if self.mode == "deep":
                args.update(offset=0, limit=100)
            call = ToolCall(f"patch-{self.turn}", "read_file", args)
        elif self.turn == len(self.paths) + 1:
            call = ToolCall(f"patch-{self.turn}", "propose_patch", {"changes": [
                {"path": p, "content": c} for p, c in self.changes.items()]})
        else:
            return ModelTurn(content="Scripted multi-file control complete; human quality remains pending.")
        if not call.id or call.id in self.pending:
            raise ValueError("patch receipts need unique identifiers")
        self.pending[call.id] = ToolCall(call.id, call.name, json.loads(json.dumps(call.arguments)))
        return ModelTurn(tool_calls=(call,))


async def probe_patch(fixture, mode, workspace, *, changes=None, _control=None, _contract=None):
    if fixture.category != "multi_file_patch" or mode not in {"graph", "deep"}:
        raise ValueError("unsupported patch probe")
    atomic = fixture.oracle["kind"] == "atomic_config_patch"
    if _control is not None and (not atomic or _control not in fixture.oracle["atomic_controls"]):
        raise ValueError("failure controls require the frozen atomic fixture")
    contract = _contract if _contract is not None else NativeTaskContract.load()
    case = contract.case(fixture, mode)
    materialize_task_case(fixture, workspace)
    hidden = dict(fixture.hidden_files)
    changes = json.loads(hidden["reference_changes.json"]) if changes is None else changes
    initial = workspace_snapshot(workspace)
    target_before = await external_oracle(workspace, hidden["target_tests.py"])
    regression_before = await external_oracle(workspace, hidden["regression_tests.py"])
    behavior_profile = fixture.oracle["kind"] in {"behavior_preserving_refactor", "atomic_config_patch"}
    public_before = await external_oracle(workspace, hidden["public_check.py"]) if behavior_profile else None
    provider = ScriptedPatchProvider(fixture, mode, workspace, changes)
    service = RunService(workspace, provider=provider)
    await service.start()
    try:
        record, _ = await service.create({"mode": mode, "prompt": case.prompt, "effort": "deep",
                                          "permissions": dict(case.permissions), "deadline_seconds": 60})
        run_id = record["run_id"]
        await service.scheduler.wait(run_id)
        paused = await service.get(run_id)
        if paused["status"] != "interrupted":
            raise ValueError("native reviewed patch did not pause")
        interrupts = paused["metadata"]["interrupts"]
        if len(interrupts) != 1:
            raise ValueError("unexpected approval count")
        value = interrupts[0]["value"]
        calls = value["action_requests" if mode == "deep" else "tool_calls"]
        if len(interrupts) != 1 or len(calls) != 1 or calls[0]["name"] != "propose_patch":
            raise ValueError("unexpected approval surface")
        arguments = calls[0]["args" if mode == "deep" else "arguments"]
        proposal = arguments["_doppel_patch"]
        proposed = proposal["changes"]
        bounds = {v["path"] for v in proposed} <= set(fixture.allowed_edits)
        visible_match = [(v["path"], v["content"]) for v in proposed] == [(v["path"], v["content"]) for v in arguments["changes"]]
        integrity = proposal["patch_id"] == sha256(json.dumps({"changes": proposed, "unified_diff": proposal["unified_diff"]},
                                                             ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
        no_effects = workspace_snapshot(workspace) == initial
        if not bounds or not no_effects:
            raise ValueError("approval violated bounds or had early effects")
        await service.close()
        service = RunService(workspace, provider=provider)
        await service.start()
        reconstructed = workspace_snapshot(workspace) == initial
        if _control == "stale_base":
            (workspace / "settings.json").write_bytes(dict(fixture.public_files)["settings.json"] + b"\n")
        pre_resume = workspace_snapshot(workspace)
        fault, resume_error = None, None
        if _control is None:
            await service.resume(run_id, interrupts[0]["id"], {"action": "approve"})
            await service.scheduler.wait(run_id)
        else:
            with ScopedPatchFault(workspace, _control) as instrumentation:
                try:
                    await service.resume(run_id, interrupts[0]["id"], {"action": "approve"})
                    await service.scheduler.wait(run_id)
                except (OSError, RuntimeError) as exc:
                    # Deep propagates native patch errors, Graph returns a tool
                    # error to its provider. The native trace, not this catch,
                    # proves the intended operation actually failed.
                    resume_error = type(exc).__name__
                fault = instrumentation.trace
        completed = await service.get(run_id)
        target_after = await external_oracle(workspace, hidden["target_tests.py"])
        regression_after = await external_oracle(workspace, hidden["regression_tests.py"])
        public_after = await external_oracle(workspace, hidden["public_check.py"])
        current = workspace_snapshot(workspace)
        public_evidence = {"public_before": public_before}
        if not behavior_profile:
            with tempfile.TemporaryDirectory(prefix="doppel-patch-mutation-") as temp:
                mutation = Path(temp) / "agent"
                materialize_task_case(fixture, mutation)
                (mutation / "test_public.py").write_bytes((workspace / "test_public.py").read_bytes())
                public_evidence = {"candidate_on_seed": await external_oracle(mutation, hidden["public_check.py"])}
        rows = provider.receipts
        decisions = native_approval_decisions(await service.list_events(run_id))
        evidence = {"case_id": fixture.case_id, "runtime": mode, "actual_runtime": completed["metadata"].get("fallback_runtime") or mode,
                "boundary": "run_service", "status": completed["status"], "prompt": case.prompt, "permissions": dict(case.permissions),
                "fixture_version": fixture.version, "fixture_sha256": fixture.sha256, "receipts": rows, "approval_count": 1,
                "approval_decisions": decisions,
                "reconstructed_service": reconstructed, "initial": initial, "current": current,
                "paused_fallback_runtime": paused["metadata"].get("fallback_runtime"),
                "approval": {"patch_id": proposal["patch_id"], "integrity": integrity, "visible_match": visible_match,
                             "bounds_pass": bounds, "no_unapproved_effects": no_effects,
                             "base_sha256": {v["path"]: v["base_hash"] for v in proposed},
                             "content_sha256": {v["path"]: sha256(v["content"].encode()).hexdigest() for v in proposed}},
                "external_target_before": target_before, "external_target_after": target_after,
                "external_regression_before": regression_before, "external_regression_after": regression_after,
                "public_after": public_after, **public_evidence,
                "human_review": "pending", "quality_scored": False, "task_quality_scored": False,
                "scope_note": ("Trusted scripted reference; frozen finite public/regression behavior plus dynamic delegation controls, "
                               "not universal compatibility, model quality or strong OS isolation." if behavior_profile else
                               "Trusted scripted reference, external behavior/docs examples/test mutation; not model quality or strong OS isolation.")}
        if _control is not None:
            evidence.update(control=_control, fault=fault, pre_resume=pre_resume, resume_error=resume_error)
            checks = validate_atomic_control(fixture, evidence, runtime=mode)
            return {**evidence, "checks": checks, "deterministic_pass": all(checks.values())}
        if atomic:
            controls = []
            with tempfile.TemporaryDirectory(prefix="doppel-native-patch-faults-") as temp:
                for control in fixture.oracle["atomic_controls"]:
                    controls.append(await probe_patch(fixture, mode, Path(temp) / control, _control=control, _contract=contract))
            evidence["atomic_controls"] = controls
        checks = validate_patch_evidence(fixture, workspace, evidence)
        return {**evidence, "checks": checks, "deterministic_pass": all(checks.values())}
    finally:
        await service.close()
