"""S9 definitions for the existing synchronous Core's product review bridge."""

import asyncio
import json
import threading

import pytest

from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.base import ResumeCommand, RunRequest
from doppel_agent.runtime.legacy import LegacyRuntime


class TwoPatches:
    def __init__(self):
        self.turns = 0

    def next_turn(self, messages, tools):
        self.turns += 1
        assert "write_file" not in {item["function"]["name"] for item in tools}
        if self.turns == 1:
            return ModelTurn(tool_calls=(
                ToolCall("first", "propose_patch", {"changes": [{"path": "one.py", "content": "first\n"}]}),
                ToolCall("second", "propose_patch", {"changes": [{"path": "two.py", "content": "second\n"}]}),
            ))
        return ModelTurn(content=json.dumps([item.content for item in messages if item.role == "tool"]))


def make_runtime(root, provider):
    return LegacyRuntime(root, provider, reviewed=True, core_options={"allow_write": True})


def command(result, action="approve", **kwargs):
    return ResumeCommand(result.run_id, result.thread_id, {
        "action": action, "_legacy_interrupt_id": result.metadata["interrupts"][0]["id"], **kwargs,
    })


def test_legacy_continues_actual_core_pending_calls_without_reissuing_model_or_prior_effect(tmp_path, monkeypatch):
    from doppel_agent.core import Core

    original, core_calls = Core.run, []

    def observed(self, *args, **kwargs):
        core_calls.append(kwargs.get("continuation"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Core, "run", observed)
    provider = TwoPatches()

    async def scenario():
        result = await make_runtime(tmp_path, provider).run(RunRequest("fixture", run_id="a" * 32, thread_id="b" * 32))
        assert result.status == "interrupted" and provider.turns == 1
        assert not (tmp_path / "one.py").exists()
        result = await make_runtime(tmp_path, provider).resume(command(result))
        assert result.status == "interrupted" and provider.turns == 1
        assert (tmp_path / "one.py").read_bytes() == b"first\n"
        assert not (tmp_path / "two.py").exists()
        (tmp_path / "one.py").write_bytes(b"later user edit\n")
        result = await make_runtime(tmp_path, provider).resume(command(result))
        assert result.status == "completed" and provider.turns == 2
        assert (tmp_path / "one.py").read_bytes() == b"later user edit\n"
        assert (tmp_path / "two.py").read_bytes() == b"second\n"
        ledger = ToolExecutionLedger(tmp_path / ".doppel-agent" / "tool-executions.sqlite3")
        assert len(ledger.list_patch_receipts(result.run_id)) == 2
        assert len(core_calls) == 3 and core_calls[0] is None and all(core_calls[1:])

    asyncio.run(scenario())


@pytest.mark.parametrize("stale", [False, True])
def test_legacy_edit_is_pinned_to_the_original_base_and_does_not_advance_on_invalid_edit(tmp_path, stale):
    provider = TwoPatches()

    async def scenario():
        runtime = make_runtime(tmp_path, provider)
        result = await runtime.run(RunRequest("fixture", run_id="2" * 32, thread_id="3" * 32))
        if stale:
            (tmp_path / "one.py").write_bytes(b"user changed base\n")
        edited = {"id": "first", "name": "propose_patch", "arguments": {
            "changes": [{"path": "one.py", "content": "reviewer edited\n"}],
        }}
        result = await runtime.resume(command(result, "edit", tool_calls=[edited]))
        assert result.status == ("failed" if stale else "interrupted")
        assert provider.turns == 1 and not (tmp_path / "two.py").exists()
        assert (tmp_path / "one.py").read_bytes() == (b"user changed base\n" if stale else b"reviewer edited\n")
        ledger = ToolExecutionLedger(tmp_path / ".doppel-agent" / "tool-executions.sqlite3")
        assert len(ledger.list_patch_receipts(result.run_id)) == (0 if stale else 1)

    asyncio.run(scenario())


def test_legacy_repeated_cancel_drains_approved_core_effect_and_does_not_advance(tmp_path, monkeypatch):
    from doppel_agent.workspace.patching import PatchService

    provider, started, release, events = TwoPatches(), threading.Event(), threading.Event(), []
    original = PatchService.apply

    def gated(self, proposal, **kwargs):
        started.set()
        assert release.wait(5)
        return original(self, proposal, **kwargs)

    class Sink:
        async def emit(self, kind, **payload):
            events.append((kind, payload))

    async def scenario():
        runtime = make_runtime(tmp_path, provider)
        pending = await runtime.run(RunRequest("fixture", run_id="4" * 32, thread_id="5" * 32))
        monkeypatch.setattr(PatchService, "apply", gated)
        task = asyncio.create_task(runtime.resume(command(pending), Sink()))
        try:
            async with asyncio.timeout(3):
                while not started.is_set():
                    await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert not task.done() and not (tmp_path / "one.py").exists()
        finally:
            release.set()
            result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert provider.turns == 1 and not (tmp_path / "two.py").exists()
        ledger = ToolExecutionLedger(tmp_path / ".doppel-agent" / "tool-executions.sqlite3")
        assert ledger.read_applied_patch(pending.run_id, "first").status == "applied"
        effects = [payload for kind, payload in events if kind == "patch.applied"]
        assert len(effects) == 1 and effects[0]["cancel_requested"]

    asyncio.run(scenario())


def test_legacy_generic_review_keeps_arguments_and_accepts_nullable_http_default(tmp_path, monkeypatch):
    calls = []

    class CommandProvider:
        def next_turn(self, messages, tools):
            if any(message.role == "tool" for message in messages):
                return ModelTurn(content="fixture done")
            return ModelTurn(tool_calls=(ToolCall("command", "run_command", {"argv": ["fixture-no-execution", "arg"]}),))

    async def fake_execution(self, tools, call, run_id, sink, stopped):
        calls.append(dict(call.arguments))
        return "fake command transport, not native evidence"

    monkeypatch.setattr(LegacyRuntime, "_execute_call", fake_execution)

    async def scenario():
        runtime = LegacyRuntime(tmp_path, CommandProvider(), reviewed=True, core_options={"allow_command": True})
        pending = await runtime.run(RunRequest("fixture", run_id="6" * 32, thread_id="7" * 32))
        assert pending.metadata["interrupts"][0]["value"]["tool_calls"][0]["arguments"] == {
            "argv": ["fixture-no-execution", "arg"],
        }
        result = await runtime.resume(command(pending, tool_calls=None))
        assert result.status == "completed" and calls == [{"argv": ["fixture-no-execution", "arg"]}]

    asyncio.run(scenario())


def test_legacy_rejects_one_pending_effect_then_reviews_the_next(tmp_path):
    provider = TwoPatches()

    async def scenario():
        runtime = make_runtime(tmp_path, provider)
        result = await runtime.run(RunRequest("fixture", run_id="c" * 32, thread_id="d" * 32))
        result = await runtime.resume(command(result, "reject"))
        assert result.status == "interrupted" and provider.turns == 1
        assert not (tmp_path / "one.py").exists()
        result = await runtime.resume(command(result))
        assert result.status == "completed" and "rejected by user" in result.answer
        assert not (tmp_path / "one.py").exists()

    asyncio.run(scenario())


def test_legacy_foreign_scope_and_consumed_decision_cannot_execute(tmp_path):
    provider = TwoPatches()

    async def scenario():
        runtime = make_runtime(tmp_path, provider)
        result = await runtime.run(RunRequest("fixture", run_id="e" * 32, thread_id="f" * 32))
        approved = command(result)
        with pytest.raises(ValueError, match="legacy_review_scope"):
            await runtime.resume(ResumeCommand(result.run_id, "1" * 32, approved.value))
        with pytest.raises(ValueError, match="legacy_review_scope"):
            await runtime.resume(ResumeCommand(result.run_id, result.thread_id, {"action": "approve", "_legacy_interrupt_id": "wrong"}))
        result = await runtime.resume(approved)
        with pytest.raises(ValueError, match="legacy_review_scope"):
            await runtime.resume(approved)
        assert result.status == "interrupted" and not (tmp_path / "two.py").exists()

    asyncio.run(scenario())
