"""Fixed, reproducible protocol for comparing Doppel's three runtimes.

The module deliberately separates protocol expansion from execution.  Loading the
manifest never invents scores; runners must record every run, including failures.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any


RUNTIMES = ("legacy", "graph", "deep")
REPEATS = (1, 2, 3)
EXPECTED_CATEGORY_COUNTS = {
    "navigation": 4,
    "known_answer_review": 4,
    "tdd_fix": 4,
    "multi_file_patch": 3,
    "approval_resume": 2,
    "mcp_workflow": 2,
    "concurrent_cancel": 1,
}


@dataclass(frozen=True)
class RuntimeCase:
    case_id: str
    category: str
    prompt: str
    permissions: dict[str, bool]
    validator: dict[str, str]


@dataclass(frozen=True)
class MatrixRun:
    case_id: str
    category: str
    runtime: str
    repeat: int
    prompt: str
    permissions: dict[str, bool]
    validator: dict[str, str]

    @property
    def run_key(self) -> str:
        return f"{self.case_id}:{self.runtime}:{self.repeat}"

    @property
    def deterministic_run_id(self) -> str:
        return sha256(self.run_key.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class RuntimeMatrix:
    protocol_version: str
    cases: tuple[RuntimeCase, ...]

    @classmethod
    def load(cls, path: Path) -> "RuntimeMatrix":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if set(payload) != {"protocol_version", "cases"}:
            raise ValueError("runtime matrix manifest contains unsupported top-level fields")
        raw_cases = payload["cases"]
        if not isinstance(raw_cases, list):
            raise ValueError("runtime matrix cases must be a list")
        cases: list[RuntimeCase] = []
        seen: set[str] = set()
        for raw in raw_cases:
            if set(raw) != {"id", "category", "prompt", "permissions", "validator"}:
                raise ValueError("runtime matrix case has unsupported fields")
            case_id = str(raw["id"])
            if not case_id or case_id in seen:
                raise ValueError("runtime matrix case ids must be non-empty and unique")
            seen.add(case_id)
            category = str(raw["category"])
            prompt = str(raw["prompt"]).strip()
            if category not in EXPECTED_CATEGORY_COUNTS or not prompt:
                raise ValueError(f"invalid runtime matrix case: {case_id}")
            permissions = raw["permissions"]
            validator = raw["validator"]
            if not isinstance(permissions, dict) or not all(
                isinstance(key, str) and isinstance(value, bool)
                for key, value in permissions.items()
            ):
                raise ValueError(f"invalid permissions for case: {case_id}")
            if (
                not isinstance(validator, dict)
                or validator.get("type") not in {"answer_contains", "event_present"}
                or not isinstance(validator.get("value"), str)
                or not validator["value"]
            ):
                raise ValueError(f"invalid validator for case: {case_id}")
            cases.append(
                RuntimeCase(case_id, category, prompt, dict(permissions), dict(validator))
            )
        matrix = cls(str(payload["protocol_version"]), tuple(cases))
        if matrix.category_counts != EXPECTED_CATEGORY_COUNTS:
            raise ValueError(
                f"runtime matrix categories must equal {EXPECTED_CATEGORY_COUNTS}, "
                f"got {matrix.category_counts}"
            )
        return matrix

    @property
    def category_counts(self) -> dict[str, int]:
        counts = Counter(case.category for case in self.cases)
        return {name: counts.get(name, 0) for name in EXPECTED_CATEGORY_COUNTS}

    def expand(self) -> tuple[MatrixRun, ...]:
        return tuple(
            MatrixRun(
                case.case_id,
                case.category,
                runtime,
                repeat,
                case.prompt,
                dict(case.permissions),
                dict(case.validator),
            )
            for case in self.cases
            for runtime in RUNTIMES
            for repeat in REPEATS
        )

    def protocol_document(self) -> dict[str, Any]:
        runs = self.expand()
        return {
            "protocol_version": self.protocol_version,
            "case_count": len(self.cases),
            "runtimes": list(RUNTIMES),
            "repeats": len(REPEATS),
            "planned_run_count": len(runs),
            "category_counts": self.category_counts,
            "run_keys": [run.run_key for run in runs],
            "results": None,
            "note": "Protocol only. No success or cost claim is implied until all runs execute.",
        }


class _RecordingSink:
    def __init__(self) -> None:
        self.types: list[str] = []

    async def emit(self, kind: str, **payload: Any) -> None:
        self.types.append(kind)


async def run_offline_smoke(
    matrix: RuntimeMatrix,
    selected_runs: Iterable[MatrixRun] | None = None,
) -> dict[str, Any]:
    """Execute runtime plumbing with MockProvider without claiming task quality."""
    from time import perf_counter

    from doppel_agent.concurrency import ResourceLimits
    from doppel_agent.provider import MockProvider
    from doppel_agent.runtime.base import RunRequest
    from doppel_agent.runtime.factory import create_runtime
    from doppel_agent.runtime.provider_adapter import ProviderAdapter

    planned = tuple(selected_runs or matrix.expand())
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="doppel-runtime-matrix-") as directory:
        workspace = Path(directory)
        (workspace / "README.md").write_text("Doppel runtime matrix fixture\n", encoding="utf-8")
        state_root = workspace / ".doppel-agent"
        state_root.mkdir()
        runtimes: dict[str, Any] = {}
        for mode in RUNTIMES:
            limits = ResourceLimits()
            provider: Any = MockProvider()
            if mode != "legacy":
                provider = ProviderAdapter(provider, profile_id="offline-matrix", limits=limits)
            runtimes[mode] = create_runtime(
                mode,
                workspace,
                provider,
                state_root=state_root,
                core_options={"max_steps": 4},
                resource_limits=limits,
            )
        for run in planned:
            sink = _RecordingSink()
            started = perf_counter()
            error = ""
            status = "failed"
            try:
                result = await runtimes[run.runtime].run(
                    RunRequest(
                        run.prompt,
                        run_id=run.deterministic_run_id,
                        thread_id=run.deterministic_run_id,
                    ),
                    sink,
                )
                status = result.status
            except Exception as exc:  # noqa: BLE001 - benchmark must retain every failure
                error = f"{type(exc).__name__}: {exc}"
            results.append(
                {
                    "run_key": run.run_key,
                    "runtime": run.runtime,
                    "case_id": run.case_id,
                    "repeat": run.repeat,
                    "status": status,
                    "wall_seconds": round(perf_counter() - started, 6),
                    "event_count": len(sink.types),
                    "error": error,
                    "task_score": None,
                }
            )
        deep = runtimes.get("deep")
        if deep is not None:
            await deep.process_supervisor.close()
    completed = sum(item["status"] == "completed" for item in results)
    return {
        "schema_version": "1.0",
        "provider": "offline-mock",
        "planned_runs": len(planned),
        "completed_runtime_paths": completed,
        "failed_runtime_paths": len(results) - completed,
        "runs": results,
        "task_quality_scored": False,
        "scope_note": (
            "This report validates runtime plumbing only. MockProvider does not measure coding "
            "quality, model cost, token efficiency, or validator success."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and expand the fixed runtime matrix")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).parent / "cases" / "runtime" / "manifest.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--offline-smoke",
        action="store_true",
        help="execute all 180 runtime paths with MockProvider; task scores remain null",
    )
    args = parser.parse_args()
    matrix = RuntimeMatrix.load(args.manifest)
    document = (
        asyncio.run(run_offline_smoke(matrix)) if args.offline_smoke else matrix.protocol_document()
    )
    rendered = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
