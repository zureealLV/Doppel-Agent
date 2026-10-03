"""Offline DeepSeek protocol and shared CNY spending guards; no paid calls."""

import json
import asyncio
import threading
import sys
import subprocess
from contextlib import contextmanager
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bench.audit_live_runtime_matrix import local_provider
from doppel_agent.core import Core
from doppel_agent.provider import OpenAICompatibleProvider


def test_explicit_nonthinking_and_output_limit_survive_tool_round_trip(tmp_path):
    (tmp_path / "service.py").write_text("def example(): pass\n", encoding="utf-8")
    with local_provider("service.py", "def ") as (url, observations):
        provider = OpenAICompatibleProvider(
            url, "deepseek-v4-flash", thinking="disabled", max_tokens=4096, temperature=0,
        )
        # Capture the actual HTTP bodies, not a replacement provider.
        original = __import__("urllib.request", fromlist=["urlopen"]).urlopen
        requests = []
        import unittest.mock
        def capture(request, **kwargs):
            requests.append(json.loads(request.data))
            return original(request, **kwargs)
        with unittest.mock.patch("doppel_agent.provider.urlopen", capture):
            result = Core(tmp_path, provider).run("Read service.py")
        assert result["status"] == "completed"
        assert len(observations) == len(requests) == 2
        assert all(r["thinking"] == {"type": "disabled"} for r in requests)
        assert all(r["max_tokens"] == 4096 for r in requests)
        assert requests[1]["messages"][-1]["role"] == "tool"


def test_shared_budget_reserves_before_each_call_across_phase_handles(tmp_path):
    from bench.cny_budget import CnyBudgetLedger, BudgetStopped

    config = {"source": "fixture", "model": "deepseek-v4-flash"}
    path = tmp_path / "shared.sqlite3"
    navigation = CnyBudgetLedger(path, config)
    review = CnyBudgetLedger(path, config)
    for index in range(4):
        handle = navigation if index % 2 == 0 else review
        attempt = handle.reserve(f"fixture-{index}")
        charged = handle.settle(attempt, 1_048_576, 4096)
        assert charged == Decimal("2.129920")
    with pytest.raises(BudgetStopped, match="budget_exhausted"):
        review.reserve("fifth-call")
    assert review.summary()["charged_upper_bound_cny"] == "8.519680"
    assert review.summary()["request_count"] == 4


@pytest.mark.parametrize("prompt,completion", [(True, 0), (-1, 0), (1.0, 0), (0, None),
                                               (1_048_577, 0), (0, 4097)])
def test_invalid_or_out_of_bound_usage_keeps_reservation_and_blocks_resume(tmp_path, prompt, completion):
    from bench.cny_budget import CnyBudgetLedger, BudgetStopped
    path = tmp_path / "shared.sqlite3"
    ledger = CnyBudgetLedger(path, {"source": "fixture"})
    attempt = ledger.reserve("first")
    with pytest.raises(BudgetStopped, match="usage_bound"):
        ledger.settle(attempt, prompt, completion)
    reopened = CnyBudgetLedger(path, {"source": "fixture"})
    with pytest.raises(BudgetStopped, match="budget_unsettled"):
        reopened.reserve("second")
    assert reopened.summary()["blocked_unknown"] is True


@pytest.mark.parametrize("runtime", ["legacy", "graph", "deep"])
def test_cny_transport_guard_is_used_by_each_runtime(tmp_path, runtime):
    from bench import live_runtime_matrix as live
    from bench.cny_budget import CnyBudgetLedger
    from bench.runtime_fixtures import FIXTURE_ROOT
    from bench.runtime_matrix import RuntimeMatrix
    matrix = RuntimeMatrix.load(live.MANIFEST)
    run = next(r for r in live.select_runs(matrix, "review-canary")
               if r.case_id == "review-01" and r.runtime == runtime)
    ledger = CnyBudgetLedger(tmp_path / "shared.sqlite3", {"source": "fixture"})
    source = (FIXTURE_ROOT / run.case_id / "public/service.py").read_bytes()
    with local_provider("service.py", "def ") as (url, requests):
        result = asyncio.run(live._run_review_case(
            run, public_source=source, base_url=url, model="deepseek-v4-flash",
            api_key="fixture-only", budget=ledger,
        ))
    assert result["status"] == "completed"
    assert len(requests) == 2
    assert result["cost_cny_upper_bound"] == "0.000192"
    assert ledger.summary()["request_count"] == 2
    assert ledger.summary()["charged_upper_bound_cny"] == "0.000192"


