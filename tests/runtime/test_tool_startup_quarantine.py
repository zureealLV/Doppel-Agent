"""FIRST startup ledger admission definitions, ALL UNRUN until whole S9.

Original stores, owner and pumps; no provider/effect/result substitution.
"""

import asyncio
import sqlite3
from unittest.mock import AsyncMock, Mock

import pytest

from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.runtime.provider_recording import ProviderReceiptError
from doppel_agent.runtime.service import RunService


@pytest.mark.parametrize("tool", ["propose_patch", "inverse_patch", "mcp__fixture__echo", "run_command"])
def test_original_unsealed_tool_fences_startup_before_recovery_settings_and_pumps(
    tmp_path, monkeypatch, tool
):
    async def scenario():
        service = RunService(tmp_path)
        ledger = ToolExecutionLedger(service.state_root / "tool-executions.sqlite3")
        ledger._begin("a" * 32, "original-call", tool, {})
        recovery, reconcile, settings = Mock(), Mock(), Mock()
        starts = [AsyncMock(), AsyncMock(), AsyncMock()]
        monkeypatch.setattr(service.runs, "recover_incomplete", recovery)
        monkeypatch.setattr(service.conversations, "reconcile", reconcile)
        monkeypatch.setattr(service.settings, "recovery_public", settings)
        for target, start in zip(
            (service.scheduler, service.subagents, service.work_order_orchestrator), starts, strict=True
        ):
            monkeypatch.setattr(target, "start", start)
        await service.start()
        assert service._metadata_ready and not service._started and service._owner.held
        assert service._tool_startup_recovery.quarantined
        assert service._tool_startup_recovery.reason == "unsealed_tool_operations"
        assert service._provider_receipt_fault.broken
        assert not recovery.called and not reconcile.called and not settings.called
        assert not any(start.called for start in starts) and not service._providers
        with pytest.raises(ProviderReceiptError):
            await service.create({"prompt": "offline", "mode": "graph", "permissions": {}})
        await service.close()
        assert service.cleanup_complete and not service._owner.held
        assert ledger.execution_status("a" * 32, "original-call") == "running"
        # A new owner cannot clear the original durable unknown with healthy IO.
        again = RunService(tmp_path)
        await again.start()
        assert again._metadata_ready and not again._started and again._provider_receipt_fault.broken
        await again.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["completed", "failed", "missing", "corrupt"])
def test_tool_startup_reader_is_existing_only_and_keeps_sealed_known_failures(tmp_path, status):
    from doppel_agent.persistence.tool_recovery import ToolOperationRecoveryQueries

    service = RunService(tmp_path)
    path = service.state_root / "tool-executions.sqlite3"
    if status in {"completed", "failed"}:
        ledger = ToolExecutionLedger(path)
        ledger._begin("a" * 32, "original-call", "read_file", {})
        ledger._finish(
            "a" * 32, "original-call", result="private", error="known" if status == "failed" else ""
        )
    elif status == "corrupt":
        path.write_bytes(b"private-not-sqlite")
    result = ToolOperationRecoveryQueries(path, service.runs.database).read()
    assert result.quarantined is (status == "corrupt")
    if status == "missing":
        assert not path.exists()  # No ledger constructor/migration/empty-store invention.
    elif status != "corrupt":
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT status,result,error FROM tool_executions").fetchone() == (
                status,
                "private",
                "known" if status == "failed" else "",
            )


@pytest.mark.parametrize("after_close", [False, True])
def test_original_tool_startup_reader_failed_disposal_retains_same_owner_without_retry(
    tmp_path, monkeypatch, after_close
):
    """Actual SQLite connection, ONLY failure-injected close; ALL UNRUN."""

    async def scenario():
        service = RunService(tmp_path)
        path = service.state_root / "tool-executions.sqlite3"
        ToolExecutionLedger(path)
        original_connect = sqlite3.connect
        originals, attempts = [], []

        class Connection:
            def __init__(self, original):
                self.original = original

            def execute(self, *args):
                return self.original.execute(*args)

            def set_progress_handler(self, *args):
                return self.original.set_progress_handler(*args)

            def close(self):
                attempts.append(self)
                if after_close:
                    self.original.close()
                raise OSError("PRIVATE_ORIGINAL_TOOL_READER_CLOSE")

        def connect(database, *args, **kwargs):
            target = str(database).startswith(path.absolute().as_uri() + "?mode=ro")
            # Explicit fixture teardown allowance only. Production connection
            # remains thread-bound and closes in its original reader worker.
            if target:
                kwargs["check_same_thread"] = False
            original = original_connect(database, *args, **kwargs)
            if target:
                wrapped = Connection(original)
                originals.append(wrapped)
                return wrapped
            return original

        monkeypatch.setattr(sqlite3, "connect", connect)
        try:
            await service.start()
            assert service._metadata_ready and not service._started and service._owner.held
            assert service._tool_startup_recovery.quarantined
            assert service._provider_receipt_fault.cleanup_uncertain
            (source,) = service._unresolved_report_reads.values()
            assert source.connection is originals[0] and source.close_attempted and not source.close_returned
            assert source.cleanup_uncertain and all(frame.close_returned for frame in source.cursors)
            for _ in range(2):
                with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
                    await service.close()
            assert service._owner.held and not service.cleanup_complete and len(attempts) == 1
        finally:
            # Fixture teardown only, after joined original reader and close task.
            if not after_close and originals:
                originals[0].original.close()
            service._owner.release()

    asyncio.run(scenario())
