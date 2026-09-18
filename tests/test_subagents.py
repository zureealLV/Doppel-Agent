import json
import unittest

from doppel_agent.core import Core
from doppel_agent.events import EventBus
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.subagents import DelegateManager
from support import workspace


class DelegationTests(unittest.TestCase):
    def test_child_reads_but_cannot_write_or_delegate_again(self):
        class ScriptedProvider:
            def next_turn(self, messages, tools):
                names = {item["function"]["name"] for item in tools}
                if "delegate_readonly" in names:
                    if messages[-1].role == "user":
                        return ModelTurn(tool_calls=(ToolCall("parent-1", "delegate_readonly", {"prompt": "Inspect note.txt"}),))
                    return ModelTurn(content="parent finished")
                if messages[-1].role == "user":
                    assert names == {"read_file", "list_files"}
                    return ModelTurn(tool_calls=(ToolCall("child-1", "read_file", {"path": "note.txt"}),))
                if messages[-1].content == "hello":
                    return ModelTurn(tool_calls=(ToolCall("child-2", "write_file", {"path": "bad.txt", "content": "bad"}),))
                return ModelTurn(content="child reported hello")

        with workspace() as root:
            (root / "note.txt").write_text("hello", encoding="utf-8")
            result = Core(root, ScriptedProvider(), allow_delegate=True).run("inspect")
            self.assertEqual(result["status"], "completed", result)
            self.assertFalse((root / "bad.txt").exists())
            events = [json.loads(line) for line in (root / ".doppel-agent" / "runs" / result["run_id"] / "events.jsonl").read_text().splitlines()]
            kinds = [event["kind"] for event in events]
            self.assertIn("subagent_started", kinds)
            self.assertIn("subagent_completed", kinds)
            self.assertTrue(any(event["kind"] == "subagent_event" and event["payload"]["child_kind"] == "tool_failed" for event in events))

    def test_budget_and_prompt_limit(self):
        with workspace() as root:
            manager = DelegateManager(root, lambda: None, EventBus("run", lambda event: None), max_delegations=0)
            with self.assertRaisesRegex(ValueError, "budget exhausted"):
                manager.execute({"prompt": "task"})
            with self.assertRaisesRegex(ValueError, "4000"):
                manager.execute({"prompt": "x" * 4001})

    def test_not_exposed_without_grant(self):
        class Provider:
            def next_turn(self, messages, tools):
                assert "delegate_readonly" not in {item["function"]["name"] for item in tools}
                return ModelTurn(content="done")

        with workspace() as root:
            self.assertEqual(Core(root, Provider()).run("hi")["status"], "completed")
