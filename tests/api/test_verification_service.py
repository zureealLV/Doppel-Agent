"""C2a isolated real-service/ledger definitions; supervisor is fake, never native."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from doppel_agent.owned_async import await_durable
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.persistence.verification_reviews import VerificationReviewStore
from doppel_agent.persistence.verification_queries import VerificationQueries
from doppel_agent.runtime.service import RunService
from doppel_agent.workspace.patching import PatchService
from doppel_agent.workspace.verification import VerificationResult
from doppel_agent.workspace.process_supervisor import ProcessCleanupError


class FakeSupervisor:
    def __init__(self):
        self.calls = []

    async def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return SimpleNamespace(exit_code=0, stdout="fixture-not-real-project-proof", stderr="", supervision="fixture_not_native")

    async def run_binary(self, argv, **kwargs):
        assert kwargs["require_tree_ownership"] is True
        value = await self.run(argv, **kwargs)
        return SimpleNamespace(exit_code=value.exit_code, stdout=value.stdout.encode(), stderr=value.stderr.encode(),
                               supervision=value.supervision)

    async def close(self):
        pass


async def source(service, *, commands=1):
    await service.start()
    run = uuid4().hex
    service.runs.create(run, uuid4().hex, "graph", {"prompt": "fixture", "mode": "graph",
                        "permissions": {"workspace_write": True, "command_execute": True}}, None)
    (service.workspace / "file.py").write_text("old\n", encoding="utf-8")
    patch = PatchService(service.workspace)
    proposal = patch.prepare([{"path": "file.py", "content": "agent\n"}])
    ledger = ToolExecutionLedger(service.state_root / "tool-executions.sqlite3")
    ledger.execute_patch_once(run, "actual-source", proposal.as_dict(), patch, proposal)
    service.runs.update(run, "completed", answer="original answer unchanged")
    service.runs.release_conversation_turn(run)
    path = service.workspace / ".doppel" / "verification.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"commands": [{"name": "step" + str(index), "argv": ["fixture-step", str(index)],
                                            "timeout_seconds": 10} for index in range(commands)]}), encoding="utf-8")
    return run, proposal.patch_id, ledger, path


async def prepare(service, run, patch):
    return await service.prepare_verification(run, "actual-source", patch, operation_id=uuid4().hex,
                                             command_execute=True, workspace_write=True)


def test_manual_verification_preview_grants_scope_and_sealed_replay_never_launch_again(tmp_path):
    async def scenario():
        service = RunService(tmp_path)
        fake = FakeSupervisor()
        service.process_supervisor = fake
        try:
            run, patch, ledger, _ = await source(service)
            original_run = service.runs.get(run)
            with pytest.raises(PermissionError, match="fresh_command_and_write"):
                await service.prepare_verification(run, "actual-source", patch, operation_id=uuid4().hex,
                                                   command_execute=True, workspace_write=False)
            view = await prepare(service, run, patch)
            assert fake.calls == [] and view["status"] == "pending"
            with pytest.raises(ValueError, match="scope"):
                await service.decide_verification(run, view["review_id"], "0" * 64, action="approve",
                                                 command_execute=True, workspace_write=True)
            assert (await service.get_verification(run, view["review_id"]))["status"] == "pending"
            with pytest.raises(KeyError):
                await service.get_verification("f" * 32, view["review_id"])
            with pytest.raises(PermissionError):
                await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="approve")
            done = await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="approve",
                                                    command_execute=True, workspace_write=True)
            assert done["status"] == "completed" and done["success"] and len(fake.calls) == 1
            assert done["operation_id"] != "actual-source" and fake.calls[0][1]["run_id"] == done["operation_id"]
            assert done["steps"][0]["result"]["supervision"] == "fixture_not_native"
            assert service.runs.get(run) == original_run
            assert ledger.read_patch_evidence(run, "actual-source")["confirmed_applied"]
            (tmp_path / "file.py").write_text("later user work", encoding="utf-8")
            replay = await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="approve",
                                                      command_execute=True, workspace_write=True)
            assert replay["decision_replayed"] and len(fake.calls) == 1
            assert (tmp_path / "file.py").read_text(encoding="utf-8") == "later user work"
            rejected = await prepare(service, run, patch)
            assert (await service.decide_verification(run, rejected["review_id"], rejected["plan"]["plan_id"], action="reject"))["status"] == "rejected"
            assert len(fake.calls) == 1
        finally:
            await service.close()

    asyncio.run(scenario())


def test_changed_config_preserves_first_sealed_result_and_source_patch(tmp_path):
    async def scenario():
        service = RunService(tmp_path)
        fake = FakeSupervisor()
        service.process_supervisor = fake
        try:
            run, patch, ledger, path = await source(service, commands=2)
            view = await prepare(service, run, patch)
            original = fake.run

            async def change(argv, **kwargs):
                result = await original(argv, **kwargs)
                path.write_bytes(path.read_bytes() + b"\n")
                return result

            fake.run = change
            done = await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="approve",
                                                    command_execute=True, workspace_write=True)
            assert done["status"] == "failed" and done["success"] is None
            assert len(done["steps"]) == 1 and done["steps"][0]["status"] == "finished"
            assert done["error_code"] == "verification_review_stale" and len(fake.calls) == 1
            with pytest.raises(RuntimeError, match="indeterminate"):
                await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="approve",
                                                  command_execute=True, workspace_write=True)
            assert len(fake.calls) == 1 and ledger.read_patch_evidence(run, "actual-source")["confirmed_applied"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_repeated_cancel_drains_fake_supervisor_and_retains_owner_lock_unknown_intent(tmp_path):
    async def scenario():
        entered, stopping, drain = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Gated(FakeSupervisor):
            async def run(self, argv, **kwargs):
                self.calls.append((argv, kwargs))
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    stopping.set()
                    await await_durable(drain.wait())
                    raise

        service, task, waiter = RunService(tmp_path), None, None
        fake = Gated()
        service.process_supervisor = fake
        try:
            run, patch, _, _ = await source(service)
            view = await prepare(service, run, patch)
            task = asyncio.create_task(service.decide_verification(run, view["review_id"], view["plan"]["plan_id"],
                                      action="approve", command_execute=True, workspace_write=True))
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            await asyncio.wait_for(stopping.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and service._owner.held
            acquired = asyncio.Event()

            async def lock_waiter():
                async with service.workspace_locks.write(tmp_path):
                    acquired.set()

            waiter = asyncio.create_task(lock_waiter())
            await asyncio.sleep(0)
            assert not acquired.is_set()
            drain.set()
            assert isinstance((await asyncio.gather(task, return_exceptions=True))[0], asyncio.CancelledError)
            await waiter
            final = await service.get_verification(run, view["review_id"])
            assert final["status"] == "cancelled" and final["has_unknown_command"] and final["success"] is None
            with pytest.raises(RuntimeError):
                await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="approve",
                                                  command_execute=True, workspace_write=True)
            assert len(fake.calls) == 1
        finally:
            drain.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            if waiter is not None:
                await asyncio.gather(waiter, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_final_seal_failure_preserves_step_evidence_and_never_authorizes_repeat(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path)
        fake = FakeSupervisor()
        service.process_supervisor = fake
        try:
            run, patch, _, _ = await source(service)
            view = await prepare(service, run, patch)
            original = VerificationReviewStore.finish

            def broken(self, run, review, status, **kwargs):
                if status == "completed":
                    raise OSError("private final seal fixture detail")
                return original(self, run, review, status, **kwargs)

            monkeypatch.setattr(VerificationReviewStore, "finish", broken)
            with pytest.raises(OSError):
                await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="approve",
                                                  command_execute=True, workspace_write=True)
            unknown = await service.get_verification(run, view["review_id"])
            assert unknown["status"] == "indeterminate" and unknown["success"] is None
            assert unknown["steps"][0]["status"] == "finished"
            assert "private final seal" not in json.dumps(unknown)
            with pytest.raises(RuntimeError):
                await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="approve",
                                                  command_execute=True, workspace_write=True)
            assert len(fake.calls) == 1
        finally:
            await service.close()

    asyncio.run(scenario())


def test_pending_cancel_requires_exact_scope_but_not_execution_grants(tmp_path):
    async def scenario():
        service, fake = RunService(tmp_path), FakeSupervisor()
        service.process_supervisor = fake
        try:
            run, patch, ledger, _ = await source(service)
            view = await prepare(service, run, patch)
            with pytest.raises(ValueError, match="scope"):
                await service.cancel_verification(run, view["review_id"], "0" * 64)
            assert (await service.get_verification(run, view["review_id"]))["status"] == "pending"
            done = await service.cancel_verification(run, view["review_id"], view["plan"]["plan_id"])
            assert done["status"] == "cancelled" and done["success"] is None and done["steps"] == []
            assert await service.cancel_verification(run, view["review_id"], view["plan"]["plan_id"]) == done
            with pytest.raises(RuntimeError):
                await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="approve",
                                                  command_execute=True, workspace_write=True)
            assert fake.calls == [] and ledger.read_patch_evidence(run, "actual-source")["confirmed_applied"]
        finally:
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("close_service", [False, True])
def test_explicit_cancel_and_close_drain_manual_task_before_owner_release(tmp_path, close_service):
    async def scenario():
        entered, stopping, drain = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Gated(FakeSupervisor):
            async def run(self, argv, **kwargs):
                self.calls.append((argv, kwargs))
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    stopping.set()
                    await await_durable(drain.wait())
                    raise

        service, fake = RunService(tmp_path), Gated()
        service.process_supervisor = fake
        task = control = None
        try:
            run, patch, ledger, _ = await source(service)
            original_run = service.runs.get(run)
            view = await prepare(service, run, patch)
            task = asyncio.create_task(service.decide_verification(run, view["review_id"], view["plan"]["plan_id"],
                action="approve", command_execute=True, workspace_write=True))
            await asyncio.wait_for(entered.wait(), 3)
            with pytest.raises(RuntimeError, match="active"):
                await service.reconcile_verification(run, view["review_id"])
            with pytest.raises(RuntimeError, match="active"):
                await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"],
                    action="approve", command_execute=True, workspace_write=True)
            control = asyncio.create_task(service.close() if close_service else
                service.cancel_verification(run, view["review_id"], view["plan"]["plan_id"]))
            await asyncio.wait_for(stopping.wait(), 3)
            assert not control.done() and not task.done() and service._owner.held
            if close_service:
                with pytest.raises(RuntimeError, match="closed"):
                    await service.prepare_verification(run, "actual-source", patch, operation_id=uuid4().hex,
                        command_execute=True, workspace_write=True)
            else:
                with pytest.raises(RuntimeError, match="active|cancel"):
                    await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"],
                        action="approve", command_execute=True, workspace_write=True)
                # Control disconnect/repeated cancel must keep its fence and
                # owner until the already-requested command drain settles.
                control.cancel()
                await asyncio.sleep(0)
                control.cancel()
                assert not control.done() and service._verification_cancels
            drain.set()
            control_result = (await asyncio.gather(control, return_exceptions=True))[0]
            if close_service:
                assert control_result is None
            else:
                assert isinstance(control_result, asyncio.CancelledError)
            assert isinstance((await asyncio.gather(task, return_exceptions=True))[0], asyncio.CancelledError)
            stored = VerificationReviewStore(ledger.database).get(run, view["review_id"])
            assert stored["status"] == "cancelled" and stored["has_unknown_command"] and stored["success"] is None
            assert service.runs.get(run) == original_run and len(fake.calls) == 1
            assert service._verification_tasks == {} and service._verification_cancels == {}
            if close_service:
                assert not service._owner.held and service.cleanup_complete
        finally:
            drain.set()
            for item in (task, control):
                if item is not None:
                    await asyncio.gather(item, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("seal", [False, True])
def test_owner_start_recovers_stored_metadata_without_config_read_or_command(tmp_path, seal):
    async def scenario():
        first = RunService(tmp_path)
        try:
            run, patch, ledger, config = await source(first)
            view = await prepare(first, run, patch)
            store = VerificationReviewStore(ledger.database)
            store.claim(run, view["review_id"], view["plan"]["plan_id"], "approve")
            command = view["plan"]["commands"][0]
            store.start_step(run, view["review_id"], 0, command)
            if seal:
                store.seal_step(run, view["review_id"], 0, VerificationResult(command["name"], tuple(command["argv"]),
                    0, True, "fixture only", "", 1, "fixture_not_native").as_dict())
            config.write_text("invalid config should not be consulted during metadata reconciliation", encoding="utf-8")
            original = first.runs.get(run)
        finally:
            await first.close()
        reconstructed, fake = RunService(tmp_path), FakeSupervisor()
        reconstructed.process_supervisor = fake
        try:
            await reconstructed.start()
            actual = await reconstructed.reconcile_verification(run, view["review_id"])
            assert actual["status"] == ("completed" if seal else "indeterminate")
            assert actual["success"] is (True if seal else None) and fake.calls == []
            assert reconstructed.runs.get(run) == original
            assert ledger.read_patch_evidence(run, "actual-source")["confirmed_applied"]
            if seal:
                replay = await reconstructed.decide_verification(run, view["review_id"], view["plan"]["plan_id"],
                    action="approve", command_execute=True, workspace_write=True)
                assert replay["decision_replayed"] and fake.calls == []
            else:
                with pytest.raises(RuntimeError):
                    await reconstructed.decide_verification(run, view["review_id"], view["plan"]["plan_id"],
                        action="approve", command_execute=True, workspace_write=True)
        finally:
            await reconstructed.close()

    asyncio.run(scenario())


def test_cleanup_failure_retains_unknown_intent_owner_and_blocks_new_execution(tmp_path, monkeypatch):
    async def scenario():
        class Unproved(FakeSupervisor):
            cleanup_failed = False

            async def run_binary(self, argv, **kwargs):
                self.calls.append((argv, kwargs))
                self.cleanup_failed = True
                raise ProcessCleanupError("private cleanup fixture must not be stored")

            async def close(self):
                if self.cleanup_failed:
                    raise ProcessCleanupError("fixture quarantine")

        service, fake = RunService(tmp_path), Unproved()
        service.process_supervisor = fake
        try:
            run, patch, ledger, _ = await source(service)
            original = service.runs.get(run)
            view = await prepare(service, run, patch)
            inverse = await service.prepare_inverse_patch(run, "actual-source", patch, operation_id=uuid4().hex, workspace_write=True)
            with pytest.raises(ProcessCleanupError):
                await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"],
                    action="approve", command_execute=True, workspace_write=True)
            # Direct stored metadata inspection, not a fresh executable admission.
            stored = VerificationReviewStore(ledger.database).get(run, view["review_id"])
            assert stored["status"] == "indeterminate" and stored["has_unknown_command"] and stored["success"] is None
            assert "private cleanup" not in json.dumps(stored)
            assert service.runs.get(run) == original and service._owner.held
            diagnostic = await service.get_verification(run, view["review_id"])
            assert diagnostic["service"]["execution_admission"] == "quarantined"
            assert not diagnostic["service"]["failed_close_diagnostic"]
            with pytest.raises(RuntimeError, match="quarantine"):
                await prepare(service, run, patch)
            with pytest.raises(ProcessCleanupError, match="quarantine"):
                await service.close()
            assert service._owner.held and not service.cleanup_complete and len(fake.calls) == 1
            async def forbidden_start():
                pytest.fail("diagnostic must not restart resources/recover unknowns")

            monkeypatch.setattr(service, "start", forbidden_start)
            monkeypatch.setattr(service.runs, "get", lambda *a, **k: pytest.fail("writable run read"))
            monkeypatch.setattr(VerificationReviewStore, "__init__", lambda *a, **k: pytest.fail("store creation"))
            actual = await service.get_verification(run, view["review_id"])
            assert actual["status"] == "indeterminate" and actual["has_unknown_command"]
            assert actual["service"]["execution_admission"] == "quarantined"
            assert actual["service"]["failed_close_diagnostic"] and not actual["lifecycle"]["approval_available"]
            rows = await service.list_verifications(run, tool_call_id="actual-source", patch_id=patch)
            assert rows["items"][0]["review_id"] == view["review_id"] and len(fake.calls) == 1
            patch_read = await service.get_patch_evidence(run, "actual-source")
            assert patch_read["confirmed_applied"] and patch_read["service"]["failed_close_diagnostic"]
            patch_rows = await service.list_patch_effects(run)
            assert patch_rows[0]["confirmed_applied"] and not patch_rows[0]["source_run_quiescent"]
            inverse_read = await service.get_inverse_patch(run, inverse["review_id"])
            assert inverse_read["status"] == "pending" and inverse_read["service"]["failed_close_diagnostic"]
            assert service._owner.held and not service.cleanup_complete
        finally:
            # Fake only: no OS process exists. Production has no reset/retry
            # path; real cleanup failure retains owner until process exit.
            monkeypatch.undo()
            fake.cleanup_failed = False
            await service._shutdown_resources()

    asyncio.run(scenario())


def test_read_query_never_starts_service_and_refuses_released_owner(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path)
        try:
            async def forbidden_start():
                pytest.fail("read query must not start")

            monkeypatch.setattr(service, "start", forbidden_start)
            with pytest.raises(RuntimeError, match="query_owner"):
                await service.list_verifications(uuid4().hex)
            with pytest.raises(ValueError):
                await service.get_verification(uuid4().hex, None)
            monkeypatch.undo()
            run, patch, _, _ = await source(service)
            view = await prepare(service, run, patch)
            await service.close()
            with pytest.raises(RuntimeError, match="query_owner"):
                await service.get_verification(run, view["review_id"])
        finally:
            await service.close()

    asyncio.run(scenario())


def test_query_worker_drains_repeated_cancel_before_close_owner_release(tmp_path, monkeypatch):
    import threading

    async def scenario():
        service = RunService(tmp_path)
        entered, release = threading.Event(), threading.Event()
        query = closing = None
        try:
            run, patch, _, _ = await source(service)
            view = await prepare(service, run, patch)
            original = VerificationQueries.detail

            def gated(self, *args, **kwargs):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("fixture query gate timed out")
                return original(self, *args, **kwargs)

            monkeypatch.setattr(VerificationQueries, "detail", gated)
            query = asyncio.create_task(service.get_verification(run, view["review_id"]))
            assert await asyncio.to_thread(entered.wait, 10)
            query.cancel()
            await asyncio.sleep(0)
            query.cancel()
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(0)
            assert not query.done() and not closing.done() and service._owner.held
            release.set()
            result = (await asyncio.gather(query, return_exceptions=True))[0]
            assert isinstance(result, asyncio.CancelledError)
            await closing
            assert service.cleanup_complete and not service._owner.held
        finally:
            release.set()
            for task in (query, closing):
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_manual_terminal_maintenance_is_after_write_lock_and_does_not_reconcile_unknown(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    async def scenario():
        service = RunService(tmp_path)
        service.process_supervisor = FakeSupervisor()
        calls = []
        original = service._maintain_verification_metadata
        write = service.workspace_locks.write
        write_held = False

        @asynccontextmanager
        async def tracked_write(*args, **kwargs):
            nonlocal write_held
            async with write(*args, **kwargs):
                write_held = True
                try:
                    yield
                finally:
                    write_held = False

        async def observed(**kwargs):
            assert not write_held  # Refuse a wrong callback position before a deadlock.
            calls.append((dict(service._verification_tasks), dict(service._verification_cancels)))
            await original(**kwargs)

        try:
            run, patch, ledger, _ = await source(service)
            monkeypatch.setattr(service.workspace_locks, "write", tracked_write)
            monkeypatch.setattr(service, "_maintain_verification_metadata", observed)
            view = await prepare(service, run, patch)
            await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"], action="reject")
            cancelled = await prepare(service, run, patch)
            await service.cancel_verification(run, cancelled["review_id"], cancelled["plan"]["plan_id"])
            assert len(calls) == 2 and calls == [({}, {}), ({}, {})]
            unknown = await prepare(service, run, patch)
            store = VerificationReviewStore(ledger.database)
            store.claim(run, unknown["review_id"], unknown["plan"]["plan_id"], "approve")
            store.start_step(run, unknown["review_id"], 0, unknown["plan"]["commands"][0])
            await service._maintain_verification_metadata()
            assert (await service.get_verification(run, unknown["review_id"]))["status"] == "running"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_result_persistence_quota_is_unknown_stable_cause_not_patch_failure_or_retry(tmp_path):
    async def scenario():
        class EscapedOutput(FakeSupervisor):
            async def run_binary(self, argv, **kwargs):
                self.calls.append((argv, kwargs))
                return SimpleNamespace(exit_code=0, stdout=b"\x00" * 400000, stderr=b"", supervision="fixture_not_native")

        service, fake = RunService(tmp_path), EscapedOutput()
        service.process_supervisor = fake
        try:
            run, patch, ledger, config = await source(service, commands=2)
            original = service.runs.get(run)
            body = json.loads(config.read_text(encoding="utf-8"))
            body["max_output_bytes"] = 1048576
            config.write_text(json.dumps(body), encoding="utf-8")
            view = await prepare(service, run, patch)
            with pytest.raises(ValueError, match="verification_evidence_budget"):
                await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"],
                    action="approve", command_execute=True, workspace_write=True)
            actual = await service.get_verification(run, view["review_id"])
            assert actual["status"] == "indeterminate" and actual["error_code"] == "verification_evidence_budget"
            assert actual["success"] is None and actual["has_unknown_command"]
            assert actual["lifecycle"]["sealed_steps"] == 1 and len(fake.calls) == 2
            reconciled = await service.reconcile_verification(run, view["review_id"])
            assert reconciled["error_code"] == "verification_evidence_budget"
            assert service.runs.get(run) == original and ledger.read_patch_evidence(run, "actual-source")["confirmed_applied"]
            with pytest.raises(RuntimeError):
                await service.decide_verification(run, view["review_id"], view["plan"]["plan_id"],
                    action="approve", command_execute=True, workspace_write=True)
            assert len(fake.calls) == 2
        finally:
            await service.close()

    asyncio.run(scenario())
