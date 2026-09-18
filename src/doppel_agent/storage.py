"""Append-only local records. A future milestone will add crash-safe indexing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RunStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _directory(self, run_id: str) -> Path:
        if not run_id or any(ch not in "0123456789abcdef" for ch in run_id):
            raise ValueError("run_id must be lowercase hexadecimal")
        directory = self.root / "runs" / run_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        directory = self._directory(run_id)
        path = directory / "events.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        if event["kind"] in {"run_started", "tool_requested", "tool_completed", "tool_failed", "run_completed", "run_failed"}:
            with (directory / "trace.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def write_session(self, run_id: str, session: dict[str, Any]) -> None:
        directory = self._directory(run_id)
        temporary = directory / "session.json.tmp"
        temporary.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(directory / "session.json")
