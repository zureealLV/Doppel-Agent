"""Request-level billing safety must hold even when runtimes swallow errors."""

import asyncio
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bench import live_runtime_matrix as live
from bench.audit_live_runtime_matrix import local_provider, transport_probe
from bench.runtime_matrix import RuntimeMatrix
from doppel_agent.provider import ModelTurn, OpenAICompatibleProvider, ProviderRequestError


MATRIX = RuntimeMatrix.load(live.MANIFEST)
RUNS = live.select_runs(MATRIX, "canary")
REVIEW_RUNS = [r for r in live.select_runs(MATRIX, "review-canary") if r.case_id == "review-01"]


@pytest.mark.parametrize("field", ["input_price", "output_price", "max_cost_usd"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), 0, -1, True, "1", None])
def test_invalid_price_or_budget_rejected_before_runner(tmp_path, field, invalid):
    calls = []

    async def runner(run):
        calls.append(run.run_key)
        return {"status": "completed", "answer": "offline", "wall_seconds": 0.1,
                "input_tokens": 12, "output_tokens": 9}

    prices = {"input_price": 1, "output_price": 2, "max_cost_usd": 1, field: invalid}
    with pytest.raises(ValueError, match="finite"):
        asyncio.run(live.execute_runs(RUNS[:2], live.ResultStore(tmp_path, {}), runner, **prices))
    assert calls == []


@pytest.mark.parametrize("invalid", ["nan", "inf", "0", "-1"])
def test_cli_rejects_invalid_prices_before_snapshot_or_store(tmp_path, monkeypatch, capsys, invalid):
    monkeypatch.setenv("DOPPEL_AGENT_API_KEY", "fixture-only")
    monkeypatch.setattr(sys, "argv", ["live", "--base-url", "https://example.invalid/v1",
        "--model", "offline", "--input-price-per-million", invalid,
        "--output-price-per-million", "2", "--max-cost-usd", "1", "--output-dir", str(tmp_path / "out")])
    snapshot_calls = []
    def snapshot():
        snapshot_calls.append(True)
        raise AssertionError("snapshot invoked before price validation")

    monkeypatch.setattr(live, "_git_snapshot", snapshot)
    with pytest.raises(SystemExit) as stopped:
        live.main()
    assert stopped.value.code == 2
    assert "finite" in capsys.readouterr().err
    assert snapshot_calls == []
    assert not (tmp_path / "out").exists()


class SequenceProvider:
    def __init__(self, usages):
        self.usages = iter(usages)
        self.calls = 0

    def next_turn(self, _messages, _tools):
        self.calls += 1
        return ModelTurn(content="fixture-only", usage=next(self.usages))


@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens"])
@pytest.mark.parametrize("invalid", [None, True, False, -1, 1.0, "1"])
def test_invalid_turn_usage_is_terminal_and_cannot_be_erased(field, invalid):
    usage = {"prompt_tokens": 12, "completion_tokens": 9, field: invalid}
    underlying = SequenceProvider([usage, {"prompt_tokens": 12, "completion_tokens": 9}])
    provider = live._UsageProvider(underlying)
    with pytest.raises(RuntimeError, match="usage_unavailable"):
        provider.next_turn([], [])
    with pytest.raises(RuntimeError, match="usage_unavailable"):
        provider.next_turn([], [])
    assert underlying.calls == 1
    assert provider.usage_complete is False
    assert provider.input_tokens == provider.output_tokens == 0


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 1}, {"completion_tokens": 1}])
def test_absent_usage_is_terminal(usage):
    provider = live._UsageProvider(SequenceProvider([usage]))
    with pytest.raises(RuntimeError, match="usage_unavailable"):
        provider.next_turn([], [])
    assert not provider.usage_complete


@pytest.mark.parametrize("usage", [
    {"prompt_tokens": 0, "completion_tokens": 0}, {"input_tokens": 12, "output_tokens": 9},
])
def test_valid_zero_and_alias_usage_is_accepted(usage):
    provider = live._UsageProvider(SequenceProvider([usage]))
    provider.next_turn([], [])
    assert provider.usage_complete
    assert provider.input_tokens == usage.get("prompt_tokens", usage.get("input_tokens"))


@pytest.mark.parametrize("run", REVIEW_RUNS, ids=lambda run: run.runtime)
def test_unknown_usage_stops_next_http_request_within_each_runtime(run):
    result = asyncio.run(transport_probe(run, b"", missing_usage=True))
    assert result["http_requests"] == 1
    assert result["unknown_usage_stopped_run_sequence"]
    assert result["failure_class"] == "usage_unavailable"


@pytest.mark.parametrize("run", REVIEW_RUNS, ids=lambda run: run.runtime)
def test_http_auth_is_preserved_with_no_fallback_request(run):
    result = asyncio.run(transport_probe(run, b"", http_status=401))
    assert result["http_requests"] == 1
    assert result["failure_class"] == "provider_auth"
    assert result["status"] == "failed"


def test_unknown_cost_resume_never_replays_or_mutates_attempt(tmp_path):
    run = REVIEW_RUNS[0]
    source = (live.FIXTURE_ROOT / run.case_id / "public/service.py").read_bytes()
    with local_provider("service.py", "def ", missing_usage=True) as (url, requests):
        async def runner(selected):
            return await live._run_review_case(selected, public_source=source,
                                               base_url=url, model="fixture", api_key="fixture-only")

        store = live.ResultStore(tmp_path, {})
        for _ in range(2):
            with pytest.raises(RuntimeError, match="usage"):
                asyncio.run(live.execute_runs([run], store, runner, input_price=1, output_price=2, max_cost_usd=1))
            current = (store.runs / f"{run.run_key.replace(':', '__')}.json").read_bytes()
            if _ == 0:
                original = current
            else:
                assert current == original
        assert len(requests) == 1
        record = store.load(run.run_key)
        assert record["usage_unknown"] is True
        assert record["cost_usd"] is None
        assert record["wall_seconds"] >= 0


