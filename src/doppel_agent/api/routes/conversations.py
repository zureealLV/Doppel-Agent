"""Native conversation endpoints, never a proxy/relabel of Legacy history."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Header

from ...runtime.service import RunService
from ..dependencies import get_run_service
from ..schemas import ConversationCreate, ConversationUpdate, GroupWrite, RunCreate, ResumeRequest
from ..schemas import WorkspaceSelectionUpdate
from .runs import create_run, get_events, resume_run, cancel_run

router = APIRouter(tags=["native conversations"])
Service = Annotated[RunService, Depends(get_run_service)]


async def call(operation, *args, owned=False, **kwargs):
    try:
        if owned:
            return await operation(*args, **kwargs)
        return await asyncio.to_thread(operation, *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "conversation or group not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        if not owned:
            raise
        raise HTTPException(503, "workspace service unavailable") from exc


def validate_profile(service, pid):
    if pid is not None and not any(p["id"] == pid for p in service.settings.public()["profiles"]):
        raise HTTPException(409, "model profile not found")


@router.get("/workspace-selection")
async def get_workspace_selection(service: Service):
    return await call(service.workspace_selection, owned=True)


@router.put("/workspace-selection")
async def set_workspace_selection(body: WorkspaceSelectionUpdate, service: Service):
    return await call(service.save_workspace_selection, owned=True, **body.model_dump())


@router.get("/conversations")
async def list_conversations(service: Service, archived: bool = False, q: str = "", limit: Annotated[int, Query(ge=1, le=200)] = 100):
    return await call(service.conversations.list, archived=archived, query=q, limit=limit)


@router.post("/conversations", status_code=201)
async def create_conversation(body: ConversationCreate, service: Service):
    validate_profile(service, body.profile_id)
    return await call(service.conversations.create, **body.model_dump())


@router.get("/conversations/{cid}")
async def get_conversation(cid: str, service: Service):
    return await call(service.conversations.get, cid)


@router.patch("/conversations/{cid}")
async def update_conversation(cid: str, body: ConversationUpdate, service: Service):
    values = body.model_dump(exclude_unset=True)
    validate_profile(service, values.get("profile_id"))
    return await call(service.conversations.update, cid, **values)


@router.delete("/conversations/{cid}")
async def delete_conversation(cid: str, service: Service):
    await call(service.conversations.delete, cid)
    return {"ok": True, "audit_retained": True}


@router.get("/conversation-groups")
async def list_groups(service: Service):
    return await call(service.conversations.groups)


@router.post("/conversation-groups", status_code=201)
async def create_group(body: GroupWrite, service: Service):
    return await call(service.conversations.save_group, body.name)


@router.patch("/conversation-groups/{gid}")
async def update_group(gid: str, body: GroupWrite, service: Service):
    return await call(service.conversations.save_group, body.name, gid)


@router.delete("/conversation-groups/{gid}")
async def delete_group(gid: str, service: Service):
    await call(service.conversations.delete_group, gid)
    return {"ok": True}


async def owned_run(service, cid, rid):
    await call(service.conversations.get, cid)
    record = await service.get(rid)
    if record is None or record.get("conversation_id") != cid:
        raise HTTPException(404, "run not found in this conversation")
    return record


@router.post("/conversations/{cid}/runs", status_code=202)
async def submit_turn(cid: str, body: RunCreate, service: Service):
    if body.conversation_id is not None and body.conversation_id != cid:
        raise HTTPException(409, "conversation id does not match route")
    body.conversation_id = cid
    return await create_run(body, service)


@router.get("/conversations/{cid}/runs/{rid}")
async def get_owned_run(cid: str, rid: str, service: Service):
    return await owned_run(service, cid, rid)


@router.get("/conversations/{cid}/runs/{rid}/events")
async def owned_events(cid: str, rid: str, service: Service, after_seq: Annotated[int, Query(ge=0)] = 0,
                       stream: bool = False, last_event_id: Annotated[str | None, Header()] = None):
    await owned_run(service, cid, rid)
    return await get_events(rid, service, after_seq, stream, last_event_id)


@router.post("/conversations/{cid}/runs/{rid}/cancel")
async def owned_cancel(cid: str, rid: str, service: Service):
    await owned_run(service, cid, rid)
    return await cancel_run(rid, service)


@router.post("/conversations/{cid}/runs/{rid}/interrupts/{iid}/resume", status_code=202)
async def owned_resume(cid: str, rid: str, iid: str, body: ResumeRequest, service: Service):
    await owned_run(service, cid, rid)
    return await resume_run(rid, iid, body, service)
