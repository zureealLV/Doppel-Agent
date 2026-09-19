import importlib.util
import sqlite3
import unittest
from pathlib import Path

from bench.run_review import PROMPT, read_key, run_case
from doppel_agent.provider import ModelTurn, ToolCall
from support import workspace


SOURCE = Path(__file__).resolve().parents[1] / "bench" / "cases" / "review_001" / "service.py"
SPEC = importlib.util.spec_from_file_location("review_001_service", SOURCE)
SERVICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVICE)


class ReviewBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))

    def test_fixture_exhibits_cross_owner_read_and_sql_injection(self):
        with sqlite3.connect(":memory:") as connection:
            SERVICE.create_schema(connection)
            connection.executemany(
                "INSERT INTO notes (id, owner_id, title, body) VALUES (?, ?, ?, ?)",
                [(1, "alice", "Alice memo", "private A"), (2, "bob", "Bob memo", "private B")],
            )
            self.assertEqual(SERVICE.fetch_note(connection, "alice", 2)["body"], "private B")
            injected = SERVICE.search_notes(connection, "alice", "%' OR 1=1 --")
            self.assertEqual({row[1] for row in injected}, {"alice", "bob"})
            self.assertFalse(SERVICE.update_note_title(connection, "alice", 2, "stolen"))

    def test_fixture_exhibits_path_traversal(self):
        root = self.root / "attachments"
        root.mkdir()
        target = SERVICE.save_attachment(root, "../escaped.txt", b"unexpected")
        self.assertEqual(target.resolve(), self.root / "escaped.txt")
        self.assertTrue(target.is_file())

    def test_runner_isolated_and_requires_file_inspection(self):
        class ScriptedProvider:
            def next_turn(self, messages, tools):
                if messages[-1].role == "user":
                    return ModelTurn(tool_calls=(ToolCall("read-1", "read_file", {"path": "service.py"}),))
                return ModelTurn(content="Review result from scripted provider")

        output, report = run_case(ScriptedProvider(), "scripted-fixture", self.root / "results")
        self.assertTrue(output.is_file())
        self.assertIn("只读代码审核", PROMPT)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["tool_requests"][0]["arguments"], {"path": "service.py"})
        self.assertEqual(report["evaluation"], "pending_human_adjudication")
        self.assertNotIn("expected_findings", report)

    def test_env_key_reader_does_not_write_secret(self):
        env = self.root / ".env"
        env.write_text("# test\nDEEPSEEK_API_KEY='test-secret-value'\n", encoding="utf-8")
        self.assertEqual(read_key(env), "test-secret-value")
        self.assertEqual(list(self.root.iterdir()), [env])

    def test_env_key_reader_accepts_raw_test_key(self):
        env = self.root / ".env"
        env.write_text("sk-test-raw-key\n", encoding="utf-8")
        self.assertEqual(read_key(env), "sk-test-raw-key")
