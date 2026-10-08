"""Generation-pinned child review; compatibility routes intentionally unchanged.

No new executor, permissions, idempotency ledger or exactly-once guarantee.
Admission/cancel acknowledgements do not prove completion or physical drain.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from ...concurrency.scheduler import QueueCapacityError

HEADERS = {"Cache-Control": "no-store"}
Identifier = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
Generation = Annotated[int, Query(ge=1, le=9007199254740991)]


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate member")
        value[key] = item
    return value


def _constant(_value):
    raise ValueError("non-finite member")


class ReviewRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        allowed = {parameter.alias for parameter in self.dependant.query_params}

        async def handle(request: Request):
            try:
                if len(request.scope.get("query_string", b"")) > 1024:
                    return JSONResponse({"detail": "subagent_review_request_budget"}, 413, headers=HEADERS)
                seen = set()
                for key, _value in request.query_params.multi_items():
                    if key not in allowed or key in seen:
                        raise ValueError("hidden query authority")
                    if (not _value.isascii() or not _value.isdecimal() or len(_value) > 16
                            or len(_value) > 1 and _value.startswith("0")):
                        raise ValueError("non-integer page authority")
                    seen.add(key)
                if request.method == "POST":
                    chunks, size = [], 0
                    async with asyncio.timeout(5):
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > 16384:
                                return JSONResponse({"detail": "subagent_review_request_budget"}, 413, headers=HEADERS)
                            chunks.append(chunk)
                    request._body = b"".join(chunks)
                    value = json.loads(request._body, object_pairs_hook=_pairs, parse_constant=_constant)
                    # Literal[True] by itself may accept 1; reject coercion before
                    # Pydantic and keep the original bounded body for the handler.
                    if not isinstance(value, dict) or value.get("confirmed") is not True:
                        raise ValueError("explicit confirmation required")
                response = await original(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except (RequestValidationError, ValueError, RecursionError):
                return JSONResponse({"detail": "invalid_subagent_review_request"}, 422, headers=HEADERS)
            except TimeoutError:
                return JSONResponse({"detail": "subagent_review_request_timeout"}, 408, headers=HEADERS)
            except HTTPException:
                raise
            except Exception:
                return JSONResponse({"detail": "subagent_review_service_unavailable"}, 503, headers=HEADERS)
        return handle


class Confirmed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]


class Prompt(Confirmed):
    prompt: str = Field(min_length=1, max_length=4000)

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank prompt")
        return value


class FollowUp(Prompt):
    # The acknowledgement must also be a JS-safe generation after increment.
    expected_generation: StrictInt = Field(ge=1, le=9007199254740990)


class Cancel(Confirmed):
    expected_generation: StrictInt = Field(ge=1, le=9007199254740991)


router = APIRouter(prefix="/subagent-review/runs/{run_id}", tags=["subagent-review"], route_class=ReviewRoute)


async def _result(operation):
    try:
        return await operation()
    except KeyError:
        raise HTTPException(404, "subagent_review_scope_not_found", headers=HEADERS) from None
    except PermissionError:
        raise HTTPException(403, "subagent_review_delegate_required", headers=HEADERS) from None
    except QueueCapacityError:
        raise HTTPException(429, "subagent_review_queue_capacity", headers=HEADERS) from None
    except ValueError as error:
        if str(error) == "subagent_generation_changed":
            raise HTTPException(409, "subagent_review_generation_changed", headers=HEADERS) from None
        if str(error) in {"invalid_subagent_history_page", "invalid_subagent_generation"}:
            raise HTTPException(422, "invalid_subagent_review_request", headers=HEADERS) from None
        # Store admission/lifecycle conflicts can follow a committed side effect;
        # even a fixed 409 is NOT a safe-to-retry or no-effect receipt.
        raise HTTPException(409, "subagent_review_conflict", headers=HEADERS) from None


def record_snapshot(record: dict) -> dict:
    history = record["history"]
    total = record.get("history_total", len(history))
    offset = record.get("history_offset", max(0, total - 16))
    limit = record.get("history_limit", 16)
    page = history if "history_total" in record else history[offset:offset + limit]
    if (type(record["generation"]) is not int or not 1 <= record["generation"] <= 9007199254740991
            or type(total) is not int or total < 0 or type(offset) is not int or not 0 <= offset <= total
            or type(limit) is not int or not 1 <= limit <= 16 or len(page) > limit
            or record["status"] not in {"queued", "running", "completed", "failed", "cancelled"}):
        raise RuntimeError("subagent_review_record_budget")
    # Source records made through the original manager are bounded. Fail closed
    # on incompatible legacy/corrupt text; never truncate a confirmed prompt or
    # silently omit a history turn. Raw errors/profile/permissions aren't returned.
    for prompt, answer in [(record["prompt"], record["answer"]),
                           *((item["prompt"], item["answer"]) for item in page)]:
        if not isinstance(prompt, str) or not isinstance(answer, str) or len(prompt) > 4000 or len(answer) > 8000:
            raise RuntimeError("subagent_review_record_budget")
    return {key: record[key] for key in
            ("subagent_id", "parent_run_id", "status", "prompt", "answer", "generation", "created_at", "updated_at")} | {
        "history": [{"prompt": item["prompt"], "answer": item["answer"]} for item in page],
        "history_total": total, "history_offset": offset, "history_limit": limit,
        "history_truncated": offset > 0 or offset + limit < total,
        "error_present": bool(record["error"]),
    }


OUTPUT = {"sensitive": True, "globally_redacted": False, "text_trusted": False}


@router.get("")
async def snapshot(run_id: Identifier, request: Request):
    value = await _result(lambda: request.app.state.run_service.subagent_review(run_id))
    return {**value, "items": [record_snapshot(item) for item in value["items"]], "output": OUTPUT}


@router.get("/{subagent_id}/history")
async def history(run_id: Identifier, subagent_id: Identifier, request: Request,
                  expected_generation: Generation, offset: Annotated[int, Query(ge=0, le=9007199254740991)] = 0,
                  limit: Annotated[int, Query(ge=1, le=16)] = 16):
    value = await _result(lambda: request.app.state.run_service.subagent_review(
        run_id, subagent_id=subagent_id, expected_generation=expected_generation, offset=offset, limit=limit,
    ))
    return {**record_snapshot(value), "service": value["service"], "physical_drain_verified": False, "output": OUTPUT}


def _receipt(run_id: str, record: dict, *, reviewed_generation: int | None) -> dict:
    if (record["parent_run_id"] != run_id
            or reviewed_generation is None and record["generation"] != 1):
        raise RuntimeError("subagent_review_receipt_scope")
    return {"parent_run_id": run_id, "reviewed_generation": reviewed_generation,
            "record": record_snapshot(record), "admission_acknowledged": True,
            "completion_verified": False, "physical_drain_verified": False, "output": OUTPUT}


@router.post("/spawn", status_code=202)
async def spawn(run_id: Identifier, body: Prompt, request: Request):
    record = await _result(lambda: request.app.state.run_service.spawn_subagent(run_id, body.prompt))
    return _receipt(run_id, record, reviewed_generation=None)


@router.post("/{subagent_id}/follow-ups", status_code=202)
async def follow_up(run_id: Identifier, subagent_id: Identifier, body: FollowUp, request: Request):
    record = await _result(lambda: request.app.state.run_service.follow_up_subagent(
        run_id, subagent_id, body.prompt, expected_generation=body.expected_generation,
    ))
    if record["subagent_id"] != subagent_id or record["generation"] != body.expected_generation + 1:
        raise RuntimeError("subagent_review_receipt_generation")
    return _receipt(run_id, record, reviewed_generation=body.expected_generation)


@router.post("/{subagent_id}/cancel")
async def cancel(run_id: Identifier, subagent_id: Identifier, body: Cancel, request: Request):
    requested = await _result(lambda: request.app.state.run_service.cancel_subagent(
        run_id, subagent_id, expected_generation=body.expected_generation,
    ))
    return {"parent_run_id": run_id, "subagent_id": subagent_id, "reviewed_generation": body.expected_generation,
            "cancel_requested": requested, "physical_drain_verified": False}
