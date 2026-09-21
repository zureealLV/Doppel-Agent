from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from doppel_agent.mcp.config import MCPConfig, MCPServerConfig


def config(root: Path, *, maximum: int = 2) -> MCPConfig:
    server = MCPServerConfig(
        "demo",
        "stdio",
        command="C:/Python/python.exe",
        args=("server.py",),
        max_concurrency=maximum,
    )
    return MCPConfig({"demo": server}, root / ".doppel" / "mcp.json")


def connector_for(session, lifecycle=None):
    lifecycle = lifecycle if lifecycle is not None else []

    @asynccontextmanager
    async def connector(server):
        lifecycle.append(("open", server.name))
        initialized = SimpleNamespace(
            protocol_version="2025-06-18",
            server_info=SimpleNamespace(name="fake", version="1.0"),
            capabilities={},
        )
        try:
            yield session, initialized
        finally:
            lifecycle.append(("close", server.name))

    return connector
