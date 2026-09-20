import asyncio
import unittest

from support import workspace

from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime import RunRequest, create_runtime


class DirectProvider:
    def next_turn(self, messages, tools):
        return ModelTurn(content="direct answer")


class ReadProvider:
    def next_turn(self, messages, tools):
        if messages[-1].role == "tool":
            return ModelTurn(content=f"observed: {messages[-1].content}")
        return ModelTurn(tool_calls=(ToolCall("call-1", "read_file", {"path": "hello.txt"}),))


class MissingProvider:
    def next_turn(self, messages, tools):
        if messages[-1].role == "tool":
            return ModelTurn(content=messages[-1].content)
        return ModelTurn(tool_calls=(ToolCall("call-1", "read_file", {"path": "missing.txt"}),))


class BudgetProvider:
    def next_turn(self, messages, tools):
        return ModelTurn(tool_calls=(ToolCall("repeat", "list_files", {"path": "."}),))


class FocusedGraphTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))

    def run_graph(self, provider, prompt="go", **options):
        runtime = create_runtime("graph", self.root, provider, core_options=options)
        return asyncio.run(runtime.run(RunRequest(prompt)))

    def test_direct_answer(self):
        result = self.run_graph(DirectProvider())
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer, "direct answer")

    def test_tool_result_returns_to_model(self):
        (self.root / "hello.txt").write_text("hello graph", encoding="utf-8")
        result = self.run_graph(ReadProvider())
        self.assertEqual(result.status, "completed")
        self.assertIn("hello graph", result.answer)
        self.assertEqual(result.metadata["step_count"], 2)

    def test_tool_error_returns_to_model(self):
        result = self.run_graph(MissingProvider())
        self.assertEqual(result.status, "completed")
        self.assertIn("Tool error", result.answer)

    def test_final_turn_rejects_tool_calls(self):
        result = self.run_graph(BudgetProvider(), max_steps=2)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.metadata["error"]["code"], "tool_call_after_budget")

    def test_unknown_tool_is_returned_as_an_observation_not_a_graph_crash(self):
        class UnknownToolProvider:
            calls = 0

            def next_turn(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurn(tool_calls=(ToolCall("unknown-1", "not_registered", {}),))
                return ModelTurn(content=messages[-1].content)

        result = self.run_graph(UnknownToolProvider())
        self.assertEqual(result.status, "completed")
        self.assertIn("unknown tool", result.answer)
