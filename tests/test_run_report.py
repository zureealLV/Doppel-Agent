"""S8 A pure report definitions, UNRUN. Numeric projection is not billing proof."""

import json

import pytest

from doppel_agent.reporting.run_report import build_report, parse_usage


PARENT, CHILD = 'a' * 32, 'b' * 32


def event(seq, kind='graph.model_finished', usage=None):
    return {'seq': seq, 'type': kind, 'payload': {'usage': usage}}


@pytest.mark.parametrize('usage', [None, {}, {'input_tokens': 1}, {'input_tokens': True, 'output_tokens': 2},
    {'input_tokens': -1, 'output_tokens': 2}, {'input_tokens': 1.0, 'output_tokens': 2},
    {'input_tokens': 2**53, 'output_tokens': 2}, {'input_tokens': 1, 'prompt_tokens': 2, 'output_tokens': 3},
    {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 4}, {'input_tokens': 'secret', 'output_tokens': 2}])
def test_missing_invalid_conflicting_usage_is_not_zero(usage):
    assert parse_usage(usage) is None


def test_aliases_and_explicit_numeric_zero_are_actual_counters():
    assert parse_usage({'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}) == (7, 3)
    assert parse_usage({'input_tokens': 0, 'output_tokens': 0}) == (0, 0)


def test_allowlist_export_never_includes_arbitrary_private_strings_or_paths():
    private = 'UNRECOGNIZABLE_CREDENTIAL_SENTINEL <script> nested PRIVATE_REASONING'
    rows = [event(1, usage={'input_tokens': 7, 'output_tokens': 3, 'arbitrary': private}),
            {'seq': 2, 'type': private, 'payload': {'prompt': private, 'path': 'C:/PRIVATE', 'api_key': private}},
            {'seq': 3, 'type': 'deep.tool_started', 'payload': {'input_preview': private}}]
    report = build_report(PARENT, {'status': 'completed', 'mode': 'graph', 'lease_active': False, 'prompt': private}, rows,
                          total=3, high_water=3, omitted_usage_rows=0, truncated=False)
    encoded = json.dumps(report)
    assert private not in encoded and 'C:/PRIVATE' not in encoded and 'PRIVATE_REASONING' not in encoded
    assert report['redaction']['free_text_exported'] is False and report['redaction']['anonymous'] is False
    assert report['usage']['observed']['input_tokens'] == 7 and report['cost']['amount'] is None
    assert report['usage']['billing_complete'] is False and report['physical_drain_verified'] is False


def test_original_outer_child_lineage_cannot_be_overridden_by_nested_payload():
    nested = {'seq': 2, 'type': 'subagent.runtime', 'payload': {'parent_run_id': PARENT, 'subagent_id': CHILD, 'generation': 3,
        'runtime_kind': 'graph.model_finished', 'runtime_payload': {'usage': {'input_tokens': 4, 'output_tokens': 1},
        'parent_run_id': 'c' * 32, 'subagent_id': 'd' * 32, 'generation': 99}}}
    report = build_report(PARENT, {'status': 'completed', 'mode': 'deep', 'lease_active': False},
        [event(1, usage={'input_tokens': 2, 'output_tokens': 1}), nested], total=2, high_water=2, omitted_usage_rows=0, truncated=False)
    assert report['usage']['root']['input_tokens'] == 2 and report['usage']['children'][0]['generation'] == 3
    assert report['usage']['children'][0]['subagent_id'] == CHILD
    assert report['usage']['observed']['input_tokens'] == 6
    nested['payload']['parent_run_id'] = 'c' * 32
    bad = build_report(PARENT, {'status': 'completed', 'mode': 'deep', 'lease_active': False}, [nested],
        total=1, high_water=2, omitted_usage_rows=0, truncated=False)
    assert bad['usage']['unknown_model_events'] == 1 and bad['usage']['observed']['input_tokens'] is None


def test_empty_missing_partial_and_bounded_records_never_claim_free_or_complete():
    for rows, total, omitted, truncated in [([], 0, 0, False), ([event(1)], 1, 0, False),
        ([event(1, usage={'input_tokens': 0, 'output_tokens': 0})], 2, 1, False),
        ([event(1, usage={'input_tokens': 4, 'output_tokens': 2})], 5001, 0, True)]:
        report = build_report(PARENT, {'status': 'completed', 'mode': 'legacy', 'lease_active': False}, rows,
            total=total, high_water=max(total, 1), omitted_usage_rows=omitted, truncated=truncated)
        assert report['usage']['state'] in {'unknown', 'partial'}
        assert report['usage']['billing_complete'] is False and report['cost']['amount'] is None


def test_invalid_export_source_or_state_fails_closed():
    with pytest.raises(ValueError):
        build_report('PRIVATE_SOURCE', {}, [], total=0, high_water=0, omitted_usage_rows=0, truncated=False)


def test_cross_event_subtotal_overflow_is_unknown_not_rounded_for_javascript():
    rows = [event(i, usage={'input_tokens': 7_000_000_000_000_000, 'output_tokens': 0}) for i in (1, 2)]
    report = build_report(PARENT, {'status': 'completed', 'mode': 'graph', 'lease_active': False}, rows,
        total=2, high_water=2, omitted_usage_rows=0, truncated=False)
    assert report['usage']['state'] == 'partial'
    assert report['usage']['observed']['subtotal_overflow'] is True
    assert report['usage']['observed']['input_tokens'] is None and report['cost']['amount'] is None
