"""Frozen whole-source navigation and blind-review read controls.

Public bytes alone reach Agent workspaces. Hidden review keys are frozen for
external human adjudication, never used to turn scripted reads into findings.
"""

import asyncio
from hashlib import sha256

from bench.audit_runtime_task_fixtures import ScriptedNavigationReader
from bench.runtime_audit_inputs import source_context
from bench.runtime_contract import NativeTaskContract
from bench.runtime_fixtures import REVIEW_CASE_IDS, TASK_CASE_IDS, TaskFixture, fixture_path, freeze_review_inputs, load_task_fixture, materialize_task_case
from bench.runtime_freeze import canonical_json, thaw_json
from bench.runtime_tdd_harness import workspace_snapshot
from bench.runtime_validators import ReadEvidenceProvider, validate_readonly_evidence
from doppel_agent.runtime.base import RunRequest
from doppel_agent.runtime.factory import create_runtime
from doppel_agent.runtime.service import RunService


NAVIGATION_READS = {
    "nav-01": {"src/doppel_agent/runtime/factory.py": ["def create_runtime(", 'if mode == "legacy":', 'if mode == "graph":', 'if mode == "deep":']},
    "nav-02": {"src/doppel_agent/workspace/service.py": ["class WorkspaceService:", "resolved.relative_to(self.root)", "self.is_protected_parts(relative.parts)"]},
    "nav-03": {"src/doppel_agent/persistence/events.py": ["class EventStore:", "seq = int(cursor.lastrowid)", "ORDER BY seq LIMIT ?"]},
}


def _snapshot_fixture(case_id, category, commit, public, oracle, hidden=(), *, source_kind="synthetic_seed"):
    public, hidden = tuple(sorted(public)), tuple(sorted(hidden))
    identity = {"case_id": case_id, "category": category, "version": "readonly-1.0", "source_commit": commit,
                "source_kind": source_kind, "public": {p: sha256(b).hexdigest() for p, b in public},
                "hidden": {p: sha256(b).hexdigest() for p, b in hidden}, "oracle": oracle, "allowed_edits": []}
    return TaskFixture(case_id, category, "readonly-1.0", commit, public, (), oracle,
                       sha256(canonical_json(identity)).hexdigest(), hidden, source_kind)


def _whole_source_snapshot(root):
    context = source_context(root)
    public = tuple((name, fixture_path(root, name).read_bytes()) for name in sorted(context["files"])
                   if name.startswith("src/doppel_agent/"))
    if not public:
        raise ValueError("whole source snapshot is required for navigation")
    if any(sha256(payload).hexdigest() != context["files"][name] for name, payload in public):
        raise ValueError("navigation source changed while freezing its snapshot")
    return context, public


def load_matrix_fixtures(root, contract):
    """Freeze all 20 cases, retaining navigation's original whole-source scope.

    Working-tree bytes are labelled as such, not passed off as a clean archive.
    Paid canaries still require their original exact-HEAD archive guard.
    """
    root = root.resolve(strict=True)
    context, public_source = _whole_source_snapshot(root)
    commit = context["commit"]
    fixtures = {case_id: load_task_fixture(case_id, root=root / "bench/cases/runtime/fixtures") for case_id in TASK_CASE_IDS}
    for case_id, required in NAVIGATION_READS.items():
        source = dict(public_source)
        if any(path not in source or any(anchor not in source[path].decode("utf-8") for anchor in anchors) for path, anchors in required.items()):
            raise ValueError("navigation anchors do not match the frozen source")
        fixtures[case_id] = _snapshot_fixture(case_id, "navigation", commit, public_source,
                                             {"kind": "navigation_evidence", "required_reads": required,
                                              "source_deleted_paths": [path for path in context["deleted_files"] if path.startswith("src/doppel_agent/")],
                                              "human_rubric": ["Explain the requested behavior with precise source citations; a successful read is not explanation correctness."]},
                                             source_kind="working_tree_snapshot")
    public_sources, keys = freeze_review_inputs(root=root / "bench/cases/runtime/fixtures")
    for case_id in REVIEW_CASE_IDS:
        public, hidden = public_sources[case_id], keys[case_id]
        lines = public.decode("utf-8").splitlines()
        fixtures[case_id] = _snapshot_fixture(case_id, "known_answer_review", commit, (("service.py", public),),
                                             {"kind": "blind_review_read_evidence", "required_reads": {"service.py": [line for line in lines if line.strip()]},
                                              "human_rubric": ["Compare a blind review's file/line, trigger, impact and minimal fix with the external key; record false positives separately."]},
                                             (("answer_key.json", hidden),))
    if set(fixtures) != {case.case_id for case in contract.matrix.cases}:
        raise ValueError("full scripted fixtures must cover the original twenty cases")
    return tuple(fixtures[case.case_id] for case in contract.matrix.cases)


async def probe_readonly(fixture, mode, workspace, *, boundary, _contract=None):
    if fixture.category not in {"navigation", "known_answer_review"} or boundary not in {"direct_factory", "run_service"}:
        raise ValueError("unsupported read-only control profile/boundary")
    contract = _contract if _contract is not None else NativeTaskContract.load()
    case = contract.case(fixture, mode, boundary=boundary)
    materialize_task_case(fixture, workspace)
    initial = workspace_snapshot(workspace)
    # Public target paths only: no anchors, rubric, key or expected finding is
    # passed to the trusted read-control provider.
    paths = list(fixture.oracle["required_reads"])
    provider = ReadEvidenceProvider(ScriptedNavigationReader(paths, mode))
    runtime, service = None, None
    try:
        if boundary == "direct_factory":
            runtime = create_runtime(mode, workspace, provider, core_options={"max_steps": 16})
            async with asyncio.timeout(60):
                result = await runtime.run(RunRequest(case.prompt))
            status, actual, metadata = result.status, result.runtime, result.metadata
        else:
            service = RunService(workspace, provider=provider)
            await service.start()
            record, _ = await service.create({"mode": mode, "prompt": case.prompt, "effort": "deep",
                                              "permissions": dict(case.permissions), "deadline_seconds": 60})
            await service.scheduler.wait(record["run_id"])
            record = await service.get(record["run_id"])
            status, actual, metadata = record["status"], mode, record["metadata"]
        validation = validate_readonly_evidence(fixture, workspace, provider.reads)
        fallback = metadata.get("fallback_runtime")
        checks = {**validation["read_checks"], "protected_inventory_unchanged": validation["source_unchanged"],
                  "native_completion": status == "completed" and actual == mode and not fallback,
                  "no_side_effect_grants": case.permissions == {}}
        return {**validation, "case_id": fixture.case_id, "fixture_version": fixture.version, "fixture_sha256": fixture.sha256,
                "runtime": mode, "actual_runtime": fallback or actual, "fallback_runtime": fallback, "boundary": boundary,
                "permissions": dict(case.permissions), "prompt": case.prompt, "status": status, "checks": checks,
                "deterministic_pass": all(checks.values()), "initial": initial, "current": workspace_snapshot(workspace),
                "reads": [{"tool": "read_file", "path": row["path"], "success": row["success"],
                           "tool_call_id": row.get("tool_call_id"), "response_sha256": sha256(row["content"].encode()).hexdigest()}
                          for row in provider.reads], "human_rubric": thaw_json(fixture.oracle["human_rubric"]),
                "scope_note": "Whole-source navigation or blind-review source reads only; no review finding/explanation quality or paid model score is inferred."}
    finally:
        if service is not None:
            await service.close()
        if runtime is not None and mode == "deep":
            await runtime.process_supervisor.close()
