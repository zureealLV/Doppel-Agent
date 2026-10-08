"""Owned work-order control/admission stepper, reusing the runtime's admission.

There is no second scheduler, provider, tool registry, or subprocess owner here.
RunService supplies its internal already-owned create/cancel callbacks; its
shutdown must close this stepper before shutting down runtime resources.
Its bounded candidate pump only drives existing runtime admission.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

from ..persistence.owned import await_durable
from ..persistence.work_orders import WorkOrderStore
from .work_orders import WorkOrderPlan


class WorkOrderOrchestrator:
    def __init__(
        self, store: WorkOrderStore,
        create_run: Callable[[dict[str, Any]], Awaitable[Any]],
        cancel_run: Callable[[str], Awaitable[Any]],
        *, admission_available: Callable[[], bool] | None = None,
    ):
        self.store = store
        self._create_run = create_run
        self._cancel_run = cancel_run
        self._admission = asyncio.Lock()
        self._closing = False
        self._available = admission_available or (lambda: True)
        self._wake = asyncio.Event()
        self._worker: asyncio.Task | None = None

    def wake(self) -> None:
        self._wake.set()

    async def start(self) -> None:
        if self._closing:
            raise RuntimeError("work-order admission is closing")
        if self._worker is not None:
            return
        await self.recover()
        self._worker = asyncio.create_task(self._pump(), name="doppel-work-order-pump")

    async def recover(self) -> None:
        # RunService must first recover its lost runtime leases under OS owner.
        await self._reconcile_candidates(serial=True)

    async def reconcile_after_drain(self) -> None:
        if not self._closing or (self._worker is not None and not self._worker.done()):
            raise RuntimeError("work-order admission must be closed before final reconciliation")
        await self._reconcile_candidates(serial=False)

    async def _reconcile_candidates(self, *, serial: bool) -> None:
        cursor = ""
        while True:
            page = await self._io(self.store.candidates, after_id=cursor)
            if not page:
                return
            for identifier in page:
                async def reconcile(identifier=identifier):
                    try:
                        return await self._io(self.store.reconcile, identifier)
                    except (ValueError, RuntimeError):
                        return await self._io(self.store.record_dispatch_error, identifier, "reconciliation_failed")
                if serial:
                    await self._serial(reconcile)
                else:
                    await reconcile()
            cursor = page[-1]

    async def _pump(self) -> None:
        while not self._closing:
            self._wake.clear()
            cursor = ""
            while not self._closing:
                try:
                    page = await self._io(self.store.candidates, after_id=cursor)
                except (sqlite3.Error, OSError) as error:
                    logging.getLogger(__name__).warning("work-order scan deferred: %s", type(error).__name__)
                    break  # Bound retries; no reservation or provider on unreadable state.
                if not page:
                    break
                for identifier in page:
                    if self._closing:
                        break
                    try:
                        await self.step(identifier)
                    except Exception as error:
                        if self._closing:
                            break
                        # No raw provider/path/exception payload in logs.
                        logging.getLogger(__name__).warning("work-order pump deferred: %s", type(error).__name__)
                        try:
                            await self._io(self.store.record_dispatch_error, identifier, "reconciliation_failed")
                        except Exception:
                            pass  # Next bounded polling cycle may recover DB IO.
                cursor = page[-1]
            if not self._closing:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.5)
                except TimeoutError:
                    pass

    async def _io(self, method, *args, **kwargs):
        return await await_durable(asyncio.to_thread(method, *args, **kwargs))

    async def _serial(self, operation):
        async def owned():
            if self._closing:
                raise RuntimeError("work-order admission is closing")
            async with self._admission:
                if self._closing:
                    raise RuntimeError("work-order admission is closing")
                return await operation()

        # A disconnected controller cannot release admission while SQLite or a
        # runtime acceptance is still in flight. Close drains this same lock.
        return await await_durable(owned())

    async def activate(self, identifier: str, *, expected_revision: int, settings: dict[str, Any]) -> dict[str, Any]:
        record = await self._serial(lambda: self._io(
            self.store.activate, identifier, expected_revision=expected_revision, settings=settings,
        ))
        self.wake()
        return record

    async def revise(self, identifier: str, plan: WorkOrderPlan, *, expected_revision: int, profiles: dict | None = None, context: dict | None = None) -> dict[str, Any]:
        return await self._serial(lambda: self._io(
            self.store.revise_plan, identifier, plan, expected_revision=expected_revision, profiles=profiles, context=context,
        ))

    async def retry(
        self, identifier: str, task_id: str, *, expected_revision: int, expected_attempt_id: str,
    ) -> dict[str, Any]:
        return await self._serial(lambda: self._io(
            self.store.retry_task, identifier, task_id, expected_revision=expected_revision,
            expected_attempt_id=expected_attempt_id,
        ))

    async def control(self, identifier: str, action: str, *, expected_revision: int) -> dict[str, Any]:
        async def operation():
            record = await self._io(self.store.control, identifier, action, expected_revision=expected_revision)
            if action == "cancel":
                return await self._cancel_order(record)
            return record

        record = await self._serial(operation)
        self.wake()
        return record

    async def step(self, identifier: str) -> dict[str, Any]:
        return await self._serial(lambda: self._step(identifier))

    async def _step(self, identifier: str) -> dict[str, Any]:
        try:
            record = await self._io(self.store.reconcile, identifier)
        except (ValueError, RuntimeError):
            # Conflicting durable data does not trigger a second admission.
            return await self._io(self.store.record_dispatch_error, identifier, "reconciliation_failed")
        if record["status"] == "cancelled":
            return await self._cancel_order(record)
        if record["status"] not in {"queued", "running"}:
            return record
        if not self._available():
            return record  # Existing queue owns its bounded backpressure.
        pending = await self._io(self.store.pending_intents, identifier)
        intent = pending[0] if pending else await self._io(
            self.store.reserve_next, identifier, expected_revision=record["active_revision"],
        )
        if intent is None:
            return await self._io(self.store.reconcile, identifier)
        try:
            # Replay uses the identical persisted key/request, not a fresh turn.
            # The callback's return text is never an acceptance oracle.
            await self._create_run(intent["request"])
        except Exception as error:
            try:
                await self._io(self.store.reconcile, identifier)
                pending = await self._io(self.store.pending_intents, identifier)
            except (ValueError, RuntimeError):
                return await self._io(self.store.record_dispatch_error, identifier, "reconciliation_failed")
            unresolved = any(item["attempt_id"] == intent["attempt_id"] for item in pending)
            if unresolved and isinstance(error, (ValueError, KeyError)):
                # Known local rejection, callback finished, admission lock held,
                # and no matching runtime: this is settled, not a timed-out IO.
                await self._io(self.store.abandon_unaccepted, intent["attempt_id"], admission_settled=True)
                code = "admission_rejected"
            else:
                # Unknown acceptance stays reserved/paused, preserving the key.
                code = "admission_failed"
            return await self._io(self.store.record_dispatch_error, identifier, code)
        try:
            record = await self._io(self.store.reconcile, identifier)
        except (ValueError, RuntimeError):
            return await self._io(self.store.record_dispatch_error, identifier, "reconciliation_failed")
        pending = await self._io(self.store.pending_intents, identifier)
        if any(item["attempt_id"] == intent["attempt_id"] for item in pending):
            return await self._io(self.store.record_dispatch_error, identifier, "admission_not_persisted")
        return record

    async def _cancel_order(self, record: dict[str, Any]) -> dict[str, Any]:
        identifier = record["work_order_id"]
        for attempt in record["attempts"]:
            if attempt["run_id"] and attempt["status"] in {
                "reserved", "accepted", "running", "awaiting_approval", "interrupted",
            }:
                try:
                    await self._cancel_run(attempt["run_id"])
                except Exception:
                    return await self._io(self.store.record_dispatch_error, identifier, "runtime_cancel_failed")
        # No create callback can be in flight while this owner holds admission.
        pending = await self._io(self.store.pending_intents, identifier)
        for intent in pending:
            await self._io(self.store.abandon_unaccepted, intent["attempt_id"], admission_settled=True)
        return await self._io(self.store.reconcile, identifier)

    async def close(self) -> None:
        self._closing = True
        self.wake()

        async def drain():
            async with self._admission:
                pass

        await await_durable(drain())
        if self._worker is not None:
            await await_durable(self._worker)
