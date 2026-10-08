"""Local work-order plans/control; every execution uses owned RunService."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from ...runtime.service import RunService
from ...context.manifest import ContextManifestError
from ..dependencies import get_run_service
from ..schemas import (
    WorkOrderActivate, WorkOrderControl, WorkOrderCreate, WorkOrderPlanReplace, WorkOrderRetry, WorkOrderSelectionUpdate,
)


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/work-orders", tags=["work-orders"], dependencies=[Depends(no_store)])
Service = Annotated[RunService, Depends(get_run_service)]
Identifier = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
TaskId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]


async def request_result(operation):
    try:
        return await operation()
    except KeyError:
        raise HTTPException(404, "work order or model profile not found", headers={"Cache-Control": "no-store"}) from None
    except ContextManifestError as error:
        reason = str(error)
        missing = {"context_record_not_found", "note_not_found", "note_scope_not_found", "note_file_source_not_found", "note_run_source_not_found"}
        conflict = {"context_file_stale", "context_file_unavailable", "selected_note_revision_changed", "context_note_source_changed", "note_run_source_not_drained"}
        raise HTTPException(404 if reason in missing else 409 if reason in conflict else 400, reason, headers={"Cache-Control": "no-store"}) from None
    except ValueError as error:
        code = 404 if str(error) in {"work order not found", "task not found in current plan", "work order list cursor not found", "run not found in this work order"} else 409
        raise HTTPException(code, str(error), headers={"Cache-Control": "no-store"}) from None
    except RuntimeError:
        raise HTTPException(503, "work-order service is unavailable", headers={"Cache-Control": "no-store"}) from None


@router.post("", status_code=201)
async def create(body: WorkOrderCreate, service: Service):
    async def operation():
        record, fresh = await service.create_work_order(body.plan.to_plan(), body.idempotency_key)
        return {**record, "replayed": not fresh}
    return await request_result(operation)


@router.get("")
async def list_orders(service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                      before_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None,
                      search: Annotated[str, Query(max_length=200)] = ""):
    return await request_result(lambda: service.list_work_orders(limit, before_id=before_id, search=search))


@router.get("/queue")
async def queue(service: Service):
    return await request_result(service.work_order_queue)


@router.get("/selection")
async def selection(service: Service):
    return await request_result(service.work_order_selection)


@router.post("/selection")
async def save_selection(body: WorkOrderSelectionUpdate, service: Service):
    return await request_result(lambda: service.set_work_order_selection(body.work_order_id, body.run_id,
        expected_revision=body.expected_revision, idempotency_key=body.idempotency_key))


@router.get("/{identifier}/selection")
async def run_selection(identifier: Identifier, service: Service):
    return await request_result(lambda: service.work_order_run_selection(identifier))


@router.get("/{identifier}")
async def get(identifier: Identifier, service: Service):
    record = await request_result(lambda: service.get_work_order(identifier))
    if record is None:
        raise HTTPException(404, "work order not found", headers={"Cache-Control": "no-store"})
    return record


@router.get("/{identifier}/plans/{revision}")
async def get_plan(identifier: Identifier, revision: Annotated[int, Path(ge=1)], service: Service):
    plan = await request_result(lambda: service.get_work_order_plan(identifier, revision))
    if plan is None:
        raise HTTPException(404, "plan revision not found", headers={"Cache-Control": "no-store"})
    return plan


@router.put("/{identifier}/plan")
async def revise(identifier: Identifier, body: WorkOrderPlanReplace, service: Service):
    return await request_result(lambda: service.revise_work_order(identifier, body.plan.to_plan(), expected_revision=body.expected_revision,
        context=body.context.model_dump() if body.context is not None else None))


@router.post("/{identifier}/activate")
async def activate(identifier: Identifier, body: WorkOrderActivate, service: Service):
    return await request_result(lambda: service.activate_work_order(identifier, expected_revision=body.expected_revision,
                                                                  settings=body.settings.model_dump()))


@router.post("/{identifier}/control")
async def control(identifier: Identifier, body: WorkOrderControl, service: Service):
    return await request_result(lambda: service.control_work_order(identifier, body.action, expected_revision=body.expected_revision))


@router.post("/{identifier}/tasks/{task_id}/retry")
async def retry(identifier: Identifier, task_id: TaskId, body: WorkOrderRetry, service: Service):
    return await request_result(lambda: service.retry_work_order_task(identifier, task_id, expected_revision=body.expected_revision,
                                                                    expected_attempt_id=body.expected_attempt_id))