def test_cny_results_resume_with_shared_ledger_not_a_second_budget(tmp_path):
    from bench import live_runtime_matrix as live
    from bench.cny_budget import CnyBudgetLedger
    from bench.runtime_fixtures import FIXTURE_ROOT
    from bench.runtime_matrix import RuntimeMatrix
    run = live.select_runs(RuntimeMatrix.load(live.MANIFEST), "review-canary")[0]
    ledger = CnyBudgetLedger(tmp_path / "shared.sqlite3", {"source": "fixture"})
    store = live.ResultStore(tmp_path / "review", {"cny_budget": ledger.configuration})
    source = (FIXTURE_ROOT / run.case_id / "public/service.py").read_bytes()
    with local_provider("service.py", "def ") as (url, requests):
        async def runner(selected):
            return await live._run_review_case(selected, public_source=source, base_url=url,
                                               model="fixture", api_key="fixture-only", budget=ledger)
        results = asyncio.run(live.execute_runs([run], store, runner, budget=ledger))
        again = asyncio.run(live.execute_runs([run], store, runner, budget=ledger))
    assert results == again
    assert len(requests) == 2
    assert results[0]["currency"] == "CNY"
    assert results[0]["cost_usd"] is None
    assert results[0]["cost_cny_upper_bound"] == "0.000192"
    report = live.summarize([run], results)
    assert report["reported_cost_cny_upper_bound"] == "0.000192"
    assert report["reported_cost_usd"] is None
    assert report["unknown_cost_runs"] == 0


def test_budget_refuses_to_replay_orphaned_run_across_output_directories(tmp_path):
    from bench import live_runtime_matrix as live
    from bench.cny_budget import CnyBudgetLedger
    from bench.runtime_matrix import RuntimeMatrix
    run = live.select_runs(RuntimeMatrix.load(live.MANIFEST), "canary")[0]
    ledger = CnyBudgetLedger(tmp_path / "shared.sqlite3", {"source": "fixture"})
    ledger.settle(ledger.reserve(run.run_key), 12, 9)
    store = live.ResultStore(tmp_path / "new-output", {"cny_budget": ledger.configuration})
    calls = []
    async def runner(selected):
        calls.append(selected)
        return {}
    with pytest.raises(RuntimeError, match="budget_orphaned_run"):
        asyncio.run(live.execute_runs([run], store, runner, budget=ledger))
    assert calls == []


def test_official_deepseek_cny_cli_requires_account_cap_before_snapshot(tmp_path, monkeypatch, capsys):
    import sys
    from bench import live_runtime_matrix as live
    monkeypatch.setattr(sys, "argv", ["live", "--base-url", "https://api.deepseek.com",
        "--model", "deepseek-v4-flash", "--max-cost-cny", "10", "--budget-ledger",
        str(tmp_path / "budget.sqlite3"), "--output-dir", str(tmp_path / "out")])
    monkeypatch.setenv("DOPPEL_AGENT_API_KEY", "fixture-only")
    calls = []
    monkeypatch.setattr(live, "_git_snapshot", lambda: calls.append("snapshot"))
    with pytest.raises(SystemExit) as stopped:
        live.main()
    assert stopped.value.code == 2
    assert "provider-side" in capsys.readouterr().err
    assert calls == []
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "budget.sqlite3").exists()


def test_async_official_deepseek_defaults_to_nonthinking():
    import httpx
    from doppel_agent.provider import AsyncOpenAICompatibleProvider, Message
    requests = []
    async def exercise():
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "offline"}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AsyncOpenAICompatibleProvider("https://api.deepseek.com", "deepseek-v4-flash",
                                                     client=client, max_tokens=4096)
            await provider.anext_turn([Message("user", "offline")], [])
    asyncio.run(exercise())
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert requests[0]["max_tokens"] == 4096


