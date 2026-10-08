"""Fixed owned Changes transport. No arbitrary argv/paths/automatic approval.

Only exact explicit POSTs decide/cancel/reconcile. GET diagnostics never restart
resources. All evidence is scoped sensitive local data, not globally S8-redacted.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ...runtime.service import RunService
from ...workspace.patching import PatchConflictError
from ..dependencies import get_run_service
from ..schemas import (ChangesConfirmed, ChangesDiff, InverseDecision, InversePrepare,
                       VerificationCancel, VerificationDecision, VerificationPrepare)


HEADERS = {"Cache-Control": "no-store"}
MAX_BODY_BYTES = 16384
MAX_QUERY_BYTES = 8192


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate request member")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("non-finite request")


class ChangesRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        allowed_queries = {parameter.alias for parameter in self.dependant.query_params}

        async def handle(request: Request):
            try:
                if len(request.scope.get("query_string", b"")) > MAX_QUERY_BYTES:
                    return JSONResponse({"detail": "changes_request_budget"}, 413, headers=HEADERS)
                # Reject hidden authority/options and duplicate query values.
                seen = set()
                for key, _value in request.query_params.multi_items():
                    if key not in allowed_queries or key in seen:
                        return JSONResponse({"detail": "invalid_changes_request"}, 422, headers=HEADERS)
                    seen.add(key)
                if request.method not in {"GET", "HEAD"}:
                    chunks, size = [], 0
                    async with asyncio.timeout(5):
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > MAX_BODY_BYTES:
                                return JSONResponse({"detail": "changes_request_budget"}, 413, headers=HEADERS)
                            chunks.append(chunk)
                    raw = b"".join(chunks)
                    # Starlette caches this same bounded body for FastAPI; no
                    # second stream read or user-controlled JSON reserialization.
                    request._body = raw
                    json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
                response = await original(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except (RequestValidationError, ValueError, RecursionError):
                return JSONResponse({"detail": "invalid_changes_request"}, 422, headers=HEADERS)
            except TimeoutError:
                return JSONResponse({"detail": "changes_request_timeout"}, 408, headers=HEADERS)
            except HTTPException:
                raise
            except Exception:
                # Unknown failures are not echoed as paths, private output or
                # tracebacks. No automatic retry/finalization of an effect.
                return JSONResponse({"detail": "changes_service_unavailable"}, 503, headers=HEADERS)
        return handle


router = APIRouter(prefix="/changes", tags=["changes"], route_class=ChangesRoute)
Service = Annotated[RunService, Depends(get_run_service)]
Identifier = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
ToolCall = Annotated[str, Query(min_length=1, max_length=256, pattern=r"^[^\x00]+$")]

NOT_FOUND = {"inverse_review_scope_unavailable", "verification_review_scope_unavailable"}
CONFLICT = {
    "verification_review_stale", "verification_review_not_pending_or_outcome_indeterminate",
    "verification_operation_active", "verification_cancel_in_progress", "verification_command_outcome_indeterminate",
    "inverse_source_run_not_quiescent", "inverse_review_not_pending_or_outcome_indeterminate",
    "inverse_review_expired", "inverse_review_stale", "patch_preimage_expired", "patch_base_changed",
}
UNAVAILABLE = {
    "verification_process_cleanup_quarantine", "verification_query_owner_unavailable", "verification_query_unavailable",
    "verification_query_source_unavailable", "verification_review_unavailable", "verification_evidence_unavailable",
    "verification_result_unavailable", "verification_completion_requires_sealed_results", "verification_evidence_budget",
    "verification_query_budget", "changes_service_closed", "patch_workspace_owner_unavailable",
    "changes_evidence_unavailable", "inverse_review_unavailable", "patch_evidence_unavailable", "patch_receipt_unavailable",
}
BAD_REQUEST = {"invalid_git_diff_path", "invalid_git_diff_selection", "invalid_git_inspection_fingerprint",
               "invalid_verification_selection", "verification_query_limit", "invalid_patch_receipt_page",
               "inverse_review_scope_unavailable_request"}
CAPACITY = {"verification_manual_operation_capacity", "verification_review_pending_budget", "inverse_review_pending_budget_exceeded"}


async def result(operation):
    try:
        return await operation()
    except KeyError:
        raise HTTPException(404, "changes_source_not_found", headers=HEADERS) from None
    except PermissionError:
        raise HTTPException(403, "changes_permission_required", headers=HEADERS) from None
    except PatchConflictError:
        # Older compatibility messages include a file path; use a category, not
        # string-prefix sanitization or an implied safe-to-retry effect.
        raise HTTPException(409, "changes_patch_conflict", headers=HEADERS) from None
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
        reason = str(error)
        if reason in NOT_FOUND:
            code = 404
        elif reason in CONFLICT:
            code = 409
        elif reason in CAPACITY:
            code = 429
        elif reason in BAD_REQUEST:
            code = 400
        elif reason in UNAVAILABLE:
            code = 503
        else:
            code, reason = 503, "changes_service_unavailable"
        raise HTTPException(code, reason, headers=HEADERS) from None


def confirmed(body):
    if body.confirmed is not True:
        raise HTTPException(403, "changes_confirmation_required", headers=HEADERS)


@router.get("/status")
async def status(service: Service):
    return await result(service.git_status)


@router.post("/diff")
async def diff(body: ChangesDiff, service: Service):
    return await result(lambda: service.git_diff(body.path, plane=body.plane,
        expected_fingerprint=body.expected_fingerprint, conflict_stage=body.conflict_stage))


@router.get("/runs/{run_id}/patches")
async def patches(run_id: Identifier, service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 100,
                  offset: Annotated[int, Query(ge=0, le=100000)] = 0):
    return await result(lambda: service.list_patch_effects(run_id, limit=limit, offset=offset))


@router.get("/runs/{run_id}/patch-evidence")
async def patch_evidence(run_id: Identifier, tool_call_id: ToolCall, service: Service):
    return await result(lambda: service.get_patch_evidence(run_id, tool_call_id))


@router.post("/runs/{run_id}/inverse-reviews")
async def prepare_inverse(run_id: Identifier, body: InversePrepare, service: Service):
    confirmed(body)
    return await result(lambda: service.prepare_inverse_patch(run_id, body.source_tool_call_id, body.source_patch_id,
        operation_id=body.operation_id, workspace_write=body.workspace_write))


@router.get("/runs/{run_id}/inverse-reviews/{review_id}")
async def inverse(run_id: Identifier, review_id: Identifier, service: Service):
    return await result(lambda: service.get_inverse_patch(run_id, review_id))


@router.post("/runs/{run_id}/inverse-reviews/{review_id}/decision")
async def decide_inverse(run_id: Identifier, review_id: Identifier, body: InverseDecision, service: Service):
    confirmed(body)
    return await result(lambda: service.decide_inverse_patch(run_id, review_id, body.patch_id,
        action=body.action, workspace_write=body.workspace_write))


@router.post("/runs/{run_id}/inverse-reviews/{review_id}/reconcile")
async def reconcile_inverse(run_id: Identifier, review_id: Identifier, body: ChangesConfirmed, service: Service):
    confirmed(body)
    return await result(lambda: service.reconcile_inverse_patch(run_id, review_id))


@router.post("/runs/{run_id}/verification-reviews")
async def prepare_verification(run_id: Identifier, body: VerificationPrepare, service: Service):
    confirmed(body)
    return await result(lambda: service.prepare_verification(run_id, body.source_tool_call_id, body.source_patch_id,
        operation_id=body.operation_id, names=body.names, command_execute=body.command_execute, workspace_write=body.workspace_write))


@router.get("/runs/{run_id}/verification-reviews")
async def verification_history(run_id: Identifier, service: Service,
    limit: Annotated[int, Query(ge=1, le=16)] = 16,
    after_id: Annotated[str, Query(pattern=r"^(?:[0-9a-f]{32})?$")] = "",
    tool_call_id: Annotated[str | None, Query(min_length=1, max_length=256, pattern=r"^[^\x00]+$")] = None,
    patch_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None):
    return await result(lambda: service.list_verifications(run_id, limit=limit, after_id=after_id,
                                                          tool_call_id=tool_call_id, patch_id=patch_id))


@router.get("/runs/{run_id}/verification-reviews/{review_id}")
async def verification(run_id: Identifier, review_id: Identifier, service: Service):
    return await result(lambda: service.get_verification(run_id, review_id))


@router.post("/runs/{run_id}/verification-reviews/{review_id}/decision")
async def decide_verification(run_id: Identifier, review_id: Identifier, body: VerificationDecision, service: Service):
    confirmed(body)
    return await result(lambda: service.decide_verification(run_id, review_id, body.plan_id,
        action=body.action, command_execute=body.command_execute, workspace_write=body.workspace_write))


@router.post("/runs/{run_id}/verification-reviews/{review_id}/cancel")
async def cancel_verification(run_id: Identifier, review_id: Identifier, body: VerificationCancel, service: Service):
    confirmed(body)
    return await result(lambda: service.cancel_verification(run_id, review_id, body.plan_id))


@router.post("/runs/{run_id}/verification-reviews/{review_id}/reconcile")
async def reconcile_verification(run_id: Identifier, review_id: Identifier, body: ChangesConfirmed, service: Service):
    confirmed(body)
    return await result(lambda: service.reconcile_verification(run_id, review_id))
