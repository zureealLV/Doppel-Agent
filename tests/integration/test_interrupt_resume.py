import asyncio
import unittest

from support import workspace

from doppel_agent.permissions import PermissionManager
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime import ResumeCommand, RunRequest
from doppel_agent.runtime.graph import GraphRuntime
from doppel_agent.tools import Tool, ToolRegistry


class WriteProvider:
    def next_turn(self, messages, tools):
        if messages[-1].role == "tool":
            return ModelTurn(content=messages[-1].content)
        return ModelTurn(tool_calls=(ToolCall("write-1", "side_effect", {"value": "hello"}),))


class InterruptResumeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))

    def runtime(self, calls):
        registry = ToolRegistry(PermissionManager(frozenset({"workspace_write"})))

        def side_effect(arguments):
            calls.append(arguments["value"])
            return f"wrote {arguments['value']}"

        registry.register(
            Tool(
                "side_effect",
                "Record one test side effect",
                "workspace_write",
                {"value": {"type": "string"}},
                side_effect,
            )
        )
        return GraphRuntime(
            self.root,
            WriteProvider(),
            tools=registry,
            checkpoint_path=self.root / "state" / "checkpoints.sqlite3",
        )

    def test_approval_interrupt_pauses_before_side_effect_and_resumes_once(self):
        calls = []
        runtime = self.runtime(calls)
        request = RunRequest("write", run_id="a1", thread_id="approval-thread")

        paused = asyncio.run(runtime.run(request))
        self.assertEqual(paused.status, "interrupted")
        self.assertEqual(calls, [])
        self.assertEqual(paused.metadata["interrupts"][0]["value"]["kind"], "tool_approval")

        finished = asyncio.run(runtime.resume(ResumeCommand("a1", "approval-thread", {"action": "approve"})))
        self.assertEqual(finished.status, "completed")
        self.assertEqual(calls, ["hello"])
        self.assertIn("wrote hello", finished.answer)

    def test_rejection_returns_to_model_without_side_effect(self):
        calls = []
        runtime = self.runtime(calls)
        request = RunRequest("write", run_id="a2", thread_id="reject-thread")
        self.assertEqual(asyncio.run(runtime.run(request)).status, "interrupted")

        finished = asyncio.run(runtime.resume(ResumeCommand("a2", "reject-thread", {"action": "reject"})))
        self.assertEqual(finished.status, "completed")
        self.assertEqual(calls, [])
        self.assertIn("rejected by user", finished.answer)

    def test_edit_replaces_arguments_before_side_effect(self):
        calls = []
        runtime = self.runtime(calls)
        request = RunRequest("write", run_id="a3", thread_id="edit-thread")
        self.assertEqual(asyncio.run(runtime.run(request)).status, "interrupted")

        finished = asyncio.run(
            runtime.resume(
                ResumeCommand(
                    "a3",
                    "edit-thread",
                    {
                        "action": "edit",
                        "tool_calls": [
                            {
                                "id": "write-1",
                                "name": "side_effect",
                                "arguments": {"value": "edited"},
                            }
                        ],
                    },
                )
            )
        )
        self.assertEqual(finished.status, "completed")
        self.assertEqual(calls, ["edited"])
        self.assertIn("wrote edited", finished.answer)
