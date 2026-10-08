"""S9 actual service/ledger inverse definitions; no provider or command needed."""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.runtime.service import RunService
from doppel_agent.workspace.patching import PatchConflictError, PatchService


async def applied_source(service, *, content="agent\n", original=b"old\n"):
    # Fixture admission uses the real store, but no runtime/provider execution.
    identifier = uuid4().hex
    if original is not None:
        (service.workspace / "file.py").write_bytes(original)
    await service.start()
    await asyncio.to_thread(service.runs.create, identifier, uuid4().hex, "graph", {
        "prompt": "fixture source", "mode": "graph", "permissions": {"workspace_write": True},
    }, None)
    patch = PatchService(service.workspace)
    proposal = patch.prepare([{"path": "file.py", "content": content}])
    ledger = ToolExecutionLedger(service.state_root / "tool-executions.sqlite3")
    await asyncio.to_thread(ledger.execute_patch_once, identifier, "source-call", proposal.as_dict(), patch, proposal)
    await asyncio.to_thread(service.runs.update, identifier, "completed", answer="fixture source")
    await asyncio.to_thread(service.runs.release_conversation_turn, identifier)
    return identifier, proposal.patch_id, ledger


def test_fresh_inverse_review_is_not_effect_and_retries_never_overwrite_later_user_changes(tmp_path):
    async def scenario():
        service = RunService(tmp_path)
        try:
            run, source_patch, ledger = await applied_source(service)
            operation = uuid4().hex
            review = await service.prepare_inverse_patch(run, "source-call", source_patch,
                                                         operation_id=operation, workspace_write=True)
            assert review["status"] == "pending" and (tmp_path / "file.py").read_bytes() == b"agent\n"
            assert review["operation_kind"] == "manual_inverse" and "before_content" not in json.dumps(review)
            assert all("content" not in item for item in review["review"]["files"])
            replay = await service.prepare_inverse_patch(run, "source-call", source_patch,
                                                        operation_id=operation, workspace_write=True)
            assert replay == review
            result = await service.decide_inverse_patch(run, operation, review["patch_id"], action="approve", workspace_write=True)
            assert result["status"] == "applied" and not result["effect_replayed"]
            assert result["result"]["receipt_source"]["origin"] == "manual_inverse"
            assert (tmp_path / "file.py").read_bytes() == b"old\n"
            assert ledger.read_applied_patch(run, review["effect_tool_call_id"]).status == "applied"
            (tmp_path / "file.py").write_bytes(b"later user edit\n")
            replay = await service.decide_inverse_patch(run, operation, review["patch_id"], action="approve", workspace_write=True)
            assert replay["effect_replayed"] and (tmp_path / "file.py").read_bytes() == b"later user edit\n"
            assert (await service.get(run))["status"] == "completed"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_inverse_requires_new_grant_exact_scope_and_unmodified_after_state(tmp_path):
    async def scenario():
        service = RunService(tmp_path)
        try:
            run, patch_id, ledger = await applied_source(service)
            with pytest.raises(PermissionError, match="inverse_write_grant_required"):
                await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=uuid4().hex, workspace_write=False)
            operation = uuid4().hex
            review = await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=operation, workspace_write=True)
            with pytest.raises(ValueError, match="inverse_review_scope"):
                await service.decide_inverse_patch(run, operation, "0" * 32, action="approve", workspace_write=True)
            with pytest.raises(KeyError):
                await service.decide_inverse_patch("f" * 32, operation, review["patch_id"], action="approve", workspace_write=True)
            (tmp_path / "file.py").write_bytes(b"user changed after review\n")
            with pytest.raises(PatchConflictError):
                await service.decide_inverse_patch(run, operation, review["patch_id"], action="approve", workspace_write=True)
            assert (tmp_path / "file.py").read_bytes() == b"user changed after review\n"
            assert len(ledger.list_patch_receipts(run)) == 1
            failed = await service.get_inverse_patch(run, operation)
            assert failed["status"] == "failed"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_inverse_of_created_empty_file_deletes_and_fresh_inverse_of_that_receipt_restores(tmp_path):
    async def scenario():
        service = RunService(tmp_path)
        try:
            run, patch_id, _ = await applied_source(service, content="", original=None)
            first = await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=uuid4().hex, workspace_write=True)
            result = await service.decide_inverse_patch(run, first["review_id"], first["patch_id"], action="approve", workspace_write=True)
            assert result["status"] == "applied" and not (tmp_path / "file.py").exists()
            second = await service.prepare_inverse_patch(run, first["effect_tool_call_id"], first["patch_id"],
                                                        operation_id=uuid4().hex, workspace_write=True)
            await service.decide_inverse_patch(run, second["review_id"], second["patch_id"], action="approve", workspace_write=True)
            assert (tmp_path / "file.py").read_bytes() == b""
        finally:
            await service.close()

    asyncio.run(scenario())


