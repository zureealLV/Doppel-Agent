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
import math
import os
import platform
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from zipfile import ZipFile
from uuid import uuid4

from bench.runtime_fixtures import REVIEW_CASE_IDS, freeze_review_inputs, materialize_review_case
from bench.runtime_matrix import MatrixRun, RuntimeMatrix
from bench.runtime_freeze import canonical_json
from bench.cny_budget import BudgetStopped, CnyBudgetLedger
from doppel_agent.provider import (
    OpenAICompatibleProvider,
    ProviderCircuitOpen,
    ProviderRequestError,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "bench" / "cases" / "runtime" / "manifest.json"
CAPABILITIES = MANIFEST.with_name("capabilities.json")
CNY_BUDGET_PATH = ROOT / ".bench-results" / "deepseek-cny-shared.sqlite3"
CANARY_CASE_IDS = ("nav-01", "nav-02", "nav-03")
RESULT_SCHEMA_VERSION = "1.2"
_SAFE_KEY = re.compile(r"[a-z0-9-]+:(legacy|graph|deep):[123]\Z")


class EvaluationProviderStopped(RuntimeError):
    """Sticky evaluation-only stop; contains no transport text or credentials."""

    def __init__(self, failure_class: str) -> None:
        self.failure_class = failure_class
        super().__init__(f"evaluation provider stopped: {failure_class}")


def _is_token_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_prices(input_price: Any, output_price: Any, max_cost_usd: Any) -> None:
    if any(not _is_finite_number(value) or value <= 0
           for value in (input_price, output_price, max_cost_usd)):
        raise ValueError("positive finite input/output prices and max_cost_usd are required")


def validate_cny_price_snapshot(today: str | None = None) -> None:
    """Fail closed on a new Shanghai date; reverify official bounds before reuse."""
    today = today or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    if today != CnyBudgetLedger.price_checked_date:
        raise ValueError("CNY price snapshot expired; reverify official prices/context bounds before paid calls")


def freeze_capability_selection(
    matrix: RuntimeMatrix,
    runs: Sequence[MatrixRun],
    path: Path = CAPABILITIES,
) -> dict[str, Any]:
    """Paid direct-factory execution must preserve its exact eligible run keys."""
    report = matrix.capability_document(path, boundary="direct_factory")
    keys = [run.run_key for run in runs]
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("live capability selection must be nonempty and unique")
    originals = {run.run_key: run for run in matrix.expand()}
    supported = set(report["supported_run_keys"])
    if any(run.run_key not in supported or originals.get(run.run_key) != run for run in runs):
        raise ValueError("live capability selection contains unsupported or modified runs")
    return {**report, "selected_run_count": len(keys), "selected_run_keys": keys}


def select_runs(matrix: RuntimeMatrix, mode: str) -> tuple[MatrixRun, ...]:
    """Full mode stays blocked until every case has a seeded, runnable fixture."""
    if mode == "canary":
        selected = tuple(
            run for run in matrix.expand()
            if run.case_id in CANARY_CASE_IDS and run.repeat == 1
        )
        if len(selected) != 9 or any(run.permissions for run in selected):
            raise ValueError("canary fixture protocol changed; re-audit before live use")
        freeze_capability_selection(matrix, selected)
        return selected
    if mode == "review-canary":
        selected = tuple(
            run for run in matrix.expand()
            if run.case_id in REVIEW_CASE_IDS and run.repeat == 1
        )
        if len(selected) != 12 or any(run.permissions for run in selected):
            raise ValueError("review fixture protocol changed; re-audit before live use")
        freeze_capability_selection(matrix, selected)
        return selected
    if mode == "full":
        raise ValueError(
            "full matrix requires seeded fixtures and equivalent runtime capabilities "
            "for review, patch, approval, MCP, and cancellation cases"
        )
    raise ValueError(f"unsupported matrix mode: {mode}")


def classify_failure(error: BaseException) -> str:
    if isinstance(error, BudgetStopped):
        return error.failure_class
    if isinstance(error, EvaluationProviderStopped):
        return error.failure_class
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
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    """Publish one complete immutable file without overwriting a competing run.

    Same-directory hard-link publication is atomic/create-only on the local
    NTFS/ext4 targets. Unsupported filesystems fail closed, not via overwrite.
    """
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(canonical_json(value))
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ResultStore:
    """One immutable result per run key; resume requires identical protocol/config."""

    def __init__(self, root: Path, configuration: dict[str, Any]) -> None:
        self.root = root.resolve()
        self.runs = self.root / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self._configuration_bytes = canonical_json(configuration)
        context = self.root / "context.json"
        if context.exists():
            self.assert_configuration()
        else:
            try:
                _write_json_exclusive(context, json.loads(self._configuration_bytes))
            except FileExistsError:
                self.assert_configuration()

    def assert_configuration(self) -> None:
        if canonical_json(json.loads((self.root / "context.json").read_bytes())) != self._configuration_bytes:
            raise ValueError("existing report configuration differs; choose a new output directory")

    def assert_prices(self, input_price, output_price, max_cost_usd) -> None:
        self.assert_configuration()
        configuration = json.loads(self._configuration_bytes)
        for key, value in (("input_price_per_million", input_price), ("output_price_per_million", output_price), ("max_cost_usd", max_cost_usd)):
            if key in configuration and canonical_json(configuration[key]) != canonical_json(value):
                raise ValueError("execution prices/budget differ from frozen report configuration")

    def _path(self, run_key: str) -> Path:
        if not _SAFE_KEY.fullmatch(run_key):
            raise ValueError("invalid run key")
        return self.runs / f"{run_key.replace(':', '__')}.json"

    def load(self, run_key: str) -> dict[str, Any] | None:
        self.assert_configuration()
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
        try:
            _write_json_exclusive(self._path(run_key), value)
        except FileExistsError as exc:
            raise ValueError("run result already exists") from exc


def _priced_cost(input_tokens: int, output_tokens: int, input_price: float, output_price: float) -> float:
    cost = float((Decimal(input_tokens) * Decimal(str(input_price))
                  + Decimal(output_tokens) * Decimal(str(output_price))) / Decimal(1_000_000))
    if not math.isfinite(cost):
        raise ValueError("cost cannot be represented as a finite number")
    return round(cost, 9)


async def execute_runs(
    runs: Sequence[MatrixRun],
    store: ResultStore,
    runner: Callable[[MatrixRun], Awaitable[dict[str, Any]]],
    *,
    input_price: float | None = None,
    output_price: float | None = None,
    max_cost_usd: float | None = None,
    budget: CnyBudgetLedger | None = None,
) -> list[dict[str, Any]]:
    """Execute serially; unknown usage halts further paid calls, including on resume.

    Legacy USD mode checks between runs, NOT a hard cap. CNY mode uses a shared
    durable per-request reservation in the transport wrapper and run claims.
    """
    if budget is None:
        validate_prices(input_price, output_price, max_cost_usd)
        store.assert_prices(input_price, output_price, max_cost_usd)
    else:
        if any(value is not None for value in (input_price, output_price, max_cost_usd)):
            raise ValueError("CNY budget cannot be combined with USD execution prices")
        store.assert_configuration()
        if json.loads(store._configuration_bytes).get("cny_budget") != budget.configuration:
            raise ValueError("CNY budget differs from frozen report configuration")
    runs = tuple(runs)
    results: list[dict[str, Any]] = []
    spent = 0.0
    for run in runs:
        previous = store.load(run.run_key)
        if previous is not None:
            results.append(previous)
            if budget is not None:
                cost, prompt, completion = budget.run_account(run.run_key)
                if (cost is None or previous.get("currency") != "CNY"
                        or previous.get("cost_cny_upper_bound") != str(cost)
                        or previous.get("input_tokens") != prompt or previous.get("output_tokens") != completion):
                    raise RuntimeError("CNY prior result and request ledger differ; paid execution stopped")
                continue
            if previous.get("cost_usd") is None:
                raise RuntimeError("usage or cost unavailable for a prior run; paid execution stopped")
            if not _is_finite_number(previous["cost_usd"]) or previous["cost_usd"] < 0:
                raise ValueError("invalid prior cost; paid execution stopped")
            spent += previous["cost_usd"]
            continue
        if budget is not None:
            budget.claim_run(run.run_key)
        elif spent >= max_cost_usd:
            break
        started = datetime.now(timezone.utc).isoformat()
        timer = perf_counter()
        observation: dict[str, Any] = {}
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
            answer, events = "", []
            wall_seconds = round(perf_counter() - timer, 6)
            input_tokens = output_tokens = None
        usage_known = _is_token_count(input_tokens) and _is_token_count(output_tokens)
        if not usage_known:
            failure_class = "usage_unavailable" if status == "completed" else failure_class
        cost = None
        if usage_known:
            if budget is not None:
                cost, prompt, completion = budget.run_account(run.run_key)
                if (prompt, completion) != (input_tokens, output_tokens):
                    cost = None
                    failure_class = "cost_unavailable"
            else:
                try:
                    cost = _priced_cost(input_tokens, output_tokens, input_price, output_price)
                except (ArithmeticError, ValueError):
                    failure_class = "cost_unavailable"
        record = {
            "schema_version": RESULT_SCHEMA_VERSION,
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
            "usage_unknown": not usage_known,
            "currency": "CNY" if budget is not None else "USD",
            "cost_usd": cost if budget is None else None,
            "cost_cny_upper_bound": str(cost) if budget is not None and cost is not None else None,
            "fixture_sha256": observation.get("fixture_sha256"),
            "fallback_runtime": observation.get("fallback_runtime"),
            "validator_signal": (
                run.validator["value"].lower() in answer.lower()
                if run.validator["type"] == "answer_contains" else
                run.validator["value"] in events
            ),
            "human_review": "pending",
        }
        store.save(run.run_key, record)
        if budget is not None:
            budget.finish_run(run.run_key)
        results.append(record)
        if cost is None:
            raise RuntimeError("usage or cost unavailable; paid execution stopped after recording the run")
        if budget is None:
            spent += cost
        elif failure_class and (failure_class.startswith("budget_") or failure_class == "usage_bound_violation"):
            raise BudgetStopped(failure_class)
    return results


def summarize(runs: Sequence[MatrixRun], results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    reported_cost = sum(item["cost_usd"] or 0 for item in results)
    finite_cost = _is_finite_number(reported_cost)
    cny_results = [item for item in results if item.get("currency") == "CNY"]
    usd_results = [item for item in results if item.get("currency", "USD") == "USD"]
    cny_total = sum((Decimal(item["cost_cny_upper_bound"]) for item in cny_results
                     if item.get("cost_cny_upper_bound") is not None), Decimal(0))
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "planned_runs": len(runs),
        "recorded_runs": len(results),
        "complete_denominator": len(results) == len(runs),
        "statuses": dict(Counter(item["status"] for item in results)),
        "failure_classes": dict(Counter(item["failure_class"] for item in results if item["failure_class"])),
        "reported_cost_usd": round(reported_cost, 9) if finite_cost and usd_results else None,
        "reported_cost_cny_upper_bound": str(cny_total) if cny_results else None,
        "aggregate_cost_unavailable": not finite_cost,
        "unknown_cost_runs": sum(item.get("cost_cny_upper_bound") is None for item in cny_results)
                             + sum(item["cost_usd"] is None for item in usd_results),
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
        "schema_version": RESULT_SCHEMA_VERSION,
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
    def __init__(self, provider: OpenAICompatibleProvider, *, budget: CnyBudgetLedger | None = None,
                 run_key: str = "") -> None:
        self.provider = provider
        self.budget = budget
        self.run_key = run_key
        self.cost_cny_upper_bound = Decimal(0)
        self.input_tokens = 0
        self.output_tokens = 0
        self.usage_complete = True
        self.failure_class: str | None = None

    def next_turn(self, messages: Any, tools: Any) -> Any:
        if self.failure_class is not None:
            raise EvaluationProviderStopped(self.failure_class)
        attempt = None
        if self.budget is not None:
            if (self.provider.completion_options != {"thinking": {"type": "disabled"},
                                                     "max_tokens": self.budget.max_tokens}
                    or self.provider.temperature != 0):
                self.failure_class = "budget_request_drift"
                raise EvaluationProviderStopped(self.failure_class)
            try:
                attempt = self.budget.reserve(self.run_key)
            except BudgetStopped as error:
                self.failure_class = error.failure_class
                if error.failure_class != "budget_exhausted":
                    self.usage_complete = False
                raise EvaluationProviderStopped(self.failure_class) from None
        try:
            turn = self.provider.next_turn(messages, tools)
        except Exception as error:
            # A request may have been billed before the transport failed.
            self.usage_complete = False
            self.failure_class = classify_failure(error)
            raise EvaluationProviderStopped(self.failure_class) from None
        usage = turn.usage or {}
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        if not _is_token_count(prompt) or not _is_token_count(completion):
            self.usage_complete = False
            self.failure_class = "usage_unavailable"
            raise EvaluationProviderStopped(self.failure_class)
        else:
            if self.budget is not None:
                try:
                    self.cost_cny_upper_bound += self.budget.settle(attempt, prompt, completion)
                except BudgetStopped as error:
                    self.failure_class = error.failure_class
                    self.usage_complete = False
                    raise EvaluationProviderStopped(self.failure_class) from None
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


def _evaluation_environment() -> dict[str, Any]:
    """Capture installed runtime versions, not only the repository's dependency ranges."""
    return {
        "python": platform.python_version(), "platform": platform.system(),
        "dependencies": {name: version(name) for name in (
            "langgraph", "deepagents", "langchain", "langchain-core", "langgraph-checkpoint-sqlite",
            "mcp", "httpx", "fastapi", "aiosqlite",
        )},
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
    }


def _extract_snapshot(archive: bytes, destination: Path) -> None:
    with ZipFile(io.BytesIO(archive)) as source:
        for member in source.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("archive path escapes benchmark workspace")
        source.extractall(destination)


async def _run_navigation_case(
    run: MatrixRun, *, archive: bytes, base_url: str, model: str, api_key: str,
    budget: CnyBudgetLedger | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="doppel-live-navigation-") as directory:
        workspace = Path(directory) / "workspace"
        workspace.mkdir()
        _extract_snapshot(archive, workspace)
        return await _run_prepared_case(run, workspace, base_url, model, api_key, budget=budget)


async def _run_review_case(
    run: MatrixRun, *, public_source: bytes, base_url: str, model: str, api_key: str,
    budget: CnyBudgetLedger | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="doppel-live-review-") as directory:
        workspace = Path(directory) / "workspace"
        digest = materialize_review_case(run.case_id, workspace, public_source=public_source)
        result = await _run_prepared_case(run, workspace, base_url, model, api_key, budget=budget)
        result["fixture_sha256"] = digest
        return result


async def _run_prepared_case(
    run: MatrixRun, workspace: Path, base_url: str, model: str, api_key: str,
    *, budget: CnyBudgetLedger | None = None,
) -> dict[str, Any]:
    from doppel_agent.concurrency import ResourceLimits
    from doppel_agent.runtime.base import RunRequest
    from doppel_agent.runtime.factory import create_runtime
    from doppel_agent.runtime.provider_adapter import ProviderAdapter

    state_root = workspace / ".doppel-agent"
    state_root.mkdir()
    options = {"thinking": "disabled", "max_tokens": budget.max_tokens} if budget is not None else {}
    provider = _UsageProvider(
        OpenAICompatibleProvider(base_url, model, api_key, timeout=120, temperature=0, **options),
        budget=budget, run_key=run.run_key,
    )
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
        failure_class = None
        fallback_runtime = None
        answer = ""
        status = "failed"
        try:
            result = await runtime.run(
                RunRequest(run.prompt, run_id=run.deterministic_run_id,
                           thread_id=run.deterministic_run_id), sink,
            )
            status, answer = result.status, result.answer
            fallback_runtime = result.metadata.get("fallback_runtime")
            failure_class = provider.failure_class or ("deep_fallback" if fallback_runtime else None)
            if provider.failure_class:
                status, answer = "failed", ""
        except Exception as error:
            failure_class = provider.failure_class or classify_failure(error)
        return {
            "status": status,
            "answer": answer,
            "event_types": sink.types,
            "wall_seconds": round(perf_counter() - started, 6),
            "input_tokens": provider.input_tokens if provider.usage_complete else None,
            "output_tokens": provider.output_tokens if provider.usage_complete else None,
            "failure_class": failure_class,
            "fallback_runtime": fallback_runtime,
            "cost_cny_upper_bound": str(provider.cost_cny_upper_bound) if budget and provider.usage_complete else None,
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
    parser.add_argument("--max-cost-cny", type=float, help="official DeepSeek shared budget; currently exactly 10 CNY")
    parser.add_argument("--budget-ledger", type=Path, help="must be the canonical project-wide shared CNY ledger")
    parser.add_argument("--confirm-provider-cap", action="store_true",
                        help="operator confirms account-side cap/isolated non-replenished balance <=10 CNY")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    matrix = RuntimeMatrix.load(MANIFEST)
    if args.preflight:
        print(json.dumps({
            "canary_run_count": len(select_runs(matrix, "canary")),
            "canary_cases": list(CANARY_CASE_IDS),
            "review_canary_run_count": len(select_runs(matrix, "review-canary")),
            "review_cases": list(REVIEW_CASE_IDS),
            "capability_boundaries": {
                boundary: matrix.capability_document(CAPABILITIES, boundary=boundary)
                for boundary in ("direct_factory", "run_service")
            },
            "full_matrix_ready": False,
            "full_blocker": "version-wide fixture/oracle acceptance, runtime parity decision, paid inputs and human adjudication pending",
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        runs = select_runs(matrix, args.mode)
        capability_selection = freeze_capability_selection(matrix, runs)
        cny_mode = args.max_cost_cny is not None
        if cny_mode:
            if not _is_finite_number(args.max_cost_cny) or args.max_cost_cny != 10:
                raise ValueError("this frozen CNY protocol requires exactly 10 CNY")
            if any(value is not None for value in (args.input_price_per_million,
                                                  args.output_price_per_million, args.max_cost_usd)):
                raise ValueError("CNY budget cannot be combined with USD prices/budget")
            if not args.confirm_provider_cap:
                raise ValueError("provider-side <=10 CNY cap/isolated balance confirmation is required")
            if args.budget_ledger is not None and args.budget_ledger.resolve() != CNY_BUDGET_PATH.resolve():
                raise ValueError("both phases must use the canonical shared ledger; cannot reset the budget with a new path")
            args.budget_ledger = CNY_BUDGET_PATH
            validate_cny_price_snapshot()
        else:
            validate_prices(args.input_price_per_million, args.output_price_per_million, args.max_cost_usd)
            if args.budget_ledger or args.confirm_provider_cap:
                raise ValueError("CNY ledger/cap options require --max-cost-cny")
        if not args.base_url or not args.model:
            raise ValueError("base URL and model are required")
        parsed = urlparse(args.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base URL must not contain credentials, query or fragment")
        if cny_mode and (args.base_url.rstrip("/") not in ("https://api.deepseek.com", "https://api.deepseek.com/v1")
                         or args.model not in ("deepseek-v4-flash", "deepseek-flash")):
            raise ValueError("CNY protocol only supports official DeepSeek Flash endpoint/model")
        if not cny_mode and parsed.hostname == "api.deepseek.com":
            raise ValueError("official DeepSeek evaluation requires the shared 10 CNY protocol, not USD mode")
        key = os.environ.get("DOPPEL_AGENT_API_KEY", "")
        if not key:
            raise ValueError("DOPPEL_AGENT_API_KEY is not configured")
        commit, archive = _git_snapshot()
        review_sources, review_keys = freeze_review_inputs()
        config = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "protocol_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
            "capability_selection": capability_selection,
            "commit": commit,
            "snapshot_sha256": hashlib.sha256(archive).hexdigest(),
            "review_fixture_sha256": {
                case_id: hashlib.sha256(payload).hexdigest()
                for case_id, payload in review_sources.items()
            },
            "review_answer_key_sha256": {
                case_id: hashlib.sha256(payload).hexdigest()
                for case_id, payload in review_keys.items()
            },
            "mode": args.mode,
            "base_url_host": parsed.netloc,
            "base_url_sha256": hashlib.sha256(args.base_url.encode("utf-8")).hexdigest(),
            "model": args.model,
            "temperature": 0,
            "input_price_per_million": args.input_price_per_million,
            "output_price_per_million": args.output_price_per_million,
            "max_cost_usd": args.max_cost_usd,
            "environment": _evaluation_environment(),
        }
        budget = None
        if cny_mode:
            identity = {name: config[name] for name in (
                "protocol_sha256", "commit", "snapshot_sha256", "review_fixture_sha256",
                "review_answer_key_sha256", "base_url_sha256", "model",
                "environment",
            )}
            identity.update(capability_sha256=hashlib.sha256(CAPABILITIES.read_bytes()).hexdigest(),
                            mapped_model="DeepSeek-V4.1-Flash", provider_cap_confirmed_by_operator=True)
            budget = CnyBudgetLedger(args.budget_ledger, identity)
            config.update(cny_budget=budget.configuration,
                          budget_ledger_path_sha256=hashlib.sha256(str(budget.path).encode()).hexdigest(),
                          model_mapping_source="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                          mapped_model="DeepSeek-V4.1-Flash", thinking="disabled", max_tokens=budget.max_tokens)
        output_dir = args.output_dir or ROOT / ".bench-results" / f"live-{args.mode}"
        store = ResultStore(output_dir, config)
        async def runner(run: MatrixRun) -> dict[str, Any]:
            if args.mode == "review-canary":
                return await _run_review_case(
                    run, public_source=review_sources[run.case_id],
                    base_url=args.base_url, model=args.model, api_key=key,
                    budget=budget,
                )
            return await _run_navigation_case(
                run, archive=archive, base_url=args.base_url, model=args.model, api_key=key,
                budget=budget,
            )
        try:
            asyncio.run(execute_runs(runs, store, runner, budget=budget,
                input_price=args.input_price_per_million, output_price=args.output_price_per_million,
                max_cost_usd=args.max_cost_usd))
        finally:
            recorded = [store.load(run.run_key) for run in runs]
            observed = [item for item in recorded if item is not None]
            report = summarize(runs, observed)
            if budget is not None:
                report["shared_budget"] = budget.summary()
            _write_json_atomic(store.root / "summary.json", report)
            _write_json_atomic(store.root / "review_queue.json", review_queue(observed))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["complete_denominator"] else 2
    except (ValueError, RuntimeError) as error:
        parser.exit(2, f"live matrix stopped: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
