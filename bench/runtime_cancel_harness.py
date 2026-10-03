"""Native service cancellation/deadline with externally bound process identities."""

import asyncio
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

from bench.runtime_fixtures import CANCEL_COMMAND_ARGV, materialize_task_case
from bench.runtime_contract import NativeTaskContract, native_approval_decisions
from bench.runtime_process_evidence import ProcessIdentity
from bench.runtime_tdd_harness import workspace_snapshot
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.service import RunService
from doppel_agent.workspace.process_supervisor import ProcessSupervisor


def command_argv(external):
    return [sys.executable, "-B", "parent.py", str(external)]


def canonical_argv(argv, external):
    # Never hide extra flags, executables or a different trace directory.
    return list(CANCEL_COMMAND_ARGV) if list(argv) == command_argv(external) else list(argv)


class NativeCommandTrace:
    """Narrow observers call the actual supervisor; no replacement tool/runtime."""

    def __init__(self, workspace, external):
        self.workspace, self.external = workspace.resolve(), external
        self.calls, self.starts, self.stack = [], [], ExitStack()

    def __enter__(self):
        original_run, original_start = ProcessSupervisor.run, ProcessSupervisor._start

        async def run(supervisor, argv, **kwargs):
            if Path(kwargs["cwd"]).resolve() != self.workspace:
                return await original_run(supervisor, argv, **kwargs)
            row = {"argv": canonical_argv(argv, self.external), "timeout_seconds": kwargs.get("timeout_seconds", 30),
                   "exception_class": None, "finished": False}
            self.calls.append(row)
            try:
                result = await original_run(supervisor, argv, **kwargs)
                row["exit_code"] = result.exit_code
                return result
            except BaseException as exc:
                row["exception_class"] = type(exc).__name__
                raise
            finally:
                row["finished"] = True

        def start(argv, cwd, env, stdout, stderr):
            managed = original_start(argv, cwd, env, stdout, stderr)
            if Path(cwd).resolve() == self.workspace:
                self.starts.append({"argv": canonical_argv(argv, self.external), "pid": managed.process.pid,
                                    "supervision": managed.supervision})
            return managed

        self.stack.enter_context(patch.object(ProcessSupervisor, "run", new=run))
        self.stack.enter_context(patch.object(ProcessSupervisor, "_start", new=staticmethod(start)))
        return self

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


class ScriptedCancelProvider:
    def __init__(self, fixture, workspace, external):
        self.base, self.workspace, self.external = dict(fixture.public_files), workspace, external
        self.turn, self.pending, self.seen, self.receipts = 0, {}, set(), []

    def next_turn(self, messages, tools):
        names = {tool["function"]["name"] for tool in tools}
        if not {"read_file", "run_command"} <= names or {"propose_patch", "execute"} & names or any(n.startswith("mcp__") for n in names):
            raise ValueError("cancellation control requires only native read/command grants")
        for message in messages:
            if message.role != "tool" or message.tool_call_id not in self.pending or message.tool_call_id in self.seen:
                continue
            self.seen.add(message.tool_call_id)
            path = self.pending[message.tool_call_id]
            self.receipts.append({"tool": "read_file", "tool_call_id": message.tool_call_id, "path": path,
                                  "response_sha256": sha256(message.content.encode()).hexdigest(),
                                  "source_lines_present": all(line in message.content for line in self.base[path].decode().splitlines() if line),
                                  "success": not message.content.startswith(("Error:", "Tool error")),
                                  "snapshot": workspace_snapshot(self.workspace)})
        self.turn += 1
        if self.turn <= 2:
            path = ("parent.py", "child.py")[self.turn - 1]
            call = ToolCall(f"cancel-read-{self.turn}", "read_file", {"path": path})
            self.pending[call.id] = path
        elif self.turn == 3:
            call = ToolCall("cancel-command", "run_command", {"argv": command_argv(self.external)})
        else:
            return ModelTurn(content="Unexpected natural completion; not cancellation evidence.")
        return ModelTurn(tool_calls=(call,))


