import json
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_conversation_persists_and_is_sent_as_context(self):
        class CapturingProvider:
            def __init__(self):
                self.calls = []

            def next_turn(self, messages, tools):
                self.calls.append([(message.role, message.content) for message in messages])
                return ModelTurn(content=f"answer-{len(self.calls)}")

        provider = CapturingProvider()
        self.server.manager._provider = lambda config: provider
        _, conversation = self.post("/api/conversations", {"title": "新对话"})
        conversation_id = conversation["id"]

        for prompt in ("first question", "follow-up question"):
            _, submitted = self.post("/api/runs", {
                "prompt": prompt, "conversation_id": conversation_id,
                "config": {"provider": "mock"}, "allow_write": False,
            })
            for _ in range(100):
                job = json.loads(self.get(f"/api/runs/{submitted['run_id']}")[1])
                if job["status"] in ("completed", "failed"):
                    break
                time.sleep(0.03)
            self.assertEqual(job["status"], "completed")

        saved = json.loads(self.get(f"/api/conversations/{conversation_id}")[1])
        self.assertEqual([item["role"] for item in saved["messages"]], ["user", "assistant", "user", "assistant"])
        self.assertEqual(saved["title"], "first question")
        self.assertIn(("user", "first question"), provider.calls[1])
        self.assertIn(("assistant", "answer-1"), provider.calls[1])
        self.assertEqual(provider.calls[1][-1], ("user", "follow-up question"))

        replacement = ConsoleServer(("127.0.0.1", 0), self.root)
        try:
            self.assertEqual(replacement.manager.conversations.get(conversation_id)["messages"], saved["messages"])
        finally:
            replacement.server_close()

    def test_review_draft_is_reused_and_does_not_start_a_run(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            drafts = list(pool.map(
                lambda _: self.post("/api/conversations/review-draft", {})[1],
                range(12),
            ))

        self.assertEqual(len({draft["id"] for draft in drafts}), 1)
        self.assertTrue(all(draft["messages"] == [] for draft in drafts))
        conversations = json.loads(self.get("/api/conversations")[1])
        self.assertEqual([item["title"] for item in conversations], ["代码审查"])
        self.assertEqual(json.loads(self.get("/api/runs")[1]), [])

    def test_new_draft_is_reused_and_global_search_matches_messages(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            drafts = list(pool.map(
                lambda _: self.post("/api/conversations/new-draft", {})[1],
                range(10),
            ))
        self.assertEqual(len({draft["id"] for draft in drafts}), 1)

        conversation_id = drafts[0]["id"]
        self.server.manager.conversations.add_message(
            conversation_id, "user", "只存在消息正文中的检索哨兵词",
        )
        encoded = "%E6%A3%80%E7%B4%A2%E5%93%A8%E5%85%B5%E8%AF%8D"
        results = json.loads(self.get(f"/api/conversations/search?q={encoded}")[1])
        self.assertEqual([item["id"] for item in results], [conversation_id])

    def test_workspace_starts_two_pane_with_one_inspector_toggle(self):
        _, page, _ = self.get("/")
        html = page.decode("utf-8")
        self.assertIn('class="app-shell inspector-hidden"', html)
        self.assertEqual(html.count('id="toggle-inspector"'), 1)
        self.assertNotIn('id="toggle-sidebar"', html)
        self.assertNotIn('id="collapse-sidebar"', html)
        self.assertNotIn('id="server-status"', html)
        self.assertIn('id="open-search"', html)
        self.assertIn('id="review-options"', html)

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI test")
    def test_saved_api_key_is_dpapi_encrypted_and_never_returned(self):
        key = "SECRET-DPAPI-SENTINEL"
        _, saved = self.post("/api/settings", {
            "config": {
                "provider": "openai", "preset": "deepseek",
                "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": key,
            }
        })
        self.assertTrue(saved["api_key_saved"])
        self.assertNotIn("api_key", saved)
        raw = (self.root / ".doppel-agent" / "provider-settings.json").read_text(encoding="utf-8")
        self.assertNotIn(key, raw)
        self.assertEqual(self.server.manager.settings.api_key(), key)
        public = json.loads(self.get("/api/settings")[1])
        self.assertTrue(public["api_key_saved"])
        self.assertNotIn("api_key", public)
        trusted = self.server.manager._provider({"profile_id": saved["active_profile_id"]})
        self.assertEqual(trusted.api_key, key)
        redirected = self.server.manager._provider({
            "profile_id": saved["active_profile_id"],
            "base_url": "https://attacker.invalid", "model": "other-model",
        })
        self.assertEqual(redirected.api_key, "")

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

    def test_mcp_approval_shows_configured_command(self):
        (self.root / ".doppel").mkdir()
        (self.root / ".doppel" / "mcp.json").write_text(json.dumps({
            "servers": {"fixture": {"command": sys.executable, "args": ["server.py"]}}
        }), encoding="utf-8")

        class ScriptedProvider:
            calls = 0

            def next_turn(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurn(tool_calls=(ToolCall("mcp-1", "mcp_list", {"server": "fixture"}),))
                return ModelTurn(content="finished")

        self.server.manager._provider = lambda config: ScriptedProvider()
        _, submitted = self.post("/api/runs", {
            "prompt": "list MCP tools", "config": {"provider": "mock"}, "allow_mcp": True,
        })
        run_id = submitted["run_id"]
        for _ in range(100):
            approvals = json.loads(self.get(f"/api/runs/{run_id}/approvals")[1])
            if approvals:
                break
            time.sleep(0.05)
        self.assertEqual(len(approvals), 1, self.server.manager.status(run_id))
        self.assertEqual(approvals[0]["arguments"]["configured_command"], sys.executable)
        self.post(f"/api/runs/{run_id}/approvals/{approvals[0]['id']}/decision", {"allow": False})
        for _ in range(100):
            job = json.loads(self.get(f"/api/runs/{run_id}")[1])
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "completed", job)
        events = json.loads(self.get(f"/api/runs/{run_id}/events")[1])
        self.assertIn("tool_failed", [event["kind"] for event in events])


class ApprovalBrokerTests(unittest.TestCase):
    def test_timeout_denies(self):
        broker = ApprovalBroker(timeout_seconds=0.01)
        self.assertFalse(broker.request("workspace_write", "write_file", {"path": "x"}))
        self.assertEqual(broker.list_pending(), [])
