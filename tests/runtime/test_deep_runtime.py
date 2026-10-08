import tempfile
import unittest
from pathlib import Path

from doppel_agent.provider import MockProvider, ModelTurn, ToolCall
from doppel_agent.runtime.base import ResumeCommand, RunRequest
from doppel_agent.runtime.deep import DeepAgentRuntime
from doppel_agent.runtime.factory import create_runtime
from doppel_agent.workspace.patching import PatchConflictError


class DeepRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_rejects_ambiguous_argument_dialects(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = DeepAgentRuntime(Path(directory), MockProvider(), max_subagents=0)
            pending = [{"action_requests": [{"name": "read_file", "args": {"path": "original.txt"}}]}]
            for other in ("different.txt", "edited.txt"):
                with self.subTest(other=other):
                    with self.assertRaisesRegex(ValueError, "ambiguous edited arguments"):
                        runtime._resume_value({"action": "edit", "tool_calls": [{
                            "name": "read_file", "args": {"path": "edited.txt"},
                            "arguments": {"path": other},
                        }]}, pending)

    async def test_offline_provider_runs_through_real_deep_agent_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = DeepAgentRuntime(root, MockProvider(), max_subagents=0)
            result = await runtime.run(RunRequest("hello"))
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.runtime, "deep")
            self.assertNotIn("fallback_runtime", result.metadata)
            self.assertEqual(result.metadata["subagent_limit"], 0)

    async def test_factory_enforces_two_subagent_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = create_runtime(
                "deep",
                root,
                MockProvider(),
                core_options={"allow_delegate": True, "max_steps": 12},
            )
            self.assertIsInstance(runtime, DeepAgentRuntime)
            self.assertEqual(runtime.max_subagents, 2)
            specs = runtime._subagents()
            self.assertEqual(len(specs), 2)
            self.assertTrue(all(spec["tools"] == [] for spec in specs))

    async def test_shell_and_delegation_are_not_exposed_without_grant(self):
        class CapturingProvider:
            def __init__(self):
                self.names = []

            def next_turn(self, messages, tools):
                self.names = [tool["function"]["name"] for tool in tools]
                return ModelTurn(content="done")

        with tempfile.TemporaryDirectory() as directory:
            provider = CapturingProvider()
            runtime = DeepAgentRuntime(Path(directory), provider, max_subagents=0)
            result = await runtime.run(RunRequest("inspect"))
            self.assertEqual(result.status, "completed")
            self.assertNotIn("execute", provider.names)
            self.assertNotIn("task", provider.names)

    async def test_validated_skill_catalog_reaches_deep_system_context(self):
        class CapturingProvider:
            def __init__(self):
                self.system = ""

            def next_turn(self, messages, tools):
                self.system = "\n".join(message.content for message in messages if message.role == "system")
                return ModelTurn(content="done")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review code with evidence\n---\nRead narrowly.",
                encoding="utf-8",
            )
            provider = CapturingProvider()
            result = await DeepAgentRuntime(root, provider, max_subagents=0).run(RunRequest("review"))
            self.assertEqual(result.status, "completed")
            self.assertIn("review", provider.system)
            self.assertIn("Review code with evidence", provider.system)

    async def test_write_uses_deep_hitl_and_constrained_backend(self):
        class WriteProvider:
            def __init__(self):
                self.turn = 0

            def next_turn(self, messages, tools):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurn(
                        tool_calls=(
                            ToolCall(
                                "deep-write",
                                "propose_patch",
                                {
                                    "changes": [
                                        {"path": "approved.txt", "content": "approved"}
                                    ]
                                },
                            ),
                        )
                    )
                return ModelTurn(content="written")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = DeepAgentRuntime(root, WriteProvider(), allow_write=True, max_subagents=0)
            request = RunRequest("write", run_id="c" * 32, thread_id="write-thread")
            interrupted = await runtime.run(request)
            self.assertEqual(interrupted.status, "interrupted")
            self.assertFalse((root / "approved.txt").exists())
            action = interrupted.metadata["interrupts"][0]["value"]["action_requests"][0]
            self.assertIn("approved", action["args"]["_doppel_patch"]["unified_diff"])
            completed = await runtime.resume(
                ResumeCommand(request.run_id, request.thread_id, {"action": "approve"})
            )
            self.assertEqual(completed.status, "completed")
            self.assertEqual((root / "approved.txt").read_text(encoding="utf-8"), "approved")

    async def test_approved_deep_patch_rejects_a_stale_workspace_base(self):
        class WriteProvider:
            def __init__(self):
                self.turn = 0

            def next_turn(self, messages, tools):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurn(
                        tool_calls=(
                            ToolCall(
                                "deep-stale",
                                "propose_patch",
                                {
                                    "changes": [
                                        {"path": "existing.txt", "content": "reviewed"}
                                    ]
                                },
                            ),
                        )
                    )
                return ModelTurn(content="written")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "existing.txt"
            target.write_text("original", encoding="utf-8")
            runtime = DeepAgentRuntime(root, WriteProvider(), allow_write=True, max_subagents=0)
            request = RunRequest("write", run_id="f" * 32, thread_id="stale-thread")
            self.assertEqual((await runtime.run(request)).status, "interrupted")
            target.write_text("external", encoding="utf-8")

            with self.assertRaisesRegex(PatchConflictError, "stale patch base"):
                await runtime.resume(
                    ResumeCommand(request.run_id, request.thread_id, {"action": "approve"})
                )

            self.assertEqual(target.read_text(encoding="utf-8"), "external")

    async def test_native_args_edit_reprepares_two_file_patch_after_runtime_recreation(self):
        class WriteProvider:
            def __init__(self):
                self.turn = 0

            def next_turn(self, messages, tools):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurn(tool_calls=(ToolCall("native-edit", "propose_patch", {
                        "changes": [{"path": "existing.txt", "content": "proposed existing\n"},
                                    {"path": "created.txt", "content": "proposed created\n"}],
                    }),))
                return ModelTurn(content="tool result received")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "existing.txt").write_bytes(b"original\r\n")
            (root / "dirty.txt").write_bytes(b"unrelated dirty\r\n")
            provider = WriteProvider()
            runtime = DeepAgentRuntime(root, provider, allow_write=True, allow_command=True,
                                       require_verification_review=True, max_subagents=0)
            request = RunRequest("edit", run_id="a" * 32, thread_id="native-args-edit-thread")
            interrupted = await runtime.run(request)
            self.assertEqual(interrupted.status, "interrupted")
            prepared = [item["value"] for item in interrupted.metadata["interrupts"]]
            original = prepared[0]["action_requests"][0]
            edited = {"name": original["name"], "args": {"changes": [
                {"path": "existing.txt", "content": "user-edited existing\n"},
                {"path": "created.txt", "content": "user-edited created\n"},
            ]}}
            self.assertEqual(runtime.ledger.list_patch_receipts(request.run_id), [])
            runtime = DeepAgentRuntime(root, provider, allow_write=True, allow_command=True,
                                       require_verification_review=True, max_subagents=0)
            completed = await runtime.resume(ResumeCommand(request.run_id, request.thread_id, {
                "action": "edit", "tool_calls": [edited], "_prepared_interrupts": prepared,
            }))
            self.assertEqual(completed.status, "completed")
            self.assertNotIn("fallback_runtime", completed.metadata)
            self.assertEqual((root / "existing.txt").read_bytes(), b"user-edited existing\n")
            self.assertEqual((root / "created.txt").read_bytes(), b"user-edited created\n")
            self.assertEqual((root / "dirty.txt").read_bytes(), b"unrelated dirty\r\n")
            rows = runtime.ledger.list_patch_receipts(request.run_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["tool_call_id"], "native-edit")
            self.assertTrue(rows[0]["confirmed_applied"])
