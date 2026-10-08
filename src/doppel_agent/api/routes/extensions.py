"""S7 passive cache and explicitly confirmed discovery, not a tool executor.

Compatibility GET /mcp/.../tools is also cache-only; discovery requires POST.
Remote text remains sensitive/untrusted, not S8-redacted.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ...mcp.types import MCPToolDescriptor

HEADERS = {"Cache-Control": "no-store"}
TOOL_LIMIT = 100


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate member")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("non-finite input")


class ExtensionRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                query = request.scope.get("query_string", b"")
                if len(query) > 1024:
                    return JSONResponse({"detail": "extension_request_budget"}, 413, headers=HEADERS)
                if query:
                    raise ValueError("no query authority")
                if request.method == "POST":
                    chunks, size = [], 0
                    async with asyncio.timeout(5):
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > 1024:
                                return JSONResponse({"detail": "extension_request_budget"}, 413, headers=HEADERS)
                            chunks.append(chunk)
                    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        raise ValueError("JSON required")
                    value = json.loads(b"".join(chunks), object_pairs_hook=_pairs, parse_constant=_constant)
                    if (not isinstance(value, dict) or set(value) != {"action", "confirmed"}
                            or value["confirmed"] is not True or value["action"] not in ("probe", "refresh", "reload_skills")):
                        raise ValueError("explicit confirmation required")
                    request.state.extension_action = value["action"]
                response = await original(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except (ValueError, RecursionError):
                return JSONResponse({"detail": "invalid_extension_request"}, 422, headers=HEADERS)
            except TimeoutError:
                return JSONResponse({"detail": "extension_request_timeout"}, 408, headers=HEADERS)
            except HTTPException:
                raise
            except Exception:
                return JSONResponse({"detail": "extension_service_unavailable"}, 503, headers=HEADERS)
        return handle


router = APIRouter(prefix="/extensions", tags=["extensions"], route_class=ExtensionRoute)


def _text(value: str, limit: int) -> str:
    # Bound before encoding so a huge remote description cannot create a second
    # huge transient byte buffer. Plain text only; no HTML/markdown rendering.
    return value[:limit].encode("utf-8", errors="replace")[:limit].decode("utf-8", errors="ignore")


def tool_snapshot(name: str, state: str, tools: tuple[MCPToolDescriptor, ...] | None) -> dict:
    projected = []
    for item in (tools or ())[:TOOL_LIMIT]:
        fields = {"logical_name": _text(item.logical_name, 256), "remote_name": _text(item.remote_name, 256),
                  "title": _text(item.title, 256), "description": _text(item.description, 512)}
        fields["text_truncated"] = any(fields[key] != getattr(item, key) for key in fields)
        fields["schema_hash"] = _text(item.schema_hash, 64)
        projected.append(fields)
    return {"server": name, "cache_state": state, "tools": projected,
            "total": len(tools) if tools is not None else None, "limit": TOOL_LIMIT,
            "truncated": tools is not None and len(tools) > TOOL_LIMIT,
            "tool_execution_verified": False,
            "output": {"sensitive": True, "globally_redacted": False, "remote_text_trusted": False}}


def skill_snapshot(catalog: dict) -> dict:
    # Registry validation may read SKILL.md bodies locally; none are returned to
    # this page or executed. Reuse its original all-or-nothing reload semantics.
    skills, warnings = catalog["skills"], catalog["warnings"]
    return {"skills": [{"name": item["name"], "description": _text(item["description"], 512),
                        "text_truncated": _text(item["description"], 512) != item["description"]}
                       for item in skills[:100]],
            "total": len(skills), "limit": 100, "truncated": len(skills) > 100,
            "warnings": [_text(item, 256) for item in warnings[:50]],
            "warning_total": len(warnings), "warning_truncated": len(warnings) > 50,
            "output": {"sensitive": True, "globally_redacted": False, "text_trusted": False,
                       "instructions_returned": False}}


async def _skills(request: Request, *, reload: bool):
    try:
        return skill_snapshot(await request.app.state.run_service.skill_catalog(reload=reload))
    except (ValueError, OSError):
        raise HTTPException(422, "extension_skill_catalog_invalid", headers=HEADERS) from None


@router.get("/skills")
async def skill_headers(request: Request):
    return await _skills(request, reload=False)


@router.post("/skills/reload")
async def reload_skills(request: Request):
    if request.state.extension_action != "reload_skills":
        raise ValueError("wrong explicit action")
    return {"action": "reload_skills", **await _skills(request, reload=True)}


@router.get("/mcp/servers")
async def servers(request: Request):
    return {"servers": await request.app.state.run_service.extension_servers()}


@router.get("/mcp/servers/{name}/tools/cached")
async def cached_tools(name: str, request: Request):
    try:
        state, tools = await request.app.state.run_service.extension_cached_tools(name)
        return tool_snapshot(name, state, tools)
    except KeyError:
        raise HTTPException(404, "extension_server_not_found", headers=HEADERS) from None


@router.post("/mcp/servers/{name}/discovery")
async def discover(name: str, request: Request):
    service = request.app.state.run_service
    if name not in service.mcp_config.servers:
        raise HTTPException(404, "extension_server_not_found", headers=HEADERS)
    action = request.state.extension_action
    if action not in ("probe", "refresh"):
        raise ValueError("wrong explicit action")
    try:
        value = await service.mcp_discover(name, probe=action == "probe", refresh=action == "refresh")
        if action == "probe":
            # health() calls list_tools: this proves only that probe completed,
            # NOT any tool execution, remote safety, new catalog or a grant.
            return {"server": name, "action": action, "probe_completed": True,
                    "protocol_version": _text(value.protocol_version, 128), "tool_execution_verified": False}
        return {"action": action, **tool_snapshot(name, "cached", value)}
    except TimeoutError:
        raise HTTPException(504, "extension_discovery_timed_out", headers=HEADERS) from None
    except Exception:
        raise HTTPException(502, "extension_discovery_failed", headers=HEADERS) from None
