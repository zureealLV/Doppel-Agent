import time
import threading
import unittest
import asyncio

from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider, ModelTurn, ToolCall
from doppel_agent.web.server import ConsoleServer
from support import workspace


class RunsApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))

    @staticmethod
    def wait(client, run_id, statuses=("completed", "failed", "interrupted")):
        for _ in range(200):
            record = client.get(f"/api/v1/runs/{run_id}").json()
            if record["status"] in statuses:
                return record
            time.sleep(0.01)
        raise AssertionError(record)

    def test_create_get_and_idempotency_replay(self):
        with TestClient(create_app(self.root, provider=MockProvider())) as client:
            body = {
                "prompt": "hello",
                "mode": "graph",
                "idempotency_key": "same-request-key",
            }
            first = client.post("/api/v1/runs", json=body)
            self.assertEqual(first.status_code, 202, first.text)
            replay = client.post("/api/v1/runs", json=body)
            self.assertEqual(replay.status_code, 202, replay.text)
            self.assertEqual(first.json()["run_id"], replay.json()["run_id"])
            self.assertTrue(replay.json()["replayed"])
            record = self.wait(client, first.json()["run_id"])
            self.assertEqual(record["status"], "completed")
            self.assertIn("Offline mock", record["answer"])

    def test_unknown_run_is_404(self):
        with TestClient(create_app(self.root, provider=MockProvider())) as client:
            self.assertEqual(client.get("/api/v1/runs/missing").status_code, 404)

    def test_write_interrupt_resume_and_durable_events(self):
        class WriteProvider:
            calls = 0

            def next_turn(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurn(
                        tool_calls=(
                            ToolCall(
                                "write-1",
                                "write_file",
                                {"path": "approved.txt", "content": "approved"},
                            ),
                        )
                    )
                return ModelTurn(content="finished")

        with TestClient(create_app(self.root, provider=WriteProvider())) as client:
            response = client.post(
                "/api/v1/runs",
                json={
                    "prompt": "write",
                    "permissions": {"workspace_write": True},
                },
            )
            run_id = response.json()["run_id"]
            paused = self.wait(client, run_id)
            self.assertEqual(paused["status"], "interrupted")
            self.assertFalse((self.root / "approved.txt").exists())
            interrupt_id = paused["metadata"]["interrupts"][0]["id"]
            resumed = client.post(
                f"/api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume",
                json={"action": "approve"},
            )
            self.assertEqual(resumed.status_code, 202, resumed.text)
            completed = self.wait(client, run_id, statuses=("completed", "failed"))
            self.assertEqual(completed["status"], "completed", completed)
            self.assertEqual(
                (self.root / "approved.txt").read_text(encoding="utf-8"),
                "approved",
            )
            events = client.get(f"/api/v1/runs/{run_id}/events").json()
            event_types = [event["type"] for event in events]
            self.assertIn("approval.requested", event_types)
            self.assertIn("approval.decided", event_types)
            self.assertEqual(
                [event["seq"] for event in events],
                sorted(event["seq"] for event in events),
            )

    def test_queue_full_returns_429(self):
        class SlowProvider:
            def next_turn(self, messages, tools):
                time.sleep(0.25)
                return ModelTurn(content="done")

        app = create_app(self.root, provider=SlowProvider(), max_active_runs=1, queue_capacity=1)
        with TestClient(app) as client:
            first = client.post("/api/v1/runs", json={"prompt": "one"})
            self.assertEqual(first.status_code, 202)
            time.sleep(0.03)
            second = client.post("/api/v1/runs", json={"prompt": "two"})
            self.assertEqual(second.status_code, 202)
            third = client.post("/api/v1/runs", json={"prompt": "three"})
            self.assertEqual(third.status_code, 429, third.text)

    def test_expired_interrupt_is_gone_not_generic_failure(self):
        class WriteProvider:
            def next_turn(self, messages, tools):
                return ModelTurn(
                    tool_calls=(
                        ToolCall(
                            "write-expired",
                            "write_file",
                            {"path": "never.txt", "content": "no"},
                        ),
                    )
                )

        app = create_app(self.root, provider=WriteProvider(), approval_ttl_seconds=0)
        with TestClient(app) as client:
            run_id = client.post(
                "/api/v1/runs",
                json={
                    "prompt": "write",
                    "permissions": {"workspace_write": True},
                },
            ).json()["run_id"]
            paused = self.wait(client, run_id)
            interrupt_id = paused["metadata"]["interrupts"][0]["id"]
            response = client.post(
                f"/api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume",
                json={"action": "approve"},
            )
            self.assertEqual(response.status_code, 410, response.text)
            expired = client.get(f"/api/v1/runs/{run_id}").json()
            self.assertEqual(expired["status"], "interrupted_expired")
            self.assertFalse((self.root / "never.txt").exists())

    def test_legacy_ui_is_available_through_same_origin_proxy(self):
        legacy = ConsoleServer(("127.0.0.1", 0), self.root)
        worker = threading.Thread(target=legacy.serve_forever, daemon=True)
        worker.start()
        app = create_app(
            self.root,
            provider=MockProvider(),
            legacy_base_url=f"http://127.0.0.1:{legacy.server_port}",
        )
        try:
            with TestClient(app) as client:
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertIn("run-form", page.text)
                self.assertEqual(client.get("/api/health").json()["status"], "ok")
                self.assertEqual(client.get("/api/v1/health").json()["status"], "ok")
        finally:
            legacy.shutdown()
            worker.join(timeout=3)
            legacy.server_close()

    def test_cancel_running_async_provider_emits_one_terminal_event(self):
        class SlowAsyncProvider:
            async def anext_turn(self, messages, tools):
                await asyncio.sleep(30)

        with TestClient(create_app(self.root, provider=SlowAsyncProvider())) as client:
            run_id = client.post("/api/v1/runs", json={"prompt": "slow"}).json()["run_id"]
            for _ in range(100):
                record = client.get(f"/api/v1/runs/{run_id}").json()
                if record["status"] == "running":
                    break
                time.sleep(0.01)
            cancelled = client.post(f"/api/v1/runs/{run_id}/cancel")
            self.assertTrue(cancelled.json()["cancel_requested"])
            terminal = self.wait(client, run_id, statuses=("cancelled",))
            self.assertEqual(terminal["status"], "cancelled")
            events = client.get(f"/api/v1/runs/{run_id}/events").json()
            self.assertEqual([event["type"] for event in events].count("run.cancelled"), 1)
