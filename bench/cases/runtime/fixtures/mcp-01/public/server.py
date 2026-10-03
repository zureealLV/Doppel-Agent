"""Finite JSON-lines MCP stdio fixture; external trace is not Agent input."""
import json
import os
from pathlib import Path
import sys

trace = Path(sys.argv[1])


def record(value):
    with trace.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


record({"kind": "start", "pid": os.getpid()})
tools = [
    {"name": "echo", "description": "Echo a text value.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}},
    {"name": "structured", "description": "Return structured-only content.", "inputSchema": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"], "additionalProperties": False}},
    {"name": "failure", "description": "Return an explicit expected server error.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}},
]
for line in sys.stdin:
    request = json.loads(line)
    method, params = request.get("method"), request.get("params") or {}
    if "id" not in request:
        continue
    if method == "initialize":
        result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "doppel-matrix-local", "version": "1.0"}}
    elif method == "tools/list":
        record({"kind": "catalog"})
        result = {"tools": tools}
    elif method == "tools/call":
        name, arguments = params["name"], params.get("arguments") or {}
        record({"kind": "call", "name": name, "arguments": arguments})
        if name == "echo":
            result = {"content": [{"type": "text", "text": arguments["text"]}], "structuredContent": {"echo": arguments["text"]}, "isError": False}
        elif name == "structured":
            result = {"content": [], "structuredContent": {"count": arguments["value"], "tag": "structured"}, "isError": False}
        elif name == "failure":
            result = {"content": [{"type": "text", "text": "fixture failure"}], "structuredContent": {"code": "EXPECTED", "detail": arguments["text"]}, "isError": True}
        else:
            raise ValueError("unexpected fixture tool")
    elif method == "ping":
        result = {}
    else:
        raise ValueError("unexpected fixture method")
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
