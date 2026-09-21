"""Validated MCP server configuration without embedded secret values."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse


_SERVER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: Literal["stdio", "streamable_http"]
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env_names: tuple[str, ...] = ()
    url: str | None = None
    auth_profile: str | None = None
    max_concurrency: int = 2

    def identity_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class MCPConfig:
    servers: dict[str, MCPServerConfig]
    source: Path


def _only_keys(value: dict[str, Any], allowed: set[str], context: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"unknown {context} fields: {', '.join(sorted(extra))}")


def _parse_server(name: str, value: Any) -> MCPServerConfig:
    if not _SERVER_NAME.fullmatch(name) or "__" in name:
        raise ValueError(f"invalid MCP server name: {name}")
    if not isinstance(value, dict):
        raise ValueError(f"MCP server {name} must be an object")
    # v0.3-v0.9 configs omitted transport because only stdio existed.
    transport = value.get("transport", "stdio" if "command" in value else None)
    common = {"transport", "max_concurrency"}
    maximum = value.get("max_concurrency", 2)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 32:
        raise ValueError(f"MCP server {name} max_concurrency must be 1..32")
    if transport == "stdio":
        _only_keys(value, common | {"command", "args", "cwd", "env_names"}, f"stdio server {name}")
        command = value.get("command")
        args = value.get("args", [])
        env_names = value.get("env_names", [])
        cwd = value.get("cwd")
        if not isinstance(command, str) or not Path(command).is_absolute():
            raise ValueError(f"MCP stdio server {name} requires an absolute command path")
        if not isinstance(args, list) or not all(isinstance(item, str) and "\x00" not in item for item in args):
            raise ValueError(f"MCP stdio server {name} args must be strings")
        if not isinstance(env_names, list) or not all(
            isinstance(item, str) and _ENV_NAME.fullmatch(item) for item in env_names
        ):
            raise ValueError(f"MCP stdio server {name} env_names are invalid")
        if cwd is not None and (not isinstance(cwd, str) or not Path(cwd).is_absolute()):
            raise ValueError(f"MCP stdio server {name} cwd must be absolute")
        return MCPServerConfig(
            name,
            "stdio",
            command=command,
            args=tuple(args),
            cwd=cwd,
            env_names=tuple(env_names),
            max_concurrency=maximum,
        )
    if transport == "streamable_http":
        _only_keys(value, common | {"url", "auth_profile"}, f"HTTP server {name}")
        url = value.get("url")
        auth_profile = value.get("auth_profile")
        if not isinstance(url, str):
            raise ValueError(f"MCP HTTP server {name} requires url")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"MCP HTTP server {name} url is invalid")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError(f"remote MCP server {name} must use HTTPS")
        if auth_profile is not None and (
            not isinstance(auth_profile, str) or not _SERVER_NAME.fullmatch(auth_profile)
        ):
            raise ValueError(f"MCP HTTP server {name} auth_profile is invalid")
        return MCPServerConfig(
            name,
            "streamable_http",
            url=url,
            auth_profile=auth_profile,
            max_concurrency=maximum,
        )
    raise ValueError(f"MCP server {name} has unsupported transport")


def load_mcp_config(workspace: Path, path: Path | None = None) -> MCPConfig:
    root = workspace.resolve(strict=True)
    source = path or root / ".doppel" / "mcp.json"
    if not source.exists():
        return MCPConfig({}, source)
    if source.is_symlink() or source.stat().st_size > 256 * 1024:
        raise ValueError("MCP config must be a small regular file")
    try:
        source.resolve(strict=True).relative_to(root)
    except ValueError as exc:
        raise ValueError("MCP config must stay inside the workspace") from exc
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("MCP config root must be an object")
    _only_keys(payload, {"servers"}, "MCP config")
    raw_servers = payload.get("servers", {})
    if not isinstance(raw_servers, dict):
        raise ValueError("MCP servers must be an object")
    if len(raw_servers) > 32:
        raise ValueError("MCP config contains too many servers")
    servers = {name: _parse_server(name, value) for name, value in raw_servers.items()}
    return MCPConfig(servers, source)
