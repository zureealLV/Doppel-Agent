"""Offline reads must use each runtime's advertised tool schema."""

import asyncio

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.service import RunService
from doppel_agent.core import Core


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_mock_read_uses_native_schema_without_tool_error(tmp_path, mode):
    (tmp_path / "proof.txt").write_text("native mock read proof", encoding="utf-8")

    async def probe():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        try:
            run, _ = await service.create({"mode": mode, "prompt": "read proof.txt", "permissions": {}})
            await service.scheduler.wait(run["run_id"])
            return await service.get(run["run_id"])
        finally:
            await service.close()

    result = asyncio.run(probe())
    assert result["status"] == "completed"
    assert not result["metadata"].get("fallback_runtime")
    assert "native mock read proof" in result["answer"], result["answer"]
    assert "Error invoking tool" not in result["answer"]


def test_mock_review_reads_only_advertised_range_tool(tmp_path):
    (tmp_path / "proof.txt").write_text("review mock read proof", encoding="utf-8")
    result = Core(tmp_path, MockProvider(), review_mode=True).run("read proof.txt")
    assert result["status"] == "completed"
    assert "1: review mock read proof" in result["answer"], result["answer"]
    assert "unknown tool" not in result["answer"]
