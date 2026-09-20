import unittest

from support import workspace

from doppel_agent.persistence.tool_ledger import ToolExecutionLedger


class ToolIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))

    def test_completed_tool_call_is_replayed_without_duplicate_side_effect(self):
        ledger = ToolExecutionLedger(self.root / "ledger.sqlite3")
        calls = []

        def operation():
            calls.append("called")
            return "ok"

        first = ledger.execute_once("run", "call", "write", {"path": "a"}, operation)
        second = ledger.execute_once("run", "call", "write", {"path": "a"}, operation)

        self.assertEqual(first, ("ok", False))
        self.assertEqual(second, ("ok", True))
        self.assertEqual(calls, ["called"])

    def test_tool_call_id_cannot_be_reused_with_new_arguments(self):
        ledger = ToolExecutionLedger(self.root / "ledger.sqlite3")
        ledger.execute_once("run", "call", "write", {"path": "a"}, lambda: "ok")
        with self.assertRaisesRegex(ValueError, "different input"):
            ledger.execute_once("run", "call", "write", {"path": "b"}, lambda: "bad")
