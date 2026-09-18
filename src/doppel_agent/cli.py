"""Minimal CLI client and daemon entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .core import Core
from .daemon import serve
from .provider import MockProvider, OpenAICompatibleProvider
from .web.server import serve_ui
from .tasks.manager import TaskManager


async def rpc_run(port: int, prompt: str) -> dict:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "run",
        "params": {"prompt": prompt, "token": os.getenv("DOPPEL_AGENT_RPC_TOKEN", "")},
    }
    writer.write((json.dumps(request) + "\n").encode("utf-8"))
    await writer.drain()
    response = await reader.readline()
    writer.close()
    await writer.wait_closed()
    if not response:
        raise RuntimeError("daemon closed connection without a response")
    return json.loads(response)


def main() -> None:
    parser = argparse.ArgumentParser(prog="doppel-agent")
    parser.add_argument("command", choices=("demo", "ask", "serve", "run", "doctor", "ui", "tasks"))
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--port", type=int)
    parser.add_argument("--provider", choices=("mock", "openai"))
    parser.add_argument("--base-url", default=os.getenv("DOPPEL_AGENT_BASE_URL", ""))
    parser.add_argument("--model", default=os.getenv("DOPPEL_AGENT_MODEL", ""))
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--allow-command", action="store_true")
    args = parser.parse_args()
    port = args.port or (8766 if args.command == "ui" else 8765)
    if args.command in ("demo", "ask", "run") and not args.prompt:
        parser.error("prompt is required for demo/ask/run")
    if args.command == "doctor":
        print(json.dumps({
            "workspace": str(args.workspace.resolve()),
            "python": __import__("sys").version.split()[0],
            "base_url_configured": bool(args.base_url),
            "model_configured": bool(args.model),
            "api_key_configured": bool(os.getenv("DOPPEL_AGENT_API_KEY")),
        }, ensure_ascii=False, indent=2))
        return
    if args.command == "ui":
        serve_ui(args.workspace, port)
        return
    if args.command == "tasks":
        if not args.prompt:
            parser.error("tasks requires a run ID")
        manager = TaskManager(args.workspace.resolve() / ".doppel-agent" / "tasks.sqlite3")
        print(json.dumps(manager.list(args.prompt), ensure_ascii=False, indent=2))
        return
    if args.command in ("demo", "ask", "serve"):
        provider_name = args.provider or ("openai" if args.command == "ask" else "mock")
        if provider_name == "openai":
            if not args.base_url or not args.model:
                parser.error("--base-url and --model are required for --provider openai")
            provider = OpenAICompatibleProvider(
                args.base_url, args.model, os.getenv("DOPPEL_AGENT_API_KEY", "")
            )
        else:
            provider = MockProvider()
        core = Core(args.workspace, provider, allow_write=args.allow_write, allow_command=args.allow_command)
        if args.command in ("demo", "ask"):
            result = core.run(args.prompt)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result["status"] != "completed":
                sys.exit(1)
        else:
            if (args.allow_write or args.allow_command) and not os.getenv("DOPPEL_AGENT_RPC_TOKEN"):
                parser.error("daemon with write/command grants requires DOPPEL_AGENT_RPC_TOKEN")
            print(f"Doppel Agent listening on 127.0.0.1:{port}", flush=True)
            asyncio.run(serve(core, port))
    else:
        response = asyncio.run(rpc_run(port, args.prompt))
        print(json.dumps(response, ensure_ascii=False, indent=2))
        if "error" in response or response.get("result", {}).get("status") != "completed":
            sys.exit(1)


if __name__ == "__main__":
    main()
