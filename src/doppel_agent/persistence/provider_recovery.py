"""Bounded SQL-read-only ORIGINAL receipt closure check before startup admission.

No report projection/samples, stores, migrations, provider/settings/key lookup,
retry, completion invention or effect reconciliation. The caller must pin the
original workspace owner and join this worker before starting execution pumps.
A closed recorded pair is not physical drain, full billing or absence of hidden
effects. Missing/limited/unreadable evidence MUST NOT clear a quarantine.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from ..provider_receipts import (
    CALL_FINISH, REQUEST_START, REQUEST_FINISH, RECEIPT_KINDS,
    _equal, _expected_summary, _frame, _group, _identifier, _integer,
)
from .verification_queries import QUERY_SECONDS, _check, _safe_database


MAX_ROWS = 10000
MAX_BODY = 65536
MAX_DECODE = 8 * 1024 * 1024


def _thread_identifier(value):
    # ORIGINAL native conversations generate native-<uuid hex>; provider call/
    # request/run identifiers remain strict32. Do not alias/strip this prefix.
    return type(value) is str and re.fullmatch(r'(?:native-)?[0-9a-f]{32}', value) is not None


@dataclass(frozen=True)
class ProviderReceiptRecovery:
    quarantined: bool
    reason: str
    receipt_rows: int = 0
    last_seq: int = 0


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('ambiguous_original_receipt')
        result[key] = value
    return result


def _constant(_value):
    raise ValueError('nonfinite_original_receipt')


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite_original_receipt')
    return result


class ProviderReceiptRecoveryQueries:
    def __init__(self, database: Path):
        self.database = database  # Path only, no constructor IO/creation.

    def read(self) -> ProviderReceiptRecovery:
        deadline = monotonic() + QUERY_SECONDS
        connection = None
        try:
            database = _safe_database(self.database, deadline)
            connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            connection.execute('BEGIN')
            # Bound scalar registration metadata before loading any body. All
            # root receipts and child carriers, not a report's selected prefix.
            rows = connection.execute('''SELECT e.seq,e.type,
                CASE WHEN typeof(e.run_id)='text' AND length(CAST(e.run_id AS BLOB))=32 THEN e.run_id END AS run_id,
                CASE WHEN typeof(e.thread_id)='text' AND length(CAST(e.thread_id AS BLOB)) IN (32,39) THEN e.thread_id END AS thread_id,
                CASE WHEN typeof(r.thread_id)='text' AND length(CAST(r.thread_id AS BLOB)) IN (32,39) THEN r.thread_id END AS registered_thread,
                CASE WHEN typeof(r.mode)='text' AND length(CAST(r.mode AS BLOB))<=16 THEN r.mode END AS registered_mode,
                typeof(e.payload_json) AS body_type,length(CAST(e.payload_json AS BLOB)) AS body_size
                FROM runtime_events e LEFT JOIN runtime_runs r ON r.run_id=e.run_id
                WHERE e.type IN ('provider.call_started','provider.call_finished',
                    'provider.request_started','provider.request_finished','subagent.runtime','deep.fallback')
                ORDER BY e.seq LIMIT ?''', (MAX_ROWS + 1,)).fetchall()
            _check(deadline)
            if len(rows) > MAX_ROWS:
                return ProviderReceiptRecovery(True, 'evidence_limit')
            return self._scan(connection, rows, deadline)
        except (OSError, sqlite3.Error, ValueError, TypeError, RecursionError, OverflowError):
            # No raw database paths/errors/private contents published. Failure,
            # missing schema, query timeout or corrupt JSON cannot mean empty.
            return ProviderReceiptRecovery(True, 'evidence_unavailable')
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _scan(connection, rows, deadline):
        calls, requests = {}, {}
        fallback_scopes = set()
        consumed = count = last = 0
        for row in rows:
            _check(deadline)
            seq, run_id, thread_id = row['seq'], row['run_id'], row['thread_id']
            if (not _integer(seq, positive=True) or seq <= last or not _identifier(run_id)
                    or not _thread_identifier(thread_id) or thread_id != row['registered_thread']):
                return ProviderReceiptRecovery(True, 'scope_unavailable', count, last)
            last = seq
            size = row['body_size']
            if row['body_type'] != 'text' or type(size) is not int or not 0 < size <= MAX_BODY:
                return ProviderReceiptRecovery(True, 'evidence_limit', count, last)
            consumed += size
            if consumed > MAX_DECODE:
                return ProviderReceiptRecovery(True, 'evidence_limit', count, last)
            body = connection.execute('SELECT payload_json FROM runtime_events WHERE seq=?', (seq,)).fetchone()
            _check(deadline)
            payload = json.loads(body[0], object_pairs_hook=_object, parse_constant=_constant, parse_float=_float)
            kind, scope = row['type'], (run_id,)
            if kind == 'deep.fallback':
                # Original Deep may invoke its existing Graph fallback WITHOUT
                # changing the registered run mode. Only this prior, bounded,
                # same-run/thread decision admits Graph receipt frames; never
                # arbitrary mixed engines or a later/foreign/child marker.
                if (row['registered_mode'] != 'deep' or type(payload) is not dict
                        or set(payload) != {'runtime', 'error'} or payload['runtime'] != 'graph'
                        or type(payload['error']) is not str
                        or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*: deep execution failed', payload['error']) is None):
                    return ProviderReceiptRecovery(True, 'scope_unavailable', count, last)
                fallback_scopes.add(scope)
                continue  # Provenance only, not a provider/transport/billing row.
            if kind == 'subagent.runtime':
                if type(payload) is not dict or type(payload.get('runtime_kind')) is not str:
                    return ProviderReceiptRecovery(True, 'malformed_receipts', count, last)
                kind = payload['runtime_kind']
                if kind not in RECEIPT_KINDS:
                    continue  # Decoded carrier only; never interpret tool/model callback as closure.
                child, generation = payload.get('subagent_id'), payload.get('generation')
                if (payload.get('parent_run_id') != run_id or not _identifier(child)
                        or not _integer(generation, positive=True)):
                    return ProviderReceiptRecovery(True, 'scope_unavailable', count, last)
                registration = connection.execute('''SELECT parent_run_id,generation FROM async_subagents
                    WHERE subagent_id=? AND parent_run_id=?''', (child, run_id)).fetchone()
                if (registration is None or not _integer(registration['generation'], positive=True)
                        or generation > registration['generation']):
                    return ProviderReceiptRecovery(True, 'scope_unavailable', count, last)
                scope = (run_id, child, generation)  # Preserve prior generation, no current rebind.
                payload = payload.get('runtime_payload')
            count += 1
            request = kind in {REQUEST_START, REQUEST_FINISH}
            finish = kind in {CALL_FINISH, REQUEST_FINISH}
            data = _frame(payload, request=request, finish=finish)
            if data is None:
                return ProviderReceiptRecovery(True, 'malformed_receipts', count, last)
            expected_engine = 'graph' if len(scope) == 3 else row['registered_mode']
            if data['engine'] != expected_engine and not (
                    expected_engine == 'deep' and data['engine'] == 'graph' and scope in fallback_scopes):
                return ProviderReceiptRecovery(True, 'scope_unavailable', count, last)
            identity = data['attempt_id' if request else 'call_id']
            (requests if request else calls).setdefault(identity, []).append(
                dict(seq=seq, scope=scope, finish=finish, data=data))
        _check(deadline)
        groups = {key: _group(frames) for key, frames in calls.items()}
        attempts = {key: _group(frames) for key, frames in requests.items()}
        if any(group['state'] != 'matched' for group in (*groups.values(), *attempts.values())):
            return ProviderReceiptRecovery(True, 'unresolved_receipts', count, last)
        linked = {key: [] for key in groups}
        for attempt in attempts.values():
            _check(deadline)
            start, finish = attempt['start'], attempt['finish']
            parent_id = start['data']['call_id']
            parent = groups.get(parent_id)
            if parent is None:
                return ProviderReceiptRecovery(True, 'unresolved_receipts', count, last)
            pstart, pfinish = parent['start'], parent['finish']
            if (start['scope'] != pstart['scope'] or not pstart['seq'] < start['seq'] < finish['seq'] < pfinish['seq']
                    or any(not _equal(start['data'].get(key), pstart['data'].get(key))
                           for key in ('version', 'engine', 'actor', 'price_receipt'))
                    or finish['data']['failure'] == 'response_cleanup'):
                return ProviderReceiptRecovery(True, 'unresolved_receipts', count, last)
            linked[parent_id].append(attempt)
        for identity, parent in groups.items():
            _check(deadline)
            observed = linked[identity]
            start, finish = parent['start']['data'], parent['finish']['data']
            if start['version'] == 1:
                if observed:
                    return ProviderReceiptRecovery(True, 'unresolved_receipts', count, last)
                continue
            transport = finish['transport']
            indices = sorted(item['start']['data']['attempt_index'] for item in observed)
            if (indices != list(range(1, len(observed) + 1))
                    or observed and transport['coverage'] == 'opaque'
                    or not _equal(transport, _expected_summary(observed, transport['coverage']))):
                return ProviderReceiptRecovery(True, 'unresolved_receipts', count, last)
        return ProviderReceiptRecovery(False, 'no_unresolved_receipts_found', count, last)
