"""Native conversation projections; runtime records/checkpoints remain authoritative.

Only the runtime DB is touched. Legacy console conversations are not imported.
Acceptance and projection helpers run inside RuntimeRunStore transactions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..conversations import _valid_id
from .database import sqlite_connection
from .migrations import apply_migrations


ACTIVE = {"queued", "running", "interrupted"}


def now() -> str:
    return datetime.now(UTC).isoformat()


def insert_conversation(db, mode, title="新对话", profile_id=None):
    if mode not in {"legacy", "graph", "deep"}:
        raise ValueError("invalid conversation mode")
    title = " ".join(title.split())[:80]
    if not title:
        raise ValueError("conversation title is required")
    cid, thread, timestamp = uuid4().hex, "native-" + uuid4().hex, now()
    db.execute(
        "INSERT INTO native_conversations(id,thread_id,mode,title,profile_id,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)", (cid, thread, mode, title, profile_id, timestamp, timestamp),
    )
    return cid


def require_conversation(db, cid, *, idle=False):
    if not _valid_id(cid):
        raise ValueError("invalid native conversation id")
    row = db.execute("SELECT * FROM native_conversations WHERE id=? AND deleted=0", (cid,)).fetchone()
    if row is None:
        raise KeyError(cid)
    if idle and db.execute(
        "SELECT 1 FROM runtime_runs WHERE conversation_id=? AND (lease_active=1 OR status IN ('queued','running','interrupted'))",
        (cid,),
    ).fetchone():
        raise ValueError("conversation has an active or interrupted turn")
    return row


def bind_turn(db, cid, mode):
    row = require_conversation(db, cid, idle=True)
    if row["mode"] != mode:
        raise ValueError("conversation runtime mode is immutable")
    if row["archived"]:
        raise ValueError("archived conversation cannot accept a turn")
    last = db.execute(
        "SELECT status,metadata_json FROM runtime_runs WHERE conversation_id=? "
        "ORDER BY created_at DESC,run_id DESC LIMIT 1", (cid,),
    ).fetchone()
    thread = row["thread_id"]
    # Partial checkpoints and Deep->Graph fallbacks must not be blindly continued.
    if last and (last["status"] != "completed" or json.loads(last["metadata_json"]).get("fallback_runtime")):
        thread = "native-" + uuid4().hex
        db.execute("UPDATE native_conversations SET thread_id=? WHERE id=?", (thread, cid))
    return thread


def project_run(db, run_id):
    row = db.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
    if not row or not row["conversation_id"]:
        return
    cid = row["conversation_id"]
    conversation = db.execute("SELECT * FROM native_conversations WHERE id=?", (cid,)).fetchone()
    if conversation["deleted"]:
        return
    request = json.loads(row["request_json"])
    model = json.loads(row["profile_json"]).get("model")
    db.execute(
        "INSERT OR IGNORE INTO native_messages(conversation_id,run_id,role,content,created_at,model) "
        "VALUES(?,?,'user',?,?,?)", (cid, run_id, request["prompt"], row["created_at"], model),
    )
    if row["status"] == "completed" and row["answer"]:
        db.execute(
            "INSERT OR IGNORE INTO native_messages(conversation_id,run_id,role,content,created_at,model) "
            "VALUES(?,?,'assistant',?,?,?)", (cid, run_id, row["answer"], row["updated_at"], model),
        )
    else:
        # A cancellation drained after a completion write wins the projection too.
        db.execute("DELETE FROM native_messages WHERE run_id=? AND role='assistant'", (run_id,))
    db.execute("UPDATE native_conversations SET updated_at=MAX(updated_at,?) WHERE id=?",
               (row["updated_at"], cid))


class NativeConversationStore:
    def __init__(self, database: Path):
        self.database = database
        apply_migrations(database)

    def selection(self):
        """Workspace-owned navigation IDs, independent of ephemeral UI origins."""
        with sqlite_connection(self.database) as db:
            saved = db.execute("SELECT 1 FROM native_workspace_selection WHERE singleton=1").fetchone()
            row = db.execute(
                "SELECT c.id AS conversation_id,r.run_id FROM native_workspace_selection w "
                "JOIN native_conversations c ON c.id=w.conversation_id AND c.deleted=0 "
                "LEFT JOIN native_conversation_selection s ON s.conversation_id=c.id "
                "LEFT JOIN runtime_runs r ON r.run_id=s.run_id AND r.conversation_id=c.id "
                "WHERE w.singleton=1",
            ).fetchone()
        return {"saved": bool(saved), "conversation_id": row["conversation_id"] if row else None,
                "run_id": row["run_id"] if row else None}

    def set_selection(self, conversation_id, run_id=None):
        """Navigation never edits conversation ordering, leases or runtime audit."""
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            if conversation_id is None:
                if run_id is not None:
                    raise ValueError("run selection requires a native conversation")
            else:
                require_conversation(db, conversation_id)
                if run_id is not None:
                    if not _valid_id(run_id):
                        raise ValueError("invalid native run id")
                    if not db.execute(
                        "SELECT 1 FROM runtime_runs WHERE run_id=? AND conversation_id=?",
                        (run_id, conversation_id),
                    ).fetchone():
                        raise KeyError("run not found in this conversation")
                db.execute(
                    "INSERT INTO native_conversation_selection(conversation_id,run_id) VALUES(?,?) "
                    "ON CONFLICT(conversation_id) DO UPDATE SET run_id=excluded.run_id",
                    (conversation_id, run_id),
                )
            db.execute(
                "INSERT INTO native_workspace_selection(singleton,conversation_id) VALUES(1,?) "
                "ON CONFLICT(singleton) DO UPDATE SET conversation_id=excluded.conversation_id",
                (conversation_id,),
            )
        return {"saved": True, "conversation_id": conversation_id, "run_id": run_id}

    def create(self, mode="graph", title="新对话", profile_id=None, *, draft=False):
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = None
            if draft:
                row = db.execute(
                    "SELECT c.id FROM native_conversations c WHERE mode=? AND title=? "
                    "AND profile_id IS ? AND archived=0 AND deleted=0 "
                    "AND NOT EXISTS(SELECT 1 FROM runtime_runs r WHERE r.conversation_id=c.id) "
                    "ORDER BY created_at,id LIMIT 1", (mode, " ".join(title.split())[:80], profile_id),
                ).fetchone()
            cid = row["id"] if row else insert_conversation(db, mode, title, profile_id)
        return self.get(cid)

    def reconcile(self):
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT run_id FROM runtime_runs WHERE conversation_id IS NOT NULL ORDER BY created_at,run_id"
            ).fetchall()
            for row in rows:
                project_run(db, row["run_id"])

    def get(self, cid):
        with sqlite_connection(self.database) as db:
            row = require_conversation(db, cid)
            result = dict(row)
            group = db.execute("SELECT name FROM native_groups WHERE id=?", (row["group_id"],)).fetchone()
            result["group_name"] = group["name"] if group else None
            result.pop("deleted")
            result["messages"] = [dict(item) for item in db.execute(
                "SELECT m.id,m.role,m.content,m.run_id,m.created_at,m.model,r.status "
                "FROM native_messages m JOIN runtime_runs r ON r.run_id=m.run_id "
                "WHERE m.conversation_id=? ORDER BY r.created_at,r.run_id,m.id", (cid,),
            )]
            result["runs"] = [dict(item) for item in db.execute(
                "SELECT run_id,thread_id,status,mode,created_at,updated_at,error,lease_active FROM runtime_runs "
                "WHERE conversation_id=? ORDER BY created_at,run_id", (cid,),
            )]
            result["active_run_id"] = next((r["run_id"] for r in result["runs"] if r["status"] in ACTIVE or r["lease_active"]), None)
            selection = db.execute(
                "SELECT run_id FROM native_conversation_selection WHERE conversation_id=?", (cid,),
            ).fetchone()
            if selection:
                result["selected_run_id"] = next(
                    (r["run_id"] for r in result["runs"] if r["run_id"] == selection["run_id"]), None,
                )
            return result

    def list(self, *, archived=False, query="", limit=100):
        query = " ".join(query.split())[:200]
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with sqlite_connection(self.database) as db:
            rows = db.execute(
                "SELECT c.id,c.thread_id,c.mode,c.title,c.profile_id,c.group_id,g.name AS group_name,"
                "c.archived,c.created_at,c.updated_at,"
                "(SELECT COUNT(*) FROM native_messages m WHERE m.conversation_id=c.id) AS message_count,"
                "COALESCE((SELECT content FROM native_messages m WHERE m.conversation_id=c.id ORDER BY id DESC LIMIT 1),'') AS preview "
                "FROM native_conversations c LEFT JOIN native_groups g ON g.id=c.group_id WHERE deleted=0 "
                "AND (? OR archived=?) AND (?='' OR c.title LIKE ? ESCAPE '\\' OR EXISTS "
                "(SELECT 1 FROM native_messages m WHERE m.conversation_id=c.id AND m.content LIKE ? ESCAPE '\\')) "
                "ORDER BY c.updated_at DESC,c.id DESC LIMIT ?",
                (bool(query), int(archived), query, f"%{escaped}%", f"%{escaped}%", max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def history(self, cid):
        """Bootstrap only successful turns, never failed tool/assistant fragments."""
        from ..context.input import compose_prompt

        with sqlite_connection(self.database) as db:
            require_conversation(db, cid)
            rows = db.execute(
                "SELECT request_json,input_snapshot_json,answer FROM runtime_runs WHERE conversation_id=? AND status='completed' "
                "ORDER BY created_at,run_id", (cid,),
            ).fetchall()
        return [message for row in rows for message in (
            {"role": "user", "content": compose_prompt(json.loads(row["request_json"])["prompt"],
                json.loads(row["input_snapshot_json"])["rendered_context"] if row["input_snapshot_json"] else "")},
            {"role": "assistant", "content": row["answer"]},
        )]

    def update(self, cid, *, title=None, archived=None, profile_id=..., group_id=...):
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            current = require_conversation(db, cid, idle=True)
            if current["archived"] and profile_id is not ...:
                raise ValueError("archived conversation profile is read-only; unarchive first")
            fields = {"updated_at": now()}
            if title is not None:
                title = " ".join(title.split())[:80]
                if not title:
                    raise ValueError("conversation title is required")
                fields["title"] = title
            if archived is not None:
                fields["archived"] = int(archived)
            if profile_id is not ...:
                fields["profile_id"] = profile_id
            if group_id is not ...:
                if group_id is not None and (not _valid_id(group_id) or not db.execute(
                    "SELECT 1 FROM native_groups WHERE id=?", (group_id,),
                ).fetchone()):
                    raise ValueError("group not found")
                fields["group_id"] = group_id
            db.execute("UPDATE native_conversations SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?",
                       (*fields.values(), cid))
        return self.get(cid)

    def delete(self, cid):
        """Remove visible history; retain runtime/checkpoint audit, never reuse IDs."""
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            require_conversation(db, cid, idle=True)
            db.execute("DELETE FROM native_conversation_selection WHERE conversation_id=?", (cid,))
            db.execute("UPDATE native_workspace_selection SET conversation_id=NULL WHERE conversation_id=?", (cid,))
            db.execute("DELETE FROM native_messages WHERE conversation_id=?", (cid,))
            db.execute("UPDATE native_conversations SET deleted=1,title='Deleted',profile_id=NULL,group_id=NULL WHERE id=?", (cid,))

    def groups(self):
        with sqlite_connection(self.database) as db:
            return [dict(row) for row in db.execute(
                "SELECT g.*, (SELECT COUNT(*) FROM native_conversations c WHERE c.group_id=g.id "
                "AND c.deleted=0 AND c.archived=0) AS conversation_count FROM native_groups g ORDER BY lower(name),id"
            )]

    def save_group(self, name, gid=None):
        name = " ".join(name.split())[:60]
        if not name or (gid is not None and not _valid_id(gid)):
            raise ValueError("invalid group")
        with sqlite_connection(self.database) as db:
            if gid:
                if not db.execute("UPDATE native_groups SET name=? WHERE id=?", (name, gid)).rowcount:
                    raise KeyError(gid)
            else:
                gid = uuid4().hex
                db.execute("INSERT INTO native_groups VALUES(?,?,?)", (gid, name, now()))
        return next(item for item in self.groups() if item["id"] == gid)

    def delete_group(self, gid):
        if not _valid_id(gid):
            raise ValueError("invalid group id")
        with sqlite_connection(self.database) as db:
            if not db.execute("DELETE FROM native_groups WHERE id=?", (gid,)).rowcount:
                raise KeyError(gid)
