"""Bounded asynchronous subagent lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ...concurrency import QueueCapacityError
from ...runtime.service import RunService
from ..dependencies import get_run_service
from ..schemas import SubagentCancelResponse, SubagentPrompt, SubagentRecord


router = APIRouter(prefix="/runs/{run_id}/subagents", tags=["subagents"])
Service = Annotated[RunService, Depends(get_run_service)]


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "run or subagent not found")
    if isinstance(exc, PermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, QueueCapacityError):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc))
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post("", response_model=SubagentRecord, status_code=status.HTTP_202_ACCEPTED)
async def create_subagent(run_id: str, body: SubagentPrompt, service: Service) -> dict:
    try:
        return await service.spawn_subagent(run_id, body.prompt)
    except (KeyError, PermissionError, QueueCapacityError, ValueError) as exc:
        raise _translate_error(exc) from exc


@router.get("", response_model=list[SubagentRecord])
async def list_subagents(run_id: str, service: Service) -> list[dict]:
    try:
        return await service.list_subagents(run_id)
    except (KeyError, PermissionError) as exc:
        raise _translate_error(exc) from exc


@router.get("/{subagent_id}", response_model=SubagentRecord)
async def get_subagent(run_id: str, subagent_id: str, service: Service) -> dict:
    try:
        return await service.get_subagent(run_id, subagent_id)
    except (KeyError, PermissionError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/{subagent_id}/follow-ups",
    response_model=SubagentRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
async def follow_up_subagent(
    run_id: str,
    subagent_id: str,
    body: SubagentPrompt,
    service: Service,
) -> dict:
    try:
        return await service.follow_up_subagent(run_id, subagent_id, body.prompt)
    except (KeyError, PermissionError, QueueCapacityError, ValueError) as exc:
        raise _translate_error(exc) from exc


@router.post("/{subagent_id}/cancel", response_model=SubagentCancelResponse)
async def cancel_subagent(
    run_id: str,
    subagent_id: str,
    service: Service,
) -> SubagentCancelResponse:
    try:
        cancelled = await service.cancel_subagent(run_id, subagent_id)
    except (KeyError, PermissionError) as exc:
        raise _translate_error(exc) from exc
    return SubagentCancelResponse(subagent_id=subagent_id, cancel_requested=cancelled)
