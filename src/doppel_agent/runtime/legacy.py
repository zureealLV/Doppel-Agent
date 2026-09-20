"""Async compatibility adapter around the original synchronous Core."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..core import Core
from ..provider import Provider
from .base import EventSink, NullEventSink, ResumeCommand, RunRequest, RuntimeResult


class LegacyRuntime:
    name = "legacy"
    supports_resume = False
    supports_cancel = False

    def __init__(
        self,
        workspace: Path,
        provider: Provider,
        *,
        state_root: Path | None = None,
        core_options: dict[str, Any] | None = None,
    ):
        self.workspace = workspace
        self.provider = provider
        self.state_root = state_root
        self.core_options = dict(core_options or {})

    async def run(self, request: RunRequest, sink: EventSink | None = None) -> RuntimeResult:
        sink = sink or NullEventSink()
        await sink.emit(
            "runtime.started",
            runtime=self.name,
            run_id=request.run_id,
            thread_id=request.thread_id,
        )
        result = await asyncio.to_thread(
            Core(
                self.workspace,
                self.provider,
                state_root=self.state_root,
                **self.core_options,
            ).run,
            request.prompt,
            run_id=request.run_id,
            history=list(request.history),
        )
        runtime_result = RuntimeResult(
            run_id=result["run_id"],
            thread_id=request.thread_id,
            status=result["status"],
            answer=result["answer"],
            runtime=self.name,
        )
        await sink.emit(
            "runtime.finished",
            runtime=self.name,
            run_id=request.run_id,
            thread_id=request.thread_id,
            status=runtime_result.status,
        )
        return runtime_result

    async def resume(self, command: ResumeCommand, sink: EventSink | None = None) -> RuntimeResult:
        raise RuntimeError("legacy runtime does not support resume")

    async def cancel(self, run_id: str) -> None:
        raise RuntimeError("legacy runtime does not support cancellation")
