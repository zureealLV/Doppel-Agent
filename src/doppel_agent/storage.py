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

    def read_session(self, run_id: str) -> dict[str, Any] | None:
        if len(run_id) != 32 or any(ch not in "0123456789abcdef" for ch in run_id):
            return None
        path = self.root / "runs" / run_id / "session.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        runs = self.root / "runs"
        if not runs.is_dir():
            return []
        paths = sorted(runs.glob("*/session.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        result = []
        for path in paths:
            session = self.read_session(path.parent.name)
            if session:
                result.append(session)
            if len(result) >= limit:
                break
        return result
