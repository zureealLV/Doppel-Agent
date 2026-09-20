"""Version 1 run lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from ...concurrency import QueueCapacityError
from ...runtime.service import RunService
from ..dependencies import get_run_service
from ..schemas import CancelResponse, ResumeRequest, RunAccepted, RunCreate
from ..sse import run_event_stream


router = APIRouter(prefix="/runs", tags=["runs"])
Service = Annotated[RunService, Depends(get_run_service)]


@router.post("", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_run(body: RunCreate, service: Service) -> RunAccepted:
    try:
        record, created = await service.create(body.model_dump(mode="json"))
    except QueueCapacityError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return RunAccepted(
        run_id=record["run_id"],
        thread_id=record["thread_id"],
        status=record["status"],
        replayed=not created,
    )


@router.get("/{run_id}")
async def get_run(run_id: str, service: Service) -> dict:
    record = await service.get(run_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return record


@router.get("/{run_id}/events")
async def get_events(
    run_id: str,
    service: Service,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    stream: bool = False,
):
    if await service.get(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    if not stream:
        return await service.list_events(run_id, after_seq)
    return StreamingResponse(
        run_event_stream(service, run_id, after_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/cancel", response_model=CancelResponse)
async def cancel_run(run_id: str, service: Service) -> CancelResponse:
    if await service.get(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    cancelled = await service.cancel(run_id)
    return CancelResponse(run_id=run_id, cancel_requested=cancelled)


@router.post(
    "/{run_id}/interrupts/{interrupt_id}/resume",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_run(
    run_id: str,
    interrupt_id: str,
    body: ResumeRequest,
    service: Service,
) -> RunAccepted:
    try:
        record = await service.resume(
            run_id,
            interrupt_id,
            body.model_dump(mode="json", exclude_none=True),
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found") from exc
    except TimeoutError as exc:
        raise HTTPException(status.HTTP_410_GONE, str(exc)) from exc
    except (ValueError, QueueCapacityError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return RunAccepted(
        run_id=record["run_id"],
        thread_id=record["thread_id"],
        status=record["status"],
    )
