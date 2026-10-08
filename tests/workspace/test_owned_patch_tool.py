"""B2a owned transport definitions, deferred to S9 whole-version execution."""

import asyncio
import json
import threading

import pytest

from doppel_agent.concurrency.limits import ResourceLimits
from doppel_agent.graph.nodes import FocusedGraphNodes
from doppel_agent.permissions import PermissionManager
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.provider import MockProvider, ToolCall
from doppel_agent.tools import ToolRegistry, patch_tool
from doppel_agent.workspace.patching import PatchService
from doppel_agent.workspace.tool_adapter import langchain_patch_tool, reset_patch_run_id, set_patch_run_id
from doppel_agent.workspace.verification import VerificationReport


class Sink:
    def __init__(self):
        self.events = []

    async def emit(self, kind, **payload):
        self.events.append((kind, payload))


class Verification:
    available = ("fixture",)

    def __init__(self, ledger, *, outcome="failed"):
        self.ledger, self.outcome, self.calls = ledger, outcome, 0
        self.started = self.release = None

    async def run(self, *, run_id):
        self.calls += 1
        rows = await asyncio.to_thread(self.ledger.list_patch_receipts, run_id)
        assert len(rows) == 1 and rows[0]["confirmed_applied"]
        if self.started is not None:
            self.started.set()
            await self.release.wait()
        if self.outcome == "raise":
            raise OSError("fixture private exception must not appear")
        return VerificationReport(self.outcome == "success", ())


def setup(tmp_path, *, verification=False, command_granted=True, limits=None):
    root = tmp_path / "owned"
    root.mkdir()
    (root / "file.py").write_bytes(b"old\n")
    ledger = ToolExecutionLedger(tmp_path / "tool-executions.sqlite3")
    pipeline = Verification(ledger) if verification else None
    tool = patch_tool(root, verification=pipeline, resource_limits=limits)
    permissions = {"workspace_write", "command_execute"} if command_granted else {"workspace_write"}
    registry = ToolRegistry(PermissionManager(frozenset(permissions)))
    registry.register(tool)
    arguments = registry.prepare_approval("propose_patch", {"changes": [{"path": "file.py", "content": "agent\n"}]})
    sink = Sink()
    nodes = FocusedGraphNodes(MockProvider(), registry, ledger=ledger, sink=sink)
    return root, ledger, pipeline, arguments, sink, nodes


@pytest.mark.parametrize("outcome", ["failed", "raise", "success"])
def test_graph_seals_effect_once_before_verification_and_replay_never_repeats_command(tmp_path, outcome, monkeypatch):
    root, ledger, pipeline, arguments, sink, nodes = setup(tmp_path, verification=True)
    pipeline.outcome = outcome

    def no_generic_reservation(*args, **kwargs):
        pytest.fail("patch was double-reserved by generic ledger")

    monkeypatch.setattr(ledger, "aexecute_once", no_generic_reservation)
    monkeypatch.setattr(ledger, "execute_once", no_generic_reservation)

    async def scenario():
        call = ToolCall("actual-call", "propose_patch", arguments)
        output = json.loads(await nodes._execute_one("run", call))
        assert pipeline.calls == 1 and (root / "file.py").read_bytes() == b"agent\n"
        assert output["patch_receipt"]["status"] == "applied" and not output["effect_replayed"]
        assert output["receipt_source"]["tool_call_id"] == "actual-call"
        assert ledger.read_applied_patch("run", "actual-call").files[0].before_content == "old\n"
        assert len(sink.events) == 1 and sink.events[0][0] == "patch.applied"
        assert "verification" not in json.loads(sink.events[0][1]["result"])
        if outcome == "raise":
            assert output["verification"]["error"] == "OSError: verification failed"
            assert "private exception" not in json.dumps(output)
        else:
            assert output["verification"]["success"] == (outcome == "success")
        (root / "file.py").write_bytes(b"later user edit\n")
        replayed = json.loads(await nodes._execute_one("run", call))
        assert replayed["effect_replayed"] and pipeline.calls == 1
        assert replayed["verification"]["status"] == "not_repeated_for_patch_replay"
        assert (root / "file.py").read_bytes() == b"later user edit\n"

    asyncio.run(scenario())


