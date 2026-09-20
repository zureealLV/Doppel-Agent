"""Runtime selection without leaking implementation details into API layers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..concurrency.limits import ResourceLimits
from ..provider import Provider
from .base import AgentRuntime
from .graph import GraphRuntime
from .legacy import LegacyRuntime


def create_runtime(
    mode: str,
    workspace: Path,
    provider: Provider,
    *,
    state_root: Path | None = None,
    core_options: dict[str, Any] | None = None,
    resource_limits: ResourceLimits | None = None,
) -> AgentRuntime:
    if mode == "legacy":
        return LegacyRuntime(
            workspace,
            provider,
            state_root=state_root,
            core_options=core_options,
        )
    if mode == "graph":
        options = dict(core_options or {})
        return GraphRuntime(
            workspace,
            provider,
            checkpoint_path=(state_root or workspace / ".doppel-agent") / "checkpoints.sqlite3",
            max_steps=int(options.get("max_steps", 8)),
            resource_limits=resource_limits,
        )
    raise ValueError(f"unsupported runtime mode: {mode}")
