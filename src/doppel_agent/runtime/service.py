"""Application service joining persistence, runtimes, events and scheduling."""

from __future__ import annotations

import asyncio
import threading
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..concurrency import (
    AsyncRunScheduler,
    QueueCapacityError,
    ResourceLimits,
    WorkspaceLockManager,
)
from ..context.manifest import ManifestReader, ManifestStore
from ..context.notes import ProjectNoteStore
from ..context.selection import ContextSelectionStore
from ..context.input import ContextInputResolver, normalize_request
from ..permissions import PermissionManager
from ..mcp import MCPClientManager, MCPToolCatalog, MCPToolExecutor, load_mcp_config
from ..mcp.tool_adapter import doppel_mcp_tools, langchain_mcp_tools
from ..persistence import EventStore, RuntimeRunStore
from ..persistence.conversations import NativeConversationStore
from ..persistence.work_orders import WorkOrderStore
from ..tasks.orchestrator import WorkOrderOrchestrator
from ..tasks.work_orders import WorkOrderPlan, execution_settings
from ..persistence.tool_ledger import ToolExecutionLedger
from ..persistence.inverse_reviews import InverseReviewStore, identifier as inverse_identifier
from ..persistence.verification_reviews import MAX_PENDING as MAX_MANUAL_VERIFICATIONS, VerificationReviewStore
from ..persistence.verification_maintenance import VerificationMaintenance
from ..persistence.verification_queries import MAX_QUERY_ROWS, VerificationQueries
from ..persistence.changes_queries import InverseEvidenceQueries, PatchEvidenceQueries
from ..persistence.ownership import WorkspaceOwner, BorrowedWorkspaceOwner
from ..persistence.retention import CheckpointRetention
from ..persistence.patch_retention import PrivatePatchRetention
from ..persistence.owned import await_durable as _await_durable
from ..persistence.provider_recovery import ProviderReceiptRecoveryQueries
from ..persistence.legacy_recovery import LegacyOperationRecoveryQueries
from ..persistence.tool_recovery import ToolOperationRecoveryQueries
from ..workspace.process_supervisor import ProcessSupervisor
from ..workspace.patching import PatchService
from ..workspace.git_reader import GitInspector
from ..workspace.git_authorization import SelectedMetadataRoot, capture_linked_metadata, check_linked_metadata
from ..workspace.git_inspection import GitInspectionError
from ..workspace.verification import VerificationPipeline
from ..provider import (
    AsyncOpenAICompatibleProvider,
    Message,
    MockProvider,
    OpenAICompatibleProvider,
    bounded_run_retries,
)
from ..settings import SettingsStore, SettingsPersistenceError
from ..billing_tariff import freeze_tariff, validate_price_receipt
from ..skills.registry import SkillRegistry
from ..reporting.queries import RunReportQueries
from ..tools import (
    ToolRegistry,
    list_files_tool,
    patch_tool,
    read_file_range_tool,
    read_file_tool,
    run_command_tool,
    search_text_tool,
    workspace_map_tool,
)
from .base import EventSink, ResumeCommand, RunRequest, RuntimeResult
from .async_subagents import AsyncSubagentManager, AsyncSubagentRequest
from .factory import create_runtime
from .provider_adapter import ProviderAdapter
from .provider_recording import ProviderReceiptFault


class EventNotifier:
    def __init__(self) -> None:
        self._conditions: dict[str, asyncio.Condition] = {}

    def _condition(self, run_id: str) -> asyncio.Condition:
        return self._conditions.setdefault(run_id, asyncio.Condition())

    async def notify(self, run_id: str) -> None:
        condition = self._condition(run_id)
        async with condition:
            condition.notify_all()

    async def wait(self, run_id: str, timeout: float = 15) -> None:
        condition = self._condition(run_id)
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout)
        except TimeoutError:
            return


class DurableEventSink(EventSink):
    def __init__(
        self,
        store: EventStore,
        notifier: EventNotifier,
        run_id: str,
        thread_id: str,
    ):
        self.store = store
        self.notifier = notifier
        self.run_id = run_id
        self.thread_id = thread_id

    async def emit(self, kind: str, **payload: Any) -> None:
        await _await_durable(asyncio.to_thread(self.store.append, self.run_id, self.thread_id, kind, payload))
        await self.notifier.notify(self.run_id)


@dataclass(frozen=True)
class ChildRuntimeSink(EventSink):
    """Original parent timeline, explicitly namespaced frozen child lineage.

    Child engine/tool text is nested payload, never authority over the parent or
    the manager's child lifecycle. No independently registered child run exists.
    Old untagged persisted events remain unknown; this adds only new receipts.
    """

    parent: EventSink
    parent_run_id: str
    subagent_id: str
    generation: int

    async def emit(self, kind: str, **payload: Any) -> None:
        # Join the whole original append+notification, not only the worker; a
        # cancelled child must not leave a detached parent event callback.
        await _await_durable(self.parent.emit(
            "subagent.runtime", parent_run_id=self.parent_run_id, subagent_id=self.subagent_id,
            generation=self.generation, runtime_kind=kind, runtime_payload=payload,
        ))


class SubagentLifecycleSink(EventSink):
    """Route child lifecycle events into the durable parent-run timeline."""

    def __init__(self, runs: RuntimeRunStore, events: EventStore, notifier: EventNotifier, *, on_terminal=None):
        self.runs = runs
        self.events = events
        self.notifier = notifier
        self.on_terminal = on_terminal

    async def emit(self, kind: str, **payload: Any) -> None:
        parent_run_id = payload.get("parent_run_id")
        if not isinstance(parent_run_id, str):
            return
        record = await asyncio.to_thread(self.runs.get, parent_run_id)
        if record is None:
            return
        await asyncio.to_thread(
            self.events.append,
            parent_run_id,
            record["thread_id"],
            kind,
            payload,
        )
        await self.notifier.notify(parent_run_id)
        if self.on_terminal is not None and kind in {
            "subagent.completed", "subagent.failed", "subagent.cancelled"
        }:
            await self.on_terminal()


def _owned_operation(method=None, *, quarantine_safe=False):
    """Original owned IO; only explicitly classified metadata/cancel may quarantine-pass.

    This is no replay authority: new effect/acceptance default refuses before IO.
    Already entered original work retains its own original drain/cleanup path.
    """
    if method is None:
        return lambda original: _owned_operation(original, quarantine_safe=quarantine_safe)
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        await self.start()
        if not quarantine_safe:
            self._assert_provider_admission()
        task = asyncio.current_task()
        self._operations.add(task)
        try:
            return await method(self, *args, **kwargs)
        finally:
            self._operations.discard(task)
    return wrapped


