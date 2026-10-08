"""User-confirmed project note revisions, separate from checkpoint/global memory.

No runtime tool is registered here. Every write, including deletion, requires
explicit confirmation and an independent metadata idempotency key. Source refs
are resolved from this project's durable records, never client-supplied hashes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..persistence.database import sqlite_connection
from ..persistence.migrations import apply_migrations
from .manifest import ContextManifestError, canonical, identifier, request_key
from .policy import ContextPolicy


class ProjectNoteError(ContextManifestError):
    """Stable reason, with no raw source/exception text."""


def _identifier(value: Any) -> str:
    try:
        return identifier(value)
    except ContextManifestError:
        raise ProjectNoteError("invalid_note_identifier") from None


def _key(value: Any) -> str:
    try:
        return request_key(value)
    except ContextManifestError:
        raise ProjectNoteError("invalid_note_request_key") from None


def _text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ProjectNoteError("invalid_note_text")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ProjectNoteError("invalid_note_text") from None
    return value.strip()


def note_payload(payload: Any) -> dict[str, Any]:
    allowed = {"kind", "title", "body", "sources", "scope_work_order_id"}
    if not isinstance(payload, dict) or set(payload) - allowed or not {"kind", "title", "body", "sources"} <= set(payload):
        raise ProjectNoteError("invalid_note_fields")
    kind = payload["kind"]
    if kind not in ("fact", "constraint", "decision"):
        raise ProjectNoteError("invalid_note_kind")
    title, body = _text(payload["title"], 200), _text(payload["body"], 16000)
    if len(body.encode("utf-8")) > 64 * 1024:
        raise ProjectNoteError("note_body_byte_limit")
    scope = payload.get("scope_work_order_id")
    if scope is not None:
        _identifier(scope)
    sources = payload["sources"]
    if not isinstance(sources, (list, tuple)) or not 1 <= len(sources) <= 8:
        raise ProjectNoteError("invalid_note_sources")
    normalized, seen = [], set()
    for source in sources:
        if not isinstance(source, dict):
            raise ProjectNoteError("invalid_note_source_fields")
        if source.get("kind") == "user" and set(source) == {"kind", "label"}:
            item = {"kind": "user", "label": _text(source["label"], 200)}
        elif source.get("kind") == "file" and set(source) == {"kind", "manifest_id", "index"}:
            if type(source["index"]) is not int or not 0 <= source["index"] < 24:
                raise ProjectNoteError("invalid_note_file_index")
            item = {"kind": "file", "manifest_id": _identifier(source["manifest_id"]), "index": source["index"]}
        elif source.get("kind") == "run" and set(source) == {"kind", "run_id"}:
            item = {"kind": "run", "run_id": _identifier(source["run_id"])}
        else:
            raise ProjectNoteError("invalid_note_source_fields")
        encoded = canonical(item)
        if encoded in seen:
            raise ProjectNoteError("duplicate_note_source")
        seen.add(encoded)
        normalized.append(item)
    return {"kind": kind, "title": title, "body": body, "scope_work_order_id": scope, "sources": normalized}


class ProjectNoteStore:
    def __init__(self, database: Path):
        self.database = database
        apply_migrations(database)

    @staticmethod
    def _confirmation(confirmed: Any) -> None:
        if confirmed is not True:
            raise ProjectNoteError("confirmation_required")

    @staticmethod
    def _revision(value: Any) -> int:
        if type(value) is not int or value < 1:
            raise ProjectNoteError("invalid_note_revision")
        return value

    @staticmethod
    def _scope(db, identifier: str | None) -> None:
        if identifier is not None and not db.execute("SELECT 1 FROM work_orders WHERE work_order_id=?", (identifier,)).fetchone():
            raise ProjectNoteError("note_scope_not_found")

    @staticmethod
    def _row(db, note_id: str, revision: int | None = None):
        return db.execute("""
            SELECT r.*,n.scope_work_order_id,n.active_revision,n.deleted AS current_deleted,n.created_at AS note_created_at
            FROM project_note_revisions r JOIN project_notes n USING(note_id)
            WHERE r.note_id=? AND r.revision=COALESCE(?,n.active_revision)
        """, (note_id, revision)).fetchone()

    @staticmethod
    def _project(row) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"])
        size = len(snapshot["body"].encode("utf-8"))
        return {**snapshot, "note_id": row["note_id"], "revision": row["revision"], "deleted": bool(row["deleted"]),
            "request_key": row["request_key"],
            "scope": "work_order" if row["scope_work_order_id"] else "project", "scope_work_order_id": row["scope_work_order_id"],
            "created_at": row["note_created_at"], "revision_created_at": row["created_at"],
            "current_revision": row["active_revision"], "current_deleted": bool(row["current_deleted"]),
            "body_bytes": size, "estimated_tokens": ContextPolicy.estimate_utf8_bytes(size),
            "estimate_method": "utf8_bytes_div4", "actual_tokens": None}

    @classmethod
    def _replay(cls, db, key: str, encoded: str) -> dict[str, Any] | None:
        row = db.execute("SELECT note_id,revision,request_json FROM project_note_revisions WHERE request_key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["request_json"] != encoded:
            raise ProjectNoteError("note_key_reused")
        return cls._project(cls._row(db, row["note_id"], row["revision"]))

    @staticmethod
    def _resolve_sources(db, payload: dict[str, Any]) -> list[dict[str, Any]]:
        resolved = []
        for source in payload["sources"]:
            if source["kind"] == "user":
                resolved.append({**source, "origin": "user_declared"})
            elif source["kind"] == "file":
                row = db.execute("SELECT snapshot_json FROM context_manifests WHERE manifest_id=?", (source["manifest_id"],)).fetchone()
                if row is None:
                    raise ProjectNoteError("note_file_source_not_found")
                entries = json.loads(row["snapshot_json"])["entries"]
                if source["index"] >= len(entries):
                    raise ProjectNoteError("note_file_source_not_found")
                entry = entries[source["index"]]
                resolved.append({**source, **{name: entry[name] for name in (
                    "path", "start_line", "end_line", "file_sha256", "content_sha256", "truncation_reasons",
                )}})
            else:
                run = db.execute("SELECT status,lease_active,answer,mode FROM runtime_runs WHERE run_id=?", (source["run_id"],)).fetchone()
                if run is None:
                    raise ProjectNoteError("note_run_source_not_found")
                if run["lease_active"] or run["status"] not in {"completed", "failed", "cancelled", "interrupted_expired"}:
                    raise ProjectNoteError("note_run_source_not_drained")
                scope = payload["scope_work_order_id"]
                if scope and not db.execute("SELECT 1 FROM work_order_attempts WHERE work_order_id=? AND run_id=?", (scope, source["run_id"])).fetchone():
                    raise ProjectNoteError("note_run_source_wrong_work_order")
                resolved.append({**source, "mode": run["mode"], "status": run["status"],
                    "answer_sha256": hashlib.sha256(run["answer"].encode("utf-8")).hexdigest()})
        return resolved

    def get(self, note_id: str, *, revision: int | None = None) -> dict[str, Any] | None:
        _identifier(note_id)
        if revision is not None:
            self._revision(revision)
        with sqlite_connection(self.database) as db:
            row = self._row(db, note_id, revision)
            return self._project(row) if row and (revision is not None or not row["current_deleted"]) else None

    def list(self, *, scope_work_order_id: str | None = None, before_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ProjectNoteError("invalid_note_list_limit")
        if scope_work_order_id is not None:
            _identifier(scope_work_order_id)
        if before_id is not None:
            _identifier(before_id)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN")
            self._scope(db, scope_work_order_id)
            where = "deleted=0 AND (scope_work_order_id IS NULL OR scope_work_order_id=?)"
            parameters: list[Any] = [scope_work_order_id]
            if before_id is not None:
                cursor = db.execute("SELECT created_at,note_id FROM project_notes WHERE note_id=?", (before_id,)).fetchone()
                if cursor is None:
                    raise ProjectNoteError("note_cursor_not_found")
                where += " AND (created_at<? OR (created_at=? AND note_id<?))"
                parameters.extend((cursor["created_at"], cursor["created_at"], before_id))
            rows = db.execute("SELECT note_id FROM project_notes WHERE " + where + " ORDER BY created_at DESC,note_id DESC LIMIT ?", (*parameters, limit)).fetchall()
            return [self._project(self._row(db, row["note_id"])) for row in rows]

    @classmethod
    def _write(cls, db, note_id: str, revision: int, key: str, encoded: str, snapshot: dict, deleted: bool, now: str) -> dict[str, Any]:
        db.execute("INSERT INTO project_note_revisions VALUES (?,?,?,?,?,?,?)", (note_id, revision, key, encoded, canonical(snapshot), int(deleted), now))
        db.execute("UPDATE project_notes SET active_revision=?,deleted=?,updated_at=? WHERE note_id=?", (revision, int(deleted), now, note_id))
        return cls._project(cls._row(db, note_id, revision))

    def create(self, payload: Any, *, confirmed: bool, idempotency_key: str) -> dict[str, Any]:
        self._confirmation(confirmed)
        key, spec = _key(idempotency_key), note_payload(payload)
        encoded = canonical({"op": "create", "payload": spec})
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, key, encoded)
            if replay:
                return replay
            self._scope(db, spec["scope_work_order_id"])
            if db.execute("SELECT COUNT(*) FROM project_notes WHERE deleted=0").fetchone()[0] >= 500:
                raise ProjectNoteError("active_note_limit")
            sources = self._resolve_sources(db, spec)
            note_id, now = uuid4().hex, datetime.now(UTC).isoformat()
            db.execute("INSERT INTO project_notes VALUES (?,?,1,0,?,?)", (note_id, spec["scope_work_order_id"], now, now))
            snapshot = {name: spec[name] for name in ("title", "kind", "body")}
            return self._write(db, note_id, 1, key, encoded, {**snapshot, "sources": sources}, False, now)

    def update(self, note_id: str, payload: Any, *, expected_revision: int, confirmed: bool, idempotency_key: str) -> dict[str, Any]:
        self._confirmation(confirmed)
        _identifier(note_id)
        revision, key, spec = self._revision(expected_revision), _key(idempotency_key), note_payload(payload)
        encoded = canonical({"op": "update", "note_id": note_id, "expected_revision": revision, "payload": spec})
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, key, encoded)
            if replay:
                return replay
            row = self._row(db, note_id)
            if row is None or row["current_deleted"]:
                raise ProjectNoteError("note_not_found")
            if row["revision"] != revision:
                raise ProjectNoteError("note_revision_changed")
            if row["scope_work_order_id"] != spec["scope_work_order_id"]:
                raise ProjectNoteError("note_scope_changed")
            sources = self._resolve_sources(db, spec)
            snapshot = {name: spec[name] for name in ("title", "kind", "body")}
            return self._write(db, note_id, revision + 1, key, encoded, {**snapshot, "sources": sources}, False, datetime.now(UTC).isoformat())

    def delete(self, note_id: str, *, expected_revision: int, confirmed: bool, idempotency_key: str) -> dict[str, Any]:
        self._confirmation(confirmed)
        _identifier(note_id)
        revision, key = self._revision(expected_revision), _key(idempotency_key)
        encoded = canonical({"op": "delete", "note_id": note_id, "expected_revision": revision})
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, key, encoded)
            if replay:
                return replay
            row = self._row(db, note_id)
            if row is None or row["current_deleted"]:
                raise ProjectNoteError("note_not_found")
            if row["revision"] != revision:
                raise ProjectNoteError("note_revision_changed")
            return self._write(db, note_id, revision + 1, key, encoded, json.loads(row["snapshot_json"]), True, datetime.now(UTC).isoformat())
