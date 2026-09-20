"""SSE formatting and replay stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from ..persistence.runs import TERMINAL_STATUSES
from ..runtime.service import RunService


def encode_sse(event: dict) -> str:
    return (
        f"id: {event['seq']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


async def run_event_stream(service: RunService, run_id: str, after_seq: int) -> AsyncIterator[str]:
    cursor = after_seq
    while True:
        events = await service.list_events(run_id, cursor)
        for event in events:
            cursor = event["seq"]
            yield encode_sse(event)
        record = await service.get(run_id)
        if record is None or record["status"] in TERMINAL_STATUSES:
            return
        if not events:
            try:
                await service.notifier.wait(run_id)
            except asyncio.CancelledError:
                return
