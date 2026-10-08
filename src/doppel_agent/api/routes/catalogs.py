"""Explicit, loopback-protected Skill/MCP management (never automatic probes)."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request, Response

from .extensions import router as extensions_router

router = APIRouter()


@router.get("/skills")
async def skills(request: Request):
    return await _skills(request, reload=False)


@router.post("/skills/reload")
async def reload_skills(request: Request):
    return await _skills(request, reload=True)


async def _skills(request: Request, *, reload: bool):
    try:
        return await request.app.state.run_service.skill_catalog(reload=reload)
    except (ValueError, OSError):
        raise HTTPException(422, "skill catalog validation failed") from None


@router.get("/mcp/servers")
async def servers(request: Request):
    # Do not expose arguments, URLs, cwd, env values or authentication material.
    return {"servers": [
        {"name": s.name, "transport": s.transport, "max_concurrency": s.max_concurrency}
        for s in request.app.state.run_service.mcp_config.servers.values()
    ]}


async def _mcp(request: Request, name: str, *, probe: bool):
    service = request.app.state.run_service
    if name not in service.mcp_config.servers:
        raise HTTPException(404, "MCP server not found")
    try:
        value = await service.mcp_discover(name, probe=probe)
        return asdict(value) if probe else {"tools": [asdict(item) for item in value]}
    except TimeoutError:
        raise HTTPException(504, "MCP discovery timed out") from None
    except Exception:
        # A connector exception can contain URLs, env/auth material or args.
        raise HTTPException(502, "MCP discovery failed") from None


@router.post("/mcp/servers/{name}/probe")
async def probe_server(name: str, request: Request):
    return await _mcp(request, name, probe=True)


@router.get("/mcp/servers/{name}/tools")
async def server_tools(name: str, request: Request, response: Response):
    # Compatibility reads are passive too: discovery belongs to an explicit POST,
    # never an origin-less GET. Keep the legacy descriptor fields for old clients.
    try:
        state, tools = await request.app.state.run_service.extension_cached_tools(name)
        response.headers['Cache-Control'] = 'no-store'
        return {"cache_state": state, "tools": [asdict(item) for item in (tools or ())]}
    except KeyError:
        raise HTTPException(404, "MCP server not found") from None
    except Exception:
        raise HTTPException(503, "MCP catalog unavailable") from None


# Keep old clients compatible; the workbench uses the new passive/confirmed paths.
router.include_router(extensions_router)
