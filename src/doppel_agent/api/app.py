"""FastAPI application factory with an owned runtime lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response

from ..runtime.service import RunService
from .routes.runs import router as runs_router


def create_app(
    workspace: Path,
    *,
    provider: Any | None = None,
    max_active_runs: int = 4,
    queue_capacity: int = 100,
    approval_ttl_seconds: int = 900,
    legacy_base_url: str | None = None,
) -> FastAPI:
    service = RunService(
        workspace,
        provider=provider,
        max_active_runs=max_active_runs,
        queue_capacity=queue_capacity,
        approval_ttl_seconds=approval_ttl_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await service.start()
        app.state.run_service = service
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(
        title="Doppel Agent Runtime API",
        version="0.10.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.run_service = service
    app.include_router(runs_router, prefix="/api/v1")

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    if legacy_base_url:

        @app.api_route(
            "/{path:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            include_in_schema=False,
        )
        async def legacy_proxy(path: str, request: Request) -> Response:
            """Keep the v0.8 UI/API same-origin while v1 routes migrate."""
            import httpx

            target = f"{legacy_base_url.rstrip('/')}/{path}"
            if request.url.query:
                target += f"?{request.url.query}"
            request_headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in {"host", "content-length", "connection"}
            }
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