def test_cny_cli_cannot_reset_budget_by_selecting_another_ledger(tmp_path, monkeypatch, capsys):
    import sys
    from bench import live_runtime_matrix as live
    monkeypatch.setattr(sys, "argv", ["live", "--base-url", "https://api.deepseek.com",
        "--model", "deepseek-v4-flash", "--max-cost-cny", "10", "--confirm-provider-cap",
        "--budget-ledger", str(tmp_path / "second-budget.sqlite3")])
    calls = []
    monkeypatch.setattr(live, "_git_snapshot", lambda: calls.append("snapshot"))
    with pytest.raises(SystemExit) as stopped:
        live.main()
    assert stopped.value.code == 2
    assert "canonical shared ledger" in capsys.readouterr().err
    assert calls == []


def test_transport_options_drift_stops_before_reserving_or_sending(tmp_path):
    from bench import live_runtime_matrix as live
    from bench.cny_budget import CnyBudgetLedger
    ledger = CnyBudgetLedger(tmp_path / "shared.sqlite3", {"source": "fixture"})
    with local_provider("service.py", "def ") as (url, requests):
        underlying = OpenAICompatibleProvider(url, "fixture", temperature=0,
                                             thinking="disabled", max_tokens=4096)
        provider = live._UsageProvider(underlying, budget=ledger, run_key="nav-01:legacy:1")
        underlying.completion_options["max_tokens"] = 5000
        with pytest.raises(RuntimeError, match="budget_request_drift"):
            provider.next_turn([], [])
        assert requests == []
    assert ledger.summary()["request_count"] == 0


@contextmanager
def bounded_http_fixture(*, usage=None, status=200):
    """Bodies stay in memory; this endpoint is always loopback and scripted."""
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            if request.get("thinking") != {"type": "disabled"} or request.get("max_tokens") != 4096:
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if status != 200:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            schema = next(t["function"] for t in request["tools"] if t["function"]["name"] == "read_file")
            argument = "file_path" if "file_path" in schema["parameters"]["properties"] else "path"
            message = {"role": "assistant", "content": "", "tool_calls": [{
                "id": "bounded-read", "type": "function", "function": {"name": "read_file",
                "arguments": json.dumps({argument: "service.py"})},
            }]}
            payload = {"choices": [{"message": message}]}
            if usage is not None:
                payload["usage"] = usage
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
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("runtime", ["legacy", "graph", "deep"])
@pytest.mark.parametrize("fault", ["exhausted", "missing_usage", "http_401", "usage_bound"])
def test_cny_stop_is_sticky_inside_run_across_fallback_and_resume(tmp_path, runtime, fault):
    from bench import live_runtime_matrix as live
    from bench.cny_budget import CnyBudgetLedger
    from bench.runtime_fixtures import FIXTURE_ROOT
    from bench.runtime_matrix import RuntimeMatrix
    run = next(r for r in live.select_runs(RuntimeMatrix.load(live.MANIFEST), "review-canary")
               if r.case_id == "review-01" and r.runtime == runtime)
    ledger = CnyBudgetLedger(tmp_path / "shared.sqlite3", {"source": "fixture"})
    source = (FIXTURE_ROOT / run.case_id / "public/service.py").read_bytes()
    if fault == "exhausted":
        for index in range(3):
            ledger.settle(ledger.reserve(f"previous-{index}"), 1_048_576, 4096)
    usage = ({"prompt_tokens": 1_048_576, "completion_tokens": 4096} if fault == "exhausted" else
             {"prompt_tokens": 12, "completion_tokens": 4097} if fault == "usage_bound" else None)
    store = live.ResultStore(tmp_path / "phase", {"cny_budget": ledger.configuration})
    expected = {"exhausted": "budget_exhausted", "missing_usage": "usage_unavailable",
                "http_401": "provider_auth", "usage_bound": "usage_bound_violation"}[fault]
    with bounded_http_fixture(usage=usage, status=401 if fault == "http_401" else 200) as (url, requests):
        async def runner(selected):
            return await live._run_review_case(selected, public_source=source, base_url=url,
                                               model="fixture", api_key="fixture-only", budget=ledger)
        with pytest.raises(RuntimeError):
            asyncio.run(live.execute_runs([run], store, runner, budget=ledger))
        original = store._path(run.run_key).read_bytes()
        record = store.load(run.run_key)
        assert record["status"] == "failed"
        assert record["failure_class"] == expected
        assert len(requests) == 1  # next tool round and Deep fallback are blocked
        if fault == "exhausted":
            # A known, pre-request rejection can be safely read on resume, but
            # cannot secretly rerun the failed attempt or acquire a new budget.
            asyncio.run(live.execute_runs([run], store, runner, budget=ledger))
            assert ledger.summary()["charged_upper_bound_cny"] == "8.519680"
        else:
            with pytest.raises(RuntimeError):
                asyncio.run(live.execute_runs([run], store, runner, budget=ledger))
            assert ledger.summary()["blocked_unknown"]
            assert ledger.summary()["unsettled_reserved_cny"] == "2.129920"
        assert len(requests) == 1
        assert store._path(run.run_key).read_bytes() == original