def test_explicit_command_grant_is_required_even_if_pipeline_was_supplied(tmp_path):
    root, ledger, pipeline, arguments, _, nodes = setup(tmp_path, verification=True, command_granted=False)
    output = json.loads(asyncio.run(nodes._execute_one("run", ToolCall("call", "propose_patch", arguments))))
    assert pipeline.calls == 0 and output["verification"]["status"] == "not_run_command_grant_missing"
    assert ledger.read_applied_patch("run", "call").status == "applied"
    assert (root / "file.py").read_bytes() == b"agent\n"


@pytest.mark.parametrize("command_granted", [False, True])
def test_product_patch_requires_separate_review_even_with_pipeline_and_on_replay(tmp_path, command_granted):
    root, ledger, pipeline, _, sink, _ = setup(tmp_path, verification=True)
    tool = patch_tool(root, verification=pipeline, require_verification_review=True)
    capabilities = {"workspace_write"}
    if command_granted:
        capabilities.add("command_execute")
    registry = ToolRegistry(PermissionManager(frozenset(capabilities)))
    registry.register(tool)
    assert set(tool.schema()["function"]["parameters"]["properties"]) == {"changes"}
    assert tool.after_effect_capability is None
    arguments = registry.prepare_approval("propose_patch", {"changes": [{"path": "file.py", "content": "agent\n"}]})
    nodes = FocusedGraphNodes(MockProvider(), registry, ledger=ledger, sink=sink)

    async def scenario():
        call = ToolCall("actual-call", "propose_patch", arguments)
        output = json.loads(await nodes._execute_one("run", call))
        marker = output["verification"]
        assert marker["status"] == "not_run_separate_review_required"
        assert marker["success"] is None and marker["results"] == [] and pipeline.calls == 0
        assert marker["operation_kind"] == "manual_verification"
        assert marker["source"] == {"run_id": "run", "tool_call_id": "actual-call",
                                    "patch_id": output["patch_id"], "durability": "sealed_tool_ledger"}
        assert "review_id" not in marker
        assert json.loads(sink.events[0][1]["result"])["verification"] == marker
        assert ledger.read_patch_evidence("run", "actual-call")["confirmed_applied"]
        (root / "file.py").write_bytes(b"later user work\n")
        replay = json.loads(await nodes._execute_one("run", call))
        assert replay["effect_replayed"] and replay["verification"] == marker and pipeline.calls == 0
        assert (root / "file.py").read_bytes() == b"later user work\n"

    asyncio.run(scenario())


def test_review_policy_never_reads_supplied_pipeline_availability(tmp_path):
    root, ledger, _, _, sink, _ = setup(tmp_path)

    class UntrustedPipeline:
        @property
        def available(self):
            pytest.fail("product patch consulted compatibility verification configuration")

        async def run(self, **kwargs):
            pytest.fail("product patch launched compatibility verification")

    tool = langchain_patch_tool(root, ledger=ledger, verification=UntrustedPipeline(),
                               allow_command=True, require_verification_review=True)
    assert set(tool.tool_call_schema["properties"]) == {"changes"}
    arguments = tool.doppel_tool.approval_preparer({"changes": [{"path": "file.py", "content": "agent\n"}]})

    async def scenario():
        token = set_patch_run_id("run", sink)
        try:
            message = await tool.ainvoke({"name": "propose_patch", "id": "actual-call", "type": "tool_call", "args": arguments})
            assert json.loads(message.content)["verification"]["status"] == "not_run_separate_review_required"
        finally:
            reset_patch_run_id(token)

    asyncio.run(scenario())


def test_command_slot_wait_cancellation_keeps_completed_effect_and_skips_future_commands(tmp_path):
    limits = ResourceLimits(command_processes=1)
    root, ledger, pipeline, arguments, sink, nodes = setup(tmp_path, verification=True, limits=limits)

    async def scenario():
        async with limits.command():
            task = asyncio.create_task(nodes._execute_one("run", ToolCall("call", "propose_patch", arguments)))
            try:
                async with asyncio.timeout(3):
                    while not sink.events:
                        await asyncio.sleep(0.01)
                assert pipeline.calls == 0 and not task.done()
                task.cancel()
                outcome = await asyncio.gather(task, return_exceptions=True)
                assert isinstance(outcome[0], asyncio.CancelledError)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert ledger.read_applied_patch("run", "call").status == "applied"
        assert (root / "file.py").read_bytes() == b"agent\n"
        assert pipeline.calls == 0

    asyncio.run(scenario())


