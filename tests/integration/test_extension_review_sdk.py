"""S7 C definitions, UNRUN. Real original SDK/engines, scripted offline provider.

ASGI HTTP is not browser/native acceptance. Manager maps becoming empty do not
prove OS process/port/Job cleanup; original N4 still requires independent handles.
"""

import asyncio
import json
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

from doppel_agent.api import create_app
from doppel_agent.mcp.client_manager import MCPCleanupError
from doppel_agent.provider import ModelTurn, ToolCall
from fixtures.mcp_extension_journal import build_server, entries, record


class MCPProvider:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def next_turn(self, messages, tools):
        self.calls.append(messages)
        latest = next(item.content for item in reversed(messages) if item.role == "user")
        if latest == "no-grant":
            assert not any(tool["function"]["name"].startswith("mcp__") for tool in tools)
            return ModelTurn(content="no extension granted")
        if messages[-1].role == "tool":
            return ModelTurn(content="remote-result:" + messages[-1].content)
        assert any(tool["function"]["name"] == "mcp__demo__echo" for tool in tools)
        return ModelTurn(tool_calls=(ToolCall("s7-mcp-call", "mcp__demo__echo", {"text": self.text}),))


@asynccontextmanager
async def local_server(transport, journal, *, fail_list=False):
    if transport == "stdio":
        yield {"transport": "stdio", "command": sys.executable,
               "args": [str(Path(__file__).resolve().parents[1] / "fixtures" / "mcp_extension_journal.py"), str(journal)]
                       + (["fail-list"] if fail_list else [])}
        return
    mcp = build_server(journal, fail_list=fail_list)
    # Socket is bound once and passed to the original uvicorn server; no free-port race.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(json_response=True, stateless_http=True),
                                          log_level="error", lifespan="on", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("fixture HTTP server did not start")
                await asyncio.sleep(0.01)
        record(journal, "started")
        yield {"transport": "streamable_http", "url": f"http://127.0.0.1:{port}/mcp"}
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 5)
        finally:
            listener.close()


async def settle(service, identifier):
    async with asyncio.timeout(10):
        await service.scheduler.wait(identifier)
    result = await service.get(identifier)
    assert result is not None
    return result


