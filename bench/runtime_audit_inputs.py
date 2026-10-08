"""Seal scripted audit inputs and separate evidence from pending adjudication.

This is provenance/drift detection in a trusted local harness, not an OS sandbox
or a cryptographic attestation of code execution. No hidden solution is exported.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import platform
import re
import subprocess

from bench.runtime_freeze import canonical_json, freeze_json, thaw_json


SOURCE_PATHS = ("src", "bench", "tests", "spikes", "scripts", "frontend/src", "frontend/tests",
                "frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.ts",
                "frontend/tsconfig.json", "pyproject.toml", "uv.lock")
OFFLINE_BUDGET = freeze_json({
    "external_model_calls_allowed": False, "max_cost_usd": 0, "repeat_ids": [1],
    "standard_service_deadline_seconds": 60, "cancel_service_deadline_seconds": 10,
    "external_oracle_timeout_seconds": 10, "service_effort": "deep", "navigation_max_steps": 16,
    "note": "Trusted scripted controls only; this is not a provider-side paid spending cap.",
})


def source_context(root):
    root = root.resolve(strict=True)
    paths = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *SOURCE_PATHS], cwd=root)
    inventory = {}
    deleted = []
    for name in sorted(set(item.decode("utf-8") for item in paths.split(b"\0") if item)):
        target = root / name
        if not target.resolve().is_relative_to(root) or target.is_symlink():
            raise ValueError("audit source input must be an owned regular file")
        if not target.exists():
            # git --cached also enumerates tracked files removed by a real
            # rebuild. Bind their absence explicitly instead of either rejecting
            # the entire dirty-tree audit or silently dropping provenance.
            deleted.append(name)
            continue
        if not target.is_file():
            raise ValueError("audit source input must be an owned regular file")
        inventory[name] = sha256(target.read_bytes()).hexdigest()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--", *SOURCE_PATHS], cwd=root, text=True).strip())
    return {"commit": commit, "evaluation_source_dirty": dirty, "files": inventory, "deleted_files": deleted,
            "files_sha256": sha256(canonical_json({"files": inventory, "deleted_files": deleted})).hexdigest(),
            "python_version": platform.python_version(), "platform": platform.system()}


@dataclass(frozen=True)
class AuditInputs:
    json_bytes: bytes

    def __post_init__(self):
        object.__setattr__(self, "json_bytes", bytes(self.json_bytes))

    @classmethod
    def capture(cls, contract, fixtures, selection, source, *, budget=None, scope="task_controls"):
        if scope not in {"task_controls", "supported_matrix"}:
            raise ValueError("unknown scripted audit scope")
        expected_budget = thaw_json(OFFLINE_BUDGET)
        if scope == "supported_matrix":
            expected_budget["repeat_ids"] = [1, 2, 3]
            expected_budget["standard_factory_deadline_seconds"] = 60
        budget = expected_budget if budget is None else budget
        if canonical_json(budget) != canonical_json(expected_budget):
            raise ValueError("scripted audit budget must forbid paid calls and preserve frozen bounds")
        contexts = {fixture.case_id: {
            "category": fixture.category, "source_kind": fixture.source_kind, "source_baseline_commit": fixture.source_commit,
            "fixture_sha256": fixture.sha256, "fixture_version": fixture.version, "allowed_edits": list(fixture.allowed_edits),
            "public_sha256": {path: sha256(payload).hexdigest() for path, payload in fixture.public_files},
            "hidden_sha256": {path: sha256(payload).hexdigest() for path, payload in fixture.hidden_files},
            "oracle": thaw_json(fixture.oracle),
        } for fixture in fixtures}
        if len(contexts) != len(fixtures) or not selection or len(selection) != len(set(selection)):
            raise ValueError("audit fixtures and selected boundary/run keys must be unique")
        original = {run.run_key: run for run in contract.matrix.expand()}
        for boundary, key in selection:
            if boundary not in contract.capabilities or key not in contract.capabilities[boundary]["supported_run_keys"]:
                raise ValueError("cannot freeze unsupported audit selection")
            run = original[key]
            if run.case_id not in contexts or run.repeat not in expected_budget["repeat_ids"] or contexts[run.case_id]["category"] != run.category:
                raise ValueError("audit selection must match a frozen fixture/profile")
        if scope == "supported_matrix":
            boundaries = {boundary for boundary, _ in selection}
            if len(boundaries) != 1 or set(contexts) != {case.case_id for case in contract.matrix.cases}:
                raise ValueError("supported audit must keep one complete boundary and all twenty fixtures")
            boundary = next(iter(boundaries))
            if set(key for _, key in selection) != set(contract.capabilities[boundary]["supported_run_keys"]):
                raise ValueError("supported audit cannot omit any eligible key or repeat")
        return cls(canonical_json({
            "schema_version": "1.1", "execution_scope": scope, "protocol_version": contract.matrix.protocol_version,
            "manifest_sha256": contract.matrix.manifest_sha256, "original_run_count": len(original),
            "capabilities": contract.capabilities, "selection": [{"boundary": boundary, "run_key": key} for boundary, key in selection],
            "cases": {case.case_id: {"category": case.category, "prompt": case.prompt, "permissions": case.permissions,
                                      "weak_validator": case.validator} for case in contract.matrix.cases},
            "fixtures": contexts, "source": source, "budget": budget,
        }))

    @property
    def sha256(self):
        return sha256(self.json_bytes).hexdigest()

    def document(self):
        # Always return a fresh JSON tree; callers cannot mutate the seal.
        return {"inputs_sha256": self.sha256, **json.loads(self.json_bytes)}

    def assert_source_unchanged(self, current):
        if canonical_json(current) != canonical_json(self.document()["source"]):
            raise ValueError("audit source changed during the batch; evidence cannot be accepted")


def normalize_observation(inputs, row):
    """Normalize trusted harness observations, never an Agent's success text."""
    context = inputs.document()
    pair = {"boundary": row.get("boundary"), "run_key": row.get("run_key")}
    if pair not in context["selection"]:
        raise ValueError("observation is outside the sealed selection")
    key = row["run_key"]
    case_id, runtime, repeat = key.split(":")
    fixture, case = context["fixtures"][case_id], context["cases"][case_id]
    if row.get("case_id") != case_id or row.get("runtime") != runtime or row.get("fixture_sha256") != fixture["fixture_sha256"] or (
        row.get("fixture_version") != fixture["fixture_version"] or row.get("permissions") != case["permissions"]
        or row.get("human_review") != "pending" or row.get("task_quality_scored") is not False
    ):
        raise ValueError("observation identity, grants or review boundary drifted")
    checks = row.get("checks", row.get("read_checks", {}))
    infrastructure_failure = row.get("infrastructure_failure", False)
    if type(infrastructure_failure) is not bool or not isinstance(checks, dict) or any(type(value) is not bool for value in checks.values()):
        raise ValueError("observation checks must be boolean trusted checks")
    if type(row.get("deterministic_pass")) is not bool or row["deterministic_pass"] != (
        bool(checks) and all(checks.values()) and not infrastructure_failure
    ):
        raise ValueError("observation pass flag is inconsistent with actual checks")
    actual, fallback = row.get("actual_runtime"), row.get("fallback_runtime") or row.get("paused_fallback_runtime")
    expected_status = "cancelled" if case["category"] == "concurrent_cancel" else "completed"
    if row["deterministic_pass"] and (actual != runtime or fallback or row.get("status") != expected_status):
        raise ValueError("fallback execution cannot satisfy native acceptance")
    reads = row.get("receipts", row.get("reads", []))
    read_evidence = [{key: receipt[key] for key in ("tool_call_id", "path", "success", "response_sha256", "source_lines_present") if key in receipt}
                     for receipt in reads if receipt.get("tool") == "read_file"]
    if any(not re.fullmatch(r"[0-9a-f]{64}", receipt.get("response_sha256", "")) for receipt in read_evidence):
        raise ValueError("read evidence must retain response hashes")
    patches = [{field: receipt[field] for field in ("tool_call_id", "response_sha256", "requested_paths", "changed_paths", "patch_id", "verification") if field in receipt}
               for receipt in reads if receipt.get("tool") == "propose_patch"]
    commands = [{field: receipt[field] for field in ("tool_call_id", "response_sha256", "argv_sha256", "exit_code", "tests_run", "failures", "errors") if field in receipt}
                for receipt in reads if receipt.get("tool") == "run_command"]
    oracles = {name: {field: outcome[field] for field in ("oracle_sha256", "exit_code", "tests_run", "failures", "errors") if field in outcome}
               for name, outcome in row.items() if name.startswith("external_") and isinstance(outcome, dict)}
    decisions = row.get("approval_decisions", [])
    if not isinstance(decisions, list) or any(not isinstance(decision, dict) or decision.get("action") not in {"approve", "reject", "edit"}
                                            or not isinstance(decision.get("interrupt_id"), str) or not decision["interrupt_id"] for decision in decisions):
        raise ValueError("approval evidence must retain actual native decisions")
    expected_source = "service_event" if row["boundary"] == "run_service" else "factory_resume_command"
    if len({decision["interrupt_id"] for decision in decisions}) != len(decisions) or any(
        decision.get("source") != expected_source or (expected_source == "service_event" and (
            type(decision.get("event_seq")) is not int or decision["event_seq"] < 1
            or not re.fullmatch(r"[0-9a-f]{64}", decision.get("event_sha256", ""))
        )) for decision in decisions
    ):
        raise ValueError("approval evidence must preserve its native boundary and distinct decision IDs")
    if expected_source == "service_event" and len({decision["event_seq"] for decision in decisions}) != len(decisions):
        raise ValueError("approval evidence contains repeated event sequences")
    for name in ("approval_count", "approval_decision_count"):
        if name in row and (type(row[name]) is not int or row[name] != len(decisions)):
            raise ValueError("approval evidence count must match actual decisions")
    if row["deterministic_pass"] and case["category"] not in {"navigation", "known_answer_review"} and not decisions:
        raise ValueError("positive side-effect controls require native approval decisions")
    processes = row.get("processes", [row["server_process"]] if "server_process" in row else [])
    return {"inputs_sha256": inputs.sha256, "run_key": key, "boundary": row["boundary"], "case_id": case_id,
            "category": case["category"], "runtime": runtime, "actual_runtime": actual, "fallback_runtime": fallback,
            "repeat": int(repeat), "status": row["status"], "fixture_sha256": fixture["fixture_sha256"],
            "raw_evidence_sha256": sha256(canonical_json(row)).hexdigest(), "reads": read_evidence,
            "patch_receipts": patches, "command_receipts": commands, "external_oracles": oracles,
            "approval_decisions": decisions, "process_exit_evidence": processes,
            "deterministic_checks": checks, "deterministic_pass": row["deterministic_pass"],
            "infrastructure_failure": infrastructure_failure, "failure_class": row.get("failure_class"),
            "human_review": "pending", "task_quality_score": None,
            "note": "Trusted scripted control evidence only; no paid/model-quality adjudication."}