def test_inverse_cancellation_drains_actual_effect_and_keeps_owner_and_lock(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = PatchService.apply

    def gated(self, proposal, **kwargs):
        started.set()
        assert release.wait(5)
        return original(self, proposal, **kwargs)

    async def scenario():
        service = RunService(tmp_path)
        task = waiter = None
        try:
            run, patch_id, ledger = await applied_source(service)
            review = await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=uuid4().hex, workspace_write=True)
            monkeypatch.setattr(PatchService, "apply", gated)
            task = asyncio.create_task(service.decide_inverse_patch(run, review["review_id"], review["patch_id"],
                                                                     action="approve", workspace_write=True))
            async with asyncio.timeout(3):
                while not started.is_set():
                    await asyncio.sleep(0.01)
            acquired = asyncio.Event()

            async def lock_waiter():
                async with service.workspace_locks.write(tmp_path):
                    acquired.set()

            waiter = asyncio.create_task(lock_waiter())
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert not task.done() and not acquired.is_set() and service._owner.held
            release.set()
            outcome = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(outcome[0], asyncio.CancelledError)
            await waiter
            assert ledger.read_applied_patch(run, review["effect_tool_call_id"]).status == "applied"
            assert (await service.get_inverse_patch(run, review["review_id"]))["status"] == "applied"
            assert (tmp_path / "file.py").read_bytes() == b"old\n"
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            if waiter is not None:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_inverse_review_seal_failure_does_not_erase_actual_effect_or_authorize_repeat(tmp_path, monkeypatch):
    from doppel_agent.persistence.inverse_reviews import InverseReviewStore

    async def scenario():
        service = RunService(tmp_path)
        try:
            run, patch_id, ledger = await applied_source(service)
            review = await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=uuid4().hex, workspace_write=True)
            original = InverseReviewStore.finish

            def broken_seal(self, *args, **kwargs):
                if kwargs.get("result") is not None:
                    raise OSError("private fixture failure must not become review error text")
                return original(self, *args, **kwargs)

            monkeypatch.setattr(InverseReviewStore, "finish", broken_seal)
            with pytest.raises(OSError):
                await service.decide_inverse_patch(run, review["review_id"], review["patch_id"], action="approve", workspace_write=True)
            assert (tmp_path / "file.py").read_bytes() == b"old\n"
            assert ledger.read_applied_patch(run, review["effect_tool_call_id"]).status == "applied"
            pending = await service.get_inverse_patch(run, review["review_id"])
            assert pending["status"] == "applying" and pending["effect_evidence"] == "requires_patch_ledger_inspection"
            (tmp_path / "file.py").write_bytes(b"new user change\n")
            with pytest.raises(RuntimeError, match="indeterminate"):
                await service.decide_inverse_patch(run, review["review_id"], review["patch_id"], action="approve", workspace_write=True)
            assert (tmp_path / "file.py").read_bytes() == b"new user change\n"
            assert "private fixture" not in json.dumps(pending)

            def forbidden_io(*args, **kwargs):
                pytest.fail("reconciliation/replay must not prepare or apply a patch")

            monkeypatch.setattr(PatchService, "apply", forbidden_io)
            monkeypatch.setattr(PatchService, "prepare_inverse", forbidden_io)
            reconciled = await service.reconcile_inverse_patch(run, review["review_id"])
            assert reconciled["status"] == "applied"
            assert reconciled["effect_evidence"] == "sealed_patch_ledger"
            assert reconciled["result"]["receipt_source"]["origin"] == "manual_inverse"
            replay = await service.decide_inverse_patch(run, review["review_id"], review["patch_id"],
                                                       action="approve", workspace_write=True)
            assert replay["effect_replayed"]
            assert (tmp_path / "file.py").read_bytes() == b"new user change\n"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_inverse_source_must_be_quiescent_and_owner_must_still_be_held(tmp_path):
    async def scenario():
        service = RunService(tmp_path)
        try:
            run, patch_id, _ = await applied_source(service)
            await asyncio.to_thread(service.runs.update, run, "running")
            with pytest.raises(ValueError, match="not_quiescent"):
                await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=uuid4().hex, workspace_write=True)
            await asyncio.to_thread(service.runs.update, run, "completed")
            service._owner.release()
            with pytest.raises(RuntimeError, match="patch_workspace_owner"):
                await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=uuid4().hex, workspace_write=True)
            assert (tmp_path / "file.py").read_bytes() == b"agent\n"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_reconcile_missing_effect_is_unknown_not_absent_and_never_approves_pending(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path)
        try:
            run, patch_id, _ = await applied_source(service)
            review = await service.prepare_inverse_patch(run, "source-call", patch_id,
                                                        operation_id=uuid4().hex, workspace_write=True)
            assert (await service.reconcile_inverse_patch(run, review["review_id"]))["status"] == "pending"
            _, store = await service._patch_services()
            store.claim(run, review["review_id"], review["patch_id"], "approve")

            def forbidden_io(*args, **kwargs):
                pytest.fail("unknown ledger evidence must never replay effects")

            monkeypatch.setattr(PatchService, "apply", forbidden_io)
            monkeypatch.setattr(PatchService, "prepare_inverse", forbidden_io)
            view = await service.reconcile_inverse_patch(run, review["review_id"])
            assert view["status"] == "indeterminate" and view["result"] is None
            assert view["effect_evidence"] == "requires_patch_ledger_inspection"
            assert not view["effect_replayed"]
            with pytest.raises(RuntimeError, match="indeterminate"):
                await service.decide_inverse_patch(run, review["review_id"], review["patch_id"],
                                                  action="approve", workspace_write=True)
            assert (tmp_path / "file.py").read_bytes() == b"agent\n"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_private_retention_cancellation_drains_worker_before_owner_and_write_lock_exit(tmp_path, monkeypatch):
    from doppel_agent.persistence.patch_retention import PrivatePatchRetention

    entered, release = threading.Event(), threading.Event()
    original = PrivatePatchRetention.apply

    def gated(self, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, *args, **kwargs)

    async def scenario():
        service = RunService(tmp_path)
        task = closer = None
        try:
            run, _, _ = await applied_source(service)
            monkeypatch.setattr(PrivatePatchRetention, "apply", gated)
            task = asyncio.create_task(service._release_turn(run))
            async with asyncio.timeout(3):
                while not entered.is_set():
                    await asyncio.sleep(0.01)
            # This fixture cleanup is not a scheduled/admitted operation; join
            # it explicitly before close, as scheduler close joins real roots.
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert not task.done() and service._owner.held
            acquired = asyncio.Event()

            async def waiter():
                async with service.workspace_locks.write(tmp_path):
                    acquired.set()

            closer = asyncio.create_task(waiter())
            await asyncio.sleep(0)
            assert not acquired.is_set()
            release.set()
            await task
            await closer
            assert acquired.is_set() and service._owner.held
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            if closer is not None:
                await asyncio.gather(closer, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_applied_manual_receipt_expiry_preserves_review_result_and_does_not_allow_new_inverse(tmp_path, monkeypatch):
    from doppel_agent.persistence.database import sqlite_connection
    from doppel_agent.persistence.patch_retention import PrivatePatchRetention

    async def scenario():
        service = RunService(tmp_path)
        try:
            run, patch_id, ledger = await applied_source(service)
            review = await service.prepare_inverse_patch(run, "source-call", patch_id,
                                                        operation_id=uuid4().hex, workspace_write=True)
            applied = await service.decide_inverse_patch(run, review["review_id"], review["patch_id"],
                                                        action="approve", workspace_write=True)
            old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
            with sqlite_connection(service.runs.database) as db:
                db.execute("UPDATE runtime_runs SET updated_at=? WHERE run_id=?", (old, run))
            with sqlite_connection(ledger.database) as db:
                db.execute("UPDATE patch_effect_receipts SET created_at=? WHERE run_id=?", (old, run))
            # Isolated fixture calls owner-only helper while service owner is
            # held and no other operation exists; production uses shared lock.
            report = PrivatePatchRetention().apply(service.state_root, cursor={"phase": "receipts", "after": ""})
            assert report["preimages_expired"] == 2
            assert all(row["confirmed_applied"] and row["preimage_state"] == "expired"
                       for row in await service.list_patch_effects(run))
            assert (await service.get_inverse_patch(run, review["review_id"]))["result"] == applied["result"]
            with pytest.raises(ValueError, match="patch_preimage_expired"):
                await service.prepare_inverse_patch(run, review["effect_tool_call_id"], review["patch_id"],
                                                    operation_id=uuid4().hex, workspace_write=True)
            (tmp_path / "file.py").write_bytes(b"new user work\n")

            def forbidden_io(*args, **kwargs):
                pytest.fail("final reviewed replay must not prepare/apply files")

            monkeypatch.setattr(PatchService, "prepare_inverse", forbidden_io)
            monkeypatch.setattr(PatchService, "apply", forbidden_io)
            assert (await service.decide_inverse_patch(run, review["review_id"], review["patch_id"],
                                                      action="approve", workspace_write=True))["effect_replayed"]
            assert (tmp_path / "file.py").read_bytes() == b"new user work\n"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_inverse_pending_survives_reconstruction_reject_does_not_run_and_scope_list_is_redacted(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path)
        try:
            run, patch_id, _ = await applied_source(service)
            review = await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=uuid4().hex, workspace_write=True)
            with pytest.raises(KeyError):
                await service.list_patch_effects("f" * 32)
            listed = await service.list_patch_effects(run)
            assert len(listed) == 1 and listed[0]["source_run_quiescent"]
            assert "before_content" not in json.dumps(listed)
        finally:
            await service.close()
        reconstructed = RunService(tmp_path)
        try:
            await reconstructed.start()  # Read APIs never acquire/recover an owner implicitly.
            restored = await reconstructed.get_inverse_patch(run, review["review_id"])
            assert {key: restored[key] for key in review} == review
            assert restored["query"]["sql_read_only"] and not restored["lifecycle"]["approval_available"]

            def no_apply(*args, **kwargs):
                pytest.fail("reject/read-only reconstruction must not execute a patch")

            monkeypatch.setattr(PatchService, "apply", no_apply)
            rejected = await reconstructed.decide_inverse_patch(run, review["review_id"], review["patch_id"],
                                                                 action="reject", workspace_write=False)
            assert rejected["status"] == "rejected" and not rejected["effect_replayed"]
            replay = await reconstructed.decide_inverse_patch(run, review["review_id"], review["patch_id"],
                                                               action="reject", workspace_write=False)
            assert replay["decision_replayed"] and not replay["effect_replayed"]
            assert (tmp_path / "file.py").read_bytes() == b"agent\n"
        finally:
            await reconstructed.close()

    asyncio.run(scenario())


