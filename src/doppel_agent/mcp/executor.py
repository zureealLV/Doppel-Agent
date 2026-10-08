"""MCP tool execution with schema, policy, idempotency and content handling."""

from __future__ import annotations

import asyncio
import json
from inspect import isawaitable
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from ..permissions import PermissionManager
from ..owned_async import await_durable
from ..persistence.evidence_json import loads as evidence_loads
from ..persistence.tool_ledger import ToolExecutionLedger, ToolLedgerAdmissionError
from .catalog import MCPToolCatalog
from .client_manager import MCPAdmissionError, MCPClientManager, MCPCleanupError, MCPInvocationError, MCPPublicationError
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
    def _acknowledgement(result: Any) -> Any:
        # Actual locked SDK returns a typed terminal CallToolResult. Neither
        # duck-typed objects, None, nor claimed/input-required results are an
        # application-supported completion acknowledgement. Revalidate data,
        # including bypassed model_construct/mutated fields, not bool coercion.
        from mcp import types

        if not isinstance(result, types.CallToolResult) or result.result_type != 'complete':
            raise MCPInvocationError()
        return types.CallToolResult.model_validate(result.model_dump(mode='python', by_alias=True), strict=True)

    @staticmethod
    def _restore(payload: str, logical_name: str) -> MCPExecutionResult:
        if not isinstance(payload, str) or len(payload.encode('utf-8')) > 8 * 1024 * 1024:
            raise MCPPublicationError()
        value = evidence_loads(payload)
        # parse_constant alone cannot reject a finite-spelled exponent that
        # overflows to inf, nor an escaped lone surrogate on historical replay.
        # Validate actual decoded JSON/UTF8 values, without default conversion.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        fields = {'success', 'logical_name', 'model_view', 'application_view', 'replayed', 'audit_id', 'error'}
        if (type(value) is not dict or set(value) != fields or type(value['success']) is not bool
                or type(value['replayed']) is not bool or value['logical_name'] != logical_name
                or type(value['model_view']) is not str or type(value['audit_id']) is not str
                or len(value['audit_id']) != 32 or any(c not in '0123456789abcdef' for c in value['audit_id'])):
            raise MCPPublicationError()
        application = value['application_view']
        if (type(application) is not dict or set(application) != {'content', 'structured_content', 'is_error'}
                or type(application['content']) is not list or type(application['is_error']) is not bool
                or application['is_error'] is value['success']
                or any(type(block) is not dict or type(block.get('type')) is not str for block in application['content'])
                or (value['success'] and value['error'] is not None)
                or (not value['success'] and type(value['error']) is not str)):
            raise MCPPublicationError()
        return MCPExecutionResult.from_dict(value)

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
            model_view = json.dumps(structured, ensure_ascii=False, allow_nan=False)[:32000]
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
        if isawaitable(result):
            async def original_audit():
                try:
                    return await result
                except BaseException:
                    self.manager._mark_publication_failed()
                    raise
            # Join SAME original audit before propagating caller cancellation.
            # This is not physical detached callback/transport drain proof.
            await await_durable(original_audit(), on_cancel=self.manager._mark_publication_failed)

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
        source = self.catalog.source_for_descriptor(descriptor)
        audit_id = uuid4().hex
        reply_returned = False

        async def operation() -> str:
            async def invoke(session: Any) -> Any:
                nonlocal reply_returned
                # Ledger/semaphore waits may outlive discovery or an explicit
                # refresh. Validate SAME selected descriptor again inside the
                # original lease, without reinterpreting input/permission under
                # a newer schema or automatically refreshing before effects.
                if self.catalog.source_for_descriptor(descriptor) is not source:
                    raise MCPAdmissionError()
                try:
                    # Observe BEFORE SDK call construction/await. SDK exceptions,
                    # including cancellation/deadline, cannot assert "not sent".
                    raw = await session.call_tool(descriptor.remote_name, arguments=arguments)
                    acknowledged = self._acknowledgement(raw)
                    reply_returned = True  # Typed final reply, not independent remote-effect proof.
                    return acknowledged
                except BaseException:
                    self.manager._mark_execution_failed()
                    raise MCPInvocationError() from None

            # Never auto-retry a tool call: transport failure after the server
            # accepted a side effect is ambiguous. SAME owner is stopped before
            # publication; same SDK exit may succeed, not clear remote unknown.
            try:
                raw = await self.manager.call(descriptor.server_name, invoke, reconnect=False,
                                              expected_source=source)
            except (MCPCleanupError, MCPAdmissionError, MCPInvocationError, MCPPublicationError):
                # Resource uncertainty is fatal to the original owner. Never
                # downgrade it to a recoverable transport/tool error or audit
                # successful normalization; the original ledger records failure.
                # Known closing/stale admission is also fatal but does NOT set
                # the manager's cleanup-uncertainty latch.
                # Entered remote outcome failure remains separate from actual
                # resource exit failure; no ordinary recoverable tool error.
                raise
            except Exception as exc:
                # Transport errors may contain authenticated URLs, headers or
                # credentials. Keep a stable class-only boundary; never retry.
                raise ConnectionError(f"MCP invocation failed ({type(exc).__name__})") from None
            try:
                normalized = self._normalize(descriptor, raw, audit_id)
                # Complete local framing BEFORE a success audit; no default=str
                # or non-finite JSON as fabricated application success.
                payload = json.dumps(normalized.to_dict(), ensure_ascii=False, allow_nan=False)
                self._restore(payload, logical_name)
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
                return payload
            except BaseException:
                self.manager._mark_publication_failed()
                raise MCPPublicationError() from None

        try:
            if self.ledger is None:
                payload, replayed = await operation(), False
            else:
                payload, replayed = await self.ledger.aexecute_once(
                    run_id, tool_call_id, logical_name, arguments, operation,
                    on_failure=self.manager._mark_publication_failed,
                )
            result = self._restore(payload, logical_name)
            if replayed:
                result = MCPExecutionResult(**{**result.to_dict(), "replayed": True})
            self.manager._check_admission()  # No late success after SAME owner fault.
            return result
        except BaseException as exc:
            if self.manager.cleanup_failed:
                raise MCPCleanupError() from None
            if self.manager.execution_failed:
                raise MCPInvocationError() from None
            if not self.manager.publication_failed:
                if isinstance(exc, (MCPAdmissionError, ToolLedgerAdmissionError)):
                    raise  # Known refused entry/input, not lost publication.
                if isinstance(exc, asyncio.CancelledError) and self.ledger is None and not reply_returned:
                    raise  # Pending pre-entry caller, no local durable reservation.
            self.manager._mark_publication_failed()
            raise MCPPublicationError() from None