def adjudication_forms(inputs, observations):
    """Separate unsupported, missing, infrastructure and pending human verdicts."""
    context, indexed = inputs.document(), {}
    for row in observations:
        pair = (row["boundary"], row["run_key"])
        if row.get("inputs_sha256") != inputs.sha256 or pair in indexed or {
            "boundary": pair[0], "run_key": pair[1],
        } not in context["selection"]:
            raise ValueError("adjudication evidence is duplicate or belongs to different inputs")
        indexed[pair] = row
    forms = {}
    for boundary, capability in context["capabilities"].items():
        excluded = {row["run_key"]: row["reasons"] for row in capability["excluded_runs"]}
        supported = set(capability["supported_run_keys"])
        items = []
        for key in sorted(supported | set(excluded)):
            observation = indexed.get((boundary, key))
            unsupported = key in excluded
            state = "unsupported" if unsupported else ("infrastructure_failure" if observation and observation["infrastructure_failure"] else
                                                       "pending_human_verdict" if observation else "not_recorded")
            items.append({"run_key": key, "boundary": boundary, "category": context["cases"][key.split(":")[0]]["category"],
                          "assessment_state": state, "unsupported_reasons": excluded.get(key, []),
                          "inputs_sha256": inputs.sha256, "evidence_sha256": observation["raw_evidence_sha256"] if observation else None,
                          "deterministic_pass": observation["deterministic_pass"] if observation else None,
                          "reviewer": None, "reviewed_utc": None, "seeded_finding_match": None, "false_positives": None,
                          "human_verdict": "pending", "human_notes": "", "task_quality_score": None})
        forms[boundary] = {"original_run_count": capability["original_run_count"],
                           "supported_run_count": capability["supported_run_count"], "excluded_run_count": capability["excluded_run_count"],
                           "recorded_control_count": sum((boundary, key) in indexed for key in supported), "items": items}
    return {"schema_version": "1.0", "inputs_sha256": inputs.sha256, "boundaries": forms,
            "task_quality_scored": False,
            "note": "Fill separate human forms after review; never edit raw evidence or convert scripted checks into model scores."}
