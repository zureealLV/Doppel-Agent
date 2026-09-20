"""Application service joining persistence, runtimes, events and scheduling."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..concurrency import (
    AsyncRunScheduler,
    QueueCapacityError,
    ResourceLimits,
    WorkspaceLockManager,
)
from ..permissions import PermissionManager
from ..persistence import EventStore, RuntimeRunStore
from ..provider import (
    AsyncOpenAICompatibleProvider,
    MockProvider,
    OpenAICompatibleProvider,
)
from ..settings import SettingsStore
from ..tools import (
    ToolRegistry,
    list_files_tool,
    read_file_range_tool,
    read_file_tool,
    run_command_tool,
    search_text_tool,
    workspace_map_tool,
    write_file_tool,
)
from .base import EventSink, ResumeCommand, RunRequest, RuntimeResult
from .factory import create_runtime
from .provider_adapter import ProviderAdapter


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
        await asyncio.to_thread(self.store.append, self.run_id, self.thread_id, kind, payload)
        await self.notifier.notify(self.run_id)


class RunService:
    def __init__(
        self,
        workspace: Path,
        *,
        provider: Any | None = None,
        max_active_runs: int = 4,
        queue_capacity: int = 100,
        approval_ttl_seconds: int = 900,
    ):
        self.workspace = workspace.resolve(strict=True)
        self.state_root = self.workspace / ".doppel-agent"
        self.state_root.mkdir(parents=True, exist_ok=True)
        database = self.state_root / "runtime.sqlite3"
        self.runs = RuntimeRunStore(database)
        self.events = EventStore(database)
        self.settings = SettingsStore(self.state_root / "provider-settings.json")
        self.scheduler = AsyncRunScheduler(max_active=max_active_runs, queue_capacity=queue_capacity)
        self.resources = ResourceLimits()
        self.workspace_locks = WorkspaceLockManager()
        self.notifier = EventNotifier()
        self.provider_override = provider
        self.approval_ttl_seconds = approval_ttl_seconds
        self._providers: dict[str, Any] = {}

    async def start(self) -> None:
        await self.scheduler.start()

    async def close(self) -> None:
        await self.scheduler.shutdown()
        for provider in self._providers.values():
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()
        self._providers.clear()

    def _provider(self, profile_id: str | None, mode: str) -> Any:
        if self.provider_override is not None:
            return self.provider_override
        profile = self.settings.profile(profile_id)
        if profile["provider"] == "mock":
            return MockProvider()
        cache_key = f"{mode}:{profile['id']}"
        if cache_key not in self._providers:
            provider_type = OpenAICompatibleProvider if mode == "legacy" else AsyncOpenAICompatibleProvider
            self._providers[cache_key] = provider_type(
                profile["base_url"], profile["model"], profile["api_key"]
            )
        return self._providers[cache_key]

    def _tools(self, permissions: dict[str, bool]) -> ToolRegistry:
        capabilities = {"workspace_read"}
        if permissions.get("workspace_write"):
            capabilities.add("workspace_write")
        if permissions.get("command_execute"):
            capabilities.add("command_execute")
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
            registry.register(write_file_tool(self.workspace))
        if permissions.get("command_execute"):
            registry.register(run_command_tool(self.workspace))
        return registry

    def _runtime(self, record: dict[str, Any]):
        request = record["request"]
        mode = record["mode"]
        provider = self._provider(request.get("profile_id"), mode)
        if mode == "graph":
            provider = ProviderAdapter(
                provider,
                profile_id=request.get("profile_id") or "default",
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
        )
        if mode == "graph":
            runtime.tools = self._tools(request["permissions"])
        return runtime

    def _sink(self, record: dict[str, Any]) -> DurableEventSink:
        return DurableEventSink(
            self.events,
            self.notifier,
            record["run_id"],
            record["thread_id"],
        )

    async def create(self, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        run_id = uuid4().hex
        thread_id = request.get("conversation_id") or uuid4().hex
        record, created = await asyncio.to_thread(
            self.runs.create,
            run_id,
            thread_id,
            request["mode"],
            request,
            request.get("idempotency_key"),
        )
        if not created:
            return record, False
        sink = self._sink(record)
        await sink.emit("run.status_changed", previous=None, status="queued")

        async def operation(token) -> RuntimeResult:
            token.raise_if_cancelled()
            await asyncio.to_thread(self.runs.update, run_id, "running")
            await sink.emit("run.status_changed", previous="queued", status="running")
            runtime = self._runtime(record)
            runtime_request = RunRequest(request["prompt"], run_id=run_id, thread_id=thread_id)
            lock = (
                self.workspace_locks.write(self.workspace)
                if request["permissions"].get("workspace_write")
                else self.workspace_locks.read(self.workspace)
            )
            try:
                async with lock:
                    token.raise_if_cancelled()
                    async with asyncio.timeout(request.get("deadline_seconds", 600)):
                        result = await runtime.run(runtime_request, sink)
            except asyncio.CancelledError:
                await sink.emit("run.cancelled")
                await asyncio.to_thread(self.runs.update, run_id, "cancelled")
                raise
            except Exception as exc:  # noqa: BLE001 - isolate a failed agent run
                error = f"{type(exc).__name__}: {exc}"
                await sink.emit("run.failed", error=error)
                await asyncio.to_thread(self.runs.update, run_id, "failed", error=error)
                raise
            await sink.emit("checkpoint.saved", thread_id=thread_id)
            if result.status == "interrupted":
                for item in result.metadata.get("interrupts", []):
                    await sink.emit(
                        "approval.requested",
                        interrupt_id=item.get("id", ""),
                        request=item.get("value"),
                    )
            await sink.emit("run.status_changed", previous="running", status=result.status)
            await asyncio.to_thread(
                self.runs.update,
                run_id,
                result.status,
                answer=result.answer,
                metadata=result.metadata,
            )
            return result

        try:
            handle = await self.scheduler.submit(run_id, operation)
        except (QueueCapacityError, RuntimeError, ValueError):
            await sink.emit("run.rejected", reason="queue_full_or_scheduler_unavailable")
            await asyncio.to_thread(
                self.runs.update,
                run_id,
                "failed",
                error="run was not accepted by the scheduler",
            )
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

    async def cancel(self, run_id: str) -> bool:
        record = await self.get(run_id)
        if record is None:
            return False
        await asyncio.to_thread(self.runs.request_cancel, run_id)
        previous_scheduler_status = self.scheduler.status(run_id)
        cancelled = await self.scheduler.cancel(run_id)
        if cancelled:
            sink = self._sink(record)
            await sink.emit("run.cancel_requested")
            if previous_scheduler_status == "queued":
                await sink.emit("run.cancelled")
                await asyncio.to_thread(self.runs.update, run_id, "cancelled")
        return cancelled

    async def resume(self, run_id: str, interrupt_id: str, value: dict[str, Any]) -> dict[str, Any]:
        record = await self.get(run_id)
        if record is None:
            raise KeyError(run_id)
        if record["status"] != "interrupted":
            raise ValueError("run is not waiting for an interrupt")
        updated = datetime.fromisoformat(record["updated_at"])
        if (datetime.now(UTC) - updated).total_seconds() > self.approval_ttl_seconds:
            await asyncio.to_thread(self.runs.update, run_id, "interrupted_expired")
            await self._sink(record).emit("approval.expired", interrupt_id=interrupt_id)
            raise TimeoutError("approval interrupt has expired")
        known = {item.get("id") for item in record["metadata"].get("interrupts", [])}
        if interrupt_id not in known:
            raise ValueError("interrupt id does not match the pending approval")
        sink = self._sink(record)
        await sink.emit("approval.decided", interrupt_id=interrupt_id, decision=value)

        if self.scheduler.status(run_id) in {"queued", "running"}:
            await self.scheduler.wait(run_id)

        async def operation(token) -> RuntimeResult:
            token.raise_if_cancelled()
            await asyncio.to_thread(self.runs.update, run_id, "running")
            runtime = self._runtime(record)
            lock = (
                self.workspace_locks.write(self.workspace)
                if record["request"]["permissions"].get("workspace_write")
                else self.workspace_locks.read(self.workspace)
            )
            try:
                async with lock:
                    async with asyncio.timeout(record["request"].get("deadline_seconds", 600)):
                        result = await runtime.resume(ResumeCommand(run_id, record["thread_id"], value), sink)
            except asyncio.CancelledError:
                await sink.emit("run.cancelled")
                await asyncio.to_thread(self.runs.update, run_id, "cancelled")
                raise
            except Exception as exc:  # noqa: BLE001 - isolate a failed agent run
                error = f"{type(exc).__name__}: {exc}"
                await sink.emit("run.failed", error=error)
                await asyncio.to_thread(self.runs.update, run_id, "failed", error=error)
                raise
            await sink.emit("checkpoint.saved", thread_id=record["thread_id"])
            await sink.emit("run.status_changed", previous="running", status=result.status)
            await asyncio.to_thread(
                self.runs.update,
                run_id,
                result.status,
                answer=result.answer,
                metadata=result.metadata,
            )
            return result

        handle = await self.scheduler.submit(run_id, operation)
        handle.future.add_done_callback(self._consume_future)
        return await self.get(run_id)