class RunService:
    def __init__(
        self,
        workspace: Path,
        *,
        provider: Any | None = None,
        max_active_runs: int = 4,
        queue_capacity: int = 100,
        approval_ttl_seconds: int = 900,
        workspace_owner: BorrowedWorkspaceOwner | None = None,
    ):
        self.workspace = workspace.resolve(strict=True)
        self.state_root = self.workspace / ".doppel-agent"
        owner_path = self.state_root / "local-mode.lock"
        if workspace_owner is not None and workspace_owner.path != owner_path:
            raise ValueError("desktop owner must match the runtime workspace")
        if workspace_owner is not None:
            workspace_owner.acquire()
        self.state_root.mkdir(parents=True, exist_ok=True)
        database = self.state_root / "runtime.sqlite3"
        self.runs = RuntimeRunStore(database)
        self.conversations = NativeConversationStore(database)
        self.context_reader = ManifestReader(self.workspace)
        self.work_orders = WorkOrderStore(database, input_resolver=ContextInputResolver(self.context_reader).resolve)
        self.context_manifests = ManifestStore(database)
        self.project_notes = ProjectNoteStore(database)
        self.context_selections = ContextSelectionStore(database)
        self.events = EventStore(database)
        # Shared only by this ORIGINAL owner lifetime/runtimes/children/fallback.
        # No cached-provider current-run mutation or automatic fault reset.
        self._provider_receipt_fault = ProviderReceiptFault()
        self._unresolved_report_reads = {}  # Private sources retained by THIS original owner.
        self.settings = SettingsStore(self.state_root / "provider-settings.json",
                                      failure=self._provider_receipt_fault.mark_failed,
                                      cleanup_failure=self._provider_receipt_fault.retain_cleanup)
        self.scheduler = AsyncRunScheduler(max_active=max_active_runs, queue_capacity=queue_capacity)
        self.resources = ResourceLimits()
        self.workspace_locks = WorkspaceLockManager()
        self.notifier = EventNotifier()
        self.provider_override = provider
        self.approval_ttl_seconds = approval_ttl_seconds
        self._started = False
        self._metadata_ready = False  # Held-owner readonly startup, not execution pumps.
        self._provider_startup_recovery = None
        self._legacy_startup_recovery = None
        self._tool_startup_recovery = None
        self._closed = False
        self._lifecycle_lock = asyncio.Lock()
        self._close_task = None
        self._stream_shutdown = threading.Event()
        self._operations: set[asyncio.Task] = set()
        self.checkpoint_retention = CheckpointRetention()
        self.private_patch_retention = PrivatePatchRetention()
        self._private_retention_cursor = None
        self._private_retention_lock = asyncio.Lock()
        self._owner = (workspace_owner if workspace_owner is not None else
                       WorkspaceOwner(owner_path, failure=self._provider_receipt_fault.mark_failed,
                                      cleanup_failure=self._provider_receipt_fault.retain_cleanup))
        self.cleanup_complete = False
        self._providers: dict[str, Any] = {}
        # Single exact ephemeral native-owner binding, never an HTTP path grant.
        self._git_metadata_roots: tuple[Path, ...] = ()
        self._git_metadata_binding = None
        self._git_metadata_grant_id = None
        self._skill_registry = None
        self._skill_lock = asyncio.Lock()
        self.mcp_discovery_timeout_seconds = 10
        self.mcp_config = load_mcp_config(self.workspace)
        self.mcp_manager = MCPClientManager(
            self.mcp_config, failure=self._provider_receipt_fault.mark_failed,
            execution_failure=self._provider_receipt_fault.mark_failed,
            publication_failure=self._provider_receipt_fault.mark_failed,
        ) if self.mcp_config.servers else None
        self.mcp_catalog = MCPToolCatalog(self.mcp_manager) if self.mcp_manager else None
        self.mcp_ledger = ToolExecutionLedger(
            self.state_root / "mcp-tool-executions.sqlite3",
            failure=self._provider_receipt_fault.mark_failed,
            cleanup_failure=self._provider_receipt_fault.retain_cleanup,
        )
        self.process_supervisor = ProcessSupervisor(failure=self._provider_receipt_fault.mark_failed,
                                                    cleanup_failure=self._provider_receipt_fault.retain_cleanup)
        self._patch_ledger = None
        self._inverse_reviews = None
        self._verification_reviews = None
        self._verification_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._verification_cancels: dict[tuple[str, str], int] = {}
        self._verification_maintenance = VerificationMaintenance()
        self._verification_maintenance_cursor = ""
        self._verification_maintenance_lock = asyncio.Lock()
        self._patch_service_lock = asyncio.Lock()
        self.subagents = AsyncSubagentManager(
            database,
            self._run_async_subagent,
            max_active=2,
            queue_capacity=16,
            max_per_parent=4,
            sink=SubagentLifecycleSink(
                self.runs, self.events, self.notifier, on_terminal=self._maintain_checkpoints
            ),
        )
        self.work_order_orchestrator = WorkOrderOrchestrator(
            self.work_orders, self._create_work_order_run, self._cancel_owned,
            admission_available=lambda: (not self._provider_receipt_fault.broken
                and self.scheduler.queued_count < self.scheduler.queue_capacity),
        )

    async def start(self) -> None:
        if self._closed or self._close_task is not None:
            raise RuntimeError("RunService is closed")
        self._assert_process_admission()
        async with self._lifecycle_lock:
            if self._closed or self._close_task is not None:
                raise RuntimeError("RunService is closed")
            self._assert_process_admission()
            if self._started or self._metadata_ready:
                return
            self._owner.acquire()
            try:
                # Read existing ORIGINAL journal with this owner before ANY
                # recovery rewrite/pump. Cancellation joins this exact SQLite
                # worker before cleanup/release, never an observation timeout.
                recovery = await _await_durable(asyncio.to_thread(
                    ProviderReceiptRecoveryQueries(self.runs.database).read))
                self._provider_startup_recovery = recovery
                # ORIGINAL Legacy journal is a separate operation source, not
                # provider SQL coverage. Join this same owned startup worker
                # path BEFORE pumps; missing/corrupt/unfinished Legacy source
                # cannot be cleared by a Core session or a clean SQL snapshot.
                legacy_recovery = await _await_durable(asyncio.to_thread(
                    LegacyOperationRecoveryQueries(self.state_root, runtime_database=self.runs.database).read))
                self._legacy_startup_recovery = legacy_recovery
                # Original tool side effects can outlive a paired provider turn:
                # e.g. patch writes returned but the final SQL seal never did.
                # Read existing ledger before metadata rewrite/pumps/settings;
                # never construct/migrate/retry a tool to diagnose its absence.
                tool_recovery = await _await_durable(asyncio.to_thread(
                    ToolOperationRecoveryQueries(self.state_root / 'tool-executions.sqlite3',
                        self.runs.database, failure=self._provider_receipt_fault.mark_failed,
                        cleanup_failure=self._retain_report_cleanup).read))
                self._tool_startup_recovery = tool_recovery
                if self._closed or self._close_task is not None:
                    raise RuntimeError("RunService is closed")
                if (recovery.quarantined or legacy_recovery.quarantined or tool_recovery.quarantined
                        or self._provider_receipt_fault.broken):
                    self._provider_receipt_fault.mark_failed()
                    self._metadata_ready = True
                    # No runtime/subagent/work-order recovery/pumps, metadata
                    # rewrites or provider lookup. Not an invented failed-close.
                    return
                # Original metadata decode only: no profile key/decrypt/provider.
                # Prior source quarantine returns BEFORE this settings read.
                try:
                    await _await_durable(asyncio.to_thread(self.settings.recovery_public))
                except SettingsPersistenceError:
                    self._provider_receipt_fault.mark_failed()
                    if self._closed or self._close_task is not None:
                        raise RuntimeError("RunService is closed") from None
                    self._metadata_ready = True
                    return
                if self._closed or self._close_task is not None:
                    raise RuntimeError("RunService is closed")
                if self._provider_receipt_fault.broken:
                    self._metadata_ready = True
                    return
                # A cancelled to_thread must finish before another owner recovers.
                await _await_durable(asyncio.to_thread(self.runs.recover_incomplete))
                await _await_durable(asyncio.to_thread(self.conversations.reconcile))
                await self.scheduler.start()
                await _await_durable(self.subagents.start())
                await _await_durable(asyncio.to_thread(self.checkpoint_retention.apply, self.state_root))
                await self._maintain_private_payloads()
                # Before public manual admission: reconcile at most one bounded
                # metadata page, never create a ledger or recover commands.
                await self._maintain_verification_metadata(recover_running=True)
                self._started = True
                self._metadata_ready = True
                await _await_durable(self.work_order_orchestrator.start())
            except BaseException:
                self._closed = True
                await _await_durable(self._shutdown_resources())
                raise

    @property
    def stream_shutdown_requested(self) -> bool:
        return self._stream_shutdown.is_set()

    def request_stream_shutdown(self) -> None:
        # Native close is synchronous/off the owner loop. Signal ONLY read-only
        # SSE termination, before Uvicorn waits for HTTP responses and enters
        # lifespan shutdown. This neither cancels/decides runs nor proves cleanup.
        # Monotonic original lifetime; no foreign loop, worker or reset/replay.
        self._stream_shutdown.set()

    async def close(self) -> None:
        self.request_stream_shutdown()
        if self._close_task is None:
            self._clear_git_metadata_grant()
            self._close_task = asyncio.create_task(self._close_owned())
        await _await_durable(asyncio.shield(self._close_task))

    async def _close_owned(self) -> None:
        async with self._lifecycle_lock:
            self._closed = True
            # Stop active manual decisions (including pre-claim lock waiters).
            # Their own cancellation path drains command/SQLite evidence before
            # the generic owned-operation join and workspace owner release.
            for task in tuple(self._verification_tasks.values()):
                if not task.done():
                    task.cancel()
            await self.work_order_orchestrator.close()
            # Public operations may be draining acceptance/approval SQLite IO.
            if self._operations:
                await asyncio.gather(*self._operations, return_exceptions=True)
            await self._shutdown_resources()

    async def _shutdown_resources(self) -> None:
        # Fail closed: if any cleanup fails, retain the owner until process exit.
        self._clear_git_metadata_grant()
        await self.work_order_orchestrator.close()
        await self.subagents.close()
        await self.scheduler.shutdown()
        await self.process_supervisor.close()
        for provider in self._providers.values():
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()
        self._providers.clear()
        if self.mcp_manager is not None:
            await self.mcp_manager.close()
        if self.settings.cleanup_uncertain:
            # Joined original workers are not evidence of a failed file lifetime.
            # Keep SAME owner and close task; no reset/foreign-loop cleanup retry.
            raise RuntimeError("settings_cleanup_unresolved")
        # Runtime/child/fallback/manual constructors all bind SAME original
        # owner fault. Joining their worker or successful SDK close cannot prove
        # an original failed SQLite close returned. Keep SAME owner/close task.
        self._provider_receipt_fault.check_cleanup()
        if self._started:
            await _await_durable(self.work_order_orchestrator.reconcile_after_drain())
        self._started = False
        self._metadata_ready = False
        self._provider_receipt_fault.check_cleanup()  # Last memory-only gate before original release.
        self._owner.release()
        self.cleanup_complete = True

    @staticmethod
    def _freeze_public_profile(profile: dict) -> dict:
        snapshot = {key: profile[key] for key in ("id", "provider", "model", "base_url")}
        # Snapshot clock date, not a claim of atomic identity with DB created_at.
        # No latest-profile read at resume/report and no credentials/reference in
        # this numeric receipt. Future-effective tariff conservatively unknown.
        snapshot["billing_price_receipt"] = freeze_tariff(profile.get("billing_tariff"), snapshot,
            freeze_date=datetime.now(UTC).date().isoformat())
        return snapshot

    def _freeze_work_order_scope(self, settings: dict, tasks, *, existing: dict | None = None) -> dict:
        options = execution_settings(settings)
        public = self.settings.public() if self.provider_override is None else None
        options["profile_id"] = options["profile_id"] or (public["active_profile_id"] if public else "scripted")
        wanted = {options["profile_id"], *(task.profile_id or options["profile_id"] for task in tasks)}
        snapshots = dict(existing or {})
        for identifier in wanted:
            if identifier in snapshots:
                continue  # A saved model/URL is not silently replaced after edits.
            if self.provider_override is not None:
                model = getattr(self.provider_override, "model", None)
                snapshots[identifier] = {"id": identifier, "provider": "explicit_override",
                    "model": model if isinstance(model, str) and model else "scripted",
                    "implementation": type(self.provider_override).__name__}
            else:
                profile = next((p for p in public["profiles"] if p["id"] == identifier), None)
                if profile is None:
                    raise ValueError("model profile not found")
                snapshots[identifier] = self._freeze_public_profile(profile)
        options["profiles"] = snapshots
        return execution_settings(options)

    async def _create_work_order_run(self, request: dict) -> tuple[dict, bool]:
        intent = await _await_durable(asyncio.to_thread(self.work_orders.intent_by_key, request["idempotency_key"]))
        if intent is None or intent["request"] != request or not intent["profile_snapshot"]:
            raise ValueError("dispatch has no matching frozen model profile")
        saved_input = intent["input_snapshot"]
        if saved_input is not None and saved_input["scope_work_order_id"] != intent["work_order_id"]:
            raise ValueError("dispatch input scope mismatch")
        return await self._create_owned(request, frozen_profile=intent["profile_snapshot"],
                                        write_scope=intent["access"] == "write", frozen_input_snapshot=saved_input)

    @_owned_operation
    async def create_work_order(self, plan: WorkOrderPlan, idempotency_key: str | None = None):
        return await _await_durable(asyncio.to_thread(self.work_orders.create, plan, idempotency_key))

    @_owned_operation(quarantine_safe=True)
    async def list_work_orders(self, limit: int = 50, *, before_id: str | None = None, search: str = ""):
        return await _await_durable(asyncio.to_thread(self.work_orders.list, limit, before_id=before_id, search=search))

    @_owned_operation(quarantine_safe=True)
    async def get_work_order(self, identifier: str):
        return await _await_durable(asyncio.to_thread(self.work_orders.get, identifier))

    @_owned_operation(quarantine_safe=True)
    async def work_order_queue(self):
        snapshot = await _await_durable(asyncio.to_thread(self.runs.active_queue))
        snapshot["scheduler"] = {"active": self.scheduler.active_count, "queued": self.scheduler.queued_count,
                                 "max_active": self.scheduler.max_active, "queue_capacity": self.scheduler.queue_capacity}
        return snapshot

    @_owned_operation(quarantine_safe=True)
    async def work_order_selection(self):
        return await _await_durable(asyncio.to_thread(self.work_orders.selection))

    @_owned_operation(quarantine_safe=True)
    async def work_order_run_selection(self, identifier: str):
        return await _await_durable(asyncio.to_thread(self.work_orders.run_selection, identifier))

    @_owned_operation
    async def set_work_order_selection(self, work_order_id: str | None, run_id: str | None,
                                       *, expected_revision: int, idempotency_key: str):
        return await _await_durable(asyncio.to_thread(self.work_orders.set_selection, work_order_id, run_id,
                                                     expected_revision=expected_revision, idempotency_key=idempotency_key))

    @_owned_operation(quarantine_safe=True)
    async def context_preview(self, files: list[dict], budget_bytes: int):
        return await _await_durable(asyncio.to_thread(self.context_reader.preview, files, budget_bytes=budget_bytes))

    @_owned_operation
    async def context_accept(self, files: list[dict], *, budget_bytes: int, confirmed: bool, idempotency_key: str):
        return await _await_durable(asyncio.to_thread(self.context_manifests.accept, self.context_reader, files,
            budget_bytes=budget_bytes, confirmed=confirmed, idempotency_key=idempotency_key))

    @_owned_operation(quarantine_safe=True)
    async def get_context_manifest(self, manifest_id: str):
        return await _await_durable(asyncio.to_thread(self.context_manifests.get, manifest_id))

    @_owned_operation(quarantine_safe=True)
    async def check_context_manifest(self, manifest_id: str):
        def check():
            manifest = self.context_manifests.get(manifest_id)
            if manifest is None:
                raise KeyError(manifest_id)
            return {"manifest_id": manifest_id, "entries": self.context_reader.check(manifest)}
        return await _await_durable(asyncio.to_thread(check))

    @_owned_operation(quarantine_safe=True)
    async def list_project_notes(self, *, scope_work_order_id: str | None = None, before_id: str | None = None, limit: int = 50):
        return await _await_durable(asyncio.to_thread(self.project_notes.list,
            scope_work_order_id=scope_work_order_id, before_id=before_id, limit=limit))

    @_owned_operation(quarantine_safe=True)
    async def get_project_note(self, note_id: str, *, revision: int | None = None):
        return await _await_durable(asyncio.to_thread(self.project_notes.get, note_id, revision=revision))

    @_owned_operation
    async def create_project_note(self, note: dict, *, confirmed: bool, idempotency_key: str):
        return await _await_durable(asyncio.to_thread(self.project_notes.create, note,
            confirmed=confirmed, idempotency_key=idempotency_key))

    @_owned_operation
    async def update_project_note(self, note_id: str, note: dict, *, expected_revision: int, confirmed: bool, idempotency_key: str):
        return await _await_durable(asyncio.to_thread(self.project_notes.update, note_id, note,
            expected_revision=expected_revision, confirmed=confirmed, idempotency_key=idempotency_key))

    @_owned_operation
    async def delete_project_note(self, note_id: str, *, expected_revision: int, confirmed: bool, idempotency_key: str):
        return await _await_durable(asyncio.to_thread(self.project_notes.delete, note_id,
            expected_revision=expected_revision, confirmed=confirmed, idempotency_key=idempotency_key))

    @_owned_operation(quarantine_safe=True)
    async def context_selection(self, scope_work_order_id: str | None = None):
        return await _await_durable(asyncio.to_thread(self.context_selections.get, scope_work_order_id))

    @_owned_operation
    async def set_context_selection(self, scope_work_order_id: str | None, manifest_id: str | None, notes: list[dict],
                                    *, expected_revision: int, idempotency_key: str):
        return await _await_durable(asyncio.to_thread(self.context_selections.set, scope_work_order_id, manifest_id, notes,
            expected_revision=expected_revision, idempotency_key=idempotency_key))

    @_owned_operation
    async def activate_work_order(self, identifier: str, *, expected_revision: int, settings: dict):
        if "profiles" in settings:
            raise ValueError("frozen profiles are server-owned")
        record = await _await_durable(asyncio.to_thread(self.work_orders.get, identifier))
        if record is None:
            raise KeyError(identifier)
        from ..tasks.work_orders import plan_from_payload
        existing = record["execution"].get("profiles", {}) if record["status"] != "draft" else {}
        # Replaying a lost activation reply must not silently resolve a newer
        # global default/model. Different explicit options still fail store CAS.
        if existing and settings.get("profile_id") is None:
            settings = {**settings, "profile_id": record["execution"]["profile_id"]}
        options = self._freeze_work_order_scope(settings, plan_from_payload(record["plan"]).tasks, existing=existing)
        return await self.work_order_orchestrator.activate(identifier, expected_revision=expected_revision, settings=options)

    @_owned_operation
    async def revise_work_order(self, identifier: str, plan: WorkOrderPlan, *, expected_revision: int, context: dict | None = None):
        record = await _await_durable(asyncio.to_thread(self.work_orders.get, identifier))
        if record is None:
            raise KeyError(identifier)
        profiles = None
        if record["status"] != "draft":
            profiles = self._freeze_work_order_scope(record["execution"], plan.tasks,
                existing=record["execution"].get("profiles", {}))["profiles"]
        return await self.work_order_orchestrator.revise(identifier, plan, expected_revision=expected_revision, profiles=profiles, context=context)

    @_owned_operation(quarantine_safe=True)
    async def control_work_order(self, identifier: str, action: str, *, expected_revision: int):
        if action not in {'pause', 'cancel'}:
            self._assert_provider_admission()
        return await self.work_order_orchestrator.control(identifier, action, expected_revision=expected_revision)

    @_owned_operation
    async def retry_work_order_task(self, identifier: str, task_id: str, *, expected_revision: int, expected_attempt_id: str):
        return await self.work_order_orchestrator.retry(identifier, task_id, expected_revision=expected_revision,
                                                       expected_attempt_id=expected_attempt_id)

    @_owned_operation(quarantine_safe=True)
    async def get_work_order_plan(self, identifier: str, revision: int):
        return await _await_durable(asyncio.to_thread(self.work_orders.plan, identifier, revision))

    @_owned_operation
    async def skill_catalog(self, *, reload: bool = False) -> dict[str, Any]:
        async with self._skill_lock:
            if self._skill_registry is None or reload:
                candidate = SkillRegistry(self.workspace)
                # Cancellation does not stop a to_thread worker. Keep the
                # original owned operation/close join until validation settles;
                # cancelled candidates must not replace the previous registry.
                await _await_durable(asyncio.to_thread(candidate.catalog))
                # Replace only after the entire candidate passes validation.
                self._skill_registry = candidate
            return {"skills": list(self._skill_registry.catalog()),
                    "warnings": list(self._skill_registry.warnings)}

    @_owned_operation(quarantine_safe=True)
    async def workspace_selection(self) -> dict[str, Any]:
        return await _await_durable(asyncio.to_thread(self.conversations.selection))

    @_owned_operation
    async def save_workspace_selection(self, conversation_id: str | None, run_id: str | None = None) -> dict[str, Any]:
        # HTTP cancellation/desktop shutdown must not release the workspace owner
        # while the preference transaction is still running in a SQLite worker.
        return await _await_durable(asyncio.to_thread(self.conversations.set_selection, conversation_id, run_id))

    @_owned_operation
    async def mcp_discover(self, name: str, *, probe: bool, refresh: bool = False):
        if self.mcp_manager is None or self.mcp_catalog is None:
            raise KeyError(name)
        async with asyncio.timeout(self.mcp_discovery_timeout_seconds):
            if probe:
                return await self.mcp_manager.health(name)
            return await self.mcp_catalog.list_server(name, refresh=refresh)

    async def extension_servers(self) -> list[dict[str, Any]]:
        # Passive projections require the already-held original owner. No start,
        # file/config reload, recovery, transport, provider or scheduler admission.
        async with self._lifecycle_lock:
            self._require_held_metadata_owner()
            return [{"name": server.name, "transport": server.transport,
                     "max_concurrency": server.max_concurrency}
                    for server in self.mcp_config.servers.values()]

    async def extension_cached_tools(self, name: str):
        async with self._lifecycle_lock:
            self._require_held_metadata_owner()
            if name not in self.mcp_config.servers or self.mcp_catalog is None:
                raise KeyError(name)
            return self.mcp_catalog.cached_server(name)

    @bounded_run_retries(fresh=True)
    async def _run_async_subagent(self, request: AsyncSubagentRequest) -> str:
        parent = await _await_durable(self.get(request.parent_run_id))
        if parent is None:
            raise KeyError(request.parent_run_id)
        parent_request = parent["request"]
        async with asyncio.timeout(parent_request.get("deadline_seconds", 600)):
            child_record = {
                "run_id": request.subagent_id,
                "thread_id": request.subagent_id,
                "mode": "graph",
                "profile_snapshot": parent.get("profile_snapshot", {}),
                "request": {
                    "prompt": request.prompt,
                    "mode": "graph",
                    "profile_id": parent_request.get("profile_id"),
                    "effort": parent_request.get("effort", "balanced"),
                    "permissions": {
                        "workspace_write": False,
                        "command_execute": False,
                        "mcp_execute": False,
                        "delegate": False,
                    },
                },
            }
            history: list[Message] = []
            for item in request.history:
                history.extend(
                    (
                        Message("user", item["prompt"]),
                        Message("assistant", item["answer"]),
                    )
                )
            runtime = await self._runtime(child_record)
            result = await runtime.run(
                RunRequest(
                    request.prompt,
                    run_id=request.subagent_id,
                    thread_id=request.subagent_id,
                    history=tuple(history),
                ),
                ChildRuntimeSink(self._sink(parent), request.parent_run_id, request.subagent_id, request.generation),
            )
            if result.status != "completed":
                raise RuntimeError(f"subagent ended with status {result.status}")
            return result.answer

    async def _require_parent_for_subagent(self, parent_run_id: str) -> dict[str, Any]:
        # Scope resolution is already-owned child admission/runner IO. Caller
        # cancellation must not forget its SQLite worker before original close.
        parent = await _await_durable(self.get(parent_run_id))
        if parent is None:
            raise KeyError(parent_run_id)
        if not parent["request"]["permissions"].get("delegate"):
            raise PermissionError("parent run did not grant delegate permission")
        return parent

    @_owned_operation
    async def spawn_subagent(self, parent_run_id: str, prompt: str) -> dict[str, Any]:
        await self._require_parent_for_subagent(parent_run_id)
        return await self.subagents.spawn(parent_run_id, prompt)

    async def list_subagents(self, parent_run_id: str) -> list[dict[str, Any]]:
        await self._require_parent_for_subagent(parent_run_id)
        return await self.subagents.list_for_parent(parent_run_id)

    async def get_subagent(self, parent_run_id: str, subagent_id: str) -> dict[str, Any]:
        await self._require_parent_for_subagent(parent_run_id)
        record = await self.subagents.get(subagent_id)
        if record is None or record["parent_run_id"] != parent_run_id:
            raise KeyError(subagent_id)
        return record

    async def subagent_review(self, parent_run_id: str, *, subagent_id: str | None = None,
                              expected_generation: int | None = None, offset: int = 0, limit: int = 16) -> dict:
        # Reviewed GET is not an owned execution operation: it MUST NOT start,
        # recover, submit or acquire a new owner. Original lifecycle lock and
        # durable worker join keep the existing owner through SQLite reads.
        def read():
            parent = self.runs.get(parent_run_id)
            if parent is None:
                raise KeyError(parent_run_id)
            if not parent["request"]["permissions"].get("delegate"):
                raise PermissionError("parent run did not grant delegate permission")
            if subagent_id is not None:
                if expected_generation is None:
                    raise ValueError("invalid_subagent_generation")
                return self.subagents.store.review_history(
                    parent_run_id, subagent_id, expected_generation=expected_generation, offset=offset, limit=limit,
                )
            value = self.subagents.store.review_snapshot(parent_run_id)
            value["capabilities"] = {"workspace_read": True, "workspace_write": False,
                                     "command_execute": False, "mcp_execute": False, "delegate": False}
            value["child_mode"] = "graph"
            value["profile_inheritance"] = "parent_snapshot" if parent.get("profile_snapshot") else "parent_profile_resolution"
            return value

        value = await self._read_held_evidence(read)
        if subagent_id is None:
            scheduler = self.subagents.scheduler
            value["limits"] = {"lifetime_per_parent": self.subagents.max_per_parent,
                               "global_active": scheduler.max_active, "global_queue": scheduler.queue_capacity}
            # Different observational layers. Terminal SQLite state and memory
            # slot counts are NOT a native transport/process-drain certificate.
            value["scheduler_observation"] = {"scope": "original_scheduler_in_memory",
                                              "active": scheduler.active_count, "queued": scheduler.queued_count}
        value["physical_drain_verified"] = False
        return value

    @_owned_operation
    async def follow_up_subagent(
        self, parent_run_id: str, subagent_id: str, prompt: str, *, expected_generation: int | None = None
    ) -> dict[str, Any]:
        await self.get_subagent(parent_run_id, subagent_id)
        if expected_generation is None:
            return await self.subagents.follow_up(subagent_id, prompt)
        return await self.subagents.follow_up(subagent_id, prompt, expected_generation=expected_generation)

    @_owned_operation(quarantine_safe=True)
    async def cancel_subagent(self, parent_run_id: str, subagent_id: str, *, expected_generation: int | None = None) -> bool:
        await self.get_subagent(parent_run_id, subagent_id)
        if expected_generation is None:
            return await self.subagents.cancel(subagent_id)
        return await self.subagents.cancel(subagent_id, expected_generation=expected_generation)

    def _provider(self, profile_id: str | None, mode: str, snapshot: dict | None = None) -> Any:
        self._assert_provider_admission()  # BEFORE override/settings/key/client lookup.
        if self.provider_override is not None:
            return self.provider_override
        profile = snapshot or self.settings.profile(profile_id)
        if profile["provider"] == "mock":
            return MockProvider()
        if profile["provider"] != "openai":
            raise ValueError("explicit provider override is required to continue this run")
        cache_key = f"{mode}:" + json.dumps({key: profile.get(key) for key in ('id', 'base_url', 'model')}, sort_keys=True)
        if cache_key not in self._providers:
            provider_type = OpenAICompatibleProvider if mode == "legacy" else AsyncOpenAICompatibleProvider
            self._providers[cache_key] = provider_type(
                profile["base_url"], profile["model"],
                self.settings.api_key(profile["id"]) if snapshot else profile["api_key"],
            )
        return self._providers[cache_key]

    def _tools(self, permissions: dict[str, bool]) -> ToolRegistry:
        capabilities = {"workspace_read"}
        if permissions.get("workspace_write"):
            capabilities.add("workspace_write")
        if permissions.get("command_execute"):
            capabilities.add("command_execute")
        if permissions.get("mcp_execute"):
            capabilities.add("mcp_execute")
        registry = ToolRegistry(PermissionManager(frozenset(capabilities)))
        for tool in (
            workspace_map_tool(self.workspace),
            search_text_tool(self.workspace),
            read_file_range_tool(self.workspace),
            read_file_tool(self.workspace),
            list_files_tool(self.workspace),
        ):
            registry.register(tool)
        if permissions.get("workspace_write"):
            # Patch approval never consumes a verification plan or opens its
            # config. Manual prepare/get/decide owns that separate operation.
            registry.register(patch_tool(self.workspace, resource_limits=self.resources,
                                         require_verification_review=True))
        if permissions.get("command_execute"):
            registry.register(run_command_tool(self.workspace, supervisor=self.process_supervisor))
        return registry

    async def _runtime(self, record: dict[str, Any]):
        self._assert_provider_admission()
        self._assert_process_admission()
        request = record["request"]
        mode = record["mode"]
        # Original persisted snapshot ONLY. Validate exact identity before the
        # original provider/key path; historical absence never reads today's
        # settings tariff. Child runtime inherits this same parent snapshot.
        snapshot = record.get("profile_snapshot") or {}
        declared = snapshot.get("billing_price_receipt")
        frozen_price = None if declared is None else validate_price_receipt(declared, profile=snapshot)
        provider = self._provider(request.get("profile_id"), mode, record.get("profile_snapshot"))
        if mode in {"graph", "deep"}:
            provider = ProviderAdapter(
                provider,
                profile_id=record.get("profile_snapshot", {}).get("id") or request.get("profile_id") or "default",
                limits=self.resources,
            )
        options = {
            "max_steps": {"quick": 6, "balanced": 8, "deep": 12}[request.get("effort", "balanced")],
            "allow_write": request["permissions"].get("workspace_write", False),
            "allow_command": request["permissions"].get("command_execute", False),
            "allow_mcp": request["permissions"].get("mcp_execute", False),
            "allow_delegate": request["permissions"].get("delegate", False),
        }
        runtime = create_runtime(
            mode,
            self.workspace,
            provider,
            state_root=self.state_root,
            core_options=options,
            resource_limits=self.resources,
            process_supervisor=self.process_supervisor,
            reviewed_legacy=mode == "legacy",
            require_verification_review=True,
            provider_receipt_fault=self._provider_receipt_fault,
            billing_price_receipt=frozen_price,
        )
        if mode == "graph":
            runtime.tools = self._tools(request["permissions"])
        if request["permissions"].get("mcp_execute") and self.mcp_catalog and self.mcp_manager:
            descriptors = await self.mcp_catalog.list_all()
            permissions = PermissionManager(frozenset({"mcp_execute"}))

            async def audit(payload: dict[str, Any]) -> None:
                await self._sink(record).emit("mcp.tool_executed", **payload)

            executor = MCPToolExecutor(
                self.mcp_manager,
                self.mcp_catalog,
                permissions,
                ledger=self.mcp_ledger if mode == "deep" else None,
                audit=audit,
            )
            if mode == "graph":
                for tool in doppel_mcp_tools(descriptors, executor):
                    runtime.tools.register(tool)
            elif mode == "deep":
                runtime.additional_tools = langchain_mcp_tools(descriptors, executor)
        return runtime

    def _sink(self, record: dict[str, Any]) -> DurableEventSink:
        return DurableEventSink(
            self.events,
            self.notifier,
            record["run_id"],
            record["thread_id"],
        )

    async def _record_cancelled(self, run_id: str, sink: DurableEventSink) -> None:
        await sink.emit("run.cancelled")
        await asyncio.to_thread(self.runs.update, run_id, "cancelled")
        await asyncio.to_thread(self.runs.release_conversation_turn, run_id)

    async def _record_failed(self, run_id: str, sink: DurableEventSink, error: str) -> None:
        await sink.emit("run.failed", error=error)
        await asyncio.to_thread(self.runs.update, run_id, "failed", error=error)

    async def _release_turn(self, run_id: str) -> None:
        # The outcome has already been durably finalized. Once lease release
        # begins, a late cancellation cannot rewrite it after a new turn has
        # acquired the conversation. Drain cleanup but keep that committed outcome.
        try:
            await _await_durable(asyncio.to_thread(self.runs.release_conversation_turn, run_id))
            await _await_durable(self.notifier.notify(run_id))
            self.work_order_orchestrator.wake()
            await self._maintain_checkpoints()
            await self._maintain_private_payloads()
            await self._maintain_verification_metadata()
        except asyncio.CancelledError:
            pass

    async def _maintain_checkpoints(self) -> None:
        try:
            await _await_durable(asyncio.to_thread(self.checkpoint_retention.apply, self.state_root))
        except (sqlite3.Error, OSError) as exc:
            # Maintenance must not rewrite a committed root or child outcome.
            logging.getLogger(__name__).warning("checkpoint retention deferred: %s", type(exc).__name__)

    async def _maintain_private_payloads(self) -> None:
        async def maintain():
            # Root-turn cleanup/start only, NOT the child terminal callback:
            # a child may finish while its parent still owns the write lock.
            async with self._private_retention_lock:
                async with self.workspace_locks.write(self.workspace):
                    owner = getattr(self._owner, "owner", self._owner)
                    if not owner.held:
                        raise RuntimeError("patch_workspace_owner_required")
                    report = await _await_durable(asyncio.to_thread(
                        self.private_patch_retention.apply, self.state_root,
                        cursor=self._private_retention_cursor,
                    ))
                    self._private_retention_cursor = report["next_cursor"]

        try:
            # Cancellation must drain SQLite, lock and cursor before owner exit.
            await _await_durable(maintain())
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
            logging.getLogger(__name__).warning("private payload retention deferred: %s", type(exc).__name__)

    @_owned_operation
    async def create(self, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if str(request.get("idempotency_key") or "").startswith("work-order:"):
            raise ValueError("work-order admission keys are reserved")
        return await self._create_owned(request)

    async def _create_owned(
        self, request: dict[str, Any], *, frozen_profile: dict | None = None, write_scope: bool = False,
        frozen_input_snapshot: dict | None = None,
    ) -> tuple[dict[str, Any], bool]:
        self._assert_provider_admission()  # Includes original internal work-order admission.
        request = normalize_request(request)
        deadline_at = asyncio.get_running_loop().time() + request.get("deadline_seconds", 600)
        replay = await asyncio.to_thread(self.runs.replay, request)
        if replay is not None:
            return replay, False
        run_id = uuid4().hex
        conversation = None
        if request.get("conversation_id") is not None:
            conversation = await asyncio.to_thread(self.conversations.get, request["conversation_id"])
        if frozen_profile is not None:
            snapshot = dict(frozen_profile)
        elif self.provider_override is not None:
            model = getattr(self.provider_override, "model", None)
            snapshot = {"id": request.get("profile_id") or "scripted", "provider": "explicit_override",
                        "model": model if isinstance(model, str) else "scripted",
                        "implementation": type(self.provider_override).__name__}
        else:
            public = self.settings.public()
            wanted = request.get("profile_id") or (conversation or {}).get("profile_id") or public["active_profile_id"]
            profile = next((p for p in public["profiles"] if p["id"] == wanted), None)
            if profile is None:
                raise ValueError("model profile not found")
            snapshot = self._freeze_public_profile(profile)
        acceptance = asyncio.create_task(asyncio.to_thread(
            self.runs.create,
            run_id,
            uuid4().hex,
            request["mode"],
            request,
            request.get("idempotency_key"),
            native=True,
            profile_snapshot=snapshot,
            expected_profile_id=conversation["profile_id"] if conversation else ...,
            write_scope=write_scope,
            input_resolver=ContextInputResolver(self.context_reader).resolve,
            frozen_input_snapshot=frozen_input_snapshot,
        ))
        try:
            record, created = await _await_durable(acceptance)
        except asyncio.CancelledError:
            if not acceptance.cancelled() and acceptance.exception() is None:
                record, created = acceptance.result()
                if created:
                    await _await_durable(self._record_cancelled(record["run_id"], self._sink(record)))
            raise
        if not created:
            return record, False
        thread_id = record["thread_id"]
        sink = self._sink(record)

        @bounded_run_retries(fresh=True)
        async def operation(token) -> RuntimeResult:
            try:
                token.raise_if_cancelled()
                if asyncio.get_running_loop().time() >= deadline_at:
                    raise TimeoutError("run deadline elapsed before execution")
                async with asyncio.timeout_at(deadline_at):
                    token.raise_if_cancelled()
                    current = await self.get(run_id)
                    if current["cancel_requested"]:
                        raise asyncio.CancelledError
                    await _await_durable(asyncio.to_thread(self.runs.update, run_id, "running"))
                    await sink.emit("run.status_changed", previous="queued", status="running")
                    runtime = await self._runtime(record)
                    if record.get("conversation_id"):
                        history = await asyncio.to_thread(self.conversations.history, record["conversation_id"])
                    else:
                        history = []
                    runtime_request = RunRequest(record["request"]["prompt"], run_id=run_id, thread_id=thread_id,
                                                 history=tuple(Message(item["role"], item["content"]) for item in history),
                                                 context_text=(record.get("input_snapshot") or {}).get("rendered_context", ""))
                    lock = (
                        self.workspace_locks.write(self.workspace)
                        if record.get("write_scope") or request["permissions"].get("workspace_write")
                        else self.workspace_locks.read(self.workspace)
                    )
                    async with lock:
                        token.raise_if_cancelled()
                        self._assert_process_admission()
                        result = await runtime.run(runtime_request, sink)
                    await sink.emit("checkpoint.saved", thread_id=thread_id)
                    if result.status == "interrupted":
                        for item in result.metadata.get("interrupts", []):
                            await sink.emit(
                                "approval.requested",
                                interrupt_id=item.get("id", ""),
                                request=item.get("value"),
                            )
                    await sink.emit("run.status_changed", previous="running", status=result.status)
                    await _await_durable(asyncio.to_thread(
                        self.runs.update,
                        run_id,
                        result.status,
                        answer=result.answer,
                        metadata=result.metadata,
                    ))
                    return result
            except asyncio.CancelledError:
                await _await_durable(self._record_cancelled(run_id, sink))
                raise
            except Exception as exc:  # noqa: BLE001 - isolate a failed agent run
                error = f"{type(exc).__name__}: run execution failed"
                await _await_durable(self._record_failed(run_id, sink, error))
                raise
            finally:
                await self._release_turn(run_id)

        try:
            await sink.emit("run.status_changed", previous=None, status="queued")
            handle = await self.scheduler.submit(run_id, operation)
        except asyncio.CancelledError:
            await _await_durable(self._record_cancelled(run_id, sink))
            raise
        except (QueueCapacityError, RuntimeError, ValueError):
            async def reject():
                await sink.emit("run.rejected", reason="queue_full_or_scheduler_unavailable")
                await asyncio.to_thread(
                    self.runs.update,
                    run_id,
                    "failed",
                    error="run was not accepted by the scheduler",
                )
                await self._release_turn(run_id)

            await _await_durable(reject())
            raise
        handle.future.add_done_callback(self._consume_future)
        return record, True

    @staticmethod
    def _consume_future(future: asyncio.Future[Any]) -> None:
        if future.cancelled():
            return
        future.exception()

    async def get(self, run_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.runs.get, run_id)

    async def list_events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.events.list, run_id, after_seq=after_seq)

    def _assert_process_admission(self) -> None:
        if getattr(self.process_supervisor, "cleanup_failed", False):
            raise RuntimeError("verification_process_cleanup_quarantine")

    def _assert_provider_admission(self) -> None:
        # Monotonic original owner latch; readonly reads cannot reset it. This
        # is new IO refusal, not drain of already entered opaque/SDK workers.
        self._provider_receipt_fault.check()

    def _clear_git_metadata_grant(self):
        self._git_metadata_roots = ()
        self._git_metadata_binding = None
        self._git_metadata_grant_id = None

    def _git_metadata_grant_view(self):
        binding = self._git_metadata_binding
        return {"active": binding is not None, "grant_id": self._git_metadata_grant_id,
                "workspace": str(self.workspace), "metadata_root": str(binding.common) if binding else None,
                "scope": "exact_linked_worktree_read_inspection_only", "persistent": False,
                "lifetime": "current_owner_until_revoke_binding_change_switch_or_close",
                "command_or_workspace_write_grant": False}

    async def _check_git_metadata_grant(self):
        binding = self._git_metadata_binding
        if binding is not None:
            try:
                await _await_durable(asyncio.to_thread(check_linked_metadata, binding))
            except GitInspectionError:
                self._clear_git_metadata_grant()
                raise

    @_owned_operation
    async def _grant_git_metadata(self, selected: SelectedMetadataRoot) -> dict:
        # Native owner has already displayed and confirmed THIS selected identity.
        # Writer preference drains existing Git readers before replacing authority;
        # no original command slot or config/objects/executable IO is required.
        async with self.workspace_locks.write(self.workspace):
            self._assert_patch_owner()
            if self._closed or self._close_task is not None:
                raise RuntimeError("changes_service_closed")
            binding = await _await_durable(asyncio.to_thread(capture_linked_metadata, self.workspace, selected))
            self._assert_patch_owner()
            if self._closed or self._close_task is not None:
                raise RuntimeError("changes_service_closed")
            self._git_metadata_binding = binding
            self._git_metadata_roots = (binding.common,)
            self._git_metadata_grant_id = uuid4().hex
            return self._git_metadata_grant_view()

    async def _revoke_git_metadata(self) -> dict:
        # Narrowing authority remains available under quarantine with held owner;
        # no start/recovery/command gate. Lifecycle BEFORE workspace lock, so close
        # cannot hold lifecycle waiting a Git reader blocked by this writer.
        async with self._lifecycle_lock:
            self._require_held_metadata_owner()
            async with self.workspace_locks.write(self.workspace):
                self._clear_git_metadata_grant()
                return self._git_metadata_grant_view()

    @_owned_operation
    async def _get_git_metadata_grant(self) -> dict:
        async with self.workspace_locks.read(self.workspace):
            self._assert_patch_owner()
            if self._closed or self._close_task is not None:
                raise RuntimeError("changes_service_closed")
            await self._check_git_metadata_grant()
            return self._git_metadata_grant_view()

    @_owned_operation
    async def git_status(self) -> dict:
        return await self._read_git_inspection()

    @_owned_operation
    async def git_diff(self, path: str, *, plane: str = "worktree", expected_fingerprint: str,
                       conflict_stage: int | None = None) -> dict:
        return await self._read_git_inspection(path=path, plane=plane, expected_fingerprint=expected_fingerprint,
                                              conflict_stage=conflict_stage)

    async def _read_git_inspection(self, *, path: str | None = None, **selection) -> dict:
        def admitted():
            self._assert_patch_owner()
            if self._closed or self._close_task is not None:
                raise RuntimeError("changes_service_closed")

        admitted()
        # Match manual/runtime workspace-before-command order, never hold every
        # command slot while waiting on a writer which itself needs a slot.
        async with self.workspace_locks.read(self.workspace):
            admitted()
            try:
                await self._check_git_metadata_grant()
            except GitInspectionError:
                return {"available": False, "reason": "git_metadata_authorization_changed", "repository_clean": None,
                        "agent_attribution": "not_inferred_from_repository_changes"}
            admitted()
            async with self.resources.command():
                admitted()
                binding = self._git_metadata_binding
                try:
                    inspector = await _await_durable(asyncio.to_thread(
                        GitInspector, self.workspace, supervisor=self.process_supervisor,
                        authorized_metadata_roots=self._git_metadata_roots,
                        **({"linked_binding": binding} if binding is not None else {})))
                except GitInspectionError as error:
                    if binding is not None:
                        self._clear_git_metadata_grant()
                    return {"available": False, "reason": "git_metadata_authorization_changed" if binding else str(error),
                            "repository_clean": None, "agent_attribution": "not_inferred_from_repository_changes"}
                admitted()
                operation_id = "changes:" + uuid4().hex
                if path is None:
                    value = await inspector.status(operation_id=operation_id)
                else:
                    # No whole-command shield: cancel reaches original supervisor;
                    # GitInspector drains its entered private view/original workers.
                    value = await inspector.diff(path, operation_id=operation_id, **selection)
                if value.get("reason") == "git_metadata_authorization_changed":
                    self._clear_git_metadata_grant()
                return value

    def _assert_patch_owner(self) -> None:
        self._assert_process_admission()
        owner = self._owner.owner if isinstance(self._owner, BorrowedWorkspaceOwner) else self._owner
        if not self._started or not owner.held:
            raise RuntimeError("patch_workspace_owner_unavailable")

    async def _patch_ledger_service(self) -> ToolExecutionLedger:
        self._assert_patch_owner()
        async with self._patch_service_lock:
            if self._patch_ledger is None:
                self._patch_ledger = await _await_durable(asyncio.to_thread(
                    ToolExecutionLedger, self.state_root / "tool-executions.sqlite3",
                    failure=self._provider_receipt_fault.mark_failed,
                    cleanup_failure=self._provider_receipt_fault.retain_cleanup,
                ))
            return self._patch_ledger

    async def _patch_services(self) -> tuple[ToolExecutionLedger, InverseReviewStore]:
        ledger = await self._patch_ledger_service()
        async with self._patch_service_lock:
            if self._inverse_reviews is None:
                self._inverse_reviews = await _await_durable(asyncio.to_thread(
                    InverseReviewStore, ledger.database, ttl_seconds=self.approval_ttl_seconds,
                ))
            return ledger, self._inverse_reviews

    async def _patch_source_record(self, run_id: str, *, quiescent: bool = False) -> dict[str, Any]:
        inverse_identifier(run_id, uuid=True)
        self._assert_patch_owner()
        record = await _await_durable(asyncio.to_thread(self.runs.get, run_id))
        if record is None:
            raise KeyError(run_id)
        if quiescent and (record["status"] not in {"completed", "failed", "cancelled", "interrupted_expired"}
                          or record["lease_active"] or self.scheduler.status(run_id) in {"queued", "running"}):
            raise ValueError("inverse_source_run_not_quiescent")
        return record

    async def _verification_services(self):
        ledger = await self._patch_ledger_service()
        async with self._patch_service_lock:
            if self._verification_reviews is None:
                self._verification_reviews = await _await_durable(asyncio.to_thread(
                    VerificationReviewStore, ledger.database, ttl_seconds=self.approval_ttl_seconds,
                ))
            return ledger, self._verification_reviews

    async def _maintain_verification_metadata(self, *, recover_running=False):
        async def maintain():
            async with self._verification_maintenance_lock:
                async with self.workspace_locks.write(self.workspace):
                    if self._closed or getattr(self.process_supervisor, "cleanup_failed", False):
                        return  # Admission may have changed while this page waited.
                    owner = getattr(self._owner, "owner", self._owner)
                    if not owner.held:
                        raise RuntimeError("verification_workspace_owner_required")
                    report = await _await_durable(asyncio.to_thread(
                        self._verification_maintenance.apply, self.state_root / "tool-executions.sqlite3",
                        after_id=self._verification_maintenance_cursor, recover_running=recover_running,
                        runtime_database=self.runs.database,
                    ))
                    self._verification_maintenance_cursor = report["next_after_id"]

        try:
            await _await_durable(maintain())
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
            # Keep original run/patch/manual outcome. Never log raw DB/output.
            logging.getLogger(__name__).warning("verification metadata maintenance deferred: %s", type(exc).__name__)

    async def _manual_verification_terminal_maintenance(self):
        # Called only after manual write locks/tasks/cancel fences settle. No
        # mutation during failed close/quarantine; diagnostic queries stay pure.
        if (self._closed or not self._started or self._close_task is not None
                or getattr(self.process_supervisor, "cleanup_failed", False)):
            return
        await self._maintain_verification_metadata()

    @staticmethod
    def _verification_grants(command_execute, workspace_write):
        # Configured scripts/interpreters may themselves write files. Original
        # run grants, a file allowlist or shell=False are not a new permission.
        if command_execute is not True or workspace_write is not True:
            raise PermissionError("verification_fresh_command_and_write_grants_required")

    async def get_verification(self, run_id: str, review_id: str) -> dict:
        """SQL-read-only existing detail, including held-owner quarantine diagnostics."""
        inverse_identifier(run_id, uuid=True)
        inverse_identifier(review_id, uuid=True)
        return await self._read_verification_evidence(run_id, review_id=review_id)

    async def list_verifications(self, run_id: str, *, limit: int = MAX_QUERY_ROWS, after_id: str = "",
                                 tool_call_id: str | None = None, patch_id: str | None = None) -> dict:
        """Bounded source-run keyset summaries, no argv/command output."""
        return await self._read_verification_evidence(run_id, limit=limit, after_id=after_id,
                                                     tool_call_id=tool_call_id, patch_id=patch_id)

    async def _read_verification_evidence(self, run_id: str, *, review_id: str | None = None, **query) -> dict:
        reader = VerificationQueries(self.state_root / "tool-executions.sqlite3", self.runs.database)
        if review_id is not None:
            return await self._read_held_evidence(reader.detail, run_id, review_id)
        return await self._read_held_evidence(reader.list, run_id, **query)

    async def _read_held_evidence(self, operation, *args, **kwargs) -> dict:
        # NOT _owned_operation: even normal reads never start/recover resources.
        # Holding the existing lifecycle lock pins owner through the original
        # SQLite worker drain; close cannot pass its operation join/release. No
        # workspace write lock, process admission bypass or new executor exists.
        async with self._lifecycle_lock:
            failed_close = self._require_held_metadata_owner()
            value = await _await_durable(asyncio.to_thread(operation, *args, **kwargs))
            value["service"] = {
                "read_only": True, "owner_held": True, "failed_close_diagnostic": failed_close,
                "execution_admission": ("quarantined" if self._provider_receipt_fault.broken
                                        or getattr(self.process_supervisor, "cleanup_failed", False)
                                        else "closed_failed" if failed_close else "not_checked_by_read"),
            }
            return value

    def _require_held_metadata_owner(self):
        # Called under original lifecycle lock, never acquire/recover an owner.
        owner = getattr(self._owner, "owner", self._owner)
        close = self._close_task
        failed_close = (close is not None and close.done() and not close.cancelled()
                        and close.exception() is not None)
        if (not owner.held or not (self._started or self._metadata_ready) or self.cleanup_complete
                or self._closed and not failed_close or close is not None and not failed_close):
            raise RuntimeError("verification_query_owner_unavailable")
        return failed_close

    @_owned_operation
    async def prepare_verification(self, run_id: str, tool_call_id: str, patch_id: str, *,
                                   operation_id: str, names: list[str] | None = None,
                                   command_execute: bool = False, workspace_write: bool = False) -> dict:
        self._verification_grants(command_execute, workspace_write)
        inverse_identifier(operation_id, uuid=True)
        inverse_identifier(tool_call_id)
        inverse_identifier(patch_id, uuid=True)

        async def prepare():
            async with self.workspace_locks.write(self.workspace):
                record = await self._patch_source_record(run_id, quiescent=True)
                ledger, store = await self._verification_services()

                def worker():
                    replay = store.prepare_replay(run_id, tool_call_id, patch_id, operation_id, names)
                    if replay is not None:
                        return replay
                    source = ledger.read_patch_evidence(run_id, tool_call_id)
                    if (source is None or not source["confirmed_applied"]
                            or source["receipt"]["patch_id"] != patch_id):
                        raise ValueError("verification_source_applied_receipt_unavailable")
                    pipeline = VerificationPipeline(self.workspace, supervisor=self.process_supervisor)
                    return store.save(run_id, tool_call_id, patch_id, operation_id, names, pipeline.prepare(names).as_dict())

                view = await _await_durable(asyncio.to_thread(worker))
                await _await_durable(self._sink(record).emit("verification.review.prepared",
                    review_id=operation_id, plan_id=view["plan"]["plan_id"], source_tool_call_id=tool_call_id,
                    source_patch_id=patch_id, status=view["status"], operation_kind="manual_verification"))
                return view

        return await _await_durable(prepare())

    @_owned_operation
    async def decide_verification(self, run_id: str, review_id: str, plan_id: str, *, action: str,
                                  command_execute: bool = False, workspace_write: bool = False) -> dict:
        if action == "approve":
            self._verification_grants(command_execute, workspace_write)
        inverse_identifier(run_id, uuid=True)
        inverse_identifier(review_id, uuid=True)
        if action not in {"approve", "reject"}:
            raise ValueError("verification_decision_must_be_approve_or_reject")
        key = (run_id, review_id)
        if key in self._verification_cancels:
            raise RuntimeError("verification_cancel_in_progress")
        if key in self._verification_tasks:
            raise RuntimeError("verification_operation_active")
        if len(self._verification_tasks) >= MAX_MANUAL_VERIFICATIONS:
            raise RuntimeError("verification_manual_operation_capacity")
        task = asyncio.current_task()
        self._verification_tasks[key] = task
        try:
            return await self._decide_verification_owned(run_id, review_id, plan_id, action=action)
        finally:
            if self._verification_tasks.get(key) is task:
                self._verification_tasks.pop(key, None)
            await self._manual_verification_terminal_maintenance()

    async def _decide_verification_owned(self, run_id: str, review_id: str, plan_id: str, *, action: str) -> dict:
        async with self.workspace_locks.write(self.workspace):
            record = await self._patch_source_record(run_id, quiescent=True)
            ledger, store = await self._verification_services()

            async def outcome(status, code=""):
                # Claim/step/final DB IO must drain even if caller cancellation
                # arrived during those IO awaits; a consumed unknown never reruns.
                current = await _await_durable(asyncio.to_thread(store.get, run_id, review_id))
                if current["status"] == "running":
                    current = await _await_durable(asyncio.to_thread(store.finish, run_id, review_id, status, error_code=code))
                await _await_durable(self._sink(record).emit("verification.outcome",
                    review_id=review_id, operation_kind="manual_verification", status=current["status"],
                    success=current["success"], has_unknown_command=current["has_unknown_command"],
                    source_tool_call_id=current["source"]["tool_call_id"], source_patch_id=current["source"]["patch_id"]))
                return current

            prior = await _await_durable(asyncio.to_thread(store.get, run_id, review_id))
            if prior["plan"]["plan_id"] != plan_id:
                raise ValueError("verification_review_scope_unavailable")
            if action not in {"approve", "reject"}:
                raise ValueError("verification_decision_must_be_approve_or_reject")
            try:
                view, selected = await _await_durable(asyncio.to_thread(store.claim, run_id, review_id, plan_id, action))
            except asyncio.CancelledError:
                # Only this attempt's fresh pending approve may have consumed
                # admission. A refused repeat/foreign decision must not rewrite
                # an older unknown operation's state.
                if prior["status"] == "pending" and action == "approve":
                    await _await_durable(outcome("cancelled", "verification_cancelled"))
                raise
            if selected is None:
                return view  # Stored final decision only, never launch again.

            try:
                def prepared_pipeline():
                    source = ledger.read_patch_evidence(run_id, view["source"]["tool_call_id"])
                    if (source is None or not source["confirmed_applied"]
                            or source["receipt"]["patch_id"] != view["source"]["patch_id"]):
                        raise ValueError("verification_source_applied_receipt_unavailable")
                    pipeline = VerificationPipeline(self.workspace, supervisor=self.process_supervisor)
                    plan = pipeline.prepare([command["name"] for command in selected["commands"]])
                    if plan.as_dict() != selected:
                        raise ValueError("verification_review_stale")
                    return pipeline, plan

                pipeline, plan = await _await_durable(asyncio.to_thread(prepared_pipeline))
                index = 0

                async def start(command):
                    self._assert_patch_owner()
                    await _await_durable(asyncio.to_thread(store.start_step, run_id, review_id, index, command.as_dict()))

                async def seal(result):
                    nonlocal index
                    await _await_durable(asyncio.to_thread(store.seal_step, run_id, review_id, index, result.as_dict()))
                    index += 1

                # Do NOT shield command execution: cancellation must reach the
                # existing supervisor, whose wait worker drains before lock exit.
                # Include resource waiting in the reviewed operation deadline.
                async with asyncio.timeout(selected["operation_timeout_seconds"]):
                    async with self.resources.command():
                        await pipeline.run_reviewed(plan, run_id=view["operation_id"], command_grant=True,
                                                    on_start=start, on_result=seal)
                return await outcome("completed")
            except asyncio.CancelledError:
                await _await_durable(outcome("cancelled", "verification_cancelled"))
                raise
            except TimeoutError:
                return await outcome("failed", "verification_deadline_exceeded")
            except Exception as exc:
                # A raw process/provider/DB message may contain private output.
                # Do not overwrite patch receipt/result or original run outcome.
                if isinstance(exc, ValueError) and str(exc).startswith(("verification_review_stale", "verification_config_")):
                    return await outcome("failed", "verification_review_stale")
                if isinstance(exc, ValueError) and str(exc) == "verification_evidence_budget":
                    await _await_durable(outcome("indeterminate", "verification_evidence_budget"))
                    raise  # Actual attempt ran, but its result could not be sealed.
                await _await_durable(outcome("indeterminate", "verification_outcome_indeterminate"))
                raise

    @_owned_operation
    async def reconcile_verification(self, run_id: str, review_id: str) -> dict:
        """Scoped metadata only: exclude live decision, never inspect/run config."""
        inverse_identifier(run_id, uuid=True)
        inverse_identifier(review_id, uuid=True)
        key = (run_id, review_id)
        if key in self._verification_tasks or key in self._verification_cancels:
            raise RuntimeError("verification_operation_active")

        async def reconcile():
            async with self.workspace_locks.write(self.workspace):
                if key in self._verification_tasks or key in self._verification_cancels:
                    raise RuntimeError("verification_operation_active")
                record = await self._patch_source_record(run_id, quiescent=True)
                _, store = await self._verification_services()
                view = await _await_durable(asyncio.to_thread(store.reconcile, run_id, review_id))
                await _await_durable(self._sink(record).emit("verification.reconciled",
                    review_id=review_id, operation_kind="manual_verification", status=view["status"],
                    success=view["success"], has_unknown_command=view["has_unknown_command"],
                    source_tool_call_id=view["source"]["tool_call_id"], source_patch_id=view["source"]["patch_id"]))
                return view

        try:
            return await _await_durable(reconcile())
        finally:
            await self._manual_verification_terminal_maintenance()

    @_owned_operation(quarantine_safe=True)
    async def cancel_verification(self, run_id: str, review_id: str, plan_id: str) -> dict:
        """Cancel exact pending/live manual operation; absent handle is unknown."""
        record = await self._patch_source_record(run_id)
        _, store = await self._verification_services()
        prior = await _await_durable(asyncio.to_thread(store.get, run_id, review_id))
        if prior["plan"]["plan_id"] != plan_id:
            raise ValueError("verification_review_scope_unavailable")
        key = (run_id, review_id)
        # No await between fencing and task lookup. Repeated control requests
        # retain the fence until every drainer has settled, not just the first.
        self._verification_cancels[key] = self._verification_cancels.get(key, 0) + 1

        async def cancel():
            task = self._verification_tasks.get(key)
            if task is not None and not task.done():
                task.cancel()
                await _await_durable(asyncio.gather(task, return_exceptions=True))
            async with self.workspace_locks.write(self.workspace):
                await self._patch_source_record(run_id, quiescent=True)

                def worker():
                    view = store.cancel_pending(run_id, review_id, plan_id)
                    if view["status"] in {"running", "indeterminate"}:
                        return store.reconcile(run_id, review_id)
                    return view

                view = await _await_durable(asyncio.to_thread(worker))
                await _await_durable(self._sink(record).emit("verification.cancel_settled",
                    review_id=review_id, operation_kind="manual_verification", status=view["status"],
                    success=view["success"], has_unknown_command=view["has_unknown_command"],
                    source_tool_call_id=view["source"]["tool_call_id"], source_patch_id=view["source"]["patch_id"]))
                return view

        try:
            # The requested cancellation, evidence and fence must settle even
            # when the control HTTP caller disconnects or cancels repeatedly.
            return await _await_durable(cancel())
        finally:
            remaining = self._verification_cancels[key] - 1
            if remaining:
                self._verification_cancels[key] = remaining
            else:
                self._verification_cancels.pop(key, None)
            await self._manual_verification_terminal_maintenance()

    def _retain_report_cleanup(self, source) -> None:
        self._unresolved_report_reads[id(source)] = source
        self._provider_receipt_fault.retain_cleanup(source)

    def _check_report_cleanup(self) -> None:
        if self._unresolved_report_reads:
            raise RuntimeError('report_read_cleanup_unresolved')

    async def run_report(self, run_id: str) -> dict:
        # Original held owner/lifecycle pins the entire bounded read worker.
        # Existing ledger metadata only; no start/recovery/new stores/provider/
        # current config/project filesystem/Git/commands or effect reconciliation.
        # Paired full v4 numeric/cost/evidence contract; original stored profile
        # source ONLY, same held worker. Source activation is not native acceptance.
        self._check_report_cleanup()
        reader = RunReportQueries(self.runs.database, self.state_root / "tool-executions.sqlite3",
            failure=self._provider_receipt_fault.mark_failed, cleanup_failure=self._retain_report_cleanup)

        def original_read():
            # Recheck AFTER lifecycle-lock acquisition, in the same held worker:
            # an already queued report cannot bypass a prior reader's close fault.
            self._check_report_cleanup()
            return reader.read_full(run_id, provider_usage=True, billing=True)

        return await self._read_held_evidence(original_read)

    async def list_patch_effects(self, run_id: str, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        reader = PatchEvidenceQueries(self.state_root / "tool-executions.sqlite3", self.runs.database)
        value = await self._read_held_evidence(reader.list, run_id, limit=limit, offset=offset)
        record, rows = value["source_run"], value["items"]
        for row in rows:
            row["source_run_quiescent"] = (record["status"] in {"completed", "failed", "cancelled", "interrupted_expired"}
                and not record["lease_active"] and self.scheduler.status(run_id) not in {"queued", "running"}
                and value["service"]["execution_admission"] == "not_checked_by_read")
            row["inverse_preparation_requires_fresh_file_check"] = True
        return rows

    async def get_patch_evidence(self, run_id: str, tool_call_id: str) -> dict:
        reader = PatchEvidenceQueries(self.state_root / "tool-executions.sqlite3", self.runs.database)
        return await self._read_held_evidence(reader.detail, run_id, tool_call_id)

    async def get_inverse_patch(self, run_id: str, review_id: str) -> dict[str, Any]:
        reader = InverseEvidenceQueries(self.state_root / "tool-executions.sqlite3", self.runs.database)
        return await self._read_held_evidence(reader.detail, run_id, review_id)

    @_owned_operation
    async def reconcile_inverse_patch(self, run_id: str, review_id: str) -> dict[str, Any]:
        inverse_identifier(review_id, uuid=True)

        async def reconcile():
            # Serialize against actual manual/runtime effects, but never read
            # or mutate workspace files, launch a command or repeat an effect.
            async with self.workspace_locks.write(self.workspace):
                await self._patch_source_record(run_id, quiescent=True)
                ledger, reviews = await self._patch_services()

                def worker():
                    view = reviews.get(run_id, review_id)
                    if view["status"] not in {"applying", "indeterminate"}:
                        return view
                    evidence = ledger.read_patch_evidence(run_id, view["effect_tool_call_id"])
                    return reviews.reconcile(run_id, review_id, evidence)

                return await _await_durable(asyncio.to_thread(worker))

        return await _await_durable(reconcile())

    @_owned_operation
    async def prepare_inverse_patch(self, run_id: str, tool_call_id: str, patch_id: str, *,
                                    operation_id: str, workspace_write: bool) -> dict[str, Any]:
        if workspace_write is not True:
            raise PermissionError("inverse_write_grant_required")
        inverse_identifier(tool_call_id)
        inverse_identifier(patch_id, uuid=True)
        inverse_identifier(operation_id, uuid=True)

        async def prepare():
            async with self.workspace_locks.write(self.workspace):
                record = await self._patch_source_record(run_id, quiescent=True)
                ledger, reviews = await self._patch_services()

                def worker():
                    previous = reviews.prepare_replay(run_id, tool_call_id, patch_id, operation_id)
                    if previous is not None:
                        return previous, False
                    receipt = ledger.read_applied_patch(run_id, tool_call_id)
                    if receipt.patch_id != patch_id:
                        raise ValueError("inverse_review_scope_unavailable")
                    proposal = PatchService(self.workspace).prepare_inverse(receipt)
                    return reviews.save(run_id, tool_call_id, patch_id, operation_id, proposal), True

                review, created = await _await_durable(asyncio.to_thread(worker))
                if created:
                    await self._sink(record).emit("patch.inverse.prepared", review_id=operation_id, patch_id=review["patch_id"],
                                                  source_tool_call_id=tool_call_id, source_patch_id=patch_id, expires_at=review["expires_at"])
                return review

        # A lost HTTP reply is an unknown prepare outcome, retried only using
        # the original operation ID. No refresh of review/base/TTL on replay.
        return await _await_durable(prepare())

    @_owned_operation
    async def decide_inverse_patch(self, run_id: str, review_id: str, patch_id: str, *,
                                   action: str, workspace_write: bool) -> dict[str, Any]:
        if action not in {"approve", "reject"}:
            raise ValueError("inverse_decision_must_be_approve_or_reject")
        if type(workspace_write) is not bool or action == "approve" and not workspace_write:
            raise PermissionError("inverse_write_grant_required")
        inverse_identifier(review_id, uuid=True)
        inverse_identifier(patch_id, uuid=True)
        caller = asyncio.current_task()

        async def decide():
            async with self.workspace_locks.write(self.workspace):
                record = await self._patch_source_record(run_id, quiescent=True)
                ledger, reviews = await self._patch_services()

                def worker():
                    review, proposal = reviews.claim(run_id, review_id, patch_id, action)
                    if proposal is None:
                        return review
                    effect_id = reviews.effect_id(review_id)
                    try:
                        # The exact copied inverse must still be bound to the
                        # actually sealed private source, not only a UI hash.
                        source = review["source"]
                        receipt = ledger.read_applied_patch(run_id, source["tool_call_id"])
                        if receipt.patch_id != source["patch_id"] or len(receipt.files) != len(proposal.changes):
                            raise ValueError("inverse_review_scope_unavailable")
                        for file, change in zip(receipt.files, proposal.changes, strict=True):
                            if (change.path != file.path or change.content != file.before_content
                                    or change.base_hash != file.after_hash or change.base_mode != file.after_mode
                                    or change.target_mode != file.base_mode):
                                raise ValueError("inverse_review_scope_unavailable")
                        output, replayed = ledger.execute_patch_once(run_id, effect_id, proposal.as_dict(),
                                                                    PatchService(self.workspace), proposal, tool_name="inverse_patch")
                        if replayed:
                            # No new manual decision may silently revive a
                            # previously reserved operation from another state.
                            raise RuntimeError("inverse_reserved_effect_already_exists")
                    except Exception:
                        status = ledger.execution_status(run_id, effect_id)
                        reviews.finish(run_id, review_id, failed=status in {None, "failed"},
                                       error_code="patch_operation_failed" if status in {None, "failed"} else "patch_outcome_indeterminate")
                        raise
                    result = json.loads(output)
                    result["receipt_source"] = {"run_id": run_id, "tool_call_id": effect_id, "origin": "manual_inverse",
                                                "durability": "sealed_tool_ledger", "source_tool_call_id": source["tool_call_id"],
                                                "source_patch_id": source["patch_id"]}
                    # Review finalization is after the actual patch ledger seal.
                    # Failure here leaves applying/unknown, never safe to replay.
                    return reviews.finish(run_id, review_id, result=result)

                outcome = await _await_durable(asyncio.to_thread(worker))
                if not outcome["decision_replayed"]:
                    if outcome["status"] == "applied":
                        await self._sink(record).emit("patch.inverse.applied", review_id=review_id,
                                                      tool_call_id=outcome["effect_tool_call_id"], result=outcome["result"],
                                                      cancel_requested=bool(caller and caller.cancelling()))
                    elif outcome["status"] == "rejected":
                        await self._sink(record).emit("patch.inverse.rejected", review_id=review_id, patch_id=patch_id)
                return outcome

        # Drain claim + actual filesystem worker + seals/events before the
        # shared workspace lock/Local Mode owner can leave, even after cancel.
        return await _await_durable(decide())

    @_owned_operation(quarantine_safe=True)
    async def cancel(self, run_id: str) -> bool:
        # Once a cancellation mutation is admitted, drain its entire durable
        # decision/dispatch even if the HTTP caller disconnects repeatedly.
        return await _await_durable(self._cancel_owned(run_id))

    async def _cancel_owned(self, run_id: str) -> bool:
        record = await self.get(run_id)
        if record is None:
            return False
        if record["status"] in {"completed", "failed", "cancelled", "interrupted_expired"}:
            return False
        await asyncio.to_thread(self.runs.request_cancel, run_id)
        previous_scheduler_status = self.scheduler.status(run_id)
        cancelled = await self.scheduler.cancel(run_id)
        pending_dispatch = record["status"] == "queued" and previous_scheduler_status not in {"queued", "running"}
        cancelled = cancelled or pending_dispatch
        interrupted = record["status"] == "interrupted"
        cancelled = cancelled or interrupted
        await self.process_supervisor.cancel_run(run_id)
        if cancelled:
            sink = self._sink(record)
            await sink.emit("run.cancel_requested")
            if previous_scheduler_status == "queued" or interrupted:
                await sink.emit("run.cancelled")
                await asyncio.to_thread(self.runs.update, run_id, "cancelled")
                await self._release_turn(run_id)
        return cancelled

    @_owned_operation
    async def resume(self, run_id: str, interrupt_id: str, value: dict[str, Any]) -> dict[str, Any]:
        accepted_at = asyncio.get_running_loop().time()
        record = await self.get(run_id)
        if record is None:
            raise KeyError(run_id)
        deadline_at = accepted_at + record["request"].get("deadline_seconds", 600)
        if record["status"] != "interrupted":
            raise ValueError("run is not waiting for an interrupt")
        known = {item.get("id") for item in record["metadata"].get("interrupts", [])}
        if interrupt_id not in known:
            raise ValueError("interrupt id does not match the pending approval")
        # Keep one transaction-owned future so cancellation can distinguish our
        # successful claim from a competing request that won instead.
        claim = asyncio.create_task(asyncio.to_thread(
            self.runs.claim_interrupt, run_id, interrupt_id, value, ttl_seconds=self.approval_ttl_seconds,
        ))
        try:
            record = await _await_durable(claim)
        except asyncio.CancelledError:
            if not claim.cancelled() and claim.exception() is None:
                await _await_durable(self._record_cancelled(run_id, self._sink(claim.result())))
            raise
        except TimeoutError:
            await self.notifier.notify(run_id)
            raise
        ownership = {"scheduled": False}
        try:
            await self.notifier.notify(run_id)
            return await self._resume_claimed(record, value, ownership, deadline_at)
        except asyncio.CancelledError:
            if not ownership["scheduled"]:
                await _await_durable(self._record_cancelled(run_id, self._sink(record)))
            raise
        except Exception as exc:
            if not ownership["scheduled"]:
                # A committed decision is consumed, never put back for blind
                # replay after scheduling failure. Preserve a terminal failure.
                error = f"{type(exc).__name__}: approval dispatch failed"
                await _await_durable(self._sink(record).emit("run.failed", error=error))
                await _await_durable(asyncio.to_thread(self.runs.update, run_id, "failed", error=error))
                await self._release_turn(run_id)
            raise

    async def _resume_claimed(self, record: dict[str, Any], value: dict[str, Any], ownership: dict[str, bool], deadline_at: float) -> dict[str, Any]:
        run_id = record["run_id"]
        sink = self._sink(record)
        if record["mode"] == "legacy":
            value = {**value, "_legacy_interrupt_id": record["metadata"]["interrupts"][0]["id"]}
        if record["mode"] == "deep":
            value = {
                **value,
                "_prepared_interrupts": [
                    item.get("value") for item in record["metadata"].get("interrupts", [])
                ],
            }

        if self.scheduler.status(run_id) in {"queued", "running"}:
            await self.scheduler.wait(run_id)

        @bounded_run_retries(fresh=True)
        async def operation(token) -> RuntimeResult:
            try:
                token.raise_if_cancelled()
                if asyncio.get_running_loop().time() >= deadline_at:
                    raise TimeoutError("run deadline elapsed before execution")
                async with asyncio.timeout_at(deadline_at):
                    token.raise_if_cancelled()
                    current = await self.get(run_id)
                    if current["cancel_requested"]:
                        raise asyncio.CancelledError
                    await _await_durable(asyncio.to_thread(self.runs.update, run_id, "running"))
                    runtime = await self._runtime(record)
                    lock = (
                        self.workspace_locks.write(self.workspace)
                        if record.get("write_scope") or record["request"]["permissions"].get("workspace_write")
                        else self.workspace_locks.read(self.workspace)
                    )
                    async with lock:
                        token.raise_if_cancelled()
                        self._assert_process_admission()
                        result = await runtime.resume(ResumeCommand(run_id, record["thread_id"], value), sink)
                    await sink.emit("checkpoint.saved", thread_id=record["thread_id"])
                    if result.status == "interrupted":
                        for item in result.metadata.get("interrupts", []):
                            await sink.emit("approval.requested", interrupt_id=item.get("id", ""), request=item.get("value"))
                    await sink.emit("run.status_changed", previous="running", status=result.status)
                    await _await_durable(asyncio.to_thread(
                        self.runs.update,
                        run_id,
                        result.status,
                        answer=result.answer,
                        metadata=result.metadata,
                    ))
                    return result
            except asyncio.CancelledError:
                await _await_durable(self._record_cancelled(run_id, sink))
                raise
            except Exception as exc:  # noqa: BLE001 - isolate a failed agent run
                error = f"{type(exc).__name__}: run execution failed"
                await _await_durable(self._record_failed(run_id, sink, error))
                raise
            finally:
                await self._release_turn(run_id)

        handle = await self.scheduler.submit(run_id, operation)
        ownership["scheduled"] = True
        handle.future.add_done_callback(self._consume_future)
        return await self.get(run_id)
