"""Version 1 run lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Header, status
from fastapi.responses import StreamingResponse

from ...concurrency import QueueCapacityError
from ...context.manifest import ContextManifestError
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
    except ContextManifestError as exc:
        reason = str(exc)
        missing = {"context_record_not_found", "note_not_found", "note_scope_not_found", "note_file_source_not_found", "note_run_source_not_found"}
        conflict = {"context_file_stale", "context_file_unavailable", "selected_note_revision_changed", "context_note_source_changed", "note_run_source_not_drained"}
        code = 404 if reason in missing else 409 if reason in conflict else 400
        raise HTTPException(code, reason, headers={"Cache-Control": "no-store"}) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found") from exc
    return RunAccepted(
        run_id=record["run_id"],
        conversation_id=record.get("conversation_id"),
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
    last_event_id: Annotated[str | None, Header()] = None,
):
    if await service.get(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    if not stream:
        return await service.list_events(run_id, after_seq)
    if last_event_id is not None:
        if len(last_event_id) > 20 or not last_event_id.isascii() or not last_event_id.isdecimal():
            raise HTTPException(422, "invalid Last-Event-ID")
        after_seq = max(after_seq, int(last_event_id))
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
        conversation_id=record.get("conversation_id"),
        thread_id=record["thread_id"],
        status=record["status"],
    )
