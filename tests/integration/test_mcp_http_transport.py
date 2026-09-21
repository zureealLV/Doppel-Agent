import asyncio
import socket
import tempfile
import unittest
from pathlib import Path

import uvicorn
from mcp.server.mcpserver import MCPServer

from doppel_agent.mcp.catalog import MCPToolCatalog
from doppel_agent.mcp.client_manager import MCPClientManager
from doppel_agent.mcp.config import MCPConfig, MCPServerConfig
from doppel_agent.mcp.executor import MCPToolExecutor
from doppel_agent.permissions import PermissionManager


class MCPStreamableHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_streamable_http_list_and_call(self):
        mcp = MCPServer("doppel-http-fixture", version="1.0")

        @mcp.tool()
        def echo(text: str) -> str:
            return f"http:{text}"

        app = mcp.streamable_http_app(json_response=True, stateless_http=True)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="error", lifespan="on", access_log=False)
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(server.started)

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = MCPConfig(
                    {
                        "http": MCPServerConfig(
                            "http",
                            "streamable_http",
                            url=f"http://127.0.0.1:{port}/mcp",
                            max_concurrency=2,
                        )
                    },
                    root / ".doppel" / "mcp.json",
                )
                manager = MCPClientManager(config)
                try:
                    catalog = MCPToolCatalog(manager)
                    tools = await catalog.list_all()
                    self.assertEqual([tool.logical_name for tool in tools], ["mcp__http__echo"])
                    executor = MCPToolExecutor(
                        manager,
                        catalog,
                        PermissionManager(frozenset({"mcp_execute"})),
                    )
                    result = await executor.execute(
                        "mcp__http__echo",
                        {"text": "hello"},
                        run_id="http-run",
                        tool_call_id="http-call",
                    )
                    self.assertTrue(result.success)
                    self.assertIn("http:hello", result.model_view)
                finally:
                    await manager.close()
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)