def test_repeated_cancel_drains_actual_patch_worker_before_receipt_or_owner_release(tmp_path, monkeypatch):
    root, ledger, _, arguments, sink, nodes = setup(tmp_path)
    started, release = threading.Event(), threading.Event()
    original = PatchService.apply

    def gated_apply(self, proposal, **kwargs):
        started.set()
        if not release.wait(5):
            raise RuntimeError("fixture patch gate timed out")
        return original(self, proposal, **kwargs)

    monkeypatch.setattr(PatchService, "apply", gated_apply)

    async def scenario():
        task = asyncio.create_task(nodes._execute_one("run", ToolCall("call", "propose_patch", arguments)))
        try:
            async with asyncio.timeout(3):
                while not started.is_set():
                    await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert not task.done() and not sink.events
            assert (root / "file.py").read_bytes() == b"old\n"
        finally:
            release.set()
            outcome = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcome[0], asyncio.CancelledError)
        assert (root / "file.py").read_bytes() == b"agent\n"
        assert ledger.read_applied_patch("run", "call").status == "applied"
        assert len(sink.events) == 1 and sink.events[0][1]["cancel_requested"]

    asyncio.run(scenario())


def test_cancel_after_verification_starts_preserves_the_sealed_effect(tmp_path):
    root, ledger, pipeline, arguments, sink, nodes = setup(tmp_path, verification=True)

    async def scenario():
        pipeline.started, pipeline.release = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(nodes._execute_one("run", ToolCall("call", "propose_patch", arguments)))
        try:
            async with asyncio.timeout(3):
                await pipeline.started.wait()
            assert pipeline.calls == 1 and len(sink.events) == 1
            assert ledger.read_applied_patch("run", "call").status == "applied"
            task.cancel()
            outcome = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(outcome[0], asyncio.CancelledError)
            assert (root / "file.py").read_bytes() == b"agent\n"
            rows = ledger.list_patch_receipts("run")
            assert len(rows) == 1 and rows[0]["confirmed_applied"]
        finally:
            pipeline.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # Fake pipeline exercises cancellation propagation, not native process drain.
    asyncio.run(scenario())


def test_deep_uses_injected_actual_id_and_never_hashes_or_trusts_argument_id(tmp_path):
    root, ledger, _, arguments, sink, _ = setup(tmp_path)
    tool = langchain_patch_tool(root, ledger=ledger)
    assert set(tool.tool_call_schema["properties"]) == {"changes"}

    async def scenario():
        token = set_patch_run_id("run", sink)
        try:
            # BaseTool must overwrite the fake argument with the real ToolCall ID.
            message = await tool.ainvoke({"name": "propose_patch", "id": "actual-deep-call", "type": "tool_call",
                                         "args": {**arguments, "tool_call_id": "forged-argument-id"}})
            output = json.loads(message.content)
            assert output["receipt_source"]["tool_call_id"] == "actual-deep-call"
            assert ledger.read_applied_patch("run", "actual-deep-call").status == "applied"
            with pytest.raises(ValueError, match="applied_receipt_unavailable"):
                ledger.read_applied_patch("run", "forged-argument-id")
            with pytest.raises(ValueError, match="InjectedToolCallId"):
                await tool.ainvoke(arguments)
        finally:
            reset_patch_run_id(token)
        assert sink.events[0][1]["tool_call_id"] == "actual-deep-call"

    asyncio.run(scenario())


def test_deep_unbound_run_or_unprepared_review_cannot_apply(tmp_path):
    root, ledger, _, arguments, _, _ = setup(tmp_path)
    tool = langchain_patch_tool(root, ledger=ledger)

    async def scenario():
        full = {"name": "propose_patch", "id": "call", "type": "tool_call", "args": arguments}
        with pytest.raises(ValueError, match="reviewed_run_scope"):
            await tool.ainvoke(full)
        token = set_patch_run_id("run")
        try:
            with pytest.raises(ValueError, match="reviewed_run_scope"):
                await tool.ainvoke({**full, "args": {"changes": arguments["changes"]}})
        finally:
            reset_patch_run_id(token)
        assert ledger.list_patch_receipts("run") == [] and (root / "file.py").read_bytes() == b"old\n"

    asyncio.run(scenario())
