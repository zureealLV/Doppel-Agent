"""Native17 FIRST-close regression: live pending SSE must drain without navigation.

Actual local kernel/API/socket/SQLite ownership; no WebView or model-quality claim.
The original client stays OPEN until original kernel.close returns. Pending approval
and file baseline must survive; no decision/cancel/provider resend is a close effect.
"""
import asyncio
import time
import urllib.request

import pytest

from doppel_agent.projects.desktop_kernel import DesktopKernel
from doppel_agent.provider import ModelTurn, ToolCall
import doppel_agent.api as api_module


@pytest.mark.parametrize("mode", ["graph", "deep", "legacy"])
def test_original_kernel_close_drains_live_pending_sse_before_lifespan_without_client_disconnect(
    tmp_path, monkeypatch, mode,
):
    calls = []

    class Scripted:
        def next_turn(self, messages, tools):
            calls.append(messages)
            return ModelTurn(tool_calls=(ToolCall(
                "original-pending", "propose_patch",
                {"changes": [{"path": "existing.txt", "content": "proposed\n"}]},
            ),))

    (tmp_path / "existing.txt").write_bytes(b"original\r\n")
    original = api_module.create_app
    monkeypatch.setattr(api_module, "create_app", lambda *a, **kw: original(*a, provider=Scripted(), **kw))
    kernel = DesktopKernel(tmp_path)
    kernel.start()
    service = kernel.app.state.run_service
    loop = kernel.app.state.runtime_loop
    response = None

    def dispatch(operation):
        return asyncio.run_coroutine_threadsafe(operation, loop).result(timeout=10)

    try:
        record, _ = dispatch(service.create({
            "mode": mode, "prompt": "pending first-close fixture",
            "permissions": {"workspace_write": True},
        }))
        for _ in range(200):
            before = dispatch(service.get(record["run_id"]))
            if before["status"] == "interrupted" and service.scheduler.status(record["run_id"]) != "running":
                break
            time.sleep(.05)
        assert before["status"] == "interrupted" and before["lease_active"]
        assert not before["metadata"].get("fallback_runtime")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        response = opener.open(
            kernel.url.rstrip("/") + "/api/v1/runs/" + record["run_id"] + "/events?stream=true", timeout=10,
        )
        assert response.status == 200 and response.readline().startswith(b"id:")
        assert kernel.api.server_state.connections
        # Crucial: do NOT close response or cancel/approve the pending run first.
        kernel.close()
        assert not kernel.api_worker.is_alive() and not kernel.legacy_worker.is_alive()
        assert service.cleanup_complete and not kernel.owner.held
        assert not kernel.api.server_state.connections and not kernel.api.server_state.tasks
        after = service.runs.get(record["run_id"])
        assert after["status"] == "interrupted" and after["lease_active"]
        assert after["metadata"] == before["metadata"]
        assert len(calls) == 1 and (tmp_path / "existing.txt").read_bytes() == b"original\r\n"
        response.read()  # EOF, not abandoned server stream/client-side workaround.
    finally:
        if response is not None:
            response.close()
        # RED run still drains SAME original kernel after original client closes.
        kernel.close()