@pytest.mark.parametrize("transport", ["stdio", "streamable_http"])
@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("text", ["fixture-ok", "fixture-error"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_extension_metadata_and_original_runtime_hitl_match_real_remote_journal(tmp_path, transport, mode, text, decision):
    async def scenario():
        journal = tmp_path / "remote.jsonl"
        async with local_server(transport, journal) as config:
            (tmp_path / ".doppel").mkdir()
            (tmp_path / ".doppel" / "mcp.json").write_text(json.dumps({"servers": {"demo": config}}), encoding="utf-8")
            provider = MCPProvider(text)
            app = create_app(tmp_path, provider=provider)
            service = app.state.run_service
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                    base = "/api/v1/extensions/mcp/servers/demo"
                    before = entries(journal)
                    inventory = await client.get("/api/v1/extensions/mcp/servers")
                    assert inventory.status_code == 200
                    cached = await client.get(base + "/tools/cached")
                    assert cached.json()["cache_state"] == "missing" and cached.json()["total"] is None
                    refused = await client.post(base + "/discovery", json={"confirmed": False, "action": "refresh"})
                    assert refused.status_code == 422 and entries(journal) == before
                    # Actual ungranted engine creation must not cause hidden discovery.
                    response = await client.post("/api/v1/runs", json={"mode": mode, "prompt": "no-grant"})
                    assert response.status_code == 202
                    ungranted = await settle(service, response.json()["run_id"])
                    assert ungranted["status"] == "completed" and "fallback_runtime" not in ungranted["metadata"]
                    assert entries(journal) == before
                    probe = await client.post(base + "/discovery", json={"confirmed": True, "action": "probe"})
                    assert probe.status_code == 200 and probe.json()["probe_completed"] is True
                    assert (await client.get(base + "/tools/cached")).json()["cache_state"] == "missing"
                    assert any(item["kind"] == "list" for item in entries(journal))
                    refreshed = await client.post(base + "/discovery", json={"confirmed": True, "action": "refresh"})
                    assert refreshed.status_code == 200 and refreshed.headers["cache-control"] == "no-store"
                    assert refreshed.json()["tools"][0]["logical_name"] == "mcp__demo__echo"
                    assert refreshed.json()["tool_execution_verified"] is False
                    before_cached = entries(journal)
                    assert (await client.get(base + "/tools/cached")).json()["tools"] == refreshed.json()["tools"]
                    assert entries(journal) == before_cached
                    assert not any(item["kind"] == "call" for item in before_cached)
                    # Original manager closes then explicitly reconnects. Old descriptors
                    # are stale only once the in-memory session generation changes.
                    await service.mcp_manager.invalidate("demo")
                    reprobed = await client.post(base + "/discovery", json={"confirmed": True, "action": "probe"})
                    assert reprobed.status_code == 200
                    stale = (await client.get(base + "/tools/cached")).json()
                    assert stale["cache_state"] == "stale_generation" and stale["total"] is None and stale["tools"] == []
                    rerefreshed = await client.post(base + "/discovery", json={"confirmed": True, "action": "refresh"})
                    assert rerefreshed.status_code == 200 and rerefreshed.json()["cache_state"] == "cached"
                    assert not any(item["kind"] == "call" for item in entries(journal))
                    started = await client.post("/api/v1/runs", json={"mode": mode, "prompt": "use-fixture",
                                                                       "permissions": {"mcp_execute": True}})
                    assert started.status_code == 202
                    rid = started.json()["run_id"]
                    interrupted = await settle(service, rid)
                    assert interrupted["status"] == "interrupted" and "fallback_runtime" not in interrupted["metadata"]
                    assert not any(item["kind"] == "call" for item in entries(journal))
                    iid = interrupted["metadata"]["interrupts"][0]["id"]
                    approved = await client.post(f"/api/v1/runs/{rid}/interrupts/{iid}/resume", json={"action": decision})
                    assert approved.status_code == 202
                    completed = await settle(service, rid)
                    assert completed["status"] == "completed" and "fallback_runtime" not in completed["metadata"]
                    calls = [item for item in entries(journal) if item["kind"] == "call"]
                    events = await service.list_events(rid)
                    receipts = [item["payload"] for item in events if item["type"] == "mcp.tool_executed"]
                    if decision == "reject":
                        assert calls == [] and receipts == []
                    else:
                        assert len(calls) == 1 and calls[0]["text"] == text
                        assert len(receipts) == 1 and receipts[0]["run_id"] == rid
                        assert receipts[0]["success"] is (text == "fixture-ok")
                        assert ("fixture-echo:fixture-ok" if text == "fixture-ok" else "MCP tool error") in completed["answer"]
                    # Repeated approval of this spent original interrupt may not call again.
                    again = await client.post(f"/api/v1/runs/{rid}/interrupts/{iid}/resume", json={"action": "approve"})
                    assert again.status_code == 409
                    assert [item for item in entries(journal) if item["kind"] == "call"] == calls
            assert not service.mcp_manager._clients and not service.mcp_manager._connections
            assert not service._owner.held  # service accounting, NOT independent OS drain proof
    asyncio.run(scenario())


@pytest.mark.parametrize("transport", ["stdio", "streamable_http"])
def test_real_sdk_metadata_error_retains_unknown_missing_cache_without_execution(tmp_path, transport):
    async def scenario():
        journal = tmp_path / "error.jsonl"
        async with local_server(transport, journal, fail_list=True) as config:
            (tmp_path / ".doppel").mkdir()
            (tmp_path / ".doppel" / "mcp.json").write_text(json.dumps({"servers": {"demo": config}}), encoding="utf-8")
            provider = MCPProvider("fixture-ok")
            app = create_app(tmp_path, provider=provider)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                    base = "/api/v1/extensions/mcp/servers/demo"
                    response = await client.post(base + "/discovery", json={"confirmed": True, "action": "refresh"})
                    assert response.status_code == 502 and response.json()["detail"] == "extension_discovery_failed"
                    assert response.headers["cache-control"] == "no-store" and "fixture-list-error" not in response.text
                    observed = entries(journal)
                    assert any(item["kind"] == "list" for item in observed)
                    assert not any(item["kind"] == "call" for item in observed) and provider.calls == []
                    missing = (await client.get(base + "/tools/cached")).json()
                    assert missing["cache_state"] == "missing" and missing["total"] is None
                    assert entries(journal) == observed
    asyncio.run(scenario())


def test_real_failed_stdio_discovery_does_not_fabricate_cached_catalog(tmp_path):
    async def scenario():
        journal = tmp_path / "failed.jsonl"
        script = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_extension_journal.py"
        (tmp_path / ".doppel").mkdir()
        (tmp_path / ".doppel" / "mcp.json").write_text(json.dumps({"servers": {"demo": {
            "transport": "stdio", "command": sys.executable, "args": [str(script), str(journal), "fail-start"],
        }}}), encoding="utf-8")
        app = create_app(tmp_path, provider=MCPProvider("fixture-ok"))
        service = app.state.run_service
        with pytest.raises(MCPCleanupError):
          async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                base = "/api/v1/extensions/mcp/servers/demo"
                result = await client.post(base + "/discovery", json={"confirmed": True, "action": "refresh"})
                assert result.status_code == 502 and result.json()["detail"] == "extension_discovery_failed"
                assert result.headers["cache-control"] == "no-store"
                assert any(item["kind"] == "started" for item in entries(journal))
                previous = entries(journal)
                cached = (await client.get(base + "/tools/cached")).json()
                assert cached["cache_state"] == "missing" and cached["total"] is None
                assert entries(journal) == previous and not any(item["kind"] == "call" for item in previous)
                assert service.mcp_manager.cleanup_failed
        assert service._owner.held and len(service.mcp_manager._connections) == 1
        frame = service.mcp_manager._connections['demo'].lifetime
        assert frame.builtin_started and frame.sdk_exit_failed and not frame.known_closed
        service._owner.release()  # Disposable fixture teardown; failed SDK exit stays unknown.
    asyncio.run(scenario())
