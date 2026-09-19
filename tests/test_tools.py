import unittest
import sys

from doppel_agent.permissions import PermissionManager
from doppel_agent.tools import ToolRegistry, read_file_tool, write_file_tool, run_command_tool
from support import workspace


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))
        (self.root / "note.txt").write_text("safe", encoding="utf-8")
        self.registry = ToolRegistry(PermissionManager())
        self.registry.register(read_file_tool(self.root))

    def test_reads_inside_workspace(self):
        self.assertEqual(self.registry.execute("read_file", {"path": "note.txt"}), "safe")

    def test_blocks_secret_bearing_files(self):
        for name in (".env", ".env.local", "credentials.json", ".npmrc", "private.pem"):
            (self.root / name).write_text("do-not-leak", encoding="utf-8")
            with self.subTest(name=name), self.assertRaises(PermissionError):
                self.registry.execute("read_file", {"path": name})

        (self.root / ".env.example").write_text("SAFE_EXAMPLE=1", encoding="utf-8")
        self.assertEqual(
            self.registry.execute("read_file", {"path": ".env.example"}),
            "SAFE_EXAMPLE=1",
        )

    def test_blocks_reserved_state_and_metadata_directories(self):
        for directory in (".doppel-agent", ".git", ".ssh"):
            (self.root / directory).mkdir()
            (self.root / directory / "data.txt").write_text("private", encoding="utf-8")
            with self.subTest(directory=directory), self.assertRaises(PermissionError):
                self.registry.execute("read_file", {"path": f"{directory}/data.txt"})

    def test_rejects_absolute_path(self):
        with self.assertRaises(PermissionError):
            self.registry.execute("read_file", {"path": str(self.root / "note.txt")})

    def test_rejects_parent_traversal(self):
        outside = self.root.parent / f"outside-{self.root.name}.txt"
        outside.write_text("unsafe", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaises(PermissionError):
            self.registry.execute("read_file", {"path": f"../{outside.name}"})

    def test_unknown_tool_fails(self):
        with self.assertRaises(ValueError):
            self.registry.execute("shell", {})

    def test_denied_capability(self):
        registry = ToolRegistry(PermissionManager(frozenset()))
        registry.register(read_file_tool(self.root))
        with self.assertRaises(PermissionError):
            registry.execute("read_file", {"path": "note.txt"})

    def test_write_requires_capability(self):
        self.registry.register(write_file_tool(self.root))
        with self.assertRaises(PermissionError):
            self.registry.execute("write_file", {"path": "new.txt", "content": "hello"})
        self.assertFalse((self.root / "new.txt").exists())

    def test_write_with_grant(self):
        registry = ToolRegistry(PermissionManager(frozenset({"workspace_write"})))
        registry.register(write_file_tool(self.root))
        registry.execute("write_file", {"path": "new.txt", "content": "hello"})
        self.assertEqual((self.root / "new.txt").read_text(encoding="utf-8"), "hello")

    def test_write_cannot_replace_secrets_or_agent_state(self):
        registry = ToolRegistry(PermissionManager(frozenset({"workspace_write"})))
        registry.register(write_file_tool(self.root))
        (self.root / ".doppel-agent").mkdir()
        for path in (".env", ".npmrc", ".doppel-agent/session.json"):
            with self.subTest(path=path), self.assertRaises(PermissionError):
                registry.execute("write_file", {"path": path, "content": "overwrite"})
            self.assertFalse((self.root / path).exists())

    def test_approval_requires_grant_and_consent(self):
        calls = []
        approver = lambda capability, name, arguments: calls.append((capability, name)) or True
        no_grant = ToolRegistry(PermissionManager(frozenset(), approver))
        no_grant.register(write_file_tool(self.root))
        with self.assertRaises(PermissionError):
            no_grant.execute("write_file", {"path": "new.txt", "content": "hello"})
        self.assertEqual(calls, [])
        denied = ToolRegistry(PermissionManager(frozenset({"workspace_write"}), lambda *_: False))
        denied.register(write_file_tool(self.root))
        with self.assertRaises(PermissionError):
            denied.execute("write_file", {"path": "new.txt", "content": "hello"})
        self.assertFalse((self.root / "new.txt").exists())
        granted = ToolRegistry(PermissionManager(frozenset({"workspace_write"}), approver))
        granted.register(write_file_tool(self.root))
        granted.execute("write_file", {"path": "new.txt", "content": "hello"})
        self.assertEqual(calls, [("workspace_write", "write_file")])

    def test_command_denied_by_default(self):
        self.registry.register(run_command_tool(self.root))
        with self.assertRaises(PermissionError):
            self.registry.execute("run_command", {"argv": ["python", "--version"]})

    def test_command_with_grant(self):
        registry = ToolRegistry(PermissionManager(frozenset({"command_execute"})))
        registry.register(run_command_tool(self.root))
        output = registry.execute("run_command", {"argv": [sys.executable, "--version"]})
        self.assertIn("exit_code=0", output)
        self.assertIn("Python", output)
