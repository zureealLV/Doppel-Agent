"""Ordered structured run events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


class EventBus:
    def __init__(self, run_id: str, sink: Callable[[dict[str, Any]], None]):
        self.run_id = run_id
        self.sink = sink
        self.sequence = 0

    def emit(self, kind: str, **payload: Any) -> dict[str, Any]:
        self.sequence += 1
        event = {
            "run_id": self.run_id,
            "sequence": self.sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "payload": payload,
        }
        self.sink(event)
        return event
