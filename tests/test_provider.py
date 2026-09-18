import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from doppel_agent.core import Core
from doppel_agent.provider import OpenAICompatibleProvider
from support import workspace


class FixtureHandler(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        self.requests.append(request)
        if len(self.requests) == 1:
            message = {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "write_file", "arguments": json.dumps({"path": "result.txt", "content": "created"})},
                }],
            }
        else:
            message = {"role": "assistant", "content": "Done."}
        payload = json.dumps({"choices": [{"message": message}], "usage": {"prompt_tokens": 42, "completion_tokens": 11, "total_tokens": 53}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


class ProviderTests(unittest.TestCase):
    def test_local_chat_completion_tool_round_trip(self):
        with workspace() as root:
            FixtureHandler.requests = []
            server = HTTPServer(("127.0.0.1", 0), FixtureHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                provider = OpenAICompatibleProvider(f"http://127.0.0.1:{server.server_port}/v1", "fixture")
                result = Core(root, provider, allow_write=True).run("create result.txt")
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["answer"], "Done.")
                self.assertEqual((root / "result.txt").read_text(encoding="utf-8"), "created")
                self.assertEqual(len(FixtureHandler.requests), 2)
                second_messages = FixtureHandler.requests[1]["messages"]
                self.assertEqual(second_messages[-1]["role"], "tool")
                self.assertEqual(second_messages[-1]["tool_call_id"], "call_1")
                events_path = root / ".doppel-agent" / "runs" / result["run_id"] / "events.jsonl"
                self.assertIn("model_usage", events_path.read_text(encoding="utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_remote_http_rejected(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleProvider("http://example.com/v1", "model")

    def test_cli_ask_with_local_provider(self):
        with workspace() as root:
            FixtureHandler.requests = []
            server = HTTPServer(("127.0.0.1", 0), FixtureHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env["DOPPEL_AGENT_BASE_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
                env["DOPPEL_AGENT_MODEL"] = "fixture"
                completed = subprocess.run(
                    [sys.executable, "-m", "doppel_agent.cli", "ask", "create a file", "--workspace", str(root), "--allow-write"],
                    env=env, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
                self.assertEqual(json.loads(completed.stdout)["answer"], "Done.")
                self.assertEqual((root / "result.txt").read_text(encoding="utf-8"), "created")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
