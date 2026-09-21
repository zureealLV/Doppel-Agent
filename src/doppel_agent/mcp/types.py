"""Transport-neutral MCP gateway values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MCPServerMetadata:
    name: str
    protocol_version: str
    server_name: str
    server_version: str
    capabilities: dict[str, Any]
    config_hash: str

    @property
    def cache_key(self) -> str:
        return ":".join(
            (self.name, self.protocol_version, self.server_name, self.server_version, self.config_hash)
        )


@dataclass(frozen=True)
class MCPToolDescriptor:
    logical_name: str
    server_name: str
    remote_name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    schema_hash: str = ""


@dataclass(frozen=True)
class MCPExecutionResult:
    success: bool
    logical_name: str
    model_view: str
    application_view: dict[str, Any]
    replayed: bool = False
    audit_id: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "logical_name": self.logical_name,
            "model_view": self.model_view,
            "application_view": self.application_view,
            "replayed": self.replayed,
            "audit_id": self.audit_id,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MCPExecutionResult":
        return cls(
            success=bool(value["success"]),
            logical_name=str(value["logical_name"]),
            model_view=str(value["model_view"]),
            application_view=dict(value["application_view"]),
            replayed=bool(value.get("replayed", False)),
            audit_id=str(value.get("audit_id", "")),
            error=value.get("error"),
        )


@dataclass
class ManagedMCPSession:
    session: Any
    metadata: MCPServerMetadata
    semaphore: Any
    valid: bool = True
    extras: dict[str, Any] = field(default_factory=dict)
