import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.web.server import ConsoleServer
from doppel_agent.web.approvals import ApprovalBroker
from support import workspace


class WebTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))
        self.server = ConsoleServer(("127.0.0.1", 0), self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def get(self, path):
        with urlopen(self.base + path, timeout=5) as response:
            return response.status, response.read(), response.headers

    def post(self, path, body, headers=None):
        request = Request(
            self.base + path, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Doppel-UI": "1", **(headers or {})},
        )
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)

    def test_serves_ui_and_health(self):
        status, html, headers = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"api-key", html)
        self.assertIn(b"run-form", html)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(json.loads(self.get("/api/health")[1])["status"], "ok")

    def test_probe_mock_and_run(self):
        self.assertTrue(self.post("/api/probe", {"config": {"provider": "mock"}})[1]["ok"])
        status, result = self.post("/api/runs", {
            "prompt": "read note.txt", "config": {"provider": "mock", "api_key": "SECRET-SENTINEL"},
            "allow_write": False, "allow_command": False,
        })
        self.assertEqual(status, 202)
        run_id = result["run_id"]
        for _ in range(50):
            job = json.loads(self.get(f"/api/runs/{run_id}")[1])
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "completed")
        self.assertIn("Tool error", job["answer"])
        events = json.loads(self.get(f"/api/runs/{run_id}/events")[1])
        self.assertTrue(events)
        self.assertEqual(json.loads(self.get(f"/api/runs/{run_id}/tasks")[1]), [])
        saved = (self.root / ".doppel-agent" / "runs" / run_id / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("SECRET-SENTINEL", saved)
        self.assertEqual(json.loads(self.get("/api/runs")[1])[0]["run_id"], run_id)
        replacement = ConsoleServer(("127.0.0.1", 0), self.root)
        try:
            self.assertEqual(replacement.manager.status(run_id)["status"], "completed")
            self.assertTrue(replacement.manager.events(run_id))
            self.assertEqual(replacement.manager.approvals(run_id), [])
            self.assertEqual(replacement.manager.recent()[0]["run_id"], run_id)
        finally:
            replacement.server_close()

    def test_rejects_cross_origin(self):
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/probe", {"config": {"provider": "mock"}}, {"Origin": "https://example.com"})
        self.assertEqual(caught.exception.code, 403)

    def test_manual_write_approval(self):
        class ScriptedProvider:
            calls = 0

            def next_turn(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurn(tool_calls=(ToolCall("write-1", "write_file", {"path": "approved.txt", "content": "approved"}),))
                return ModelTurn(content="finished")

        self.server.manager._provider = lambda config: ScriptedProvider()
        _, submitted = self.post("/api/runs", {
            "prompt": "write an approved file", "config": {"provider": "mock"}, "allow_write": True,
        })
        run_id = submitted["run_id"]
        for _ in range(100):
            approvals = json.loads(self.get(f"/api/runs/{run_id}/approvals")[1])
            if approvals:
                break
            time.sleep(0.05)
        self.assertEqual(len(approvals), 1, self.server.manager.status(run_id))
        self.assertFalse((self.root / "approved.txt").exists())
        approval_id = approvals[0]["id"]
        self.assertTrue(self.post(f"/api/runs/{run_id}/approvals/{approval_id}/decision", {"allow": True})[1]["ok"])
        for _ in range(100):
            job = json.loads(self.get(f"/api/runs/{run_id}")[1])
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual((self.root / "approved.txt").read_text(encoding="utf-8"), "approved")


class ApprovalBrokerTests(unittest.TestCase):
    def test_timeout_denies(self):
        broker = ApprovalBroker(timeout_seconds=0.01)
        self.assertFalse(broker.request("workspace_write", "write_file", {"path": "x"}))
        self.assertEqual(broker.list_pending(), [])
