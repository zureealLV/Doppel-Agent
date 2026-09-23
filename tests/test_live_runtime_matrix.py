"""The paid matrix must fail closed before it can manufacture a score."""

import asyncio
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zipfile import ZipFile

from bench.live_runtime_matrix import (
    CANARY_CASE_IDS,
    ResultStore,
    _run_navigation_case,
    _run_review_case,
    classify_failure,
    execute_runs,
    review_queue,
    select_runs,
    summarize,
)
from bench.runtime_matrix import RuntimeMatrix
from doppel_agent.provider import ProviderRequestError


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "bench/cases/runtime/manifest.json"


class LiveRuntimeMatrixTests(unittest.TestCase):
    def setUp(self):
        self.matrix = RuntimeMatrix.load(MANIFEST)

    def test_canary_has_three_grounded_read_only_cases_for_each_runtime(self):
        runs = select_runs(self.matrix, "canary")
        self.assertEqual(len(runs), 9)
        self.assertEqual({run.case_id for run in runs}, set(CANARY_CASE_IDS))
        self.assertEqual({run.runtime for run in runs}, {"legacy", "graph", "deep"})
        self.assertEqual({run.repeat for run in runs}, {1})
        self.assertTrue(all(not run.permissions for run in runs))

    def test_full_matrix_fails_closed_until_all_cases_have_fixtures(self):
        with self.assertRaisesRegex(ValueError, "fixture"):
            select_runs(self.matrix, "full")

    def test_review_canary_has_four_grounded_cases_for_each_runtime(self):
        runs = select_runs(self.matrix, "review-canary")
        self.assertEqual(len(runs), 12)
        self.assertEqual({run.case_id for run in runs},
                         {"review-01", "review-02", "review-03", "review-04"})
        self.assertEqual({run.runtime for run in runs}, {"legacy", "graph", "deep"})
        self.assertTrue(all(run.repeat == 1 and not run.permissions for run in runs))

    def test_provider_error_has_stable_class_not_secret_message(self):
        error = ProviderRequestError("secret-token", kind="rate_limited", attempts=3, status_code=429)
        self.assertEqual(classify_failure(error), "provider_rate_limit")
        self.assertEqual(classify_failure(RuntimeError("arbitrary secret")), "internal_error")

    def test_store_rejects_config_drift_and_skips_recorded_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"protocol_sha256": "abc", "commit": "123", "model": "fixture"}
            store = ResultStore(root, config)
            store.save("nav-01:legacy:1", {"run_key": "nav-01:legacy:1", "status": "failed"})
            self.assertEqual(store.load("nav-01:legacy:1")["status"], "failed")
            self.assertEqual(ResultStore(root, config).load("nav-01:legacy:1")["status"], "failed")
            with self.assertRaisesRegex(ValueError, "configuration"):
                ResultStore(root, {**config, "model": "different"})
            self.assertEqual(len(list((root / "runs").glob("*.json"))), 1)

    def test_execution_keeps_failed_run_and_resume_does_not_repeat_it(self):
        runs = select_runs(self.matrix, "canary")[:2]
        calls = []

        async def fake_runner(run):
            calls.append(run.run_key)
            if run == runs[0]:
                return {"status": "failed", "answer": "secret from caught exception", "failure_class": "verification_failed",
                        "input_tokens": 10, "output_tokens": 5, "wall_seconds": 0.1,
                        "event_types": []}
            return {"status": "completed", "answer": "runtime", "input_tokens": 10,
                    "output_tokens": 5, "wall_seconds": 0.1, "event_types": []}

        with tempfile.TemporaryDirectory() as directory:
            store = ResultStore(Path(directory), {"model": "fixture"})
            first = asyncio.run(execute_runs(runs, store, fake_runner, input_price=1, output_price=2,
                                             max_cost_usd=1))
            self.assertEqual(len(first), 2)
            self.assertEqual(first[0]["failure_class"], "verification_failed")
            self.assertEqual(first[0]["answer"], "")
            second = asyncio.run(execute_runs(runs, store, fake_runner, input_price=1,
                                              output_price=2, max_cost_usd=1))
            self.assertEqual(second, first)
            self.assertEqual(len(calls), 2)
            report = summarize(runs, second)
            self.assertEqual(report["recorded_runs"], 2)
            self.assertFalse(report["task_quality_scored"])
            self.assertNotIn("success_rate", report)
            queue = review_queue(second)
            self.assertEqual(len(queue["items"]), 2)
            self.assertTrue(all(item["human_review"] == "pending" for item in queue["items"]))

    def test_exception_is_redacted_and_stops_for_unknown_spend(self):
        runs = select_runs(self.matrix, "canary")[:2]

        async def fake_runner(_run):
            raise RuntimeError("api-key-should-not-appear")

        with tempfile.TemporaryDirectory() as directory:
            store = ResultStore(Path(directory), {"model": "fixture"})
            with self.assertRaisesRegex(RuntimeError, "usage"):
                asyncio.run(execute_runs(runs, store, fake_runner, input_price=1,
                                         output_price=2, max_cost_usd=1))
            self.assertEqual(store.load(runs[0].run_key)["failure_class"], "internal_error")
            self.assertNotIn("api-key", json.dumps(store.load(runs[0].run_key)))
            self.assertIsNone(store.load(runs[1].run_key))

    def test_missing_usage_stops_further_paid_runs(self):
        runs = select_runs(self.matrix, "canary")[:2]
        calls = []

        async def fake_runner(run):
            calls.append(run.run_key)
            return {"status": "completed", "answer": "ok", "input_tokens": None,
                    "output_tokens": None, "wall_seconds": 0.1, "event_types": []}

        with tempfile.TemporaryDirectory() as directory:
            store = ResultStore(Path(directory), {"model": "fixture"})
            with self.assertRaisesRegex(RuntimeError, "usage"):
                asyncio.run(execute_runs(runs, store, fake_runner, input_price=1,
                                         output_price=2, max_cost_usd=1))
            self.assertEqual(len(calls), 1)
            self.assertEqual(store.load(runs[0].run_key)["failure_class"], "usage_unavailable")
            self.assertIsNone(store.load(runs[1].run_key))

    def test_local_http_fixture_exercises_three_live_runtime_adapters(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers["Content-Length"])
                request = json.loads(self.rfile.read(size))
                assert request["temperature"] == 0
                answer = {
                    "choices": [{"message": {"role": "assistant", "content": "runtime factory is in src/doppel_agent/runtime/factory.py"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
                }
                body = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        memory = io.BytesIO()
        with ZipFile(memory, "w") as archive:
            archive.writestr("src/doppel_agent/runtime/factory.py", "def create_runtime(): pass\n")
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            runs = tuple(run for run in select_runs(self.matrix, "canary") if run.case_id == "nav-01")
            async def exercise():
                return [await _run_navigation_case(
                    run, archive=memory.getvalue(),
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model="fixture", api_key="fixture-secret",
                ) for run in runs]
            results = asyncio.run(exercise())
            self.assertEqual([item["status"] for item in results], ["completed"] * 3)
            self.assertTrue(all(item["input_tokens"] >= 12 for item in results))
            self.assertTrue(all(item["output_tokens"] >= 9 for item in results))
            self.assertTrue(all("runtime.finished" in item["event_types"] for item in results))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_local_http_fixture_runs_blind_review_case(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers["Content-Length"])
                request = json.loads(self.rfile.read(size))
                assert "answer_key" not in json.dumps(request)
                body = json.dumps({
                    "choices": [{"message": {"role": "assistant", "content": "service.py:14 needs owner check"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
                }).encode()
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
            runs = tuple(run for run in select_runs(self.matrix, "review-canary")
                         if run.case_id == "review-01")
            source = (ROOT / "bench/cases/runtime/fixtures/review-01/public/service.py").read_bytes()
            async def exercise():
                return [await _run_review_case(
                    run, public_source=source,
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model="fixture", api_key="fixture-secret",
                ) for run in runs]
            results = asyncio.run(exercise())
            self.assertEqual([item["status"] for item in results], ["completed"] * 3)
            self.assertTrue(all(item["fixture_sha256"] for item in results))
            self.assertTrue(all(item["input_tokens"] >= 12 for item in results))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
