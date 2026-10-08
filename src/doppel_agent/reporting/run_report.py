"""Allowlist-only numeric report. Never export arbitrary private stored text.

Provider counters observed in recorded events are not complete billing, prices,
model quality, an account cap, source ownership or physical resource-drain proof.
"""

from __future__ import annotations

import re

from ..provider_usage import MAX_SAFE, parse_usage as parse_usage


MAX_EVENTS = 5000
MODEL_KINDS = frozenset({'graph.model_finished', 'deep.model_finished', 'provider.model_finished'})
COUNT_KINDS = frozenset({'graph.tool_finished', 'deep.tool_finished', 'mcp.tool_executed', 'patch.applied'})
STATES = frozenset({'queued', 'running', 'interrupted', 'completed', 'failed', 'cancelled', 'interrupted_expired'})
MODES = frozenset({'legacy', 'graph', 'deep'})


def _integer(value):
    return type(value) is int and 0 <= value <= MAX_SAFE


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) is not None


def _subtotal(items):
    known = len(items)
    inputs, outputs = sum(item[0] for item in items), sum(item[1] for item in items)
    overflow = inputs + outputs > MAX_SAFE
    return {'known_model_events': known, 'input_tokens': inputs if known and not overflow else None,
            'output_tokens': outputs if known and not overflow else None, 'subtotal_overflow': overflow}


def build_report(run_id: str, source: dict, events: list[dict], *, total: int, high_water: int,
                 omitted_usage_rows: int, truncated: bool) -> dict:
    if (not _identifier(run_id) or source.get('status') not in STATES or source.get('mode') not in MODES
            or type(source.get('lease_active')) is not bool or any(not _integer(item) for item in (total, high_water, omitted_usage_rows))
            or type(truncated) is not bool or len(events) > MAX_EVENTS or len(events) > total):
        raise ValueError('report_evidence_unavailable')
    root, children, observed = [], {}, []
    unknown = 0  # undecodable child envelopes aren't proven model events
    counts = {kind: 0 for kind in sorted(COUNT_KINDS)}
    previous = 0
    for event in events:
        seq = event.get('seq')
        if not _integer(seq) or not previous < seq <= high_water:
            raise ValueError('report_evidence_unavailable')
        previous = seq
        kind, payload = event.get('type'), event.get('payload')
        payload = payload if isinstance(payload, dict) else {}
        scope = None
        if kind == 'subagent.runtime':
            kind, nested = payload.get('runtime_kind'), payload.get('runtime_payload')
            if type(kind) is not str:
                continue  # malformed envelope cannot impersonate a model/tool enum
            if kind in MODEL_KINDS:
                if (payload.get('parent_run_id') != run_id or not _identifier(payload.get('subagent_id'))
                        or not _integer(payload.get('generation')) or payload['generation'] < 1):
                    unknown += 1
                    continue
                scope = (payload['subagent_id'], payload['generation'])
            payload = nested if isinstance(nested, dict) else {}
        if kind in COUNT_KINDS:
            counts[kind] += 1
        if kind not in MODEL_KINDS:
            continue
        usage = parse_usage(payload.get('usage'))
        if usage is None:
            unknown += 1
            continue
        observed.append(usage)
        (root if scope is None else children.setdefault(scope, [])).append(usage)
    subtotal = _subtotal(observed)
    partial = unknown > 0 or omitted_usage_rows > 0 or truncated or subtotal['subtotal_overflow']
    state = 'partial' if observed and partial else 'unknown' if not observed else 'known_for_recorded_model_events'
    return {
        'version': 1, 'run_id': run_id,
        'source': {'status': source['status'], 'mode': source['mode'], 'lease_active': source['lease_active']},
        'snapshot': {'event_total': total, 'event_high_water': high_water, 'scanned_events': len(events),
                     'limit': MAX_EVENTS, 'truncated': truncated, 'omitted_usage_rows': omitted_usage_rows,
                     'consistency': 'same_runtime_database_read_transaction'},
        'redaction': {'policy': 'allowlisted_identifiers_enums_numeric_counters_only', 'free_text_exported': False,
                      'anonymous': False, 'original_evidence_modified': False},
        'usage': {'state': state, 'observed': subtotal, 'root': _subtotal(root),
                  'children': [{'subagent_id': child, 'generation': generation, **_subtotal(items)}
                               for (child, generation), items in sorted(children.items())],
                  'unknown_model_events': unknown, 'unclassified_usage_envelopes': omitted_usage_rows, 'billing_complete': False,
                  'old_child_attribution': 'missing_tags_not_reconstructed', 'estimated_tokens_as_actual': False},
        'cost': {'state': 'unknown', 'amount': None, 'currency': None, 'price_snapshot': None,
                 'reason': 'no_frozen_billing_price_receipt', 'account_cap_guaranteed': False},
        'recorded_effect_counts': counts,
        'physical_drain_verified': False, 'task_quality_scored': False,
        'query': {'sql_read_only': True, 'filesystem_zero_write_guarantee': False},
    }
