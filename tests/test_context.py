import unittest

from doppel_agent.context.policy import ContextPolicy
from doppel_agent.provider import Message, ToolCall


class ContextPolicyTests(unittest.TestCase):
    def test_compacts_complete_tool_groups_preserving_goal(self):
        messages = [Message("system", "system invariant"), Message("user", "original task")]
        for index in range(5):
            call = ToolCall(f"call-{index}", "read_file", {"path": f"{index}.txt"})
            messages.extend([
                Message("assistant", "", tool_calls=(call,)),
                Message("tool", "x" * 1200, tool_call_id=call.id),
            ])
        compacted, report = ContextPolicy(limit_tokens=1200).compact(messages)
        self.assertTrue(report["compacted"])
        self.assertLess(report["estimated_after"], report["estimated_before"])
        self.assertEqual(compacted[0].content, "system invariant")
        self.assertEqual(compacted[1].content, "original task")
        self.assertTrue(any("compacted" in message.content for message in compacted))
        for index, message in enumerate(compacted):
            if message.role == "tool":
                self.assertEqual(compacted[index - 1].role, "assistant")

    def test_refuses_oversized_original_goal(self):
        messages = [Message("system", "rules"), Message("user", "x" * 3000)]
        with self.assertRaisesRegex(RuntimeError, "context_budget_exceeded"):
            ContextPolicy(limit_tokens=256).compact(messages)
