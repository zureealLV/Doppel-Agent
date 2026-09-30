"""Offline production-factory fixture audit; never a model-quality score."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import tempfile

from bench.runtime_fixtures import TASK_CASE_IDS, TaskFixture, load_task_fixture, materialize_task_case
from bench.runtime_matrix import RuntimeMatrix
from bench.runtime_tdd_harness import probe_tdd
from bench.runtime_patch_harness import probe_patch
from bench.runtime_validators import ReadEvidenceProvider, validate_navigation_evidence
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.base import RunRequest
from doppel_agent.runtime.factory import create_runtime


ROOT = Path(__file__).resolve().parents[1]
MODES = ("legacy", "graph", "deep")


class ScriptedNavigationReader:
    """Read public files, following native pagination, without receiving a rubric."""

    def __init__(self, paths: list[str], mode: str) -> None:
        self.paths = paths
        self.mode = mode
        self.turn = 0
        self.path_index = 0

    def next_turn(self, messages, tools):
        names = {tool["function"]["name"] for tool in tools}
        if "read_file" not in names or any(name.startswith("mcp__") for name in names):
            raise ValueError("navigation requires native read tools without an MCP grant")
        offset = 0
        if self.turn:
            latest = next(message for message in reversed(messages) if message.role == "tool")
            continuation = re.search(r"next offset (\d+) @@", latest.content) if self.mode == "deep" else None
            if continuation:
                offset = int(continuation[1])
            else:
                self.path_index += 1
        self.turn += 1
        if self.path_index < len(self.paths):
            arguments = {"file_path" if self.mode == "deep" else "path": self.paths[self.path_index]}
            if self.mode == "deep":
                arguments.update(offset=offset, limit=100)
            return ModelTurn(tool_calls=(ToolCall(str(self.turn), "read_file", arguments),))
        return ModelTurn(content="Scripted source reads completed. Explanation correctness is not adjudicated.")


async def probe_navigation(fixture: TaskFixture, mode: str, workspace: Path) -> dict:
    if fixture.category != "navigation" or mode not in MODES:
        raise ValueError("unsupported navigation probe")
    materialize_task_case(fixture, workspace)
    # Only the public path inventory reaches the scripted provider, not the
    # external expected anchors/rubric. No custom runtime tool is injected.
    provider = ReadEvidenceProvider(ScriptedNavigationReader([path for path, _ in fixture.public_files], mode))
    runtime = create_runtime(mode, workspace, provider, core_options={"max_steps": 16})
    try:
        result = await runtime.run(RunRequest("Find the MCP tool adapter and explain the policy boundary."))
        validation = validate_navigation_evidence(fixture, workspace, provider.reads)
        fallback = result.metadata.get("fallback_runtime")
        return {
            **validation, "runtime": mode, "actual_runtime": fallback or result.runtime, "boundary": "direct_factory",
            "fallback_runtime": fallback, "status": result.status,
            "deterministic_pass": validation["deterministic_pass"] and result.status == "completed"
            and result.runtime == mode and not fallback,
            "reads": [{"path": row["path"], "success": row["success"], "tool": "read_file",
                       "response_sha256": sha256(row["content"].encode()).hexdigest(),
                       "response_bytes": len(row["content"].encode())} for row in provider.reads],
        }
    finally:
        if mode == "deep":
            await runtime.process_supervisor.close()


async def audit() -> dict:
    matrix = RuntimeMatrix.load(ROOT / "bench/cases/runtime/manifest.json")
    fixtures = [load_task_fixture(case_id) for case_id in TASK_CASE_IDS]
    capabilities = {boundary: matrix.capability_document(ROOT / "bench/cases/runtime/capabilities.json", boundary=boundary)
                    for boundary in ("direct_factory", "run_service")}
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output([
        "git", "status", "--porcelain", "--", "src", "bench", "tests",
    ], cwd=ROOT, text=True).strip())
    with tempfile.TemporaryDirectory(prefix="doppel-task-fixtures-") as temporary:
        reports = []
        for fixture in fixtures:
            boundary = "direct_factory" if fixture.category == "navigation" else "run_service"
            modes = MODES if fixture.category == "navigation" else (("graph", "deep") if fixture.category == "multi_file_patch" else ("graph",))
            for mode in modes:
                key = f"{fixture.case_id}:{mode}:1"
                if key not in capabilities[boundary]["supported_run_keys"]:
                    raise ValueError("fixture probe is unsupported by its frozen production boundary")
                workspace = Path(temporary) / fixture.case_id / mode
                if fixture.category == "navigation":
                    result = await probe_navigation(fixture, mode, workspace)
                elif fixture.category == "multi_file_patch":
                    result = await probe_patch(fixture, mode, workspace)
                else:
                    result = await probe_tdd(fixture, workspace)
                reports.append({**result, "run_key": key})
    return {
        "schema_version": "1.2", "recorded_at": datetime.now(UTC).isoformat(),
        "source_commit": commit, "evaluation_source_dirty": dirty,
        "protocol_version": matrix.protocol_version, "manifest_sha256": matrix.manifest_sha256,
        "provider": "local-scripted-reference-controls-no-network", "boundary": "explicit_per_run",
        "fixture_contexts": {fixture.case_id: {"source_kind": fixture.source_kind, "source_baseline_commit": fixture.source_commit,
                                                "fixture_version": fixture.version, "fixture_sha256": fixture.sha256} for fixture in fixtures},
        "original_run_count": 180,
        "eligible_denominators": {boundary: data["supported_run_count"] for boundary, data in capabilities.items()},
        "fixture_cases": list(TASK_CASE_IDS), "runs": reports,
        "passed": all(report["deterministic_pass"] for report in reports),
        "full_matrix_ready": False, "task_quality_scored": False,
        "scope_note": "Ready fixture controls only, one repeat; per-run factory/service boundaries, not a pooled quality rate. Live 9/12 canaries unchanged; full mode blocked.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(audit())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "fixture_cases": report["fixture_cases"],
                      "full_matrix_ready": False, "task_quality_scored": False}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
