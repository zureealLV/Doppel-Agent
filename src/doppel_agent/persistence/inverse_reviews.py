"""Fresh operator-reviewed inverse proposals in the existing patch ledger DB.

Private proposal bytes are not provider capabilities. A pending review is not
permission; applying/indeterminate reviews are never automatically retried.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..context.manifest import ContextManifestError, relative_path
from ..workspace.patch_receipts import content_hash, valid_hash
from ..workspace.patching import PatchProposal, PatchService
from .database import sqlite_connection
from .evidence_json import loads as evidence_loads


MAX_PRIVATE_BYTES = 4 * 1024 * 1024
MAX_PENDING = 256


def identifier(value: Any, *, uuid: bool = False) -> str:
    if (not isinstance(value, str) or not value or len(value) > 256 or "\x00" in value
            or uuid and re.fullmatch(r"[0-9a-f]{32}", value) is None):
        raise ValueError("inverse_review_scope_unavailable")
    return value


def _dump(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf-8")) > MAX_PRIVATE_BYTES:
        raise ValueError("inverse_review_budget_exceeded")
    return raw


def _load(raw: Any) -> Any:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PRIVATE_BYTES:
        raise ValueError("inverse_review_unavailable")
    try:
        return evidence_loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("inverse_review_unavailable") from None


def _review(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {"unified_diff", "files"}
            or not isinstance(value["unified_diff"], str) or not isinstance(value["files"], list)
            or not 1 <= len(value["files"]) <= 32):
        raise ValueError("inverse_review_unavailable")
    paths, components = set(), {}
    for file in value["files"]:
        if not isinstance(file, dict) or set(file) != {"path", "base_hash", "base_mode", "target_hash", "target_mode", "action"}:
            raise ValueError("inverse_review_unavailable")
        try:
            path = relative_path(file["path"])
        except ContextManifestError:
            raise ValueError("inverse_review_unavailable") from None
        if path.casefold() in paths:
            raise ValueError("inverse_review_unavailable")
        paths.add(path.casefold())
        parts = path.split("/")
        for count in range(1, len(parts) + 1):
            spelling = "/".join(parts[:count])
            if components.get(spelling.casefold(), spelling) != spelling:
                raise ValueError("inverse_review_unavailable")
            components[spelling.casefold()] = spelling
        for hash_key, mode_key in (("base_hash", "base_mode"), ("target_hash", "target_mode")):
            mode = file[mode_key]
            if (not valid_hash(file[hash_key]) or (file[hash_key] == "missing") != (mode is None)
                    or mode is not None and (type(mode) is not int or not 0 <= mode <= 0o777)):
                raise ValueError("inverse_review_unavailable")
        if file["action"] != ("delete" if file["target_hash"] == "missing" else "restore"):
            raise ValueError("inverse_review_unavailable")
    return value


def _projection(proposal: PatchProposal) -> dict[str, Any]:
    return _review({"unified_diff": proposal.unified_diff, "files": [{
        "path": change.path, "base_hash": change.base_hash, "base_mode": change.base_mode,
        "target_hash": content_hash(change.content), "target_mode": change.target_mode,
        "action": "delete" if change.content is None else "restore",
    } for change in proposal.changes]})


class InverseReviewStore:
    def __init__(self, database: Path, *, ttl_seconds: int = 900):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("invalid_inverse_review_ttl")
        self.database, self.ttl_seconds = database, ttl_seconds
        with sqlite_connection(database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS inverse_patch_reviews (
                review_id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL,
                source_tool_call_id TEXT NOT NULL, source_patch_id TEXT NOT NULL,
                patch_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','applying','applied','failed','rejected','expired','indeterminate')),
                proposal_json TEXT NOT NULL, review_json TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT '',
                decision TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS ix_inverse_source ON inverse_patch_reviews(source_run_id,source_tool_call_id)")
            db.execute("CREATE INDEX IF NOT EXISTS ix_inverse_private_retention ON inverse_patch_reviews(status,review_id)")

    @staticmethod
    def effect_id(review_id: str) -> str:
        return "manual-inverse:" + identifier(review_id, uuid=True)

    @staticmethod
    def _row(db, run_id: str, review_id: str):
        row = db.execute("""SELECT review_id,source_run_id,source_tool_call_id,source_patch_id,
            patch_id,created_at,expires_at,status,decision,error_code,
            CASE WHEN length(CAST(proposal_json AS BLOB))<=4194304 THEN proposal_json END AS proposal_json,
            CASE WHEN length(CAST(review_json AS BLOB))<=4194304 THEN review_json END AS review_json,
            CASE WHEN length(CAST(result_json AS BLOB))<=4194304 THEN result_json END AS result_json
            FROM inverse_patch_reviews WHERE review_id=? AND source_run_id=?""", (review_id, run_id)).fetchone()
        if row is None:
            raise ValueError("inverse_review_scope_unavailable")
        identifier(row["review_id"], uuid=True)
        identifier(row["source_run_id"], uuid=True)
        identifier(row["source_tool_call_id"])
        identifier(row["source_patch_id"], uuid=True)
        identifier(row["patch_id"], uuid=True)
        if (row["status"] not in {"pending", "applying", "applied", "failed", "rejected", "expired", "indeterminate"}
                or row["decision"] not in {"", "approve", "reject"}
                or row["error_code"] not in {"", "patch_operation_failed", "patch_outcome_indeterminate"}):
            raise ValueError("inverse_review_unavailable")
        for key in ("created_at", "expires_at"):
            try:
                parsed = datetime.fromisoformat(row[key])
                if parsed.utcoffset() != timedelta(0) or parsed.isoformat() != row[key]:
                    raise ValueError("inverse_review_unavailable")
            except (TypeError, ValueError):
                raise ValueError("inverse_review_unavailable") from None
        lifetime = (datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])).total_seconds()
        if not 0 < lifetime <= 86400:
            raise ValueError("inverse_review_unavailable")
        return row

    @staticmethod
    def _pending_proposal(row) -> PatchProposal:
        try:
            proposal = PatchProposal.from_dict(_load(row["proposal_json"]))
            if (proposal.patch_id != row["patch_id"]
                    or proposal.patch_id != PatchService._proposal_id(proposal.changes, proposal.unified_diff)
                    or _projection(proposal) != _review(_load(row["review_json"]))):
                raise ValueError("inverse_review_invalid_proposal")
            return proposal
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError("inverse_review_unavailable") from None

    @classmethod
    def _expire(cls, db, run_id: str, review_id: str) -> None:
        # Expiry destroys only this private proposal, never the source receipt
        # or effect ledger/audit identity. No claim of secure disk erasure.
        row = cls._row(db, run_id, review_id)
        cls._public(row)
        if row["status"] != "pending" or datetime.fromisoformat(row["expires_at"]) > datetime.now(UTC):
            return
        cls._pending_proposal(row)
        db.execute("""UPDATE inverse_patch_reviews SET status='expired',proposal_json=''
            WHERE source_run_id=? AND review_id=? AND status='pending'""",
                   (run_id, review_id))

    @classmethod
    def _public(cls, row, *, replayed: bool = False) -> dict[str, Any]:
        review = _review(_load(row["review_json"]))
        result = _load(row["result_json"]) if row["result_json"] else None
        if (row["status"] == "applied") != (result is not None):
            raise ValueError("inverse_review_unavailable")
        if result is not None:
            if (not isinstance(result, dict) or set(result) != {"patch_id", "changed_paths", "unified_diff", "patch_receipt", "receipt_source"}
                    or result["patch_id"] != row["patch_id"] or result["unified_diff"] != review["unified_diff"]
                    or result["changed_paths"] != [file["path"] for file in review["files"]]):
                raise ValueError("inverse_review_unavailable")
            receipt = result["patch_receipt"]
            if (not isinstance(receipt, dict) or set(receipt) != {"schema", "patch_id", "status", "files"}
                    or type(receipt["schema"]) is not int or receipt["schema"] != 1
                    or receipt["patch_id"] != row["patch_id"] or receipt["status"] != "applied"
                    or not isinstance(receipt["files"], list) or len(receipt["files"]) != len(review["files"])):
                raise ValueError("inverse_review_unavailable")
            for file, planned in zip(receipt["files"], review["files"], strict=True):
                if (not isinstance(file, dict) or set(file) != {"path", "base_hash", "base_mode", "after_hash", "after_mode", "outcome"}
                        or file["outcome"] != "applied" or file["path"] != planned["path"]
                        or file["base_hash"] != planned["base_hash"] or file["base_mode"] != planned["base_mode"]
                        or type(file["base_mode"]) is not type(planned["base_mode"])
                        or file["after_hash"] != planned["target_hash"] or file["after_mode"] != planned["target_mode"]
                        or type(file["after_mode"]) is not type(planned["target_mode"])):
                    raise ValueError("inverse_review_unavailable")
            source = result["receipt_source"]
            if source != {"run_id": row["source_run_id"], "tool_call_id": cls.effect_id(row["review_id"]),
                          "origin": "manual_inverse", "durability": "sealed_tool_ledger",
                          "source_tool_call_id": row["source_tool_call_id"], "source_patch_id": row["source_patch_id"]}:
                raise ValueError("inverse_review_unavailable")
        return {"review_id": row["review_id"], "operation_kind": "manual_inverse",
                "source": {"run_id": row["source_run_id"], "tool_call_id": row["source_tool_call_id"], "patch_id": row["source_patch_id"]},
                "effect_tool_call_id": cls.effect_id(row["review_id"]), "patch_id": row["patch_id"],
                "created_at": row["created_at"], "expires_at": row["expires_at"], "status": row["status"],
                "review": review, "result": result,
                "error_code": row["error_code"] or None, "effect_replayed": replayed and row["status"] == "applied",
                "decision_replayed": replayed,
                "effect_evidence": "sealed_patch_ledger" if row["status"] == "applied" else
                    "requires_patch_ledger_inspection" if row["status"] in {"applying", "failed", "indeterminate"} else "review_not_applied"}

    def get(self, run_id: str, review_id: str) -> dict[str, Any]:
        identifier(run_id, uuid=True)
        identifier(review_id, uuid=True)
        with sqlite_connection(self.database) as db:
            self._expire(db, run_id, review_id)
            return self._public(self._row(db, run_id, review_id))

    def prepare_replay(self, run_id: str, tool_call_id: str, patch_id: str, review_id: str) -> dict[str, Any] | None:
        for value in (run_id, patch_id, review_id):
            identifier(value, uuid=True)
        identifier(tool_call_id)
        with sqlite_connection(self.database) as db:
            existing = db.execute("SELECT source_run_id,source_tool_call_id,source_patch_id FROM inverse_patch_reviews WHERE review_id=?", (review_id,)).fetchone()
            if existing is None:
                return None
            if tuple(existing) != (run_id, tool_call_id, patch_id):
                raise ValueError("inverse_review_scope_unavailable")
            self._expire(db, run_id, review_id)
            return self._public(self._row(db, run_id, review_id))

    def save(self, run_id: str, tool_call_id: str, patch_id: str, review_id: str, proposal: PatchProposal) -> dict[str, Any]:
        # Caller prepared it under the admitted owner/workspace lock against
        # exactly the applied source receipt; no live filesystem reads here.
        for value in (run_id, patch_id, review_id):
            identifier(value, uuid=True)
        identifier(tool_call_id)
        raw = _dump(proposal.as_dict())
        identifier(proposal.patch_id, uuid=True)
        if proposal.patch_id != PatchService._proposal_id(proposal.changes, proposal.unified_diff):
            raise ValueError("inverse_review_invalid_proposal")
        review = _dump(_projection(proposal))
        now = datetime.now(UTC)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            # A bounded number of live reviews; old pending expiry is observable
            # by exact lookup and separate owner maintenance, not a mass scan.
            live = db.execute("""SELECT COUNT(*) FROM inverse_patch_reviews
                WHERE status='applying' OR status='pending' AND expires_at>?""", (now.isoformat(),)).fetchone()[0]
            if live >= MAX_PENDING:
                raise ValueError("inverse_review_pending_budget_exceeded")
            db.execute("""INSERT INTO inverse_patch_reviews(review_id,source_run_id,source_tool_call_id,
                source_patch_id,patch_id,created_at,expires_at,status,proposal_json,review_json)
                VALUES(?,?,?,?,?,?,?,'pending',?,?)""",
                       (review_id, run_id, tool_call_id, patch_id, proposal.patch_id, now.isoformat(),
                        (now + timedelta(seconds=self.ttl_seconds)).isoformat(), raw, review))
            return self._public(self._row(db, run_id, review_id))

    def claim(self, run_id: str, review_id: str, patch_id: str, action: str) -> tuple[dict[str, Any], PatchProposal | None]:
        if action not in {"approve", "reject"}:
            raise ValueError("inverse_decision_must_be_approve_or_reject")
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, run_id, review_id)
            row = self._row(db, run_id, review_id)
            if row["patch_id"] != patch_id:
                raise ValueError("inverse_review_scope_unavailable")
            if row["status"] == "applied" and action == "approve":
                return self._public(row, replayed=True), None
            if row["status"] == "rejected" and action == "reject":
                return self._public(row, replayed=True), None
            if row["status"] != "pending":
                outcome, proposal = self._public(row), None
            elif action == "reject":
                db.execute("UPDATE inverse_patch_reviews SET status='rejected',proposal_json='',decision='reject' WHERE review_id=?", (review_id,))
                outcome, proposal = self._public(self._row(db, run_id, review_id)), None
            else:
                proposal = self._pending_proposal(row)
                db.execute("UPDATE inverse_patch_reviews SET status='applying',decision='approve' WHERE review_id=? AND status='pending'", (review_id,))
                outcome = self._public(self._row(db, run_id, review_id))
        # Commit expiry before refusing execution. Applying/failed/unknown are
        # never an instruction to call execute_patch_once again.
        if outcome["status"] not in {"applying", "rejected"}:
            raise RuntimeError("inverse_review_not_pending_or_outcome_indeterminate")
        if outcome["status"] == "applying" and proposal is None:
            raise RuntimeError("inverse_review_not_pending_or_outcome_indeterminate")
        return outcome, proposal

    def finish(self, run_id: str, review_id: str, *, result: dict[str, Any] | None = None,
               failed: bool = False, error_code: str = "") -> dict[str, Any]:
        state = "applied" if result is not None else "failed" if failed else "indeterminate"
        if error_code not in {"", "patch_operation_failed", "patch_outcome_indeterminate"}:
            raise ValueError("inverse_review_invalid_error")
        with sqlite_connection(self.database) as db:
            cursor = db.execute("""UPDATE inverse_patch_reviews SET status=?,result_json=?,error_code=?,proposal_json=''
                WHERE source_run_id=? AND review_id=? AND status='applying'""",
                                (state, _dump(result) if result is not None else "", error_code, run_id, review_id))
            if cursor.rowcount != 1:
                raise ValueError("inverse_review_scope_unavailable")
            return self._public(self._row(db, run_id, review_id))

    def reconcile(self, run_id: str, review_id: str, evidence: dict | None) -> dict[str, Any]:
        """Only reconcile already-consumed unknown metadata, never execute IO."""
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, run_id, review_id)
            if row["status"] not in {"applying", "indeterminate"}:
                return self._public(row)
            if row["decision"] != "approve":
                raise ValueError("inverse_review_scope_unavailable")
            state, result, code = "indeterminate", None, "patch_outcome_indeterminate"
            if evidence is not None:
                if evidence["tool_name"] != "inverse_patch":
                    raise ValueError("inverse_evidence_scope_unavailable")
                if evidence["confirmed_applied"]:
                    result = dict(evidence["result"])
                    result["receipt_source"] = {"run_id": run_id, "tool_call_id": self.effect_id(review_id),
                                                "origin": "manual_inverse", "durability": "sealed_tool_ledger",
                                                "source_tool_call_id": row["source_tool_call_id"], "source_patch_id": row["source_patch_id"]}
                    state, code = "applied", ""
                elif evidence["operation_status"] == "failed":
                    state, code = "failed", "patch_operation_failed"
            db.execute("""UPDATE inverse_patch_reviews SET status=?,result_json=?,error_code=?,proposal_json=''
                WHERE source_run_id=? AND review_id=? AND status IN ('applying','indeterminate')""",
                       (state, _dump(result) if result is not None else "", code, run_id, review_id))
            # Full existing public/result/provenance validation before commit.
            # An absent/running/failed ledger row is not proof of no file effect.
            return self._public(self._row(db, run_id, review_id))
