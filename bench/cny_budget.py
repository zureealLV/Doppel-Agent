"""Shared, fail-closed request ledger for the bounded official DeepSeek canary.

No exchange rate, cache discount or off-peak discount is assumed. Amounts are
conservative CNY upper bounds, NOT an assertion of the provider's billed cost.
The guarantee depends on the frozen provider token/price bounds being honoured;
an account-side cap is still required. A lost response is never auto-refunded.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from bench.runtime_freeze import canonical_json


class BudgetStopped(RuntimeError):
    def __init__(self, failure_class: str):
        self.failure_class = failure_class
        super().__init__(failure_class)


class CnyBudgetLedger:
    currency = "CNY"
    input_price = Decimal("2")  # peak, all input treated as cache miss
    output_price = Decimal("8")
    max_input_tokens = 1_048_576  # entire documented 1M context, not a text estimate
    max_tokens = 4096
    total = Decimal("10")
    price_checked_date = "2026-10-03"

    def __init__(self, path: Path, identity: dict):
        self.path = path.resolve()
        self.configuration = {
            "schema_version": "1.0", "identity": identity,
            "currency": self.currency, "total_cny": str(self.total),
            "input_price_per_million": str(self.input_price),
            "output_price_per_million": str(self.output_price),
            "max_input_tokens": self.max_input_tokens, "max_tokens": self.max_tokens,
            "thinking": "disabled", "temperature": 0,
            "price_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
            "price_checked_date": self.price_checked_date, "cost_basis": "peak_uncached_upper_bound",
        }
        self._context = canonical_json(self.configuration).decode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS context (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, run_key TEXT NOT NULL, state TEXT NOT NULL,
                reserved TEXT NOT NULL, charged TEXT, prompt_tokens INTEGER,
                completion_tokens INTEGER, failure_class TEXT, started_utc TEXT NOT NULL
            )""")
            db.execute("CREATE TABLE IF NOT EXISTS run_claims (run_key TEXT PRIMARY KEY, state TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO context VALUES (1, ?)", (self._context,))
            self._check(db)

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            yield db

    def _check(self, db):
        row = db.execute("SELECT value FROM context WHERE id=1").fetchone()
        if row is None or row[0] != self._context:
            raise ValueError("shared budget configuration differs; execution stopped")

    @classmethod
    def cost(cls, prompt_tokens: int, completion_tokens: int) -> Decimal:
        return ((Decimal(prompt_tokens) * cls.input_price + Decimal(completion_tokens) * cls.output_price)
                / 1_000_000).quantize(Decimal("0.000001"))

    def reserve(self, run_key: str) -> str:
        """Commit one worst-case reservation BEFORE sending HTTP; serialize callers."""
        amount = self.cost(self.max_input_tokens, self.max_tokens)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check(db)
            if db.execute("SELECT 1 FROM attempts WHERE state != 'settled' LIMIT 1").fetchone():
                raise BudgetStopped("budget_unsettled")
            spent = sum((Decimal(row[0]) for row in db.execute("SELECT charged FROM attempts")), Decimal(0))
            if spent + amount > self.total:
                raise BudgetStopped("budget_exhausted")
            attempt = uuid4().hex
            db.execute("INSERT INTO attempts (id, run_key, state, reserved, started_utc) VALUES (?, ?, 'pending', ?, ?)",
                       (attempt, run_key, str(amount), datetime.now(UTC).isoformat()))
        return attempt

    def settle(self, attempt: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
        if (type(prompt_tokens) is not int or type(completion_tokens) is not int
                or not 0 <= prompt_tokens <= self.max_input_tokens
                or not 0 <= completion_tokens <= self.max_tokens):
            # Deliberately retain the committed reservation. It may be billed,
            # and neither the cost nor the promised bound can now be trusted.
            raise BudgetStopped("usage_bound_violation")
        amount = self.cost(prompt_tokens, completion_tokens)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check(db)
            changed = db.execute("""UPDATE attempts SET state='settled', charged=?,
                prompt_tokens=?, completion_tokens=? WHERE id=? AND state='pending'""",
                (str(amount), prompt_tokens, completion_tokens, attempt)).rowcount
            if changed != 1:
                raise ValueError("attempt is not pending; refusing duplicate settlement")
        return amount

    def claim_run(self, run_key: str) -> None:
        """An interrupted run is not safe to replay just because its JSON is absent."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check(db)
            if (db.execute("SELECT 1 FROM attempts WHERE run_key=? LIMIT 1", (run_key,)).fetchone()
                    or db.execute("SELECT 1 FROM run_claims WHERE run_key=?", (run_key,)).fetchone()):
                raise BudgetStopped("budget_orphaned_run")
            if (db.execute("SELECT 1 FROM run_claims WHERE state='active' LIMIT 1").fetchone()
                    or db.execute("SELECT 1 FROM attempts WHERE state != 'settled' LIMIT 1").fetchone()):
                raise BudgetStopped("budget_unsettled")
            db.execute("INSERT INTO run_claims VALUES (?, 'active')", (run_key,))

    def finish_run(self, run_key: str) -> None:
        # Call only AFTER immutable result publication. A crash on either side
        # leaves a claim that stops subsequent execution, never a free replay.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check(db)
            if db.execute("UPDATE run_claims SET state='complete' WHERE run_key=? AND state='active'",
                          (run_key,)).rowcount != 1:
                raise ValueError("run is not active")

    def run_account(self, run_key: str) -> tuple[Decimal | None, int, int]:
        with self._connect() as db:
            self._check(db)
            rows = db.execute("SELECT state, charged, prompt_tokens, completion_tokens FROM attempts WHERE run_key=?",
                              (run_key,)).fetchall()
        if any(row[0] != "settled" for row in rows):
            return None, 0, 0
        return (sum((Decimal(row[1]) for row in rows), Decimal(0)),
                sum(row[2] for row in rows), sum(row[3] for row in rows))

    def summary(self) -> dict:
        with self._connect() as db:
            self._check(db)
            rows = db.execute("SELECT state, reserved, charged FROM attempts").fetchall()
            active = db.execute("SELECT count(*) FROM run_claims WHERE state='active'").fetchone()[0]
        charged = sum((Decimal(row[2]) for row in rows if row[2] is not None), Decimal(0))
        held = sum((Decimal(row[1]) for row in rows if row[0] != "settled"), Decimal(0))
        return {"currency": self.currency, "total_cny": str(self.total),
                "charged_upper_bound_cny": str(charged), "unsettled_reserved_cny": str(held),
                "available_cny": str(self.total - charged - held), "request_count": len(rows),
                "blocked_unknown": any(row[0] != "settled" for row in rows) or bool(active),
                "active_run_claims": active,
                "provider_billed_cost_known": False}
