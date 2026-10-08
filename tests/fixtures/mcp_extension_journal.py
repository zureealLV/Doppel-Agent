"""S7 disposable SDK fixture. Definitions only until S9, never auto-start/import IO.

Each journal belongs exclusively to one fixture. Not OS process-identity/drain
proof; S9 native gates must bind original process handles independently.
"""

import json
import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer


def entries(journal: Path) -> list[dict]:
    if not journal.exists():
        return []
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]


def record(journal: Path, kind: str, **payload) -> None:
    # Fixed disposable file, one fixture writer. No stdout corrupting stdio JSON-RPC.
    with journal.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"kind": kind, "pid": os.getpid(), **payload}) + "\n")


def build_server(journal: Path, *, fail_list: bool = False) -> MCPServer:
    class JournalServer(MCPServer):
        async def list_tools(self):
            record(journal, "list")
            if fail_list:
                raise ValueError("fixture-list-error")
            return await super().list_tools()

    server = JournalServer("doppel-s7-journal", version="1.0")

    @server.tool()
    def echo(text: str) -> str:
        """Echo fixture text, or return a deterministic MCP tool error."""
        record(journal, "call", tool="echo", text=text)
        if text == "fixture-error":
            raise ValueError("fixture-tool-error")
        return "fixture-echo:" + text

    return server


if __name__ == "__main__":
    journal = Path(sys.argv[1]).resolve(strict=False)
    record(journal, "started")
    if len(sys.argv) > 2 and sys.argv[2] == "fail-start":
        # Fixed failure, no user/provider exception material.
        raise SystemExit(3)
    build_server(journal, fail_list=len(sys.argv) > 2 and sys.argv[2] == "fail-list").run(transport="stdio")
