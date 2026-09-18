import importlib.util
import json
import sys
import unittest
from pathlib import Path

from doppel_agent.mcp_bridge import MCPBridge, mcp_tools
from doppel_agent.permissions import PermissionManager
from doppel_agent.tools import ToolRegistry
from support import workspace


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))
        (self.root / ".doppel").mkdir()

    def configure(self):
        fixture = Path(__file__).parent / "fixtures" / "mcp_echo.py"
        (self.root / ".doppel" / "mcp.json").write_text(json.dumps({
            "servers": {"echo": {"command": sys.executable, "args": [str(fixture)]}}
        }), encoding="utf-8")

    def test_rejects_relative_command(self):
        (self.root / ".doppel" / "mcp.json").write_text(
            json.dumps({"servers": {"bad": {"command": "python", "args": []}}}), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            MCPBridge(self.root)

    def test_requires_capability(self):
        self.configure()
        bridge = MCPBridge(self.root)
        registry = ToolRegistry(PermissionManager())
        for tool in mcp_tools(bridge):
            registry.register(tool)
        with self.assertRaises(PermissionError):
            registry.execute("mcp_list", {"server": "echo"})

    def test_grant_still_requires_approval(self):
        self.configure()
        bridge = MCPBridge(self.root)
        registry = ToolRegistry(PermissionManager(frozenset({"mcp_execute"}), lambda *_: False))
        for tool in mcp_tools(bridge):
            registry.register(tool)
        with self.assertRaises(PermissionError):
            registry.execute("mcp_list", {"server": "echo"})
        self.assertIn("configured_command", bridge.approval_context({"server": "echo"}))

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "install doppel-agent[mcp]")
    def test_stdio_list_and_call(self):
        self.configure()
        bridge = MCPBridge(self.root)
        registry = ToolRegistry(PermissionManager(frozenset({"mcp_execute"})))
        for tool in mcp_tools(bridge):
            registry.register(tool)
        listed = json.loads(registry.execute("mcp_list", {"server": "echo"}))
        self.assertIn("echo", [tool["name"] for tool in listed])
        result = json.loads(registry.execute("mcp_call", {
            "server": "echo", "tool": "echo", "arguments_json": '{"text":"hello MCP"}',
        }))
        self.assertFalse(result["is_error"])
        self.assertIn("hello MCP", " ".join(result["content"]))
