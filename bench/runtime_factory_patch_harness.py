"""Direct production Deep factory patches, without claiming service durability."""

import asyncio
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from bench.runtime_contract import NativeTaskContract
from bench.runtime_fixtures import materialize_task_case
from bench.runtime_patch_controls import ScopedPatchFault, validate_atomic_control
from bench.runtime_patch_harness import ScriptedPatchProvider
from bench.runtime_tdd_harness import external_oracle, workspace_snapshot
from bench.runtime_validators import validate_patch_evidence
from doppel_agent.runtime.base import ResumeCommand, RunRequest
from doppel_agent.runtime.factory import create_runtime


async def probe_factory_patch(fixture, mode, workspace, *, _contract=None, _control=None):
    if fixture.category != "multi_file_patch" or mode != "deep" or (
        _control is not None and (fixture.oracle["kind"] != "atomic_config_patch" or _control not in fixture.oracle["atomic_controls"])
    ):
        raise ValueError("unsupported direct-factory patch control")
    contract = _contract if _contract is not None else NativeTaskContract.load()
    case = contract.case(fixture, mode, boundary="direct_factory")
    materialize_task_case(fixture, workspace)
    hidden, initial = dict(fixture.hidden_files), workspace_snapshot(workspace)
    before = {"external_target_before": await external_oracle(workspace, hidden["target_tests.py"]),
              "external_regression_before": await external_oracle(workspace, hidden["regression_tests.py"])}
    behavior = fixture.oracle["kind"] in {"behavior_preserving_refactor", "atomic_config_patch"}
    if behavior:
        before["public_before"] = await external_oracle(workspace, hidden["public_check.py"])
    provider = ScriptedPatchProvider(fixture, mode, workspace, json.loads(hidden["reference_changes.json"]))
    runtime = create_runtime(mode, workspace, provider, core_options={"max_steps": 12,
                             "allow_write": case.permissions.get("workspace_write", False),
                             "allow_command": case.permissions.get("command_execute", False)})
    original_runtime = runtime
    request = RunRequest(case.prompt)
    try:
        async with asyncio.timeout(60):
            paused = await runtime.run(request)
        if paused.status != "interrupted" or paused.runtime != mode or paused.metadata.get("fallback_runtime"):
            raise ValueError("direct production Deep factory did not produce its native patch interrupt")
        interrupts = paused.metadata.get("interrupts", [])
        if len(interrupts) != 1:
            raise ValueError("direct factory patch must have exactly one pending interrupt")
        actions = interrupts[0]["value"]["action_requests"]
        if len(actions) != 1 or actions[0]["name"] != "propose_patch":
            raise ValueError("unexpected direct factory approval surface")
        arguments = actions[0]["args"]
        proposal, changes = arguments["_doppel_patch"], arguments["_doppel_patch"]["changes"]
        integrity = proposal["patch_id"] == sha256(json.dumps({"changes": changes, "unified_diff": proposal["unified_diff"]},
                                 ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
        bounds = {row["path"] for row in changes} <= set(fixture.allowed_edits)
        visible = [(row["path"], row["content"]) for row in changes] == [(row["path"], row["content"]) for row in arguments["changes"]]
        no_effects = workspace_snapshot(workspace) == initial
        if not (integrity and bounds and visible and no_effects):
            raise ValueError("direct factory native approval violated reviewed bounds")
        if _control == "stale_base":
            (workspace / "settings.json").write_bytes(dict(fixture.public_files)["settings.json"] + b"\n")
        pre_resume = workspace_snapshot(workspace)
        command = ResumeCommand(request.run_id, request.thread_id, {"action": "approve"})
        resumed, error, fault = None, None, None
        if _control is None:
            async with asyncio.timeout(60):
                resumed = await runtime.resume(command)
        else:
            with ScopedPatchFault(workspace, _control) as observer:
                try:
                    async with asyncio.timeout(60):
                        resumed = await runtime.resume(command)
                except (OSError, RuntimeError) as exc:
                    error = type(exc).__name__
                fault = observer.trace
        current = workspace_snapshot(workspace)
        after = {"external_target_after": await external_oracle(workspace, hidden["target_tests.py"]),
                 "external_regression_after": await external_oracle(workspace, hidden["regression_tests.py"]),
                 "public_after": await external_oracle(workspace, hidden["public_check.py"])}
        if not behavior:
            with tempfile.TemporaryDirectory(prefix="doppel-factory-test-sensitivity-") as temp:
                seed = Path(temp) / "agent"
                materialize_task_case(fixture, seed)
                (seed / "test_public.py").write_bytes((workspace / "test_public.py").read_bytes())
                after["candidate_on_seed"] = await external_oracle(seed, hidden["public_check.py"])
        evidence = {"case_id": fixture.case_id, "fixture_version": fixture.version, "fixture_sha256": fixture.sha256,
                    "runtime": mode, "actual_runtime": resumed.runtime if resumed is not None else runtime.name,
                    "boundary": "direct_factory", "status": resumed.status if resumed is not None else "failed",
                    "fallback_runtime": resumed.metadata.get("fallback_runtime") if resumed is not None else None,
                    "paused_fallback_runtime": paused.metadata.get("fallback_runtime"), "permissions": dict(case.permissions), "prompt": case.prompt,
                    "factory_same_instance": runtime is original_runtime, "reconstructed_service": False, "approval_count": 1,
                    "initial": initial, "current": current, "receipts": provider.receipts,
                    "approval": {"patch_id": proposal["patch_id"], "integrity": integrity, "visible_match": visible,
                                 "bounds_pass": bounds, "no_unapproved_effects": no_effects,
                                 "base_sha256": {row["path"]: row["base_hash"] for row in changes},
                                 "content_sha256": {row["path"]: sha256(row["content"].encode()).hexdigest() for row in changes}},
                    "approval_decisions": [{"source": "factory_resume_command", "interrupt_id": interrupts[0]["id"], "action": "approve"}],
                    **before, **after, "human_review": "pending", "task_quality_scored": False,
                    "scope_note": "Actual same-instance production Deep factory patch/HITL controls, not RunService reconstruction, paid/model quality or OS isolation."}
        if _control is not None:
            evidence.update(control=_control, fault=fault, pre_resume=pre_resume, resume_error=error)
            checks = validate_atomic_control(fixture, evidence, runtime=mode, boundary="direct_factory")
        else:
            if fixture.oracle["kind"] == "atomic_config_patch":
                controls = []
                with tempfile.TemporaryDirectory(prefix="doppel-factory-atomic-controls-") as temp:
                    for control in fixture.oracle["atomic_controls"]:
                        controls.append(await probe_factory_patch(fixture, mode, Path(temp) / control, _contract=contract, _control=control))
                evidence["atomic_controls"] = controls
            checks = validate_patch_evidence(fixture, workspace, evidence, boundary="direct_factory")
        return {**evidence, "checks": checks, "deterministic_pass": all(checks.values())}
    finally:
        await runtime.process_supervisor.close()