@pytest.mark.parametrize("settled", [False, True])
def test_real_process_exit_retains_request_and_orphaned_run_claim(tmp_path, settled):
    from bench.cny_budget import CnyBudgetLedger, BudgetStopped
    path = tmp_path / "shared.sqlite3"
    script = """
import os, sys
from pathlib import Path
from bench.cny_budget import CnyBudgetLedger
ledger = CnyBudgetLedger(Path(sys.argv[1]), {"source": "crash-fixture"})
ledger.claim_run("nav-01:legacy:1")
attempt = ledger.reserve("nav-01:legacy:1")
if sys.argv[2] == "True":
    ledger.settle(attempt, 12, 9)
os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", script, str(path), str(settled)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    ledger = CnyBudgetLedger(path, {"source": "crash-fixture"})
    assert ledger.summary()["blocked_unknown"]
    with pytest.raises(BudgetStopped, match="budget_orphaned_run"):
        ledger.claim_run("nav-01:legacy:1")
    with pytest.raises(BudgetStopped, match="budget_unsettled"):
        ledger.claim_run("review-01:legacy:1")
    assert ledger.summary()["request_count"] == 1


def test_competing_budget_handles_cannot_both_reserve(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from bench.cny_budget import CnyBudgetLedger, BudgetStopped
    path = tmp_path / "shared.sqlite3"
    handles = [CnyBudgetLedger(path, {"source": "fixture"}) for _ in range(2)]
    barrier = threading.Barrier(2)
    def reserve(index):
        barrier.wait(timeout=5)
        try:
            handles[index].reserve(f"contender-{index}")
            return "reserved"
        except BudgetStopped as error:
            return error.failure_class
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, (0, 1)))
    assert sorted(results) == ["budget_unsettled", "reserved"]
    assert handles[0].summary()["request_count"] == 1


def test_budget_config_drift_and_duplicate_settlement_are_rejected(tmp_path):
    from bench.cny_budget import CnyBudgetLedger
    path = tmp_path / "shared.sqlite3"
    ledger = CnyBudgetLedger(path, {"source": "fixture"})
    with pytest.raises(ValueError, match="configuration"):
        CnyBudgetLedger(path, {"source": "changed-model-or-source"})
    attempt = ledger.reserve("first")
    ledger.settle(attempt, 0, 0)
    with pytest.raises(ValueError, match="duplicate"):
        ledger.settle(attempt, 1, 1)
    assert ledger.summary()["charged_upper_bound_cny"] == "0.000000"


def test_two_cli_phases_share_frozen_budget_and_dependency_snapshot(tmp_path, monkeypatch):
    import io
    from importlib.metadata import version
    from zipfile import ZipFile
    from bench import live_runtime_matrix as live
    ledger_path = tmp_path / "shared.sqlite3"
    monkeypatch.setattr(live, "CNY_BUDGET_PATH", ledger_path)
    # Offline acceptance of a frozen 2026-10-03 price fixture, not a claim that
    # future real calls can use an expired pricing snapshot.
    monkeypatch.setattr(live, "validate_cny_price_snapshot", lambda: None, raising=False)
    memory = io.BytesIO()
    with ZipFile(memory, "w") as archive:
        archive.writestr("service.py", "def offline(): pass\n")
    monkeypatch.setattr(live, "_git_snapshot", lambda: ("0" * 40, memory.getvalue()))
    monkeypatch.setenv("DOPPEL_AGENT_API_KEY", "fixture-only")
    navigation, review = live._run_navigation_case, live._run_review_case
    with local_provider("service.py", "def ") as (url, requests):
        async def local_navigation(run, **options):
            return await navigation(run, **{**options, "base_url": url})
        async def local_review(run, **options):
            return await review(run, **{**options, "base_url": url})
        monkeypatch.setattr(live, "_run_navigation_case", local_navigation)
        monkeypatch.setattr(live, "_run_review_case", local_review)
        for phase in ("canary", "review-canary", "review-canary"):
            monkeypatch.setattr(sys, "argv", ["live", "--mode", phase, "--base-url", "https://api.deepseek.com",
                "--model", "deepseek-v4-flash", "--max-cost-cny", "10", "--confirm-provider-cap",
                "--output-dir", str(tmp_path / phase)])
            assert live.main() == 0
        assert len(requests) == 42  # 9+12 cases, two real local HTTP tool turns each; resume adds none
    contexts = [json.loads((tmp_path / phase / "context.json").read_bytes()) for phase in ("canary", "review-canary")]
    assert contexts[0]["cny_budget"] == contexts[1]["cny_budget"]
    assert contexts[0]["environment"]["dependencies"]["langgraph"] == version("langgraph")
    assert contexts[0]["cny_budget"]["identity"]["environment"] == contexts[0]["environment"]
    summary = json.loads((tmp_path / "review-canary" / "summary.json").read_bytes())
    assert summary["shared_budget"]["request_count"] == 42
    assert summary["shared_budget"]["total_cny"] == "10"
    assert summary["shared_budget"]["charged_upper_bound_cny"] == "0.004032"
    assert summary["task_quality_scored"] is False


def test_cny_price_snapshot_cannot_silently_become_stale():
    from bench import live_runtime_matrix as live
    live.validate_cny_price_snapshot("2026-10-03")
    with pytest.raises(ValueError, match="price snapshot expired"):
        live.validate_cny_price_snapshot("2026-10-04")


def test_refreshed_price_snapshot_cannot_reset_an_existing_ledger(tmp_path, monkeypatch):
    from bench.cny_budget import CnyBudgetLedger

    path = tmp_path / "shared.sqlite3"
    with monkeypatch.context() as older:
        older.setattr(CnyBudgetLedger, "price_checked_date", "2026-10-02")
        ledger = CnyBudgetLedger(path, {"source": "fixture"})
        attempt = ledger.reserve("old-snapshot-request")
        ledger.settle(attempt, 100, 20)
        before = ledger.summary()
    assert CnyBudgetLedger.price_checked_date == "2026-10-03"
    with pytest.raises(ValueError, match="configuration differs"):
        CnyBudgetLedger(path, {"source": "fixture"})
    with monkeypatch.context() as older:
        older.setattr(CnyBudgetLedger, "price_checked_date", "2026-10-02")
        assert CnyBudgetLedger(path, {"source": "fixture"}).summary() == before


@pytest.mark.parametrize("max_tokens", [0, -1, True, False, 1.5, "4096"])
def test_invalid_output_limit_is_rejected_before_network(max_tokens):
    with pytest.raises(ValueError, match="positive integer"):
        OpenAICompatibleProvider("https://api.deepseek.com", "deepseek-v4-flash", max_tokens=max_tokens)


def test_thinking_enabled_is_not_claimed_without_history_support():
    with pytest.raises(ValueError, match="history contract"):
        OpenAICompatibleProvider("https://api.deepseek.com", "deepseek-v4-flash", thinking="enabled")
    assert OpenAICompatibleProvider("https://unrelated.example/v1", "fixture").completion_options == {}