def validate_cancel_evidence(fixture, workspace, evidence, *, _termination="cancel"):
    if fixture.case_id != "cancel-01" or _termination not in {"cancel", "deadline"}:
        raise ValueError("unsupported cancellation validation profile")
    base = {p: sha256(payload).hexdigest() for p, payload in fixture.public_files}
    processes, calls, starts, receipts = (evidence.get(key, []) for key in ("processes", "native_commands", "native_starts", "receipts"))
    checks = {
        "frozen_native_identity": evidence.get("case_id") == fixture.case_id and evidence.get("fixture_sha256") == fixture.sha256
        and evidence.get("fixture_version") == fixture.version and evidence.get("boundary") == "run_service"
        and evidence.get("runtime") == evidence.get("actual_runtime") == "graph" and not evidence.get("paused_fallback_runtime"),
        "bounded_command_permission": evidence.get("permissions") == {"command_execute": True}
        and evidence.get("approval_argv") == list(CANCEL_COMMAND_ARGV) and evidence.get("approval_decision_count") == 1,
        "no_early_execution_or_writes": type(evidence.get("processes_before_approval")) is int and evidence["processes_before_approval"] == 0
        and evidence.get("initial") == evidence.get("paused_snapshot") == evidence.get("current") == base,
        "native_supervised_process_started_once": len(calls) == len(starts) == 1
        and calls[0].get("argv") == starts[0].get("argv") == list(CANCEL_COMMAND_ARGV)
        and calls[0].get("finished") is True and calls[0].get("exception_class") == "CancelledError"
        and type(calls[0].get("timeout_seconds")) in {int, float} and calls[0]["timeout_seconds"] > fixture.oracle["deadline_seconds"]
        and type(starts[0].get("pid")) is int and starts[0]["pid"] > 0
        and starts[0].get("supervision") in {"job_object", "taskkill_fallback", "process_group"},
        "actual_parent_and_child_identities_exited": len(processes) == 2 and {row.get("role") for row in processes} == {"parent", "child"}
        and len({row.get("pid") for row in processes}) == 2 and all(
            type(row.get("pid")) is int and row["pid"] > 0 and row.get("alive_before_termination") is True
            and row.get("bound_before_shutdown") is True and row.get("exited") is True
            and row.get("identity_backend") in {"windows-process-handle", "linux-pidfd"} for row in processes),
        "scheduler_and_supervisor_drained": type(evidence.get("scheduler_active_after_close")) is int
        and evidence["scheduler_active_after_close"] == 0 and type(evidence.get("supervisor_active_after_close")) is int
        and evidence["supervisor_active_after_close"] == 0,
        "native_source_reads": [row.get("path") for row in receipts] == ["parent.py", "child.py"]
        and len({row.get("tool_call_id") for row in receipts}) == 2 and all(
            row.get("tool") == "read_file" and row.get("success") is True and row.get("source_lines_present") is True
            and row.get("snapshot") == base and isinstance(row.get("response_sha256"), str) and len(row["response_sha256"]) == 64
            for row in receipts),
        "durable_termination_not_natural_completion": evidence.get("termination") == _termination and (
            evidence.get("status") == "cancelled" and evidence.get("cancel_accepted") is True
            and evidence.get("error_class") == "CancelledError" and evidence.get("cancelled_event_count") == 1
            and evidence.get("failed_event_count") == 0 if _termination == "cancel" else
            evidence.get("status") == "failed" and evidence.get("cancel_accepted") is None
            and evidence.get("error_class") == "TimeoutError" and evidence.get("durable_error_class") == "TimeoutError"
            and evidence.get("cancelled_event_count") == 0 and evidence.get("failed_event_count") == 1
            and evidence.get("deadline_seconds") == fixture.oracle["deadline_seconds"]
            and type(evidence.get("elapsed_seconds")) in {int, float} and evidence["elapsed_seconds"] >= fixture.oracle["deadline_seconds"]),
    }
    if workspace is not None:
        checks["snapshot_matches_disk"] = evidence.get("current") == workspace_snapshot(workspace)
    if _termination == "cancel":
        control = evidence.get("deadline_control", {})
        checks["deadline_counterpart_exercised"] = bool(control) and all(
            validate_cancel_evidence(fixture, None, control, _termination="deadline").values())
    return checks


async def _ready_processes(external):
    async with asyncio.timeout(8):
        while True:
            try:
                return {role: json.loads((external / f"{role}.json").read_bytes()) for role in ("parent", "child")}
            except (FileNotFoundError, json.JSONDecodeError):
                await asyncio.sleep(0.025)


