"""MCP tool execution with schema, policy, idempotency and content handling."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from ..permissions import PermissionManager
from ..persistence.tool_ledger import ToolExecutionLedger
from .catalog import MCPToolCatalog
from .client_manager import MCPClientManager
from .types import MCPExecutionResult, MCPToolDescriptor


AuditSink = Callable[[dict[str, Any]], Awaitable[None] | None]


def _sanitized(value: Any, *, key: str = "") -> Any:
    lowered = key.casefold()
    if any(marker in lowered for marker in ("token", "secret", "password", "api_key", "authorization")):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): _sanitized(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_sanitized(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:1000] + ("..." if len(value) > 1000 else "")
    return value


def _validate_type(name: str, value: Any, expected: str) -> None:
    valid = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "null": value is None,
    }.get(expected, True)
    if not valid:
        raise ValueError(f"MCP argument {name} must be {expected}")


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("MCP tool arguments must be an object")
    if schema.get("type", "object") != "object":
        raise ValueError("MCP tool root schema must be an object")
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ValueError("invalid MCP input schema")
    missing = [name for name in required if name not in arguments]
    if missing:
        raise ValueError(f"missing MCP arguments: {', '.join(missing)}")
    if schema.get("additionalProperties") is False:
        extra = set(arguments) - set(properties)
        if extra:
            raise ValueError(f"unknown MCP arguments: {', '.join(sorted(extra))}")
    for name, value in arguments.items():
        field = properties.get(name)
        if isinstance(field, dict) and isinstance(field.get("type"), str):
            _validate_type(name, value, field["type"])


class MCPToolExecutor:
    def __init__(
        self,
        manager: MCPClientManager,
        catalog: MCPToolCatalog,
        permissions: PermissionManager,
        *,
        ledger: ToolExecutionLedger | None = None,
        audit: AuditSink | None = None,
    ) -> None:
        self.manager = manager
        self.catalog = catalog
        self.permissions = permissions
        self.ledger = ledger
        self.audit = audit

    @staticmethod
    def _normalize(descriptor: MCPToolDescriptor, result: Any, audit_id: str) -> MCPExecutionResult:
        blocks: list[dict[str, Any]] = []
        model_parts: list[str] = []
        for content in getattr(result, "content", []) or []:
            kind = getattr(content, "type", type(content).__name__)
            if hasattr(content, "model_dump"):
                payload = content.model_dump(mode="json", by_alias=True, exclude_none=True)
            else:
                payload = {"type": kind, "value": str(content)}
            blocks.append(payload)
            if kind == "text":
                model_parts.append(str(getattr(content, "text", "")))
            elif kind in {"image", "audio"}:
                data = str(getattr(content, "data", ""))
                model_parts.append(
                    f"[{kind}: {getattr(content, 'mime_type', 'application/octet-stream')}, base64_chars={len(data)}]"
                )
            elif kind == "resource_link":
                model_parts.append(
                    f"[resource: {getattr(content, 'name', '')} {getattr(content, 'uri', '')}]"
                )
            elif kind == "resource":
                resource = getattr(content, "resource", None)
                uri = getattr(resource, "uri", "")
                text = getattr(resource, "text", None)
                model_parts.append(f"[embedded resource: {uri}]" + (f"\n{text[:32000]}" if text else ""))
            else:
                model_parts.append(f"[{kind} content]")
        structured = getattr(result, "structured_content", None)
        is_error = bool(getattr(result, "is_error", False))
        model_view = "\n".join(part for part in model_parts if part).strip()
        if not model_view and structured is not None:
            model_view = json.dumps(structured, ensure_ascii=False, default=str)[:32000]
        if not model_view:
            model_view = "MCP tool returned no content."
        error = model_view if is_error else None
        return MCPExecutionResult(
            success=not is_error,
            logical_name=descriptor.logical_name,
            model_view=model_view,
            application_view={"content": blocks, "structured_content": structured, "is_error": is_error},
            audit_id=audit_id,
            error=error,
        )

    async def _audit(self, payload: dict[str, Any]) -> None:
        if self.audit is None:
            return
        result = self.audit(payload)
        if asyncio.iscoroutine(result):
            await result

    async def execute(
        self,
        logical_name: str,
        arguments: dict[str, Any],
        *,
        run_id: str,
        tool_call_id: str,
    ) -> MCPExecutionResult:
        descriptor = await self.catalog.get(logical_name)
        validate_arguments(descriptor.input_schema, arguments)
        decision = self.permissions.check("mcp_execute", logical_name, arguments)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        audit_id = uuid4().hex

        async def operation() -> str:
            async def invoke(session: Any) -> Any:
                return await session.call_tool(descriptor.remote_name, arguments=arguments)

            # Never auto-retry a tool call: transport failure after the server
            # accepted a side effect is ambiguous. The session is invalidated,
            # but a human/new run must decide whether to try again.
            raw = await self.manager.call(descriptor.server_name, invoke, reconnect=False)
            normalized = self._normalize(descriptor, raw, audit_id)
            await self._audit(
                {
                    "audit_id": audit_id,
                    "run_id": run_id,
                    "tool_call_id": tool_call_id,
                    "logical_name": logical_name,
                    "arguments": _sanitized(arguments),
                    "success": normalized.success,
                    "error": normalized.error,
                }
            )
            return json.dumps(normalized.to_dict(), ensure_ascii=False, default=str)

        if self.ledger is None:
            payload = await operation()
            return MCPExecutionResult.from_dict(json.loads(payload))
        payload, replayed = await self.ledger.aexecute_once(
            run_id,
            tool_call_id,
            logical_name,
            arguments,
            operation,
        )
        result = MCPExecutionResult.from_dict(json.loads(payload))
        if replayed:
            result = MCPExecutionResult(**{**result.to_dict(), "replayed": True})
        return result
