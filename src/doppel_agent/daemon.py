"""Localhost JSON-RPC 2.0 over NDJSON. One request per connection in P0."""

from __future__ import annotations

import asyncio
import hmac
import json
import os

from .core import Core
from .protocol import ProtocolError, error, parse_request, result


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, core: Core) -> None:
    request_id = None
    try:
        raw = await reader.readline()
        if not raw or len(raw) > 128 * 1024:
            raise ProtocolError(-32600, "Invalid Request")
        request = parse_request(raw.decode("utf-8"))
        request_id = request.get("id")
        if request["method"] == "ping":
            response = result(request_id, {"status": "ok"})
        elif request["method"] == "run":
            required_token = os.getenv("DOPPEL_AGENT_RPC_TOKEN", "")
            supplied_token = request.get("params", {}).get("token", "")
            if required_token and (not isinstance(supplied_token, str) or not hmac.compare_digest(required_token, supplied_token)):
                raise ProtocolError(-32001, "Unauthorized", request_id)
            prompt = request.get("params", {}).get("prompt")
            if not isinstance(prompt, str):
                raise ProtocolError(-32602, "prompt must be a string", request_id)
            response = result(request_id, await asyncio.to_thread(core.run, prompt))
        else:
            response = error(request_id, -32601, "Method not found")
    except ProtocolError as exc:
        response = error(exc.request_id, exc.code, str(exc))
    except (UnicodeError, ValueError) as exc:
        response = error(request_id, -32600, f"Invalid Request: {exc}")
    except Exception:
        response = error(request_id, -32603, "Internal error")
    writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def serve(core: Core, port: int) -> None:
    server = await asyncio.start_server(lambda reader, writer: handle_client(reader, writer, core), "127.0.0.1", port, limit=128 * 1024)
    async with server:
        await server.serve_forever()
