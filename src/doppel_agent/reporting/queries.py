"""One bounded, same-DB transaction over original registered runs/events only."""

import json
import math
import sqlite3
from time import monotonic

from ..persistence.verification_queries import QUERY_SECONDS, _check, _safe_database
from .run_report import MAX_EVENTS, MODEL_KINDS, build_report
from .provider_usage import RECEIPT_KINDS, build_provider_usage
from .provider_cost import build_provider_cost
from .read_lifetime import ReportReadCleanupError, ReportReadSource


MAX_PAYLOAD = 65536
MAX_DECODE = 8 * 1024 * 1024


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('report_json_ambiguous')
        value[key] = item
    return value


def _constant(_value):
    raise ValueError('report_json_nonfinite')


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError('report_json_nonfinite')
    return parsed


class RunReportQueries:
    def __init__(self, database, ledger_database=None, *, failure=None, cleanup_failure=None):
        self.database = database  # paths only, no creation/migrations/IO
        self.ledger_database = ledger_database
        self._failure, self._cleanup_failure = failure, cleanup_failure
        self._unresolved_reads = {}

    def _retain_read(self, source):
        self._unresolved_reads[id(source)] = source
        if self._cleanup_failure is not None:
            self._cleanup_failure(source)

    def _check_read(self):
        if self._unresolved_reads:
            raise ReportReadCleanupError(next(iter(self._unresolved_reads.values())))

    def read_full(self, run_id, *, provider_usage=False, billing=False):
        from .evidence import ReportEvidenceQueries

        self._check_read()
        if type(provider_usage) is not bool or type(billing) is not bool or billing and not provider_usage:
            raise ValueError('report_evidence_unavailable')

        # One original service worker joins both bounded phases. Runtime event
        # and original receipt phases are deliberately labelled NOT atomic.
        # Each phase has its own checked3s SQL/path budget, not a hard OS deadline.
        # Strict direct-reader opt-ins preserve default v1/v2 and usage-only v3.
        # Original service now selects paired full4; no HTTP flags/new worker,
        # deployment/native acceptance implied or current-price lookup.
        report = self.read(run_id, provider_usage=True, billing=True) if billing else self.read(run_id, provider_usage=True) if provider_usage else self.read(run_id)
        if self.ledger_database is None:
            raise ValueError('report_evidence_unavailable')
        report['version'] = 4 if billing else 3 if provider_usage else 2
        report['evidence'] = ReportEvidenceQueries(self.database, self.ledger_database,
            failure=self._failure, cleanup_failure=self._retain_read).read(run_id)
        return report

    def read(self, run_id, *, provider_usage=False, billing=False):
        self._check_read()
        if type(provider_usage) is not bool or type(billing) is not bool or billing and not provider_usage:
            raise ValueError('report_evidence_unavailable')
        lifetime = None
        deadline = monotonic() + QUERY_SECONDS
        try:
            path = _safe_database(self.database, deadline)
            lifetime = ReportReadSource(failure=self._failure, cleanup_failure=self._retain_read)
            connection = lifetime.open_original(sqlite3.connect, path.as_uri() + '?mode=ro')
            connection.row_factory = sqlite3.Row
            connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA trusted_schema=OFF')
            connection.execute('BEGIN')
            source = connection.execute('''SELECT
                CASE WHEN typeof(status)='text' AND length(CAST(status AS BLOB))<=32 THEN status END AS status,
                CASE WHEN typeof(mode)='text' AND length(CAST(mode AS BLOB))<=16 THEN mode END AS mode,
                CASE WHEN typeof(lease_active)='integer' THEN lease_active END AS lease_active
                FROM runtime_runs WHERE run_id=?''', (run_id,)).fetchone()
            if source is None:
                raise KeyError('report_scope_not_found')
            if source['lease_active'] not in (0, 1):
                raise ValueError('report_evidence_unavailable')
            # OPTIONAL original saved profile body, SAME held transaction. First
            # bound bytes before loading; no settings/latest provider/credential
            # accessor. Raw snapshot never exported. Cost omission isn't zero.
            profile, profile_omitted, consumed = None, False, 0
            if billing:
                metadata = connection.execute('SELECT typeof(profile_json),length(CAST(profile_json AS BLOB)) FROM runtime_runs WHERE run_id=?',
                                              (run_id,)).fetchone()
                _check(deadline)
                size = metadata[1] if metadata is not None else None
                if metadata is None or type(size) is not int or size <= 0 or size > MAX_PAYLOAD or size > MAX_DECODE:
                    profile_omitted = True
                elif metadata[0] == 'text':
                    consumed = size
                    try:
                        body = connection.execute('SELECT profile_json FROM runtime_runs WHERE run_id=?', (run_id,)).fetchone()
                        _check(deadline)
                        profile = json.loads(body[0], object_pairs_hook=_object, parse_constant=_constant, parse_float=_finite_float)
                        if type(profile) is not dict:
                            profile = None
                    except (ValueError, TypeError, RecursionError, OverflowError):
                        profile = None
            totals = connection.execute('SELECT COUNT(*),COALESCE(MAX(seq),0) FROM runtime_events WHERE run_id=?', (run_id,)).fetchone()
            selected_kinds = MODEL_KINDS | RECEIPT_KINDS if provider_usage else MODEL_KINDS
            placeholders = ','.join('?' for _ in selected_kinds)
            # SQL omits all nonusage payloads before they reach Python; arbitrary
            # free text/type/error/settings is never returned by the projection.
            # Iterate metadata first. Reserve bounded encoded bytes BEFORE even
            # fetching a selected body, using this SAME original transaction.
            # Not a hard SQLite/OS memory/time guarantee or another executor.
            rows = connection.execute(f'''SELECT seq,
                CASE WHEN length(CAST(type AS BLOB))<=128 THEN type END AS type,
                CASE WHEN type IN ({placeholders},'subagent.runtime')
                    THEN length(CAST(payload_json AS BLOB)) ELSE 0 END AS size
                FROM runtime_events WHERE run_id=? ORDER BY seq LIMIT ?''',
                (*sorted(selected_kinds), run_id, MAX_EVENTS))
            events, omitted = [], 0
            for row in rows:
                _check(deadline)
                payload = None
                missing_body = (provider_usage and (row['type'] in selected_kinds or row['type'] == 'subagent.runtime')
                                and (type(row['size']) is not int or row['size'] <= 0))
                if missing_body:
                    omitted += 1
                elif row['size']:
                    if type(row['size']) is not int or row['size'] < 0 or row['size'] > MAX_PAYLOAD or consumed + row['size'] > MAX_DECODE:
                        omitted += 1
                    else:
                        consumed += row['size']
                        try:
                            body = connection.execute('SELECT payload_json FROM runtime_events WHERE run_id=? AND seq=?',
                                                      (run_id, row['seq'])).fetchone()
                            _check(deadline)
                            options = {'parse_float': _finite_float} if provider_usage else {}
                            payload = json.loads(body[0] if body is not None else None,
                                                 object_pairs_hook=_object, parse_constant=_constant, **options)
                            if provider_usage and type(payload) is not dict:
                                raise ValueError('report_json_frame_invalid')
                        except (ValueError, TypeError, RecursionError, OverflowError):
                            payload = None
                            omitted += 1
                events.append({'seq': row['seq'], 'type': row['type'] if payload is not None or not row['size'] and not missing_body else None, 'payload': payload})
            report = build_report(run_id, {'status': source['status'], 'mode': source['mode'], 'lease_active': bool(source['lease_active'])},
                                  events, total=totals[0], high_water=totals[1], omitted_usage_rows=omitted, truncated=totals[0] > MAX_EVENTS)
            report['query']['decoded_bytes'] = consumed
            if provider_usage:
                report['version'] = 4 if billing else 3  # direct opts; original service owns full4/evidence pairing
                report['usage_basis'] = 'provider_usage_only_not_compat_model_events'
                units = [] if billing else None
                report['provider_usage'] = build_provider_usage(run_id, events,
                    omitted_usage_rows=omitted, truncated=totals[0] > MAX_EVENTS,
                    **({'pricing_units': units} if billing else {}))
                if billing:
                    report['cost'] = build_provider_cost(units, profile, report['provider_usage'], source_omitted=profile_omitted)
                    report['redaction']['policy'] = 'allowlisted_identifiers_enums_numeric_counters_and_tariff_contract_only'
            _check(deadline)
            return report
        except (KeyError, ReportReadCleanupError):
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError, RecursionError):
            raise ValueError('report_evidence_unavailable') from None
        finally:
            if lifetime is not None:
                lifetime.close_original()  # SAME worker/thread, independent once disposal.
