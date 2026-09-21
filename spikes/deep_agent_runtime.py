"""Offline Deep Agents 0.7 integration spike.

Run with:
    uv run --extra agent python spikes/deep_agent_runtime.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.base import RunRequest
from doppel_agent.runtime.deep import DeepAgentRuntime


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="doppel-deep-spike-") as directory:
        workspace = Path(directory)
        runtime = DeepAgentRuntime(workspace, MockProvider(), max_subagents=0)
        result = await runtime.run(RunRequest("Inspect the workspace without modifying it."))
        if result.metadata.get("fallback_runtime"):
            raise RuntimeError(f"Deep Agents integration fell back: {result.metadata}")
        print(
            {
                "runtime": result.runtime,
                "status": result.status,
                "answer": result.answer,
                "metadata": result.metadata,
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
