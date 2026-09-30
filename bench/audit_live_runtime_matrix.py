"""Offline adversarial audit of the live runner; never calls an external model.

Exit 1 means a desired safety gate failed, not that model quality was measured.
The local HTTP fixture forces a real read-tool round trip rather than supplying
an immediate canned final answer. Raw provider requests/credentials are not saved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from bench.live_runtime_matrix import (
    ROOT, ResultStore, _UsageProvider, _run_navigation_case, _run_review_case,
    execute_runs, select_runs,
)
from bench.runtime_fixtures import FIXTURE_ROOT
from bench.runtime_matrix import RuntimeMatrix
from doppel_agent.provider import ModelTurn


@contextmanager
def local_provider(path: str, marker: str, *, missing_usage: bool = False, http_status: int = 200):
    observations: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            tool_messages = [m for m in request["messages"] if m["role"] == "tool"]
            observations.append({
                "temperature_zero": request.get("temperature") == 0,
                "answer_key_visible": "answer_key" in json.dumps(request),
                "source_received": any(marker in m["content"] for m in tool_messages),
            })
            if http_status != 200:
                self.send_response(http_status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not tool_messages:
                schema = next(t["function"] for t in request["tools"] if t["function"]["name"] == "read_file")
                argument = "file_path" if "file_path" in schema["parameters"]["properties"] else "path"
                message = {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "offline-read", "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({argument: path})},
                }]}
            else:
                message = {"role": "assistant", "content": "Offline scripted transport audit only."}
            payload = {"choices": [{"message": message}]}
            if not missing_usage or tool_messages:
                payload["usage"] = {"prompt_tokens": 12, "completion_tokens": 9}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", observations
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def transport_probe(run, archive: bytes, *, missing_usage: bool = False, http_status: int = 200):
    navigation = {
        "nav-01": ("src/doppel_agent/runtime/factory.py", "def create_runtime"),
        "nav-02": ("src/doppel_agent/workspace/service.py", "class WorkspaceService"),
        "nav-03": ("src/doppel_agent/persistence/events.py", "class EventStore"),
    }
    path, marker = navigation.get(run.case_id, ("service.py", "def "))
    with local_provider(path, marker, missing_usage=missing_usage, http_status=http_status) as (url, requests):
        async def runner(selected):
            options = {"base_url": url, "model": "offline-audit", "api_key": "fixture-only"}
            if selected.case_id.startswith("review-"):
                return await _run_review_case(
                    selected, public_source=(FIXTURE_ROOT / selected.case_id / "public/service.py").read_bytes(),
                    **options,
                )
            return await _run_navigation_case(selected, archive=archive, **options)

        with tempfile.TemporaryDirectory(prefix="doppel-audit-store-") as directory:
            store = ResultStore(Path(directory), {"provider": "local-scripted-fixture"})
            stopped = False
            try:
                await execute_runs([run], store, runner, input_price=1, output_price=2, max_cost_usd=1)
            except RuntimeError:
                stopped = True
            result = store.load(run.run_key)
            calls_before_resume = len(requests)
            if not stopped:
                await execute_runs([run], store, runner, input_price=1, output_price=2, max_cost_usd=1)
            return {
                "run_key": run.run_key, "http_requests": len(requests),
                "resume_replayed": len(requests) != calls_before_resume,
                "status": result["status"], "failure_class": result["failure_class"],
                "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"],
                "unknown_usage_stopped_run_sequence": stopped,
                "source_received": any(r["source_received"] for r in requests),
                "answer_key_visible": any(r["answer_key_visible"] for r in requests),
                "temperature_zero": all(r["temperature_zero"] for r in requests),
            }


def invalid_usage_probe():
    class Provider:
        def __init__(self, usages):
            self.usages = iter(usages)

        def next_turn(self, _messages, _tools):
            return ModelTurn(usage=next(self.usages))

    observations = {}
    for name, usages in {
        "boolean": [{"prompt_tokens": True, "completion_tokens": True}],
        "negative_then_positive": [
            {"prompt_tokens": -1, "completion_tokens": -1},
            {"prompt_tokens": 12, "completion_tokens": 9},
        ],
    }.items():
        tracked = _UsageProvider(Provider(usages))
        rejected = False
        for _ in usages:
            try:
                tracked.next_turn([], [])
            except (ValueError, RuntimeError):
                rejected = True
                break
        observations[name] = {"usage_complete": tracked.usage_complete,
                              "invalid_usage_rejected": rejected or not tracked.usage_complete,
                              "input_tokens": tracked.input_tokens, "output_tokens": tracked.output_tokens}
    return observations


async def invalid_prices_probe(runs):
    observations = {}
    for name, prices in {
        "nan_input_price": (float("nan"), 2, 1),
        "infinite_input_price": (float("inf"), 2, 1),
        "nan_budget": (1, 2, float("nan")),
    }.items():
        calls = []

        async def runner(run, calls=calls):
            calls.append(run.run_key)
            return {"status": "completed", "answer": "offline", "wall_seconds": 0.1,
                    "input_tokens": 12, "output_tokens": 9, "event_types": []}

        with tempfile.TemporaryDirectory(prefix="doppel-audit-prices-") as directory:
            store = ResultStore(Path(directory), {"provider": "no-network"})
            rejected = False
            results = []
            try:
                results = await execute_runs(runs[:2], store, runner,
                                             input_price=prices[0], output_price=prices[1], max_cost_usd=prices[2])
            except ValueError:
                rejected = True
            observations[name] = {
                "rejected_before_call": rejected and not calls, "runner_calls": len(calls),
                "non_finite_cost": any(not math.isfinite(r["cost_usd"]) for r in results),
            }
    return observations


async def audit():
    matrix = RuntimeMatrix.load(ROOT / "bench/cases/runtime/manifest.json")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    runtime_source_dirty = bool(subprocess.check_output([
        "git", "status", "--porcelain", "--", "src/doppel_agent", "bench/live_runtime_matrix.py",
        "bench/runtime_matrix.py", "bench/runtime_fixtures.py",
    ], cwd=ROOT, text=True).strip())
    archive = subprocess.check_output(["git", "archive", "--format=zip", "HEAD", "src/doppel_agent"], cwd=ROOT)
    runs = select_runs(matrix, "canary") + select_runs(matrix, "review-canary")
    happy = [await transport_probe(run, archive) for run in runs]
    review_runs = [r for r in runs if r.case_id == "review-01"]
    missing = [await transport_probe(run, archive, missing_usage=True) for run in review_runs]
    auth = [await transport_probe(run, archive, http_status=401) for run in review_runs]
    usage = invalid_usage_probe()
    prices = await invalid_prices_probe(runs)
    gates = {
        "all_21_tool_round_trips": all(
            r["status"] == "completed" and not r["failure_class"] and r["http_requests"] == 2
            and r["source_received"] and r["temperature_zero"] and not r["answer_key_visible"]
            and not r["resume_replayed"] and r["input_tokens"] == 24 and r["output_tokens"] == 18
            for r in happy
        ),
        "unknown_usage_blocks_next_http_call": all(r["http_requests"] == 1 for r in missing),
        "auth_failure_blocks_fallback_http_call": all(r["http_requests"] == 1 for r in auth),
        "auth_failure_has_stable_taxonomy": all(r["failure_class"] == "provider_auth" for r in auth),
        "invalid_per_call_usage_rejected": all(r["invalid_usage_rejected"] for r in usage.values()),
        "non_finite_prices_and_budget_rejected": all(r["rejected_before_call"] for r in prices.values()),
    }
    return {
        "schema_version": "1.0", "recorded_at": datetime.now(UTC).isoformat(), "source_commit": commit,
        "runtime_source_dirty": runtime_source_dirty,
        "provider": "local-scripted-http-or-no-network", "gates": gates,
        "passed": all(gates.values()), "happy_paths": happy, "missing_usage": missing,
        "http_401": auth, "invalid_usage": usage, "invalid_prices": prices,
        "scope_note": "Adversarial plumbing/accounting audit, not paid execution or model-quality evidence.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(audit())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"gates": report["gates"], "passed": report["passed"]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
