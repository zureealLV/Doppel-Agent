"""Per-tool, per-run approval broker for the local web console."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class ApprovalBroker:
    def __init__(self, timeout_seconds: float = 120):
        self.timeout_seconds = timeout_seconds
        self.condition = threading.Condition()
        self.pending: dict[str, dict[str, Any]] = {}
        self.decisions: dict[str, bool] = {}

    def request(self, capability: str, tool_name: str, arguments: dict[str, Any]) -> bool:
        approval_id = uuid4().hex
        with self.condition:
            self.pending[approval_id] = {
                "id": approval_id,
                "capability": capability,
                "tool": tool_name,
                "arguments": arguments,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            approved = self.condition.wait_for(
                lambda: approval_id in self.decisions,
                timeout=self.timeout_seconds,
            )
            self.pending.pop(approval_id, None)
            return self.decisions.pop(approval_id, False) if approved else False

    def list_pending(self) -> list[dict[str, Any]]:
        with self.condition:
            return [dict(item) for item in self.pending.values()]

    def decide(self, approval_id: str, allow: bool) -> bool:
        with self.condition:
            if approval_id not in self.pending or approval_id in self.decisions:
                return False
            self.decisions[approval_id] = allow
            self.condition.notify_all()
            return True
