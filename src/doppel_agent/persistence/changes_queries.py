"""Existing-only scoped patch/inverse evidence, never preimage export or recovery."""

from datetime import UTC, datetime, timedelta

from .inverse_reviews import InverseReviewStore, identifier
from .tool_ledger import ToolExecutionLedger
from .verification_queries import ExistingEvidenceQueries, _check


MAX_BLOB = 4 * 1024 * 1024
MAX_PAGE_DECODE = 16 * 1024 * 1024


def _date(value):
    try:
        date = datetime.fromisoformat(value)
        if date.utcoffset() != timedelta(0) or date.isoformat() != value:
            raise ValueError("changes_evidence_unavailable")
        return date
    except (TypeError, ValueError):
        raise ValueError("changes_evidence_unavailable") from None


class PatchEvidenceQueries(ExistingEvidenceQueries):
    required_tables = ("tool_executions", "patch_effect_receipts")

    @staticmethod
    def _evidence(db, run, call):
        # Size/count before any content fetch. Raw receipt/result are internal
        # framing only; never return stored receipt_json/private accepted bytes.
        size = db.execute("""SELECT length(CAST(t.result AS BLOB)),
            COALESCE(length(CAST(p.receipt_json AS BLOB)),0),COALESCE(length(CAST(p.summary_json AS BLOB)),0)
            FROM tool_executions t LEFT JOIN patch_effect_receipts p USING(run_id,tool_call_id)
            WHERE t.run_id=? AND t.tool_call_id=?""", (run, call)).fetchone()
        if size is None:
            raise KeyError(call)
        if any(type(n) is not int or not 0 <= n <= MAX_BLOB for n in size):
            raise ValueError("changes_evidence_unavailable")
        return sum(size)

    @staticmethod
    def _row(db, run, call):
        return db.execute("""SELECT
            CASE WHEN typeof(t.tool_name)='text' AND length(CAST(t.tool_name AS BLOB))<=32 THEN t.tool_name END AS tool_name,
            CASE WHEN typeof(t.status)='text' AND length(CAST(t.status AS BLOB))<=16 THEN t.status END AS status,
            CASE WHEN typeof(p.patch_id)='text' AND length(CAST(p.patch_id AS BLOB))=32 THEN p.patch_id END AS patch_id,
            p.patch_id IS NOT NULL AS has_receipt,
            CASE WHEN typeof(p.preimage_expired_at)='text' AND length(CAST(p.preimage_expired_at AS BLOB))<=64
                 THEN p.preimage_expired_at END AS preimage_expired_at,
            p.preimage_expired_at IS NOT NULL AS expired,
            CASE WHEN typeof(t.result)='text' AND length(CAST(t.result AS BLOB))<=4194304 THEN t.result END AS result,
            CASE WHEN typeof(p.receipt_json)='text' AND length(CAST(p.receipt_json AS BLOB))<=4194304 THEN p.receipt_json END AS receipt_json,
            CASE WHEN typeof(p.summary_json)='text' AND length(CAST(p.summary_json AS BLOB))<=4194304 THEN p.summary_json END AS summary_json
            FROM tool_executions t LEFT JOIN patch_effect_receipts p USING(run_id,tool_call_id)
            WHERE t.run_id=? AND t.tool_call_id=?""", (run, call)).fetchone()

    @classmethod
    def _public(cls, db, run, call):
        row = cls._row(db, run, call)
        if (row is None or row["has_receipt"] and row["patch_id"] is None
                or row["expired"] and row["preimage_expired_at"] is None):
            raise ValueError("changes_evidence_unavailable")
        if row["patch_id"] is not None:
            identifier(row["patch_id"], uuid=True)
        return ToolExecutionLedger._patch_evidence_row(row), row["preimage_expired_at"]

    def detail(self, run, call):
        identifier(run, uuid=True)
        identifier(call)
        with self._connection() as (db, deadline):
            source = self._source(db, run)
            size = self._evidence(db, run, call)
            value, _ = self._public(db, run, call)
            _check(deadline)
            value["source"] = {"run_id": run, "tool_call_id": call}
            value["source_run"] = source
            value["evidence"] = "durable_patch_effect_not_current_git_ownership"
            value["output"] = {"sensitive": True, "globally_redacted": False, "accepted_preimage_blobs_exported": False}
            value["query"] = {"sql_read_only": True, "decode_bytes": size, "filesystem_zero_write_guarantee": False}
            return value

    def list(self, run, *, limit=100, offset=0):
        identifier(run, uuid=True)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError("invalid_patch_receipt_page")
        with self._connection() as (db, deadline):
            source = self._source(db, run)
            candidates = db.execute("""SELECT
                CASE WHEN typeof(tool_call_id)='text' AND length(CAST(tool_call_id AS BLOB))<=1024 THEN tool_call_id END AS tool_call_id,
                CASE WHEN typeof(created_at)='text' AND length(CAST(created_at AS BLOB))<=64 THEN created_at END AS created_at
                FROM patch_effect_receipts WHERE run_id=? ORDER BY created_at,tool_call_id LIMIT ? OFFSET ?""",
                (run, limit, offset)).fetchall()
            items, consumed = [], 0
            for candidate in candidates:
                _check(deadline)
                call = identifier(candidate["tool_call_id"])
                _date(candidate["created_at"])
                size = self._evidence(db, run, call)
                if consumed + size > MAX_PAGE_DECODE:
                    raise ValueError("patch_receipt_page_budget_exceeded")
                evidence, expired_at = self._public(db, run, call)
                if evidence["receipt"] is None:
                    raise ValueError("changes_evidence_unavailable")
                consumed += size
                items.append({"run_id": run, "tool_call_id": call, "tool_name": evidence["tool_name"],
                    "operation_status": evidence["operation_status"], "created_at": candidate["created_at"],
                    "receipt": evidence["receipt"], "preimage_state": evidence["preimage_state"],
                    "preimage_expired_at": expired_at, "confirmed_applied": evidence["confirmed_applied"],
                    "evidence": "durable_patch_effect_not_current_git_ownership"})
            _check(deadline)
            return {"items": items, "source_run": source, "query": {"sql_read_only": True, "decode_bytes": consumed,
                    "filesystem_zero_write_guarantee": False, "page_decode_limit_bytes": MAX_PAGE_DECODE}}


