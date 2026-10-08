"""Project/work-order picked context, never implicit runtime input or grants."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..persistence.database import sqlite_connection
from ..persistence.migrations import apply_migrations
from .manifest import ContextManifestError, canonical, identifier, request_key


def note_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or len(value) > 16:
        raise ContextManifestError("invalid_selected_notes")
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"note_id", "revision"}:
            raise ContextManifestError("invalid_selected_note_fields")
        nid, revision = identifier(item["note_id"]), item["revision"]
        if type(revision) is not int or revision < 1 or nid in seen:
            raise ContextManifestError("invalid_selected_note_revision")
        seen.add(nid)
        result.append({"note_id": nid, "revision": revision})
    return result


class ContextSelectionStore:
    def __init__(self, database: Path):
        self.database = database
        apply_migrations(database)

    @staticmethod
    def _scope(db, scope: str | None) -> str:
        if scope is None:
            return "project"
        identifier(scope)
        if not db.execute("SELECT 1 FROM work_orders WHERE work_order_id=?", (scope,)).fetchone():
            raise ContextManifestError("note_scope_not_found")
        return scope

    @staticmethod
    def _project(row, scope: str | None) -> dict[str, Any]:
        return {"saved": row is not None, "scope_work_order_id": scope,
            "manifest_id": row["manifest_id"] if row else None,
            "notes": json.loads(row["note_refs_json"]) if row else [],
            "revision": row["revision"] if row else 0, "request_key": row["request_key"] if row else None}

    def get(self, scope_work_order_id: str | None = None) -> dict[str, Any]:
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN")
            scope = self._scope(db, scope_work_order_id)
            row = db.execute("SELECT * FROM context_selections WHERE scope_key=?", (scope,)).fetchone()
            return self._project(row, scope_work_order_id)

    def set(self, scope_work_order_id: str | None, manifest_id: str | None, notes: Any,
            *, expected_revision: int, idempotency_key: str) -> dict[str, Any]:
        if manifest_id is not None:
            identifier(manifest_id)
        refs, key = note_refs(notes), request_key(idempotency_key)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ContextManifestError("invalid_context_selection_revision")
        encoded = canonical({"scope_work_order_id": scope_work_order_id, "manifest_id": manifest_id,
            "notes": refs, "expected_revision": expected_revision})
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._scope(db, scope_work_order_id)
            row = db.execute("SELECT * FROM context_selections WHERE scope_key=?", (scope,)).fetchone()
            if row and row["request_key"] == key:
                if row["request_json"] != encoded:
                    raise ContextManifestError("context_selection_key_reused")
                return self._project(row, scope_work_order_id)
            current_revision = row["revision"] if row else 0
            if current_revision != expected_revision:
                raise ContextManifestError("context_selection_revision_changed")
            if manifest_id is not None and not db.execute("SELECT 1 FROM context_manifests WHERE manifest_id=?", (manifest_id,)).fetchone():
                raise ContextManifestError("context_record_not_found")
            for ref in refs:
                note = db.execute("SELECT * FROM project_notes WHERE note_id=?", (ref["note_id"],)).fetchone()
                if note is None or note["deleted"]:
                    raise ContextManifestError("note_not_found")
                if note["active_revision"] != ref["revision"]:
                    raise ContextManifestError("selected_note_revision_changed")
                if note["scope_work_order_id"] not in (None, scope_work_order_id):
                    raise ContextManifestError("selected_note_wrong_scope")
            db.execute("""
                INSERT INTO context_selections VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(scope_key) DO UPDATE SET
                manifest_id=excluded.manifest_id,note_refs_json=excluded.note_refs_json,revision=excluded.revision,
                request_key=excluded.request_key,request_json=excluded.request_json,updated_at=excluded.updated_at
            """, (scope, scope_work_order_id, manifest_id, canonical(refs), current_revision + 1, key, encoded, datetime.now(UTC).isoformat()))
            return self._project(db.execute("SELECT * FROM context_selections WHERE scope_key=?", (scope,)).fetchone(), scope_work_order_id)