async def probe_cancel(fixture, mode, workspace, *, _termination="cancel", _contract=None):
    if fixture.case_id != "cancel-01" or mode != "graph" or _termination not in {"cancel", "deadline"}:
        raise ValueError("unsupported native cancellation probe")
    contract = _contract if _contract is not None else NativeTaskContract.load()
    case = contract.case(fixture, mode)
    materialize_task_case(fixture, workspace)
    initial, identities = workspace_snapshot(workspace), []
    with tempfile.TemporaryDirectory(prefix="doppel-process-ready-") as temp:
        external = Path(temp)
        provider = ScriptedCancelProvider(fixture, workspace, external)
        service = RunService(workspace, provider=provider)
        await service.start()
        try:
            with NativeCommandTrace(workspace, external) as trace:
                record, _ = await service.create({"mode": mode, "prompt": case.prompt, "effort": "deep",
                                                  "permissions": dict(case.permissions), "deadline_seconds": fixture.oracle["deadline_seconds"]})
                run_id = record["run_id"]
                await asyncio.wait_for(service.scheduler.wait(run_id), 30)
                paused = await service.get(run_id)
                if paused["status"] != "interrupted" or len(paused["metadata"].get("interrupts", [])) != 1:
                    raise ValueError("native process command did not pause for approval")
                interrupt = paused["metadata"]["interrupts"][0]
                actions = interrupt["value"]["tool_calls"]
                if len(actions) != 1 or actions[0]["name"] != "run_command":
                    raise ValueError("unexpected native cancellation approval")
                argv = canonical_argv(actions[0]["arguments"]["argv"], external)
                before_count, paused_snapshot = len(trace.calls) + len(trace.starts) + len(list(external.iterdir())), workspace_snapshot(workspace)
                started = time.monotonic()
                await service.resume(run_id, interrupt["id"], {"action": "approve"})
                ready = await _ready_processes(external)
                for role in ("parent", "child"):
                    identity = ProcessIdentity(ready[role]["pid"])
                    identities.append((role, identity))
                    if identity.exited():
                        raise ValueError("process exited before native termination was exercised")
                accepted = await service.cancel(run_id) if _termination == "cancel" else None
                error = None
                try:
                    await asyncio.wait_for(service.scheduler.wait(run_id), 30)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    error = "CancelledError"
                except TimeoutError:
                    error = "TimeoutError"
                terminal = await service.get(run_id)
                elapsed = time.monotonic() - started
                processes = [{**await identity.wait_exited(), "role": role, "alive_before_termination": True}
                             for role, identity in identities]
                events = await service.list_events(run_id)
                await service.close()
                evidence = {"case_id": fixture.case_id, "fixture_sha256": fixture.sha256, "fixture_version": fixture.version,
                            "runtime": mode, "actual_runtime": terminal["metadata"].get("fallback_runtime") or mode,
                            "paused_fallback_runtime": paused["metadata"].get("fallback_runtime"), "boundary": "run_service",
                            "termination": _termination, "status": terminal["status"], "error_class": error,
                            "durable_error_class": (terminal.get("error") or "").split(":", 1)[0] or None,
                            "cancel_accepted": accepted, "deadline_seconds": fixture.oracle["deadline_seconds"], "elapsed_seconds": elapsed,
                            "permissions": dict(case.permissions), "approval_argv": argv, "processes_before_approval": before_count,
                            "initial": initial, "paused_snapshot": paused_snapshot, "current": workspace_snapshot(workspace),
                            "processes": processes, "native_commands": trace.calls, "native_starts": trace.starts,
                            "receipts": provider.receipts, "scheduler_active_after_close": service.scheduler.active_count,
                            "supervisor_active_after_close": service.process_supervisor.active_count,
                            "approval_decision_count": sum(row["type"] == "approval.decided" for row in events),
                            "approval_decisions": native_approval_decisions(events),
                            "cancelled_event_count": sum(row["type"] == "run.cancelled" for row in events),
                            "failed_event_count": sum(row["type"] == "run.failed" for row in events),
                            "human_review": "pending", "task_quality_scored": False,
                            "scope_note": "Finite trusted scripted process-tree controls; native supervisor and OS-bound identity evidence, not paid/model quality or strong isolation."}
        finally:
            try:
                await service.close()
            finally:
                for _, identity in identities:
                    identity.close()
    if _termination == "cancel":
        with tempfile.TemporaryDirectory(prefix="doppel-deadline-control-") as temp:
            evidence["deadline_control"] = await probe_cancel(fixture, mode, Path(temp) / "agent", _termination="deadline", _contract=contract)
    checks = validate_cancel_evidence(fixture, workspace, evidence, _termination=_termination)
    return {**evidence, "checks": checks, "deterministic_pass": all(checks.values())}
