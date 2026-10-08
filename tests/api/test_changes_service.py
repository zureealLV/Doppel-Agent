"""D1 S9 service definitions; isolated metadata and a fake Git reader only."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.runtime import service as service_module
from doppel_agent.runtime.service import RunService
from doppel_agent.workspace.patching import PatchService


async def source(service):
    await service.start()
    run = uuid4().hex
    service.runs.create(run, uuid4().hex, "graph", {"prompt": "fixture", "mode": "graph"}, None)
    (service.workspace / "file.txt").write_text("private preimage fixture", encoding="utf-8")
    patch = PatchService(service.workspace)
    proposal = patch.prepare([{"path": "file.txt", "content": "changed fixture"}])
    ledger = ToolExecutionLedger(service.state_root / "tool-executions.sqlite3")
    ledger.execute_patch_once(run, "source-call", proposal.as_dict(), patch, proposal)
    service.runs.update(run, "completed")
    service.runs.release_conversation_turn(run)
    return run, proposal.patch_id, ledger


def test_fixed_git_reader_borrows_supervisor_owner_resources_and_rechecks_admission(tmp_path, monkeypatch):
    async def scenario():
        calls = []

        class Inspector:
            def __init__(self, workspace, **kwargs):
                calls.append((workspace, kwargs))

            async def status(self, **kwargs):
                calls.append(("status", kwargs))
                return {"available": False, "reason": "not_git_repository", "repository_clean": None}

            async def diff(self, path, **kwargs):
                calls.append(("diff", path, kwargs))
                return {"available": False, "reason": "stale_git_inspection", "repository_clean": None}

        monkeypatch.setattr(service_module, "GitInspector", Inspector)
        service = RunService(tmp_path)
        try:
            status = await service.git_status()
            assert status["repository_clean"] is None and calls[0][1]["supervisor"] is service.process_supervisor
            assert calls[0][1]["authorized_metadata_roots"] == ()
            diff = await service.git_diff("file.txt", plane="staged", expected_fingerprint="a" * 64)
            assert diff["reason"] == "stale_git_inspection"
            assert calls[-1][1:] == ("file.txt", {"plane": "staged", "expected_fingerprint": "a" * 64,
                "conflict_stage": None, "operation_id": calls[-1][2]["operation_id"]})
            count = len(calls)
            # Fake only: this flag represents cleanup quarantine, no OS process.
            service.process_supervisor._cleanup_failed = True
            with pytest.raises(RuntimeError, match="quarantine"):
                await service.git_status()
            assert len(calls) == count
        finally:
            service.process_supervisor._cleanup_failed = False
            await service.close()

    asyncio.run(scenario())


def test_patch_and_inverse_get_are_existing_sql_reads_without_expiration_or_private_preimage(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path)
        try:
            run, patch, ledger = await source(service)
            inverse = await service.prepare_inverse_patch(run, "source-call", patch, operation_id=uuid4().hex, workspace_write=True)
            old = datetime.now(UTC) - timedelta(seconds=10)
            with sqlite_connection(ledger.database) as db:
                db.execute("UPDATE inverse_patch_reviews SET created_at=?,expires_at=? WHERE review_id=?",
                    ((old - timedelta(seconds=1)).isoformat(), old.isoformat(), inverse["review_id"]))
            async def forbidden():
                pytest.fail("read restarted service")

            monkeypatch.setattr(service, "start", forbidden)
            monkeypatch.setattr(service.runs, "get", lambda *a: pytest.fail("writable source get"))
            monkeypatch.setattr(ToolExecutionLedger, "__init__", lambda *a, **k: pytest.fail("ledger construction"))
            rows = await service.list_patch_effects(run)
            assert rows[0]["confirmed_applied"] and "content" not in rows[0]["receipt"]["files"][0]
            evidence = await service.get_patch_evidence(run, "source-call")
            assert evidence["confirmed_applied"] and evidence["result"]["patch_id"] == patch
            assert "receipt_json" not in evidence and evidence["output"]["sensitive"]
            read = await service.get_inverse_patch(run, inverse["review_id"])
            assert read["status"] == "pending" and read["lifecycle"]["effective_status"] == "expired"
            assert not read["lifecycle"]["approval_available"]
            with sqlite_connection(ledger.database) as db:
                assert db.execute("SELECT status,proposal_json FROM inverse_patch_reviews WHERE review_id=?",
                    (inverse["review_id"],)).fetchone()[0] == "pending"
        finally:
            monkeypatch.undo()
            await service.close()

    asyncio.run(scenario())