class InverseEvidenceQueries(ExistingEvidenceQueries):
    required_tables = ("inverse_patch_reviews",)

    def detail(self, run, review):
        identifier(run, uuid=True)
        identifier(review, uuid=True)
        with self._connection() as (db, deadline):
            source = self._source(db, run)
            size = db.execute("""SELECT length(CAST(proposal_json AS BLOB)),length(CAST(review_json AS BLOB)),
                length(CAST(result_json AS BLOB)),
                length(CAST(source_tool_call_id AS BLOB)),length(CAST(created_at AS BLOB)),length(CAST(expires_at AS BLOB)),
                length(CAST(status AS BLOB)),length(CAST(decision AS BLOB)),length(CAST(error_code AS BLOB)),
                length(CAST(source_patch_id AS BLOB)),length(CAST(patch_id AS BLOB))
                FROM inverse_patch_reviews WHERE source_run_id=? AND review_id=?""", (run, review)).fetchone()
            if size is None:
                raise ValueError("inverse_review_scope_unavailable")
            limits = (MAX_BLOB, MAX_BLOB, MAX_BLOB, 1024, 64, 64, 32, 16, 64, 32, 32)
            if any(type(n) is not int or not 0 <= n <= maximum for n, maximum in zip(size, limits, strict=True)):
                raise ValueError("changes_evidence_unavailable")
            row = InverseReviewStore._row(db, run, review)
            expected = ("" if row["status"] in {"pending", "expired"} else
                        "reject" if row["status"] == "rejected" else "approve")
            if row["decision"] != expected:
                raise ValueError("inverse_review_unavailable")
            if row["status"] == "pending":
                InverseReviewStore._pending_proposal(row)  # Exact hash/projection, no workspace reads.
            value = InverseReviewStore._public(row)
            expired = row["status"] == "pending" and _date(row["expires_at"]) <= datetime.now(UTC)
            value["lifecycle"] = {"stored_status": row["status"], "effective_status": "expired" if expired else row["status"],
                "pending_expired": expired, "approval_available": False, "retry_available": False}
            value["source_run"] = source
            value["output"] = {"sensitive": True, "globally_redacted": False, "raw_proposal_blob_exported": False}
            value["query"] = {"sql_read_only": True, "decode_bytes": sum(size[:3]), "filesystem_zero_write_guarantee": False}
            _check(deadline)
            return value
