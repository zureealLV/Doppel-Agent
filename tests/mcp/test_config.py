import json
import tempfile
import unittest
from pathlib import Path

from doppel_agent.mcp.config import load_mcp_config


class MCPConfigTests(unittest.TestCase):
    def test_loads_stdio_and_streamable_http_without_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / ".doppel" / "mcp.json"
            config.parent.mkdir()
            config.write_text(
                json.dumps(
                    {
                        "servers": {
                            "local": {
                                "transport": "stdio",
                                "command": "C:/Python/python.exe",
                                "args": ["C:/tools/server.py"],
                                "env_names": ["SERVICE_TOKEN"],
                                "max_concurrency": 2,
                            },
                            "remote": {
                                "transport": "streamable_http",
                                "url": "https://example.com/mcp",
                                "auth_profile": "example-oauth",
                                "max_concurrency": 4,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_mcp_config(root)
            self.assertEqual(loaded.servers["local"].env_names, ("SERVICE_TOKEN",))
            self.assertEqual(loaded.servers["remote"].transport, "streamable_http")

    def test_rejects_inline_headers_and_insecure_remote_http(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / ".doppel" / "mcp.json"
            config.parent.mkdir()
            config.write_text(
                '{"servers":{"bad":{"transport":"streamable_http","url":"http://example.com/mcp","headers":{"Authorization":"secret"}}}}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_mcp_config(root)
