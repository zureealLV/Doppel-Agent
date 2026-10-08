"""Current owned project only; no filesystem picker or implicit migration."""

from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...projects.service import current_project

router = APIRouter()


@router.get("/projects/current")
async def project_identity(request: Request) -> JSONResponse:
    identity = current_project(request.app.state.run_service.workspace)
    return JSONResponse(asdict(identity), headers={"Cache-Control": "no-store"})
