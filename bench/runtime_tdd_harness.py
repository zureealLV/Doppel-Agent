"""Supported Graph RunService TDD controls; external oracles never enter tools."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import tempfile

from bench.runtime_fixtures import TaskFixture, TDD_ARGV, materialize_task_case
from bench.runtime_validators import validate_tdd_evidence
from bench.runtime_contract import NativeTaskContract, native_approval_decisions
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.service import RunService
from doppel_agent.workspace.process_supervisor import ProcessSupervisor
from doppel_agent.workspace.verification import VerificationPipeline


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def command_argv() -> list[str]:
    return [sys.executable if item == "{python}" else item for item in TDD_ARGV]


def workspace_snapshot(workspace: Path) -> dict[str, str]:
    return {p.relative_to(workspace).as_posix(): "symlink" if p.is_symlink() else sha256(p.read_bytes()).hexdigest()
            for p in workspace.rglob("*")
            if p.is_file() and p.relative_to(workspace).parts[0] != ".doppel-agent"}


def unittest_outcome(exit_code: int | None, text: str) -> dict:
    run = re.search(r"Ran (\d+) tests? in", text)
    failures = re.search(r"failures=(\d+)", text)
    errors = re.search(r"errors=(\d+)", text)
    return {"exit_code": exit_code, "tests_run": int(run[1]) if run else 0,
            "failures": int(failures[1]) if failures else 0,
            "errors": int(errors[1]) if errors else 0}


class ScriptedTddProvider:
    """Trusted positive control, not a blind or paid model solution."""

    def __init__(self, test_source: bytes, fixed_source: bytes):
        self.test_source, self.fixed_source = test_source, fixed_source
        self.turn = 0

    def next_turn(self, messages, tools):
        names = {tool["function"]["name"] for tool in tools}
        if not {"read_file", "propose_patch", "run_command"} <= names:
            raise ValueError("TDD requires the production service tool surface")
        self.turn += 1
        calls = {
            1: ("read_file", {"path": "service.py"}),
            2: ("propose_patch", {"changes": [{"path": "test_candidate.py", "content": self.test_source.decode()}]}),
            3: ("run_command", {"argv": command_argv()}),
            4: ("propose_patch", {"changes": [{"path": "service.py", "content": self.fixed_source.decode()}]}),
            5: ("run_command", {"argv": command_argv()}),
        }
        if self.turn in calls:
            name, arguments = calls[self.turn]
            return ModelTurn(tool_calls=(ToolCall(f"tdd-{self.turn}", name, arguments),))
        return ModelTurn(content="Scripted TDD control finished; quality still requires human adjudication.")


class TddEvidenceProvider:
    def __init__(self, provider, workspace: Path):
        self.provider, self.workspace = provider, workspace
        self.receipts: list[dict] = []
        self.pending: dict = {}
        self.seen: set[str] = set()

    def next_turn(self, messages, tools):
        for message in messages:
            call_id = message.tool_call_id
            if message.role != "tool" or call_id not in self.pending or call_id in self.seen:
                continue
            self.seen.add(call_id)
            call = self.pending[call_id]
            row = {"tool_call_id": call_id, "tool": call.name,
                   "response_sha256": sha256(message.content.encode()).hexdigest(),
                   "snapshot": workspace_snapshot(self.workspace)}
            if call.name == "read_file":
                row["path"] = call.arguments["path"]
            elif call.name == "run_command":
                code = re.match(r"exit_code=(-?\d+)\n", message.content)
                row.update(unittest_outcome(int(code[1]) if code else None, message.content))
                row["argv_sha256"] = digest(call.arguments["argv"])
            elif call.name == "propose_patch":
                row["requested_paths"] = [change["path"] for change in call.arguments["changes"]]
                try:
                    output = json.loads(message.content)
                    verification = output["verification"]["results"]
                    row["changed_paths"] = output["changed_paths"]
                    row["verification"] = [{**unittest_outcome(item["exit_code"], item["stdout"] + item["stderr"]),
                                            "success": item["success"], "argv_sha256": digest(item["argv"])}
                                           for item in verification]
                except (ValueError, KeyError, TypeError):
                    row["changed_paths"], row["verification"] = [], []
            self.receipts.append(row)
        turn = self.provider.next_turn(messages, tools)
        for call in turn.tool_calls:
            if not call.id or call.id in self.pending:
                raise ValueError("TDD evidence needs unique non-empty tool-call IDs")
            self.pending[call.id] = ToolCall(call.id, call.name, json.loads(json.dumps(call.arguments)))
        return turn


async def external_oracle(workspace: Path, payload: bytes) -> dict:
    # Frozen bytes are executed in a fresh external directory. Its path and
    # source are never passed to the runtime/provider or copied into workspace.
    with tempfile.TemporaryDirectory(prefix="doppel-external-oracle-") as temporary:
        script = Path(temporary) / "check.py"
        script.write_bytes(payload)
        supervisor = ProcessSupervisor()
        try:
            result = await supervisor.run([sys.executable, "-B", str(script), "-q"], cwd=workspace,
                                          run_id="external-oracle", timeout_seconds=10,
                                          env=VerificationPipeline._safe_env())
            return {**unittest_outcome(result.exit_code, result.stdout + result.stderr),
                    "oracle_sha256": sha256(payload).hexdigest()}
        finally:
            await supervisor.close()


async def probe_tdd(fixture: TaskFixture, workspace: Path, *, _contract=None) -> dict:
    if fixture.category != "tdd_fix":
        raise ValueError("TDD probe requires a TDD fixture")
    contract = _contract if _contract is not None else NativeTaskContract.load()
    case = contract.case(fixture, "graph")
    materialize_task_case(fixture, workspace)
    hidden = dict(fixture.hidden_files)
    target_before = await external_oracle(workspace, hidden["target_tests.py"])
    regression_before = await external_oracle(workspace, hidden["regression_tests.py"])
    config = workspace / ".doppel" / "verification.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"commands": [{"name": "candidate", "argv": command_argv(), "timeout_seconds": 10}]}),
                      encoding="utf-8")
    initial = workspace_snapshot(workspace)
    provider = TddEvidenceProvider(ScriptedTddProvider(hidden["scripted_test.py"], hidden["reference_source.py"]), workspace)
    service = RunService(workspace, provider=provider)
    approvals = []
    await service.start()
    try:
        record, _ = await service.create({"mode": "graph", "prompt": case.prompt,
                                          "effort": "deep", "permissions": dict(case.permissions),
                                          "deadline_seconds": 60})
        run_id = record["run_id"]
        for _ in range(5):
            await service.scheduler.wait(run_id)
            record = await service.get(run_id)
            if record["status"] != "interrupted":
                break
            interrupts = record["metadata"]["interrupts"]
            requested = interrupts[0]["value"]["tool_calls"]
            previous = provider.receipts[-1]["snapshot"] if provider.receipts else initial
            bounds = all(
                (call["name"] == "run_command" and call["arguments"] == {"argv": command_argv()})
                or (call["name"] == "propose_patch" and {c["path"] for c in call["arguments"]["changes"]} <= set(fixture.allowed_edits))
                for call in requested
            )
            unchanged = workspace_snapshot(workspace) == previous
            approvals.append({"tool": requested[0]["name"], "bounds_pass": bounds,
                              "no_unapproved_effects": unchanged})
            if not bounds or not unchanged:
                raise ValueError("TDD approval violated frozen edit/argv/no-effect bounds")
            await service.resume(run_id, interrupts[0]["id"], {"action": "approve"})
        target_after = await external_oracle(workspace, hidden["target_tests.py"])
        regression_after = await external_oracle(workspace, hidden["regression_tests.py"])
        decisions = native_approval_decisions(await service.list_events(run_id))
        evidence = {"initial": initial, "receipts": provider.receipts, "approvals": approvals,
                    "approval_decisions": decisions,
                    "external_target_before": target_before, "external_regression_before": regression_before,
                    "external_target_after": target_after, "external_regression_after": regression_after,
                    "status": record["status"], "fallback_runtime": record["metadata"].get("fallback_runtime")}
        checks = validate_tdd_evidence(fixture, workspace, evidence, argv_sha256=digest(command_argv()))
        return {"case_id": fixture.case_id, "prompt": case.prompt, "protocol_version": contract.matrix.protocol_version,
                "permissions": dict(case.permissions),
                "fixture_sha256": fixture.sha256, "fixture_version": fixture.version,
                "source_kind": fixture.source_kind, "source_baseline_commit": fixture.source_commit,
                "runtime": "graph", "actual_runtime": evidence["fallback_runtime"] or "graph", "boundary": "run_service",
                "checks": checks, "deterministic_pass": all(checks.values()), **evidence,
                "command_exit_codes": [r["exit_code"] for r in provider.receipts if r["tool"] == "run_command"],
                "patch_verification_exit_codes": [v["exit_code"] for r in provider.receipts if r["tool"] == "propose_patch"
                                                  for v in r["verification"]],
                "approval_count": len(approvals), "human_review": "pending", "task_quality_scored": False,
                "scope_note": "Trusted scripted control and subprocess tests, not model quality or strong OS isolation."}
    finally:
        await service.close()
