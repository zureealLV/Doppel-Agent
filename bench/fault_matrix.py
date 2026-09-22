"""Deterministic fault-injection matrix for runtime reliability gates.

This is deliberately an offline systems benchmark. It exercises production
boundaries with fixed fakes and records raw outcomes; it does not score model
quality or pretend that MockTransport is a live provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import httpx
from mcp import types

from doppel_agent.api.sse import run_event_stream
from doppel_agent.mcp.catalog import MCPToolCatalog
from doppel_agent.mcp.client_manager import MCPClientManager
from doppel_agent.mcp.config import MCPConfig, MCPServerConfig
from doppel_agent.mcp.executor import MCPToolExecutor
from doppel_agent.permissions import PermissionManager
from doppel_agent.persistence import EventStore, RuntimeRunStore
from doppel_agent.provider import (
    AsyncOpenAICompatibleProvider,
    Message,
    ProviderCircuitOpen,
    ProviderRequestError,
)
from doppel_agent.runtime.service import RunService
from doppel_agent.workspace.process_supervisor import ProcessSupervisor


Scenario = Callable[[], Awaitable[dict[str, Any]]]


def _result(
    *,
    injected_fault: str,
    expected: str,
    observed: dict[str, Any],
    passed: bool,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "injected_fault": injected_fault,
        "expected": expected,
        "observed": observed,
        "passed": passed,
        "failure_reason": failure_reason,
    }


def _provider_response(request: httpx.Request, content: str = "ok") -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=request,
    )


async def _provider_429_retry_after() -> dict[str, Any]:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.125"}, request=request)
        return _provider_response(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AsyncOpenAICompatibleProvider(
        "https://fault.invalid/v1",
        "fixture",
        client=client,
        sleep=lambda delay: _append_delay(delays, delay),
        random_source=lambda: 0.5,
    )
    try:
        turn = await provider.anext_turn([Message("user", "probe")], [])
        observed = {"attempts": attempts, "delays": delays, "content": turn.content}
        passed = observed == {"attempts": 2, "delays": [0.125], "content": "ok"}
        return _result(
            injected_fault="HTTP 429 with Retry-After=0.125",
            expected="one bounded retry after the server-requested delay",
            observed=observed,
            passed=passed,
            failure_reason=None if passed else "retry policy diverged",
        )
    finally:
        await client.aclose()


async def _append_delay(delays: list[float], delay: float) -> None:
    delays.append(delay)


async def _provider_exception_case(
    name: str,
    error_factory: Callable[[httpx.Request], Exception],
) -> dict[str, Any]:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise error_factory(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AsyncOpenAICompatibleProvider(
        "https://fault.invalid/v1",
        "fixture",
        client=client,
        max_attempts=2,
        sleep=lambda _delay: asyncio.sleep(0),
        random_source=lambda: 0.5,
    )
    terminal = ""
    failure_kind = ""
    try:
        await provider.anext_turn([Message("user", "probe")], [])
    except ProviderRequestError as exc:
        terminal = str(exc)
        failure_kind = exc.kind
    finally:
        await client.aclose()
    observed = {
        "attempts": attempts,
        "failure_kind": failure_kind,
        "terminal_error": terminal,
    }
    expected_kind = "timeout" if name == "read timeout" else "connection"
    expected_message = "provider timeout" if expected_kind == "timeout" else "provider connection failed"
    passed = (
        attempts == 2
        and failure_kind == expected_kind
        and terminal == expected_message
    )
    return _result(
        injected_fault=name,
        expected="two total attempts followed by an explicit terminal error",
        observed=observed,
        passed=passed,
        failure_reason=None if passed else "network failure was not bounded",
    )


async def _provider_connect_refused() -> dict[str, Any]:
    return await _provider_exception_case(
        "connection refused",
        lambda request: httpx.ConnectError("refused", request=request),
    )


async def _provider_read_timeout() -> dict[str, Any]:
    return await _provider_exception_case(
        "read timeout",
        lambda request: httpx.ReadTimeout("timed out", request=request),
    )


async def _provider_5xx() -> dict[str, Any]:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AsyncOpenAICompatibleProvider(
        "https://fault.invalid/v1",
        "fixture",
        client=client,
        max_attempts=2,
        sleep=lambda _delay: asyncio.sleep(0),
        random_source=lambda: 0.5,
    )
    terminal = ""
    try:
        await provider.anext_turn([Message("user", "probe")], [])
    except RuntimeError as exc:
        terminal = str(exc)
    finally:
        await client.aclose()
    observed = {"attempts": attempts, "terminal_error": terminal}
    passed = attempts == 2 and terminal == "provider HTTP 503"
    return _result(
        injected_fault="HTTP 503",
        expected="bounded retry followed by status-preserving terminal error",
        observed=observed,
        passed=passed,
        failure_reason=None if passed else "5xx handling diverged",
    )


async def _provider_half_open() -> dict[str, Any]:
    now = [10.0]
    attempts = 0
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request)
        probe_started.set()
        await release_probe.wait()
        return _provider_response(request, "recovered")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AsyncOpenAICompatibleProvider(
        "https://fault.invalid/v1",
        "fixture",
        client=client,
        max_attempts=1,
        circuit_failure_threshold=1,
        circuit_reset_seconds=5,
        clock=lambda: now[0],
    )
    blocked = False
    try:
        try:
            await provider.anext_turn([Message("user", "trip")], [])
        except RuntimeError:
            pass
        now[0] += 6
        probe = asyncio.create_task(provider.anext_turn([Message("user", "probe")], []))
        await probe_started.wait()
        try:
            await provider.anext_turn([Message("user", "fan-out")], [])
        except ProviderCircuitOpen:
            blocked = True
        release_probe.set()
        recovered = (await probe).content
    finally:
        release_probe.set()
        await client.aclose()
    observed = {"attempts": attempts, "parallel_probe_blocked": blocked, "content": recovered}
    passed = observed == {
        "attempts": 2,
        "parallel_probe_blocked": True,
        "content": "recovered",
    }
    return _result(
        injected_fault="open circuit reaches reset window under concurrent demand",
        expected="exactly one half-open probe",
        observed=observed,
        passed=passed,
        failure_reason=None if passed else "half-open probe fanned out",
    )


def _mcp_config(root: Path) -> MCPConfig:
    return MCPConfig(
        {
            "fault": MCPServerConfig(
                "fault", "stdio", command=sys.executable, args=("fixture.py",), max_concurrency=2
            )
        },
        root / "mcp.json",
    )


def _initialized() -> SimpleNamespace:
    return SimpleNamespace(
        protocol_version="2025-06-18",
        server_info=SimpleNamespace(name="fixture", version="1.0"),
        capabilities={},
    )


class _McpSession:
    def __init__(self, schema_type: str = "string", *, disconnect: bool = False):
        self.schema_type = schema_type
        self.disconnect = disconnect
        self.tool_calls = 0

    async def list_tools(self, *, params: Any = None) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="mutate",
                    inputSchema={
                        "type": "object",
                        "properties": {"value": {"type": self.schema_type}},
                        "required": ["value"],
                    },
                )
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.tool_calls += 1
        if self.disconnect:
            raise ConnectionError("disconnected after side effect may have committed")
        return types.CallToolResult(content=[types.TextContent(text="ok")])


async def _mcp_disconnect_no_retry() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        session = _McpSession(disconnect=True)

        @asynccontextmanager
        async def connector(_server: MCPServerConfig):
            yield session, _initialized()

        manager = MCPClientManager(_mcp_config(Path(directory)), connector=connector)
        executor = MCPToolExecutor(
            manager,
            MCPToolCatalog(manager),
            PermissionManager(frozenset({"mcp_execute"})),
        )
        terminal = ""
        try:
            await executor.execute(
                "mcp__fault__mutate", {"value": "x"}, run_id="run", tool_call_id="call"
            )
        except ConnectionError as exc:
            terminal = str(exc)
        finally:
            await manager.close()
        observed = {"remote_calls": session.tool_calls, "terminal_error": terminal}
        passed = session.tool_calls == 1 and "disconnected after side effect" in terminal
        return _result(
            injected_fault="MCP disconnect after a side-effecting call is sent",
            expected="invalidate session but never auto-retry the ambiguous tool call",
            observed=observed,
            passed=passed,
            failure_reason=None if passed else "ambiguous side effect was duplicated",
        )


async def _mcp_schema_reconnect() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        sessions = [_McpSession("string"), _McpSession("integer")]
        opened = 0

        @asynccontextmanager
        async def connector(_server: MCPServerConfig):
            nonlocal opened
            session = sessions[opened]
            opened += 1
            yield session, _initialized()

        manager = MCPClientManager(_mcp_config(Path(directory)), connector=connector)
        catalog = MCPToolCatalog(manager)
        first = await catalog.list_server("fault")
        await manager.invalidate("fault")
        second = await catalog.list_server("fault")
        await manager.close()
        observed = {
            "sessions_opened": opened,
            "first_hash": first[0].schema_hash,
            "second_hash": second[0].schema_hash,
        }
        passed = opened == 2 and first[0].schema_hash != second[0].schema_hash
        return _result(
            injected_fault="MCP reconnect with unchanged server version but changed tool schema",
            expected="session generation invalidates the schema cache",
            observed=observed,
            passed=passed,
            failure_reason=None if passed else "stale tool schema survived reconnect",
        )


async def _sse_slow_replay() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "runtime.sqlite3"
        runs = RuntimeRunStore(database)
        events = EventStore(database)
        record, _ = runs.create("slow-sse", "thread", "graph", {"prompt": "x"}, None)
        for index in range(4):
            events.append(record["run_id"], record["thread_id"], "fixture.event", {"index": index})
        runs.update(record["run_id"], "completed", answer="done")

        class Service:
            notifier = SimpleNamespace(wait=lambda _run_id: asyncio.sleep(0))

            async def list_events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
                return events.list(run_id, after_seq=after_seq)

            async def get(self, run_id: str) -> dict[str, Any] | None:
                return runs.get(run_id)

        chunks: list[str] = []
        async for chunk in run_event_stream(Service(), record["run_id"], 1):
            chunks.append(chunk)
            await asyncio.sleep(0.005)
        ids = [int(line[4:]) for chunk in chunks for line in chunk.splitlines() if line.startswith("id: ")]
        durable_ids = [item["seq"] for item in events.list(record["run_id"], after_seq=1)]
        observed = {"streamed_ids": ids, "durable_ids": durable_ids}
        passed = ids == durable_ids and ids == sorted(set(ids))
        return _result(
            injected_fault="slow SSE consumer reconnects after sequence 1",
            expected="ordered durable replay with no duplicate event IDs",
            observed=observed,
            passed=passed,
            failure_reason=None if passed else "SSE replay diverged from durable log",
        )


async def _restart_recovery() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        service = RunService(root)
        queued, _ = service.runs.create("queued", "tq", "graph", {"prompt": "q"}, None)
        running, _ = service.runs.create("running", "tr", "deep", {"prompt": "r"}, None)
        service.runs.update(running["run_id"], "running")
        await service.start()
        try:
            records = [await service.get(item["run_id"]) for item in (queued, running)]
            event_types = [
                (await service.list_events(item["run_id"]))[-1]["type"]
                for item in (queued, running)
            ]
        finally:
            await service.close()
        statuses = [record["status"] for record in records if record is not None]
        observed = {"statuses": statuses, "terminal_events": event_types}
        passed = statuses == ["failed", "failed"] and event_types == [
            "run.recovered_after_restart",
            "run.recovered_after_restart",
        ]
        return _result(
            injected_fault="service restart loses queued/running in-memory leases",
            expected="atomic failed terminal states plus durable recovery events",
            observed=observed,
            passed=passed,
            failure_reason=None if passed else "incomplete run remained non-terminal",
        )


async def _process_tree_timeout() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        supervisor = ProcessSupervisor()
        terminal = ""
        try:
            await supervisor.run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=Path(directory),
                run_id="timeout",
                timeout_seconds=0.1,
            )
        except TimeoutError as exc:
            terminal = str(exc)
        observed = {"terminal_error": terminal, "active_after": supervisor.active_count}
        passed = "timed out" in terminal and supervisor.active_count == 0
        await supervisor.close()
        return _result(
            injected_fault="command exceeds deadline",
            expected="terminate supervised process tree and release registry entry",
            observed=observed,
            passed=passed,
            failure_reason=None if passed else "timed-out process remained registered",
        )


SCENARIOS: tuple[tuple[str, Scenario], ...] = (
    ("provider_429_retry_after", _provider_429_retry_after),
    ("provider_connection_refused", _provider_connect_refused),
    ("provider_read_timeout", _provider_read_timeout),
    ("provider_5xx", _provider_5xx),
    ("provider_half_open", _provider_half_open),
    ("mcp_disconnect_no_retry", _mcp_disconnect_no_retry),
    ("mcp_schema_reconnect", _mcp_schema_reconnect),
    ("sse_slow_replay", _sse_slow_replay),
    ("restart_lease_recovery", _restart_recovery),
    ("process_tree_timeout", _process_tree_timeout),
)


async def run_fault_matrix() -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {}
    for name, scenario in SCENARIOS:
        try:
            results[name] = await scenario()
        except Exception as exc:  # noqa: BLE001 - a report must retain unexpected failures
            results[name] = _result(
                injected_fault=name,
                expected="scenario completes with an explicit assertion",
                observed={"exception_type": type(exc).__name__, "exception": str(exc)},
                passed=False,
                failure_reason=f"unexpected {type(exc).__name__}: {exc}",
            )
    passed_count = sum(1 for result in results.values() if result["passed"])
    denominator = len(results)
    return {
        "schema_version": "1.0",
        "recorded_at": datetime.now(UTC).isoformat(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "denominator": denominator,
        "passed_count": passed_count,
        "failed_count": denominator - passed_count,
        "passed": passed_count == denominator,
        "scenarios": results,
        "scope_note": (
            "Offline deterministic fault-injection evidence. Provider and MCP transports are fixed "
            "fakes; this report is not a live-model quality or external-service availability claim."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Doppel deterministic fault matrix")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run_fault_matrix())
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
