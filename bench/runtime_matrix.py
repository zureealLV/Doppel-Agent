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
CAPABILITY_FEATURES = frozenset({
    "workspace_read", "reviewed_patch", "command_execute", "post_patch_verification",
    "durable_resume", "mcp_gateway", "cancellation", "process_tree_cleanup",
})
CAPABILITY_BOUNDARIES = ("direct_factory", "run_service")
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
    manifest_sha256: str = ""

    @classmethod
    def load(cls, path: Path) -> "RuntimeMatrix":
        raw_manifest = path.read_bytes()
        payload = json.loads(raw_manifest)
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
        matrix = cls(str(payload["protocol_version"]), tuple(cases), sha256(raw_manifest).hexdigest())
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

    def capability_document(self, path: Path, *, boundary: str) -> dict[str, Any]:
        """Freeze eligibility, including excluded keys; never unlock live full mode."""
        if boundary not in CAPABILITY_BOUNDARIES:
            raise ValueError("unknown capability boundary")
        raw = path.read_bytes()
        contract = json.loads(raw)
        if not isinstance(contract, dict) or set(contract) != {
            "contract_version", "protocol_sha256", "boundaries", "cases",
        } or contract["contract_version"] != "1.0":
            raise ValueError("unsupported capability contract schema/version")
        # The original manifest serialization is intentionally preserved by load().
        if contract["protocol_sha256"] != self.manifest_sha256:
            raise ValueError("capability contract does not match frozen manifest")
        boundaries = contract["boundaries"]
        if not isinstance(boundaries, dict) or set(boundaries) != set(CAPABILITY_BOUNDARIES):
            raise ValueError("capability contract must cover both execution boundaries")
        for runtimes in boundaries.values():
            if not isinstance(runtimes, dict) or set(runtimes) != set(RUNTIMES):
                raise ValueError("capability contract must cover all runtimes")
            for features in runtimes.values():
                if not isinstance(features, dict) or set(features) != CAPABILITY_FEATURES:
                    raise ValueError("capability contract must cover all features")
                for entry in features.values():
                    if not isinstance(entry, dict) or set(entry) != {"supported", "grants", "reason"}:
                        raise ValueError("invalid capability entry")
                    if type(entry["supported"]) is not bool or not isinstance(entry["reason"], str) or not entry["reason"].strip():
                        raise ValueError("capability support must be boolean with a reason")
                    if not isinstance(entry["grants"], list) or any(
                        not isinstance(grant, str) or grant not in {"workspace_write", "command_execute", "mcp_execute"}
                        for grant in entry["grants"]
                    ) or len(set(entry["grants"])) != len(entry["grants"]):
                        raise ValueError("invalid capability permission grants")
        cases = contract["cases"]
        if not isinstance(cases, dict) or set(cases) != {case.case_id for case in self.cases}:
            raise ValueError("capability contract must cover every case exactly")
        for case in cases.values():
            if not isinstance(case, dict) or set(case) != {"requires", "note"}:
                raise ValueError("invalid case capability requirements")
            requirements = case["requires"]
            if not isinstance(requirements, list) or not requirements or any(
                not isinstance(feature, str) or feature not in CAPABILITY_FEATURES
                for feature in requirements
            ) or len(set(requirements)) != len(requirements):
                raise ValueError("unknown or duplicate case capability requirement")
            if not isinstance(case["note"], str) or not case["note"].strip():
                raise ValueError("case capability requirement needs a scope note")
        supported = []
        excluded = []
        for run in self.expand():
            reasons = []
            for feature in cases[run.case_id]["requires"]:
                entry = boundaries[boundary][run.runtime][feature]
                if not entry["supported"]:
                    reasons.append(f"{feature}: {entry['reason']}")
                else:
                    missing = [grant for grant in entry["grants"] if not run.permissions.get(grant)]
                    if missing:
                        reasons.append(f"{feature}: required grants absent: {', '.join(missing)}")
            if reasons:
                excluded.append({"run_key": run.run_key, "reasons": reasons})
            else:
                supported.append(run.run_key)
        return {
            "contract_version": contract["contract_version"],
            "contract_sha256": sha256(raw).hexdigest(),
            "protocol_sha256": self.manifest_sha256,
            "boundary": boundary,
            "original_run_count": len(self.expand()),
            "supported_run_count": len(supported),
            "excluded_run_count": len(excluded),
            "supported_run_keys": supported,
            "excluded_runs": excluded,
            "case_requirements": cases,
            "full_matrix_ready": False,
            "task_quality_scored": False,
            "note": "Capability eligibility only, not seeded-fixture or quality acceptance. Unsupported keys are not model failures.",
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
    parser.add_argument("--capabilities", type=Path, default=Path(__file__).parent / "cases/runtime/capabilities.json")
    parser.add_argument("--boundary", choices=CAPABILITY_BOUNDARIES)
    parser.add_argument(
        "--offline-smoke",
        action="store_true",
        help="execute all 180 runtime paths with MockProvider; task scores remain null",
    )
    args = parser.parse_args()
    matrix = RuntimeMatrix.load(args.manifest)
    if args.boundary and args.offline_smoke:
        parser.error("capability eligibility is separate from unscored Mock plumbing")
    document = matrix.capability_document(args.capabilities, boundary=args.boundary) if args.boundary else (
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
