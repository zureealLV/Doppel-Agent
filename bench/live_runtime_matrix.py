"""Fail-closed live matrix runner; only grounded read-only canary cases are enabled.

This is infrastructure evidence, not a task-quality benchmark. In particular,
the legacy/graph/deep runtimes do not yet have equivalent write/MCP capabilities.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from zipfile import ZipFile

from bench.runtime_fixtures import FIXTURE_ROOT, REVIEW_CASE_IDS, materialize_review_case
from bench.runtime_matrix import MatrixRun, RuntimeMatrix
from doppel_agent.provider import (
    OpenAICompatibleProvider,
    ProviderCircuitOpen,
    ProviderRequestError,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "bench" / "cases" / "runtime" / "manifest.json"
CANARY_CASE_IDS = ("nav-01", "nav-02", "nav-03")
_SAFE_KEY = re.compile(r"[a-z0-9-]+:(legacy|graph|deep):[123]\Z")


def select_runs(matrix: RuntimeMatrix, mode: str) -> tuple[MatrixRun, ...]:
    """Full mode stays blocked until every case has a seeded, runnable fixture."""
    if mode == "canary":
        selected = tuple(
            run for run in matrix.expand()
            if run.case_id in CANARY_CASE_IDS and run.repeat == 1
        )
        if len(selected) != 9 or any(run.permissions for run in selected):
            raise ValueError("canary fixture protocol changed; re-audit before live use")
        return selected
    if mode == "review-canary":
        selected = tuple(
            run for run in matrix.expand()
            if run.case_id in REVIEW_CASE_IDS and run.repeat == 1
        )
        if len(selected) != 12 or any(run.permissions for run in selected):
            raise ValueError("review fixture protocol changed; re-audit before live use")
        return selected
    if mode == "full":
        raise ValueError(
            "full matrix requires seeded fixtures and equivalent runtime capabilities "
            "for review, patch, approval, MCP, and cancellation cases"
        )
    raise ValueError(f"unsupported matrix mode: {mode}")


def classify_failure(error: BaseException) -> str:
    if isinstance(error, ProviderCircuitOpen):
        return "provider_circuit_open"
    if isinstance(error, ProviderRequestError):
        if error.kind == "rate_limited":
            return "provider_rate_limit"
        if error.kind == "timeout":
            return "provider_timeout"
        if error.kind == "connection":
            return "provider_connection"
        if error.status_code in (401, 403):
            return "provider_auth"
        return "provider_http"
    if isinstance(error, asyncio.TimeoutError):
        return "runtime_timeout"
    return "internal_error"


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class ResultStore:
    """One immutable result per run key; resume requires identical protocol/config."""

    def __init__(self, root: Path, configuration: dict[str, Any]) -> None:
        self.root = root.resolve()
        self.runs = self.root / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        context = self.root / "context.json"
        if context.exists():
            if json.loads(context.read_text(encoding="utf-8")) != configuration:
                raise ValueError("existing report configuration differs; choose a new output directory")
        else:
            _write_json_atomic(context, configuration)

    def _path(self, run_key: str) -> Path:
        if not _SAFE_KEY.fullmatch(run_key):
            raise ValueError("invalid run key")
        return self.runs / f"{run_key.replace(':', '__')}.json"

    def load(self, run_key: str) -> dict[str, Any] | None:
        path = self._path(run_key)
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("run_key") != run_key:
            raise ValueError("stored run key does not match filename")
        return value

    def save(self, run_key: str, value: dict[str, Any]) -> None:
        if value.get("run_key") != run_key or self.load(run_key) is not None:
            raise ValueError("run result already exists or has a different key")
        _write_json_atomic(self._path(run_key), value)


def _priced_cost(input_tokens: int, output_tokens: int, input_price: float, output_price: float) -> float:
    return round((input_tokens * input_price + output_tokens * output_price) / 1_000_000, 9)


async def execute_runs(
    runs: Sequence[MatrixRun],
    store: ResultStore,
    runner: Callable[[MatrixRun], Awaitable[dict[str, Any]]],
    *,
    input_price: float,
    output_price: float,
    max_cost_usd: float,
) -> list[dict[str, Any]]:
    """Execute serially; unknown usage halts further paid calls, including on resume.

    The cost threshold is checked *between* runs. A single run can exceed it;
    it is not a provider-side hard spending cap.
    """
    if min(input_price, output_price, max_cost_usd) <= 0:
        raise ValueError("positive input/output prices and max_cost_usd are required")
    results: list[dict[str, Any]] = []
    spent = 0.0
    for run in runs:
        previous = store.load(run.run_key)
        if previous is not None:
            results.append(previous)
            if previous.get("cost_usd") is None:
                raise RuntimeError("usage unavailable for a prior run; paid execution stopped")
            spent += previous["cost_usd"]
            continue
        if spent >= max_cost_usd:
            break
        started = datetime.now(timezone.utc).isoformat()
        try:
            observation = await runner(run)
            status = str(observation["status"])
            failure_class = observation.get("failure_class")
            if status != "completed" and not failure_class:
                failure_class = "runtime_failed"
            # Legacy Core turns caught exception text into its "answer". Never
            # persist that text for a failed run: transports may include secrets.
            answer = str(observation.get("answer", "")) if status == "completed" else ""
            events = list(observation.get("event_types") or [])
            wall_seconds = float(observation["wall_seconds"])
            input_tokens = observation.get("input_tokens")
            output_tokens = observation.get("output_tokens")
        except Exception as error:  # noqa: BLE001 - every paid attempt must be recorded
            status = "failed"
            failure_class = classify_failure(error)
            answer, events, wall_seconds = "", [], None
            input_tokens = output_tokens = None
        usage_known = (
            isinstance(input_tokens, int) and not isinstance(input_tokens, bool)
            and isinstance(output_tokens, int) and not isinstance(output_tokens, bool)
            and input_tokens >= 0 and output_tokens >= 0
        )
        if not usage_known:
            failure_class = "usage_unavailable" if status == "completed" else failure_class
        cost = (
            _priced_cost(input_tokens, output_tokens, input_price, output_price)
            if usage_known else None
        )
        record = {
            "run_key": run.run_key,
            "case_id": run.case_id,
            "runtime": run.runtime,
            "repeat": run.repeat,
            "run_id": run.deterministic_run_id,
            "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "failure_class": failure_class,
            "answer": answer,
            "event_types": events,
            "wall_seconds": wall_seconds,
            "input_tokens": input_tokens if usage_known else None,
            "output_tokens": output_tokens if usage_known else None,
            "cost_usd": cost,
            "fixture_sha256": observation.get("fixture_sha256") if usage_known and status == "completed" else None,
            "validator_signal": (
                run.validator["value"].lower() in answer.lower()
                if run.validator["type"] == "answer_contains" else
                run.validator["value"] in events
            ),
            "human_review": "pending",
        }
        store.save(run.run_key, record)
        results.append(record)
        if cost is None:
            raise RuntimeError("usage unavailable; paid execution stopped after recording the run")
        spent += cost
    return results


def summarize(runs: Sequence[MatrixRun], results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "planned_runs": len(runs),
        "recorded_runs": len(results),
        "complete_denominator": len(results) == len(runs),
        "statuses": dict(Counter(item["status"] for item in results)),
        "failure_classes": dict(Counter(item["failure_class"] for item in results if item["failure_class"])),
        "reported_cost_usd": round(sum(item["cost_usd"] or 0 for item in results), 9),
        "unknown_cost_runs": sum(item["cost_usd"] is None for item in results),
        "task_quality_scored": False,
        "human_review_pending": len(results),
        "scope_note": (
            "Runtime completion and keyword signals are not task-quality success. "
            "This read-only canary is not the 20x3x3 matrix."
        ),
    }


def review_queue(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Keep adjudication separate from weak automatic keyword/event signals."""
    return {
        "schema_version": "1.0",
        "items": [
            {
                "run_key": item["run_key"],
                "status": item["status"],
                "failure_class": item["failure_class"],
                "validator_signal": item["validator_signal"],
                "answer": item["answer"],
                "human_review": item["human_review"],
            }
            for item in results
        ],
        "note": "Human verdicts belong in a separate adjudication file; do not mutate raw runs.",
    }


