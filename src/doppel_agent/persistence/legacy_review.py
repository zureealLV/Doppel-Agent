"""Bounded pending Core-loop approvals in the existing owned tool ledger DB.

This is a continuation record, not an executor or automatic crash recovery.
Private model context never appears in the public approval projection.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..loop import LoopContinuation
from ..provider import Message, ToolCall
from .database import sqlite_connection


MAX_BYTES = 4 * 1024 * 1024


def _call(value: Any) -> ToolCall:
    if not isinstance(value, dict) or set(value) != {"id", "name", "arguments"}:
        raise ValueError("legacy_review_invalid_call")
    for key, limit in (("id", 256), ("name", 128)):
        if (
            not isinstance(value[key], str)
            or not value[key]
            or len(value[key]) > limit
            or "\x00" in value[key]
        ):
            raise ValueError("legacy_review_invalid_call")
    if not isinstance(value["arguments"], dict):
        raise ValueError("legacy_review_invalid_call")
    return ToolCall(value["id"], value["name"], value["arguments"])


def encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("legacy_review_snapshot_budget")
    return raw


def decode(raw: str) -> tuple[dict[str, Any], LoopContinuation]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("legacy_review_snapshot_budget")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "run_id",
        "thread_id",
        "prompt",
        "context_text",
        "continuation",
    }:
        raise ValueError("legacy_review_invalid_snapshot")
    if type(payload["schema"]) is not int or payload["schema"] != 1:
        raise ValueError("legacy_review_invalid_snapshot")
    for key, limit in (("run_id", 128), ("thread_id", 128), ("prompt", 100_000), ("context_text", MAX_BYTES)):
        if not isinstance(payload[key], str) or len(payload[key]) > limit:
            raise ValueError("legacy_review_invalid_snapshot")
    if not payload["run_id"] or not payload["thread_id"] or not payload["prompt"].strip():
        raise ValueError("legacy_review_invalid_snapshot")
    frame = payload["continuation"]
    if not isinstance(frame, dict) or set(frame) != {"step", "messages", "pending_calls"}:
        raise ValueError("legacy_review_invalid_snapshot")
    if type(frame["step"]) is not int or not 1 <= frame["step"] <= 1000:
        raise ValueError("legacy_review_invalid_snapshot")
    if not isinstance(frame["messages"], list) or not 1 <= len(frame["messages"]) <= 1024:
        raise ValueError("legacy_review_invalid_snapshot")
    if not isinstance(frame["pending_calls"], list) or not 1 <= len(frame["pending_calls"]) <= 64:
        raise ValueError("legacy_review_invalid_snapshot")
    messages = []
    for item in frame["messages"]:
        if not isinstance(item, dict) or set(item) != {"role", "content", "tool_call_id", "tool_calls"}:
            raise ValueError("legacy_review_invalid_snapshot")
        if item["role"] not in {"system", "user", "assistant", "tool"} or not isinstance(
            item["content"], str
        ):
            raise ValueError("legacy_review_invalid_snapshot")
        if item["tool_call_id"] is not None and (
            not isinstance(item["tool_call_id"], str)
            or not item["tool_call_id"]
            or len(item["tool_call_id"]) > 256
        ):
            raise ValueError("legacy_review_invalid_snapshot")
        if not isinstance(item["tool_calls"], list) or len(item["tool_calls"]) > 64:
            raise ValueError("legacy_review_invalid_snapshot")
        messages.append(
            Message(
                item["role"],
                item["content"],
                item["tool_call_id"],
                tuple(_call(call) for call in item["tool_calls"]),
            )
        )
    pending = tuple(_call(call) for call in frame["pending_calls"])
    assistant_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].role == "assistant"), None
    )
    if assistant_index is None:
        raise ValueError("legacy_review_invalid_pending_boundary")
    assistant = messages[assistant_index]
    if len(pending) > len(assistant.tool_calls) or len({call.id for call in assistant.tool_calls}) != len(
        assistant.tool_calls
    ):
        raise ValueError("legacy_review_invalid_pending_boundary")
    if pending != assistant.tool_calls[-len(pending) :]:
        raise ValueError("legacy_review_invalid_pending_boundary")
    done = messages[assistant_index + 1 :]
    prefix = assistant.tool_calls[: -len(pending)]
    if len(done) != len(prefix) or any(
        message.role != "tool" or message.tool_call_id != call.id
        for message, call in zip(done, prefix, strict=True)
    ):
        raise ValueError("legacy_review_invalid_pending_boundary")
    # Reuse the actual assistant call objects, so approved edits stay visible to
    # the resumed Core provider context as well as the executed pending call.
    return payload, LoopContinuation(frame["step"], tuple(messages), assistant.tool_calls[-len(pending) :])


class LegacyReviewStore:
    def __init__(self, database: Path):
        self.database = database
        with sqlite_connection(database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS legacy_review_continuations (
                run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, interrupt_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','consumed')),
                snapshot_json TEXT NOT NULL, decision_json TEXT NOT NULL DEFAULT ''
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(legacy_review_continuations)")}
            if "snapshot_expired_at" not in columns:
                db.execute("ALTER TABLE legacy_review_continuations ADD COLUMN snapshot_expired_at TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_legacy_private_retention ON legacy_review_continuations(snapshot_expired_at,run_id)"
            )

    def ensure_new(self, run_id: str) -> None:
        with sqlite_connection(self.database) as db:
            if db.execute("SELECT 1 FROM legacy_review_continuations WHERE run_id=?", (run_id,)).fetchone():
                raise ValueError("legacy_review_run_already_exists")

    def save(
        self, run_id: str, thread_id: str, prompt: str, context_text: str, frame: LoopContinuation
    ) -> str:
        payload = {
            "schema": 1,
            "run_id": run_id,
            "thread_id": thread_id,
            "prompt": prompt,
            "context_text": context_text,
            "continuation": asdict(frame),
        }
        raw = encode(payload)
        decode(raw)  # Structural consistency is required before publishing a review.
        interrupt_id = uuid4().hex
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT thread_id,status,snapshot_expired_at FROM legacy_review_continuations WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if previous and (
                previous["thread_id"] != thread_id
                or previous["status"] != "consumed"
                or previous["snapshot_expired_at"] is not None
            ):
                raise ValueError("legacy_review_scope_unavailable")
            db.execute(
                """INSERT INTO legacy_review_continuations(run_id,thread_id,interrupt_id,status,snapshot_json,decision_json)
                VALUES(?,?,?,'pending',?,'')
                ON CONFLICT(run_id) DO UPDATE SET interrupt_id=excluded.interrupt_id,
                status='pending',snapshot_json=excluded.snapshot_json,decision_json='',snapshot_expired_at=NULL""",
                (run_id, thread_id, interrupt_id, raw),
            )
        return interrupt_id

    def load(
        self, run_id: str, thread_id: str, interrupt_id: str
    ) -> tuple[str, dict[str, Any], LoopContinuation]:
        with sqlite_connection(self.database) as db:
            row = db.execute(
                """SELECT snapshot_json FROM legacy_review_continuations
                WHERE run_id=? AND thread_id=? AND interrupt_id=? AND status='pending'
                AND snapshot_expired_at IS NULL
                AND length(CAST(snapshot_json AS BLOB))<=?""",
                (run_id, thread_id, interrupt_id, MAX_BYTES),
            ).fetchone()
        if row is None:
            raise ValueError("legacy_review_scope_unavailable")
        payload, frame = decode(row["snapshot_json"])
        if payload["run_id"] != run_id or payload["thread_id"] != thread_id:
            raise ValueError("legacy_review_scope_unavailable")
        return row["snapshot_json"], payload, frame

    def consume(
        self, run_id: str, thread_id: str, interrupt_id: str, raw: str, decision: dict[str, Any]
    ) -> None:
        encoded = encode(decision)
        with sqlite_connection(self.database) as db:
            cursor = db.execute(
                """UPDATE legacy_review_continuations SET status='consumed',decision_json=?
                WHERE run_id=? AND thread_id=? AND interrupt_id=? AND status='pending' AND snapshot_json=?
                AND snapshot_expired_at IS NULL""",
                (encoded, run_id, thread_id, interrupt_id, raw),
            )
            if cursor.rowcount != 1:
                raise ValueError("legacy_review_scope_unavailable")
