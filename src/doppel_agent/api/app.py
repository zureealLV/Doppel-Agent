"""FastAPI application factory with an owned runtime lifespan."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .. import __version__
from ..runtime.service import RunService
from ..runtime.provider_recording import ProviderReceiptError
from ..settings import SettingsPersistenceError
from ..mcp.client_manager import MCPAdmissionError, MCPCleanupError, MCPInvocationError, MCPPublicationError
from ..persistence.tool_ledger import ToolLedgerPersistenceError
from ..persistence.ownership import BorrowedWorkspaceOwner
from .routes.runs import router as runs_router
from .routes.subagents import router as subagents_router
from .routes.subagent_review import router as subagent_review_router
from .routes.conversations import router as conversations_router
from .routes.catalogs import router as catalogs_router
from .routes.projects import router as projects_router
from .routes.work_orders import router as work_orders_router
from .routes.context import router as context_router
from .routes.changes import router as changes_router
from .routes.reports import router as reports_router


def create_app(
    workspace: Path,
    *,
    provider: Any | None = None,
    max_active_runs: int = 4,
    queue_capacity: int = 100,
    approval_ttl_seconds: int = 900,
    legacy_base_url: str | None = None,
    workspace_owner: BorrowedWorkspaceOwner | None = None,
) -> FastAPI:
    service = RunService(
        workspace,
        provider=provider,
        max_active_runs=max_active_runs,
        queue_capacity=queue_capacity,
        approval_ttl_seconds=approval_ttl_seconds,
        workspace_owner=workspace_owner,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime_lifespan_entered = True
        await service.start()
        app.state.run_service = service
        app.state.runtime_loop = asyncio.get_running_loop()
        app.state.runtime_thread_id = threading.get_ident()
        try:
            yield
        finally:
            try:
                await service.close()
            finally:
                app.state.runtime_loop = None
                app.state.runtime_thread_id = None

    app = FastAPI(
        title="Doppel Agent Runtime API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.run_service = service
    app.state.runtime_lifespan_entered = False
    app.state.runtime_loop = None
    app.state.runtime_thread_id = None

    @app.exception_handler(ProviderReceiptError)
    async def provider_quarantine(_request: Request, _error: ProviderReceiptError):
        # Exact original safe error; no raw provider/key/path/exception. Does not
        # start/retry/reconcile an unknown invocation or manufacture a close.
        return JSONResponse({'error': 'provider_receipt_unavailable'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})

    @app.exception_handler(SettingsPersistenceError)
    async def settings_unavailable(_request: Request, _error: SettingsPersistenceError):
        return JSONResponse({'error': 'provider_settings_persistence_unavailable'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})

    @app.exception_handler(MCPCleanupError)
    async def mcp_cleanup_unresolved(_request: Request, _error: MCPCleanupError):
        return JSONResponse({'error': 'mcp_cleanup_unresolved'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})

    @app.exception_handler(MCPAdmissionError)
    async def mcp_admission_closed(_request: Request, _error: MCPAdmissionError):
        return JSONResponse({'error': 'mcp_admission_closed'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})

    @app.exception_handler(MCPInvocationError)
    async def mcp_execution_unresolved(_request: Request, _error: MCPInvocationError):
        return JSONResponse({'error': 'mcp_execution_unresolved'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})

    @app.exception_handler(MCPPublicationError)
    async def mcp_publication_unresolved(_request: Request, _error: MCPPublicationError):
        return JSONResponse({'error': 'mcp_publication_unresolved'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})

    @app.exception_handler(ToolLedgerPersistenceError)
    async def tool_ledger_unresolved(_request: Request, _error: ToolLedgerPersistenceError):
        return JSONResponse({'error': 'tool_ledger_unresolved'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        changes = request.url.path.startswith(("/api/v1/changes", "/api/v1/extensions", "/api/v1/subagent-review", "/api/v1/reports"))
        headers = {"Cache-Control": "no-store"} if changes else None
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"error": "invalid host"}, status_code=403, headers=headers)
        origin = request.headers.get("origin")
        if origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"error": "invalid origin"}, status_code=403, headers=headers)
        response = await call_next(request)
        if changes:
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(runs_router, prefix="/api/v1")
    app.include_router(subagents_router, prefix="/api/v1")
    app.include_router(subagent_review_router, prefix="/api/v1")
    app.include_router(conversations_router, prefix="/api/v1")
    app.include_router(catalogs_router, prefix="/api/v1")
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(work_orders_router, prefix="/api/v1")
    app.include_router(context_router, prefix="/api/v1")
    app.include_router(changes_router, prefix="/api/v1")
    app.include_router(reports_router, prefix="/api/v1")

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    if legacy_base_url:

        @app.get("/", include_in_schema=False)
        async def default_workspace() -> RedirectResponse:
            """Open native Vue; /legacy/ retains the original console and data.

            API-only deployments do not expose this route because they have no
            asset bridge. Navigation never submits a run or migrates histories.
            """
            return RedirectResponse("/runtime/", status_code=307, headers={"Cache-Control": "no-store"})

        @app.api_route(
            "/{path:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            include_in_schema=False,
        )
        async def legacy_proxy(path: str, request: Request) -> Response:
            """Keep legacy compatibility and Vue assets on the native API origin."""
            import httpx

            # Validate the browser-facing origin before translating it to the
            # separately bound loopback console. Forwarding it verbatim rejects
            # legitimate desktop POSTs because the two servers use different
            # ports; simply stripping it would discard the console's CSRF gate.
            if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
                return JSONResponse({"error": "invalid host"}, status_code=403)
            origin = request.headers.get("origin")
            if origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}":
                return JSONResponse({"error": "invalid origin"}, status_code=403)
            target = f"{legacy_base_url.rstrip('/')}/{path}"
            if request.url.query:
                target += f"?{request.url.query}"
            request_headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in {"host", "content-length", "connection"}
            }
            if origin is not None:
                request_headers["origin"] = legacy_base_url.rstrip("/")
            async with httpx.AsyncClient(timeout=65) as client:
                upstream = await client.request(
                    request.method,
                    target,
                    content=await request.body(),
                    headers=request_headers,
                )
            response_headers = {
                key: value
                for key, value in upstream.headers.items()
                if key.lower() not in {"content-length", "connection", "transfer-encoding"}
            }
            return Response(
                upstream.content,
                status_code=upstream.status_code,
                headers=response_headers,
                media_type=upstream.headers.get("content-type"),
            )

    return app
