"""Original RunService/SDK/HITL/ledger, disposable bytes, scripted provider only."""

import asyncio
import json

import pytest

from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.service import RunService


class TwoFileEditProvider:
    model = "scripted-native-edit"

    def __init__(self):
        self.calls = 0

    def next_turn(self, messages, tools):
        self.calls += 1
        if any(message.role == "tool" for message in messages):
            return ModelTurn(content="tool result received")
        return ModelTurn(tool_calls=(ToolCall("original-two-file-edit", "propose_patch", {
            "changes": [{"path": "existing.txt", "content": "proposed existing\n"},
                        {"path": "created.txt", "content": "proposed created\n"}],
        }),))


@pytest.mark.parametrize("mode,dialect", [("deep", "args"), ("deep", "arguments"), ("graph", "arguments")])
def test_original_service_edit_applies_user_contents_once_and_survives_reconstruction(tmp_path, mode, dialect):
    (tmp_path / "existing.txt").write_bytes(b"original\r\n")
    (tmp_path / "dirty.txt").write_bytes(b"unrelated dirty\r\n")
    provider = TwoFileEditProvider()

    async def scenario():
        service = RunService(tmp_path, provider=provider)
        try:
            record, created = await service.create({"prompt": "two-file edit", "mode": mode,
                "effort": "balanced", "permissions": {"workspace_write": True, "command_execute": True,
                                                        "mcp_execute": False, "delegate": False}})
            assert created
            run = record["run_id"]
            await service.scheduler.wait(run)
            pending = await service.get(run)
            assert pending["status"] == "interrupted" and pending["lease_active"]
            interrupt = pending["metadata"]["interrupts"][0]
            key = "action_requests" if mode == "deep" else "tool_calls"
            original = interrupt["value"][key][0]
            edited = {**original, dialect: {"changes": [
                {"path": "existing.txt", "content": "user-edited existing\n"},
                {"path": "created.txt", "content": "user-edited created\n"},
            ]}}
            if dialect == "arguments":
                edited.pop("args", None)
            ledger = ToolExecutionLedger(service.state_root / "tool-executions.sqlite3")
            assert ledger.list_patch_receipts(run) == []
            assert (tmp_path / "existing.txt").read_bytes() == b"original\r\n"
            assert not (tmp_path / "created.txt").exists()
            await service.resume(run, interrupt["id"], {"action": "edit", "tool_calls": [edited]})
            await service.scheduler.wait(run)
            completed = await service.get(run)
            assert completed["status"] == "completed" and not completed["lease_active"]
            assert not completed["metadata"].get("fallback_runtime")
            events = await service.list_events(run)
            effects = [event for event in events if event["type"] == "patch.applied"]
            assert len(effects) == 1
            assert effects[0]["payload"]["tool_call_id"] == "original-two-file-edit"
            output = json.loads(effects[0]["payload"]["result"])
            assert output["verification"]["status"] == "not_run_separate_review_required"
            assert output["verification"]["success"] is None
            assert provider.calls == 2
            rows = ledger.list_patch_receipts(run)
            assert len(rows) == 1 and rows[0]["confirmed_applied"]
            assert rows[0]["tool_call_id"] == "original-two-file-edit"
            assert (tmp_path / "existing.txt").read_bytes() == b"user-edited existing\n"
            assert (tmp_path / "created.txt").read_bytes() == b"user-edited created\n"
            assert (tmp_path / "dirty.txt").read_bytes() == b"unrelated dirty\r\n"
            assert not any(event["type"] == "deep.fallback" for event in events)
            if mode == "deep":
                assert not any(event["type"].startswith("graph.") for event in events)
        finally:
            await service.close()
        reconstructed = RunService(tmp_path, provider=provider)
        try:
            await reconstructed.start()
            assert (await reconstructed.get(run))["status"] == "completed"
            ledger = ToolExecutionLedger(reconstructed.state_root / "tool-executions.sqlite3")
            assert len(ledger.list_patch_receipts(run)) == 1
            assert provider.calls == 2  # Reopening history must not resend the effect/model call.
            assert (tmp_path / "dirty.txt").read_bytes() == b"unrelated dirty\r\n"
        finally:
            await reconstructed.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["internal_metadata", "new_path", "stale_base", "ambiguous_dialect"])
def test_deep_native_edit_keeps_prepared_patch_guards_fail_closed(tmp_path, invalid):
    (tmp_path / "existing.txt").write_bytes(b"original\r\n")
    (tmp_path / "dirty.txt").write_bytes(b"unrelated dirty\r\n")
    provider = TwoFileEditProvider()

    async def scenario():
        service = RunService(tmp_path, provider=provider)
        try:
            record, _ = await service.create({"prompt": "invalid edit", "mode": "deep", "effort": "balanced",
                "permissions": {"workspace_write": True, "command_execute": False,
                                "mcp_execute": False, "delegate": False}})
            run = record["run_id"]
            await service.scheduler.wait(run)
            pending = await service.get(run)
            assert pending["status"] == "interrupted"
            interrupt = pending["metadata"]["interrupts"][0]
            original = interrupt["value"]["action_requests"][0]
            arguments = {"changes": [
                {"path": "existing.txt", "content": "edited existing\n"},
                {"path": "created.txt", "content": "edited created\n"},
            ]}
            edited = {"name": original["name"], "args": arguments}
            expected = b"original\r\n"
            if invalid == "internal_metadata":
                arguments["_doppel_patch"] = original["args"]["_doppel_patch"]
            elif invalid == "new_path":
                arguments["changes"][1]["path"] = "unauthorized.txt"
            elif invalid == "stale_base":
                expected = b"external user bytes\r\n"
                (tmp_path / "existing.txt").write_bytes(expected)
            else:
                edited["arguments"] = {"changes": [{"path": "created.txt", "content": "conflicting"}]}
            await service.resume(run, interrupt["id"], {"action": "edit", "tool_calls": [edited]})
            reason = {"internal_metadata": "only changes", "new_path": "paths or workspace base changed",
                      "stale_base": "paths or workspace base changed",
                      "ambiguous_dialect": "ambiguous edited arguments"}[invalid]
            with pytest.raises(ValueError, match=reason):
                await service.scheduler.wait(run)
            terminal = await service.get(run)
            assert terminal["status"] == "failed" and not terminal["lease_active"]
            assert provider.calls == 1
            events = await service.list_events(run)
            assert not any(event["type"] in {"patch.applied", "deep.tool_started", "deep.fallback"}
                           or event["type"].startswith("graph.") for event in events)
            ledger = ToolExecutionLedger(service.state_root / "tool-executions.sqlite3")
            assert ledger.list_patch_receipts(run) == []
            assert (tmp_path / "existing.txt").read_bytes() == expected
            assert (tmp_path / "dirty.txt").read_bytes() == b"unrelated dirty\r\n"
            assert not (tmp_path / "created.txt").exists()
            assert not (tmp_path / "unauthorized.txt").exists()
        finally:
            await service.close()

    asyncio.run(scenario())
