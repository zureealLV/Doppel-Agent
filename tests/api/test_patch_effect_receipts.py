"""S9 owned-service fixtures: actual runtime/HITL/ledger, scripted model only."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.service import RunService


class PatchProvider:
    model = "scripted-patch-receipt"

    def next_turn(self, messages, tools):
        previous = [message for message in messages if message.role == "tool"]
        if previous:
            return ModelTurn(content=previous[-1].content)
        names = {tool["function"]["name"] for tool in tools}
        assert "propose_patch" in names
        assert "write_file" not in names
        return ModelTurn(tool_calls=(ToolCall("actual-runtime-call", "propose_patch", {
            "changes": [{"path": "file.py", "content": "agent\n"}],
        }),))


@pytest.mark.parametrize("mode", ["graph", "deep", "legacy"])
def test_actual_service_review_and_reconstruction_preserve_private_patch_effect(tmp_path, mode):
    root = tmp_path / "owned-workspace"
    root.mkdir()
    (root / "file.py").write_bytes(b"old\n")

    async def scenario():
        service = RunService(root, provider=PatchProvider())
        try:
            record, created = await service.create({"prompt": "patch fixture", "mode": mode,
                "effort": "balanced", "permissions": {"workspace_write": True, "command_execute": False,
                                                        "mcp_execute": False, "delegate": False}})
            assert created
            run_id = record["run_id"]
            await service.scheduler.wait(run_id)
            pending = await service.get(run_id)
            assert pending["status"] == "interrupted" and pending["lease_active"]
            assert (root / "file.py").read_bytes() == b"old\n"
            ledger = ToolExecutionLedger(service.state_root / "tool-executions.sqlite3")
            assert ledger.list_patch_receipts(run_id) == []
            interrupt = pending["metadata"]["interrupts"][0]
            await service.resume(run_id, interrupt["id"], {"action": "approve"})
            await service.scheduler.wait(run_id)
            completed = await service.get(run_id)
            assert completed["status"] == "completed" and not completed["lease_active"]
            assert completed["metadata"].get("fallback_runtime") is None
            receipt = await asyncio.to_thread(ledger.read_applied_patch, run_id, "actual-runtime-call")
            assert receipt.status == "applied" and receipt.files[0].before_content == "old\n"
            events = await service.list_events(run_id)
            effects = [event for event in events if event["type"] == "patch.applied"]
            assert len(effects) == 1
            payload = effects[0]["payload"]
            output = json.loads(payload["result"])
            assert payload["tool_call_id"] == "actual-runtime-call"
            assert output["receipt_source"]["durability"] == "sealed_tool_ledger"
            assert "before_content" not in output["patch_receipt"]["files"][0]
            assert (root / "file.py").read_bytes() == b"agent\n"
        finally:
            await service.close()
        reconstructed = RunService(root, provider=PatchProvider())
        try:
            await reconstructed.start()
            ledger = ToolExecutionLedger(reconstructed.state_root / "tool-executions.sqlite3")
            assert ledger.read_applied_patch(run_id, "actual-runtime-call").status == "applied"
            (root / "file.py").write_bytes(b"later user change\n")
            assert ledger.list_patch_receipts(run_id)[0]["confirmed_applied"]
            assert (root / "file.py").read_bytes() == b"later user change\n"
        finally:
            await reconstructed.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["graph", "deep", "legacy"])
@pytest.mark.parametrize("config_body", ["not valid JSON", '{"commands":[{"name":"fixture","argv":["fixture-only"],"timeout_seconds":10}]}'])
def test_product_patch_grant_is_not_command_review_and_manual_review_preserves_source(tmp_path, mode, config_body):
    """Actual runtime/HITL/ledger definitions; fake supervisor, no project proof."""
    (tmp_path / "file.py").write_bytes(b"old\n")
    config = tmp_path / ".doppel" / "verification.json"
    config.parent.mkdir()
    config.write_text(config_body, encoding="utf-8")

    class ReviewOnlySupervisor:
        cleanup_failed = False  # Disposable fixture owns no native process tree.
        def __init__(self):
            self.reviewed = False
            self.calls = []

        async def run(self, argv, **kwargs):
            assert self.reviewed, "patch approval implicitly launched verification"
            self.calls.append((argv, kwargs))
            return SimpleNamespace(exit_code=0, stdout="fixture only", stderr="", supervision="fixture_not_native")

        async def run_binary(self, argv, **kwargs):
            assert kwargs["require_tree_ownership"] is True
            value = await self.run(argv, **kwargs)
            return SimpleNamespace(exit_code=value.exit_code, stdout=value.stdout.encode(), stderr=value.stderr.encode(),
                                   supervision=value.supervision)

        async def close(self):
            pass

    async def scenario():
        service = RunService(tmp_path, provider=PatchProvider())
        fake = ReviewOnlySupervisor()
        service.process_supervisor = fake
        try:
            record, _ = await service.create({"prompt": "patch fixture", "mode": mode, "effort": "balanced",
                "permissions": {"workspace_write": True, "command_execute": True, "mcp_execute": False, "delegate": False}})
            run = record["run_id"]
            await service.scheduler.wait(run)
            pending = await service.get(run)
            assert pending["status"] == "interrupted" and fake.calls == []
            await service.resume(run, pending["metadata"]["interrupts"][0]["id"], {"action": "approve"})
            await service.scheduler.wait(run)
            completed = await service.get(run)
            assert completed["status"] == "completed" and completed["metadata"].get("fallback_runtime") is None
            assert fake.calls == [] and (tmp_path / "file.py").read_bytes() == b"agent\n"
            assert service._verification_reviews is None  # Patch did not create a manual review implicitly.
            effects = [event for event in await service.list_events(run) if event["type"] == "patch.applied"]
            assert len(effects) == 1
            # Deep SDK adds a human-edit notice to the tool reply. The actual
            # original effect event remains JSON; answer is a wrapped echo.
            assert completed["answer"].endswith(effects[0]["payload"]["result"])
            output = json.loads(effects[0]["payload"]["result"])
            marker = output["verification"]
            assert marker["status"] == "not_run_separate_review_required" and marker["success"] is None
            assert marker["source"]["run_id"] == run and marker["source"]["tool_call_id"] == "actual-runtime-call"
            assert marker["source"]["patch_id"] == output["patch_id"] and "review_id" not in marker
            ledger = ToolExecutionLedger(service.state_root / "tool-executions.sqlite3")
            assert ledger.read_patch_evidence(run, "actual-runtime-call")["confirmed_applied"]
            original = service.runs.get(run)
            config.write_text('{"commands":[{"name":"fixture","argv":["fixture-only"],"timeout_seconds":10}]}', encoding="utf-8")
            view = await service.prepare_verification(run, "actual-runtime-call", output["patch_id"],
                operation_id=uuid4().hex, command_execute=True, workspace_write=True)
            assert view["status"] == "pending" and fake.calls == []
            assert (await service.get_verification(run, view["review_id"]))["plan"] == view["plan"]
            rejected = await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="reject")
            assert rejected["status"] == "rejected" and fake.calls == []
            fresh = await service.prepare_verification(run, "actual-runtime-call", output["patch_id"],
                operation_id=uuid4().hex, command_execute=True, workspace_write=True)
            with pytest.raises(PermissionError, match="fresh_command_and_write"):
                await service.decide_verification(run, fresh["review_id"], fresh["plan"]["plan_id"], action="approve")
            assert fake.calls == [] and (await service.get_verification(run, fresh["review_id"]))["status"] == "pending"
            fake.reviewed = True
            done = await service.decide_verification(run, fresh["review_id"], fresh["plan"]["plan_id"], action="approve",
                command_execute=True, workspace_write=True)
            assert done["status"] == "completed" and done["success"] and len(fake.calls) == 1
            scoped = await service.list_verifications(run, tool_call_id="actual-runtime-call", patch_id=output["patch_id"])
            assert {item["review_id"] for item in scoped["items"]} == {view["review_id"], fresh["review_id"]}
            actual = await service.get_verification(run, fresh["review_id"])
            assert actual["provenance"]["source_run"]["status"] == "completed"
            assert actual["source"] == {"run_id": run, "tool_call_id": "actual-runtime-call", "patch_id": output["patch_id"]}
            assert actual["lifecycle"]["all_planned_attempts_sealed"] and not actual["lifecycle"]["approval_available"]
            assert actual["output"]["sensitive"] and len(fake.calls) == 1
            assert done["steps"][0]["result"]["supervision"] == "fixture_not_native"
            assert service.runs.get(run) == original
            replay = await service.decide_verification(run, fresh["review_id"], fresh["plan"]["plan_id"], action="approve",
                command_execute=True, workspace_write=True)
            assert replay["decision_replayed"] and len(fake.calls) == 1
            assert ledger.read_patch_evidence(run, "actual-runtime-call")["confirmed_applied"]
        finally:
            await service.close()

    asyncio.run(scenario())
