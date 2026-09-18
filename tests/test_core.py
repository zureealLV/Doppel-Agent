import json
import unittest

from doppel_agent.core import Core
from doppel_agent.provider import ModelTurn, ToolCall
from support import workspace


class RepeatingProvider:
    def next_turn(self, messages, tools):
        return ModelTurn(tool_calls=(ToolCall("repeat", "missing", {}),))


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))

    def test_mock_read_and_persistence(self):
        (self.root / "hello.txt").write_text("hello world", encoding="utf-8")
        result = Core(self.root).run("read hello.txt")
        self.assertEqual(result["status"], "completed")
        self.assertIn("hello world", result["answer"])
        run_dir = self.root / ".doppel-agent" / "runs" / result["run_id"]
        session = json.loads((run_dir / "session.json").read_text(encoding="utf-8"))
        events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        trace = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(session["status"], "completed")
        self.assertEqual([event["sequence"] for event in events], list(range(1, len(events) + 1)))
        self.assertIn("tool_completed", [event["kind"] for event in events])
        self.assertIn("tool_completed", [event["kind"] for event in trace])

    def test_tool_failure_returned_to_model(self):
        result = Core(self.root).run("read missing.txt")
        self.assertEqual(result["status"], "completed")
        self.assertIn("Tool error", result["answer"])

    def test_loop_budget(self):
        result = Core(self.root, RepeatingProvider()).run("go")
        self.assertEqual(result["status"], "failed")
        self.assertIn("max_steps_exceeded", result["answer"])
