"""Shared contracts for every Doppel Agent execution runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from ..provider import Message


@dataclass(frozen=True)
class RunRequest:
    prompt: str
    run_id: str = field(default_factory=lambda: uuid4().hex)
    thread_id: str = field(default_factory=lambda: uuid4().hex)
    history: tuple[Message, ...] = ()

    def __post_init__(self) -> None:
        if not self.prompt.strip() or len(self.prompt) > 100_000:
            raise ValueError("prompt must contain 1 to 100000 characters")
        if not self.run_id or not self.thread_id:
            raise ValueError("run_id and thread_id are required")
        if any(character not in "0123456789abcdef" for character in self.run_id):
            raise ValueError("run_id must be lowercase hexadecimal")


@dataclass(frozen=True)
class ResumeCommand:
    run_id: str
    thread_id: str
    value: Any


@dataclass(frozen=True)
class RuntimeResult:
    run_id: str
    thread_id: str
    status: str
    answer: str
    runtime: str
    metadata: dict[str, Any] = field(default_factory=dict)


class EventSink(Protocol):
    async def emit(self, kind: str, **payload: Any) -> None: ...


class NullEventSink:
    async def emit(self, kind: str, **payload: Any) -> None:
        return None


class AgentRuntime(Protocol):
    name: str
    supports_resume: bool
    supports_cancel: bool

    async def run(self, request: RunRequest, sink: EventSink | None = None) -> RuntimeResult: ...

    async def resume(self, command: ResumeCommand, sink: EventSink | None = None) -> RuntimeResult: ...

    async def cancel(self, run_id: str) -> None: ...