@pytest.mark.parametrize("code, kind", [(401, "http_status"), (403, "http_status"),
                                        (429, "rate_limited"), (503, "http_status")])
def test_synchronous_http_adapter_emits_typed_sanitized_errors(code, kind):
    with local_provider("service.py", "def ", http_status=code) as (url, requests):
        provider = OpenAICompatibleProvider(url, "fixture", api_key="fixture-secret")
        with pytest.raises(ProviderRequestError) as error:
            provider.next_turn([], [])
        assert error.value.kind == kind
        assert error.value.status_code == code
        assert error.value.attempts == 1
        assert "fixture-secret" not in str(error.value)
        assert len(requests) == 1


def test_synchronous_connection_refusal_has_stable_class():
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        provider = OpenAICompatibleProvider(f"http://127.0.0.1:{reserved.getsockname()[1]}/v1",
                                           "fixture", timeout=3)
        with pytest.raises(ProviderRequestError) as error:
            provider.next_turn([], [])
    assert error.value.kind == "connection"
    assert error.value.attempts == 1
    assert live.classify_failure(error.value) == "provider_connection"


def test_synchronous_timeout_has_stable_class():
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            release.wait(timeout=2)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = OpenAICompatibleProvider(f"http://127.0.0.1:{server.server_port}/v1", "fixture", timeout=0.05)
        with pytest.raises(ProviderRequestError) as error:
            provider.next_turn([], [])
        assert error.value.kind == "timeout"
        assert error.value.attempts == 1
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("elapsed", [0.000953, 0.025])
def test_failed_attempt_has_elapsed_time_and_independent_usage_flag(tmp_path, monkeypatch, elapsed):
    # Windows' event-loop clock can wake a short sleep early. Check the actual
    # measurement contract with an injected clock, not a sleep lower bound.
    ticks = iter((100.0, 100.0 + elapsed))
    monkeypatch.setattr(live, "perf_counter", lambda: next(ticks))

    async def runner(_run):
        await asyncio.sleep(0)
        raise ProviderRequestError("fixture-secret", kind="timeout", attempts=1)

    store = live.ResultStore(tmp_path, {})
    with pytest.raises(RuntimeError, match="usage"):
        asyncio.run(live.execute_runs(RUNS[:1], store, runner, input_price=1, output_price=2, max_cost_usd=1))
    record = store.load(RUNS[0].run_key)
    assert record["failure_class"] == "provider_timeout"
    assert record["usage_unknown"] is True
    assert record["wall_seconds"] == elapsed
    assert "fixture-secret" not in json.dumps(record)


def test_failed_review_keeps_fixture_hash(tmp_path):
    run = REVIEW_RUNS[1]
    source = (live.FIXTURE_ROOT / run.case_id / "public/service.py").read_bytes()
    with local_provider("service.py", "def ", http_status=401) as (url, _):
        async def runner(selected):
            return await live._run_review_case(selected, public_source=source,
                                               base_url=url, model="fixture", api_key="fixture-only")

        store = live.ResultStore(tmp_path, {})
        with pytest.raises(RuntimeError, match="usage"):
            asyncio.run(live.execute_runs([run], store, runner, input_price=1, output_price=2, max_cost_usd=1))
        assert len(store.load(run.run_key)["fixture_sha256"]) == 64


def test_nonfinite_previous_cost_blocks_resume(tmp_path):
    run = RUNS[0]
    store = live.ResultStore(tmp_path, {})
    (store.runs / f"{run.run_key.replace(':', '__')}.json").write_text(
        json.dumps({"run_key": run.run_key, "cost_usd": float("nan")}), encoding="utf-8",
    )
    calls = []

    async def runner(selected):
        calls.append(selected.run_key)
        return {}

    with pytest.raises(ValueError, match="cost"):
        asyncio.run(live.execute_runs(RUNS[:2], store, runner, input_price=1, output_price=2, max_cost_usd=1))
    assert not calls


def test_large_finite_price_does_not_overflow_before_per_million_division(tmp_path):
    async def runner(_run):
        return {"status": "completed", "answer": "offline", "wall_seconds": 0.1,
                "input_tokens": 12, "output_tokens": 9}

    results = asyncio.run(live.execute_runs(RUNS[:1], live.ResultStore(tmp_path, {}), runner,
                                           input_price=1e308, output_price=2, max_cost_usd=1))
    assert results[0]["cost_usd"] == pytest.approx(1.2e303)


def test_unrepresentable_cost_is_recorded_and_stops_further_attempts(tmp_path):
    calls = []

    async def runner(run):
        calls.append(run.run_key)
        return {"status": "completed", "answer": "offline", "wall_seconds": 0.1,
                "input_tokens": 10**310, "output_tokens": 9}

    store = live.ResultStore(tmp_path, {})
    with pytest.raises(RuntimeError, match="cost"):
        asyncio.run(live.execute_runs(RUNS[:2], store, runner, input_price=1e308, output_price=2, max_cost_usd=1))
    record = store.load(RUNS[0].run_key)
    assert record["failure_class"] == "cost_unavailable"
    assert record["usage_unknown"] is False
    assert record["cost_usd"] is None
    assert record["input_tokens"] == 10**310
    assert len(calls) == 1


def test_nonfinite_aggregate_does_not_destroy_summary():
    results = [{"status": "completed", "failure_class": None, "cost_usd": 1e308}] * 2
    report = live.summarize(RUNS[:2], results)
    assert report["reported_cost_usd"] is None
    assert report["aggregate_cost_unavailable"] is True
    json.dumps(report, allow_nan=False)