class _UsageProvider:
    def __init__(self, provider: OpenAICompatibleProvider) -> None:
        self.provider = provider
        self.input_tokens = 0
        self.output_tokens = 0
        self.usage_complete = True

    def next_turn(self, messages: Any, tools: Any) -> Any:
        try:
            turn = self.provider.next_turn(messages, tools)
        except Exception:
            # A request may have been billed before the transport failed.
            self.usage_complete = False
            raise
        usage = turn.usage or {}
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        if not isinstance(prompt, int) or not isinstance(completion, int):
            self.usage_complete = False
        else:
            self.input_tokens += prompt
            self.output_tokens += completion
        return turn


class _EventSink:
    def __init__(self) -> None:
        self.types: list[str] = []

    async def emit(self, kind: str, **payload: Any) -> None:
        self.types.append(kind)


def _git_snapshot() -> tuple[str, bytes]:
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True)
    if status.stdout.strip():
        raise ValueError("live evaluation requires a clean Git worktree")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    archive = subprocess.run(
        ["git", "archive", "--format=zip", "HEAD", "src/doppel_agent"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout
    return commit, archive


def _extract_snapshot(archive: bytes, destination: Path) -> None:
    with ZipFile(io.BytesIO(archive)) as source:
        for member in source.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("archive path escapes benchmark workspace")
        source.extractall(destination)


async def _run_navigation_case(
    run: MatrixRun, *, archive: bytes, base_url: str, model: str, api_key: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="doppel-live-navigation-") as directory:
        workspace = Path(directory) / "workspace"
        workspace.mkdir()
        _extract_snapshot(archive, workspace)
        return await _run_prepared_case(run, workspace, base_url, model, api_key)


async def _run_review_case(
    run: MatrixRun, *, public_source: bytes, base_url: str, model: str, api_key: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="doppel-live-review-") as directory:
        workspace = Path(directory) / "workspace"
        digest = materialize_review_case(run.case_id, workspace, public_source=public_source)
        result = await _run_prepared_case(run, workspace, base_url, model, api_key)
        result["fixture_sha256"] = digest
        return result


async def _run_prepared_case(
    run: MatrixRun, workspace: Path, base_url: str, model: str, api_key: str,
) -> dict[str, Any]:
    from doppel_agent.concurrency import ResourceLimits
    from doppel_agent.runtime.base import RunRequest
    from doppel_agent.runtime.factory import create_runtime
    from doppel_agent.runtime.provider_adapter import ProviderAdapter

    state_root = workspace / ".doppel-agent"
    state_root.mkdir()
    provider = _UsageProvider(OpenAICompatibleProvider(base_url, model, api_key, timeout=120, temperature=0))
    runtime_provider: Any = provider
    if run.runtime != "legacy":
        runtime_provider = ProviderAdapter(provider, profile_id="live-canary", limits=ResourceLimits())
    runtime = create_runtime(
        run.runtime, workspace, runtime_provider,
        state_root=state_root, core_options={"max_steps": 8},
    )
    sink = _EventSink()
    started = perf_counter()
    try:
        result = await runtime.run(
            RunRequest(run.prompt, run_id=run.deterministic_run_id,
                       thread_id=run.deterministic_run_id), sink,
        )
        return {
            "status": result.status,
            "answer": result.answer,
            "event_types": sink.types,
            "wall_seconds": round(perf_counter() - started, 6),
            "input_tokens": provider.input_tokens if provider.usage_complete else None,
            "output_tokens": provider.output_tokens if provider.usage_complete else None,
            "failure_class": (
                "deep_fallback" if result.metadata.get("fallback_runtime") else None
            ),
        }
    finally:
        if run.runtime == "deep":
            await runtime.process_supervisor.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Grounded, resumable read-only live canary")
    parser.add_argument("--preflight", action="store_true", help="inspect protocol without paid calls")
    parser.add_argument("--mode", choices=("canary", "review-canary", "full"), default="canary")
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--input-price-per-million", type=float)
    parser.add_argument("--output-price-per-million", type=float)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    matrix = RuntimeMatrix.load(MANIFEST)
    if args.preflight:
        print(json.dumps({
            "canary_run_count": len(select_runs(matrix, "canary")),
            "canary_cases": list(CANARY_CASE_IDS),
            "review_canary_run_count": len(select_runs(matrix, "review-canary")),
            "review_cases": list(REVIEW_CASE_IDS),
            "full_matrix_ready": False,
            "full_blocker": "remaining navigation/write/approval/MCP/cancel fixtures and runtime parity absent",
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        runs = select_runs(matrix, args.mode)
        if not all((args.base_url, args.model, args.input_price_per_million,
                    args.output_price_per_million, args.max_cost_usd)):
            raise ValueError("base URL, model, token prices and max cost are required")
        parsed = urlparse(args.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base URL must not contain credentials, query or fragment")
        key = os.environ.get("DOPPEL_AGENT_API_KEY", "")
        if not key:
            raise ValueError("DOPPEL_AGENT_API_KEY is not configured")
        commit, archive = _git_snapshot()
        review_sources = {
            case_id: (FIXTURE_ROOT / case_id / "public" / "service.py").read_bytes()
            for case_id in REVIEW_CASE_IDS
        }
        config = {
            "schema_version": "1.0",
            "protocol_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
            "commit": commit,
            "snapshot_sha256": hashlib.sha256(archive).hexdigest(),
            "review_fixture_sha256": {
                case_id: hashlib.sha256(payload).hexdigest()
                for case_id, payload in review_sources.items()
            },
            "mode": args.mode,
            "base_url_host": parsed.netloc,
            "base_url_sha256": hashlib.sha256(args.base_url.encode("utf-8")).hexdigest(),
            "model": args.model,
            "temperature": 0,
            "input_price_per_million": args.input_price_per_million,
            "output_price_per_million": args.output_price_per_million,
            "max_cost_usd": args.max_cost_usd,
        }
        output_dir = args.output_dir or ROOT / ".bench-results" / f"live-{args.mode}"
        store = ResultStore(output_dir, config)
        async def runner(run: MatrixRun) -> dict[str, Any]:
            if args.mode == "review-canary":
                return await _run_review_case(
                    run, public_source=review_sources[run.case_id],
                    base_url=args.base_url, model=args.model, api_key=key,
                )
            return await _run_navigation_case(
                run, archive=archive, base_url=args.base_url, model=args.model, api_key=key,
            )
        try:
            asyncio.run(execute_runs(
                runs, store, runner, input_price=args.input_price_per_million,
                output_price=args.output_price_per_million, max_cost_usd=args.max_cost_usd,
            ))
        finally:
            recorded = [store.load(run.run_key) for run in runs]
            observed = [item for item in recorded if item is not None]
            report = summarize(runs, observed)
            _write_json_atomic(store.root / "summary.json", report)
            _write_json_atomic(store.root / "review_queue.json", review_queue(observed))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["complete_denominator"] else 2
    except (ValueError, RuntimeError) as error:
        parser.exit(2, f"live matrix stopped: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