def test_expired_review_cannot_approve_or_refresh_and_private_proposal_is_cleared(tmp_path):
    from doppel_agent.persistence.database import sqlite_connection

    async def scenario():
        service = RunService(tmp_path)
        try:
            run, patch_id, ledger = await applied_source(service)
            review = await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=uuid4().hex, workspace_write=True)
            now = datetime.now(UTC)
            with sqlite_connection(ledger.database) as db:
                db.execute("UPDATE inverse_patch_reviews SET created_at=?,expires_at=? WHERE review_id=?",
                           ((now - timedelta(seconds=901)).isoformat(), (now - timedelta(seconds=1)).isoformat(), review["review_id"]))
            with pytest.raises(RuntimeError, match="not_pending"):
                await service.decide_inverse_patch(run, review["review_id"], review["patch_id"], action="approve", workspace_write=True)
            replayed = await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=review["review_id"], workspace_write=True)
            assert replayed["status"] == "expired" and replayed["patch_id"] == review["patch_id"]
            with sqlite_connection(ledger.database) as db:
                assert db.execute("SELECT proposal_json FROM inverse_patch_reviews WHERE review_id=?", (review["review_id"],)).fetchone()[0] == ""
            assert (tmp_path / "file.py").read_bytes() == b"agent\n" and len(ledger.list_patch_receipts(run)) == 1
        finally:
            await service.close()

    asyncio.run(scenario())


def test_same_prepare_key_cannot_change_source_or_refresh_after_user_change_and_live_budget_is_explicit(tmp_path, monkeypatch):
    import doppel_agent.persistence.inverse_reviews as review_module

    async def scenario():
        service = RunService(tmp_path)
        try:
            run, patch_id, _ = await applied_source(service)
            monkeypatch.setattr(review_module, "MAX_PENDING", 1)
            review = await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=uuid4().hex, workspace_write=True)
            with pytest.raises(ValueError, match="pending_budget"):
                await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=uuid4().hex, workspace_write=True)
            (tmp_path / "file.py").write_bytes(b"user after preparation\n")
            replayed = await service.prepare_inverse_patch(run, "source-call", patch_id, operation_id=review["review_id"], workspace_write=True)
            assert replayed == review  # Same frozen review; not silently refreshed.
            with pytest.raises(ValueError, match="inverse_review_scope"):
                await service.prepare_inverse_patch(run, "foreign-call", patch_id, operation_id=review["review_id"], workspace_write=True)
            assert (tmp_path / "file.py").read_bytes() == b"user after preparation\n"
        finally:
            await service.close()

    asyncio.run(scenario())
