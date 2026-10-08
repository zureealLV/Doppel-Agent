"""Explicit project context previews/acceptance and confirmed note metadata.

These routes never submit a run, grant tools or modify workspace source files.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from ...context.manifest import ContextManifestError
from ...runtime.service import RunService
from ..dependencies import get_run_service
from ..schemas import ContextAccept, ContextPreview, ContextSelectionWrite, NoteConfirmedWrite, NoteDelete, NoteUpdate


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/context", tags=["context"], dependencies=[Depends(no_store)])
Service = Annotated[RunService, Depends(get_run_service)]
Identifier = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


async def result(operation):
    try:
        return await operation()
    except ContextManifestError as error:
        reason = str(error)
        if reason in {"context_record_not_found", "note_not_found", "note_scope_not_found", "note_file_source_not_found", "note_run_source_not_found", "note_cursor_not_found"}:
            code = 404
        elif reason in {"manifest_key_reused", "note_key_reused", "stale_file", "note_revision_changed", "note_scope_changed", "note_run_source_not_drained",
                        "context_selection_key_reused", "context_selection_revision_changed", "selected_note_revision_changed"}:
            code = 409
        else:
            code = 400
        raise HTTPException(code, reason, headers={"Cache-Control": "no-store"}) from None
    except KeyError:
        raise HTTPException(404, "context_record_not_found", headers={"Cache-Control": "no-store"}) from None
    except (RuntimeError, OSError, sqlite3.Error):
        raise HTTPException(503, "context_service_unavailable", headers={"Cache-Control": "no-store"}) from None


@router.post("/preview")
async def preview(body: ContextPreview, service: Service):
    return await result(lambda: service.context_preview([file.model_dump() for file in body.files], body.budget_bytes))


@router.get("/selection")
async def selection(service: Service, scope_work_order_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None):
    return await result(lambda: service.context_selection(scope_work_order_id))


@router.post("/selection")
async def save_selection(body: ContextSelectionWrite, service: Service):
    return await result(lambda: service.set_context_selection(body.scope_work_order_id, body.manifest_id,
        [ref.model_dump() for ref in body.notes], expected_revision=body.expected_revision, idempotency_key=body.idempotency_key))


@router.post("/manifests", status_code=201)
async def accept(body: ContextAccept, service: Service):
    return await result(lambda: service.context_accept([file.model_dump() for file in body.files],
        budget_bytes=body.budget_bytes, confirmed=body.confirmed, idempotency_key=body.idempotency_key))


@router.get("/manifests/{manifest_id}")
async def manifest(manifest_id: Identifier, service: Service):
    record = await result(lambda: service.get_context_manifest(manifest_id))
    if record is None:
        raise HTTPException(404, "context_record_not_found", headers={"Cache-Control": "no-store"})
    return record


@router.post("/manifests/{manifest_id}/check")
async def check(manifest_id: Identifier, service: Service):
    return await result(lambda: service.check_context_manifest(manifest_id))


@router.get("/notes")
async def list_notes(service: Service, scope_work_order_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None,
                     before_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None,
                     limit: Annotated[int, Query(ge=1, le=100)] = 50):
    return await result(lambda: service.list_project_notes(scope_work_order_id=scope_work_order_id, before_id=before_id, limit=limit))


@router.post("/notes", status_code=201)
async def create_note(body: NoteConfirmedWrite, service: Service):
    return await result(lambda: service.create_project_note(body.note.model_dump(), confirmed=body.confirmed, idempotency_key=body.idempotency_key))


@router.get("/notes/{note_id}")
async def note(note_id: Identifier, service: Service, revision: Annotated[int | None, Query(ge=1)] = None):
    record = await result(lambda: service.get_project_note(note_id, revision=revision))
    if record is None:
        raise HTTPException(404, "note_not_found", headers={"Cache-Control": "no-store"})
    return record


@router.put("/notes/{note_id}")
async def update_note(note_id: Identifier, body: NoteUpdate, service: Service):
    return await result(lambda: service.update_project_note(note_id, body.note.model_dump(),
        expected_revision=body.expected_revision, confirmed=body.confirmed, idempotency_key=body.idempotency_key))


@router.post("/notes/{note_id}/delete")
async def delete_note(note_id: Identifier, body: NoteDelete, service: Service):
    return await result(lambda: service.delete_project_note(note_id,
        expected_revision=body.expected_revision, confirmed=body.confirmed, idempotency_key=body.idempotency_key))
