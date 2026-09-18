"""Small JSON-RPC 2.0 subset used over newline-delimited JSON."""

from __future__ import annotations

import json
from typing import Any


class ProtocolError(ValueError):
    def __init__(self, code: int, message: str, request_id: Any = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


def parse_request(line: str) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(-32700, "Parse error") from exc
    if not isinstance(value, dict):
        raise ProtocolError(-32600, "Invalid Request")
    request_id = value.get("id")
    if (
        value.get("jsonrpc") != "2.0"
        or not isinstance(value.get("method"), str)
        or not isinstance(value.get("params", {}), dict)
        or isinstance(request_id, (dict, list, bool))
    ):
        raise ProtocolError(-32600, "Invalid Request", request_id)
    return value


def result(request_id: Any, payload: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
