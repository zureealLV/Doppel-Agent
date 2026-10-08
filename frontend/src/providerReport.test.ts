// B2b4b definitions FIRST, ALL UNRUN. No actual HTTP/journal/native/model proof.
import { describe, expect, it } from 'vitest';
import { parseRunReport } from './runReportApi';
import { reportFixture, reportRun } from './testSupport/runReportFixture';
import { reportV2Fixture } from './testSupport/reportEvidenceFixture';
import { providerSubtotal, receiptDetails, receiptSample, receiptUsage, reportV3Fixture } from './testSupport/providerReportFixture';

it('retains v3 numeric receipt samples without granting price or billing authority', () => {
  const value = reportV3Fixture();
  for (const sample of value.provider_usage.samples.items) {
    if (sample.receipt_version !== null) Object.assign(sample, { receipt_version: 3 });
  }
  const parsed = parseRunReport(value, reportRun);
  expect(parsed.provider_usage?.selected.total_tokens).toBe(value.provider_usage.selected.total_tokens);
  expect(parsed.cost.state).toBe('unknown');
  expect(JSON.stringify(parsed.provider_usage)).not.toContain('price_receipt');
});

const bad: Array<(v: ReturnType<typeof reportV3Fixture>) => unknown> = [
  v => Object.assign(v, { usage_basis: 'add_callback_to_requests' }),
  v => Object.assign(v.provider_usage, { price: 'PRIVATE' }),
  v => Object.assign(v.provider_usage.counts, { private_response: 'PRIVATE' }),
  v => Object.assign(v.provider_usage, { billing_complete: true }),
  v => Object.assign(v.provider_usage, { wire_request_coverage: 'complete' }),
  v => Object.assign(v.provider_usage.samples.items[0]!, { receipt_version: 4 }),
  v => Object.assign(v.provider_usage.counts, { call_ids: 2 }),
  v => Object.assign(v.provider_usage.counts, { request_returned: 2 }),
  v => Object.assign(v.provider_usage.counts, { requests_linked: 0 }),
  v => Object.assign(v.provider_usage.counts, { callbacks_matched: 0 }),
  v => Object.assign(v.provider_usage.selected, { request_units: 2 }),
  v => Object.assign(v.provider_usage.selected, { total_tokens: 11 }),
  v => Object.assign(v.provider_usage.scopes.root, { input_tokens: 8, total_tokens: 11 }),
  v => Object.assign(v.provider_usage.scopes, { children_total: 1 }),
  v => Object.assign(v.provider_usage.samples, { emitted: 4 }),
  v => Object.assign(v.provider_usage.samples.items[0]!, { receipt_version: true }),
  v => Object.assign(v.provider_usage.samples.items[0]!, { attempt_id: 'PRIVATE' }),
  v => Object.assign(v.provider_usage.samples.items[0]!, { selected: false }),
  v => Object.assign(v.provider_usage.samples.items[0]!.usage!, { input_tokens: true }),
  v => Object.assign(v.provider_usage.samples.items[0]!.usage_details, { cache_state: 'known' }),
  v => Object.assign(v.provider_usage.samples.items[0]!.usage_details, { reasoning_state: 'known', reasoning_output_tokens: 4 }),
  v => Object.assign(v.provider_usage.samples.items[1]!, { outcome: 'failed' }),
  v => Object.assign(v.provider_usage.samples.items[2]!, { call_id: 'b'.repeat(32) }),
  v => Object.assign(v.provider_usage.samples.items[2]!, { callback_linkage: 'unmatched' }),
  v => Object.assign(v.provider_usage.samples.items[2]!, { selected: true }),
  v => { Object.assign(v.usage.root, { known_model_events: 0, input_tokens: null, output_tokens: null });
    Object.assign(v.usage.observed, { known_model_events: 0, input_tokens: null, output_tokens: null }); v.usage.state = 'unknown'; },
  v => { v.usage.root.input_tokens = 8; v.usage.observed.input_tokens = 8; },
];
describe('v3 original observation projection', () => {
  it('keeps v1/v2 historical schema, and freezes v3 request subtotal WITHOUT adding old logical/callback counters', () => {
    expect(parseRunReport(reportFixture(), reportRun).version).toBe(1);
    expect(parseRunReport(reportV2Fixture(), reportRun).version).toBe(2);
    const wire = reportV3Fixture(), parsed = parseRunReport(wire, reportRun);
    expect(parsed.version).toBe(3); expect(parsed.provider_usage?.selected.total_tokens).toBe(10);
    expect(parsed.provider_usage?.compatibility.known_units).toBe(0);
    expect(parsed.provider_usage?.samples.items.filter(row => row.selected)).toHaveLength(1);
    wire.provider_usage.samples.items[0]!.usage!.input_tokens = 900;
    expect(parsed.provider_usage?.samples.items[0]?.usage?.input_tokens).toBe(7);
    expect(Object.isFrozen(parsed.provider_usage?.samples.items[0]?.usage_details)).toBe(true);
  });
  it.each(bad)('rejects private or numerically contradictory projections', mutate => {
    const wire = reportV3Fixture(); mutate(wire); expect(() => parseRunReport(wire, reportRun)).toThrow('invalid_run_report');
  });
  it('requires all v3 fields/evidence, and forbids canonical extras in historical versions', () => {
    const wire = reportV3Fixture(), { provider_usage: _p, ...missing } = wire;
    expect(() => parseRunReport(missing, reportRun)).toThrow('invalid_run_report');
    const { evidence: _e, ...noEvidence } = wire;
    expect(() => parseRunReport(noEvidence, reportRun)).toThrow('invalid_run_report');
    expect(() => parseRunReport({ ...wire, version: 2 }, reportRun)).toThrow('invalid_run_report');
  });
  it('refuses object/array getters, private extras, symbols, sparse samples and untrusted prototypes BEFORE invoking them', () => {
    for (const field of ['input_tokens', 'known_units']) {
      const wire = reportV3Fixture(); let invoked = false;
      Object.defineProperty(wire.provider_usage.selected, field, { enumerable: true, get() { invoked = true; return 7; } });
      expect(() => parseRunReport(wire, reportRun)).toThrow('invalid_run_report'); expect(invoked).toBe(false);
    }
    for (const mutate of [
      (items: unknown[]) => Object.assign(items, { private: 'SECRET' }),
      (items: unknown[]) => Object.defineProperty(items, '0', { enumerable: true, get() { throw new Error('PRIVATE_GETTER'); } }),
      (items: unknown[]) => delete items[0],
      (items: unknown[]) => Object.assign(items, { [Symbol('private')]: 'SECRET' }),
    ]) {
      const wire = reportV3Fixture(); mutate(wire.provider_usage.samples.items);
      expect(() => parseRunReport(wire, reportRun)).toThrow('invalid_run_report');
    }
  });
  it('retains failed retry observations and visibly partial unmatched callbacks without charging compatibility', () => {
    const wire = reportV3Fixture(), p = wire.provider_usage;
    p.state = 'partial'; p.selected = { ...providerSubtotal(true), known_units: 2, request_units: 2,
      input_tokens: 9, output_tokens: 4, total_tokens: 13 }; p.scopes.root = { ...p.selected };
    p.compatibility = providerSubtotal(true, 'compatibility_callback');
    Object.assign(wire.snapshot, { event_total: 7, event_high_water: 7, scanned_events: 7 });
    Object.assign(p.counts, { request_rows: 4, request_ids: 2, requests_matched: 2, requests_linked: 2,
      request_failed: 1, request_method_entered: 2, callbacks_matched: 0, callbacks_unmatched: 1 });
    const failed = receiptSample(); Object.assign(failed, { outcome: 'failed', usage: receiptUsage(2, 1) });
    const returned = receiptSample('request', 5); Object.assign(returned, { attempt_id: 'e'.repeat(32), attempt_index: 2 });
    const callback = receiptSample('compatibility_callback', 7); callback.callback_linkage = 'unmatched';
    Object.assign(p.samples, { total: 4, emitted: 4, items: [failed, returned, receiptSample('logical_call', 6), callback] });
    const parsed = parseRunReport(wire, reportRun);
    expect(parsed.provider_usage?.selected.total_tokens).toBe(13);
    expect(parsed.provider_usage?.compatibility.total_tokens).toBe(10); expect(parsed.provider_usage?.counts.request_failed).toBe(1);
  });
  it('preserves explicit zero and invalid cache dimension without manufacturing historical details', () => {
    const wire = reportV3Fixture(), p = wire.provider_usage;
    p.selected = providerSubtotal(true, 'request', receiptUsage(0, 0)); p.scopes.root = { ...p.selected };
    Object.assign(wire.usage.root, { input_tokens: 0, output_tokens: 0 });
    Object.assign(wire.usage.observed, { input_tokens: 0, output_tokens: 0 });
    for (const item of p.samples.items) item.usage = receiptUsage(0, 0);
    p.samples.items[0]!.usage_details.cache_state = 'invalid';
    expect(parseRunReport(wire, reportRun).provider_usage?.selected.total_tokens).toBe(0);
    p.samples.items[0]!.receipt_version = 1;
    expect(() => parseRunReport(wire, reportRun)).toThrow('invalid_run_report');
    p.samples.items[0]!.usage_details = receiptDetails();
    expect(parseRunReport(wire, reportRun).provider_usage?.samples.items[0]?.usage_details.cache_state).toBe('unknown');
  });
  it('does not fake zero when no canonical records exist but old compatibility events do', () => {
    const wire = reportV3Fixture(), p = wire.provider_usage;
    p.selected = providerSubtotal(); p.scopes.root = providerSubtotal(); p.state = 'unknown';
    for (const key of Object.keys(p.counts)) p.counts[key as keyof typeof p.counts] = 0;
    p.compatibility = providerSubtotal(true, 'compatibility_callback');
    Object.assign(p.counts, { callbacks: 1, callbacks_unmatched: 1 });
    const callback = receiptSample('compatibility_callback', 5); callback.callback_linkage = 'unmatched';
    Object.assign(p.samples, { total: 1, emitted: 1, items: [callback] });
    expect(parseRunReport(wire, reportRun).provider_usage?.selected.input_tokens).toBeNull();
  });
  it('keeps a known late request with one pending unknown and unsettled logical call, not a zero-charge cancellation', () => {
    const wire = reportV3Fixture(), p = wire.provider_usage;
    for (const key of Object.keys(p.counts)) p.counts[key as keyof typeof p.counts] = 0;
    Object.assign(p.counts, { call_rows: 1, call_ids: 1, calls_unsettled: 1, request_rows: 3, request_ids: 2,
      requests_matched: 1, requests_unsettled: 1, requests_linked: 2, request_returned: 1, request_method_entered: 1 });
    Object.assign(p.selected, { unknown_units: 1, request_units: 2 }); p.scopes.root = { ...p.selected }; p.state = 'partial';
    Object.assign(p.samples, { total: 1, emitted: 1, items: [receiptSample()] });
    Object.assign(wire.snapshot, { event_total: 4, event_high_water: 4, scanned_events: 4 });
    for (const item of [wire.usage.root, wire.usage.observed]) Object.assign(item, { known_model_events: 0, input_tokens: null, output_tokens: null });
    wire.usage.state = 'unknown';
    const parsed = parseRunReport(wire, reportRun);
    expect(parsed.provider_usage?.selected.total_tokens).toBe(10); expect(parsed.provider_usage?.selected.unknown_units).toBe(1);
  });
  it('bounds first16 children/samples while preserving exact other-children counts and unsafe subtotal flags', () => {
    const wire = reportV3Fixture(), p = wire.provider_usage, max = Number.MAX_SAFE_INTEGER;
    for (const key of Object.keys(p.counts)) p.counts[key as keyof typeof p.counts] = 0;
    Object.assign(p.counts, { call_rows: 36, call_ids: 18, calls_matched: 18, call_returned: 18, call_method_entered: 18 });
    p.selected = { ...providerSubtotal(), known_units: 18, logical_call_units: 18, subtotal_overflow: true }; p.state = 'partial';
    p.scopes.root = providerSubtotal();
    const children = Array.from({ length: 16 }, (_, index) => ({ ...providerSubtotal(true, 'logical_call', receiptUsage(max, 0)),
      subagent_id: (index + 1).toString(16).padStart(32, '0'), generation: 2 }));
    Object.assign(p.scopes, { children_total: 18, children, children_omitted: 2, truncated: true,
      other_children: { ...providerSubtotal(), known_units: 2, logical_call_units: 2, subtotal_overflow: true } });
    const items = children.map((child, index) => {
      const item = receiptSample('logical_call', 2 * (index + 1));
      Object.assign(item, { selected: true, call_id: (index + 100).toString(16).padStart(32, '0'), usage: receiptUsage(max, 0) });
      Object.assign(item.scope, { kind: 'child', subagent_id: child.subagent_id, generation: 2 }); return item;
    });
    Object.assign(p.samples, { total: 18, emitted: 16, omitted: 2, truncated: true, items });
    Object.assign(wire.snapshot, { event_total: 36, event_high_water: 36, scanned_events: 36 });
    for (const part of [wire.usage.root, wire.usage.observed]) Object.assign(part, { known_model_events: 0, input_tokens: null, output_tokens: null });
    wire.usage.state = 'unknown';
    const parsed = parseRunReport(wire, reportRun);
    expect(parsed.provider_usage?.selected.input_tokens).toBeNull(); expect(parsed.provider_usage?.scopes.other_children.known_units).toBe(2);
    p.scopes.children[1]!.subagent_id = p.scopes.children[0]!.subagent_id;
    expect(() => parseRunReport(wire, reportRun)).toThrow('invalid_run_report');
  });
  it('rejects false root/child attribution even when the combined numeric subtotal agrees', () => {
    const wire = reportV3Fixture(), p = wire.provider_usage;
    Object.assign(p.selected, { known_units: 2, request_units: 2, input_tokens: 14, output_tokens: 6, total_tokens: 20 });
    Object.assign(p.scopes, { children_total: 1, children: [{ ...providerSubtotal(true), subagent_id: 'f'.repeat(32), generation: 2 }] });
    Object.assign(p.counts, { request_rows: 4, request_ids: 2, requests_matched: 2, requests_linked: 2, request_returned: 2, request_method_entered: 2 });
    const second = receiptSample('request', 5); Object.assign(second, { attempt_id: 'e'.repeat(32), attempt_index: 2 });
    Object.assign(p.samples, { total: 4, emitted: 4, items: [receiptSample(), second, receiptSample('logical_call', 6), receiptSample('compatibility_callback', 7)] });
    Object.assign(wire.snapshot, { event_total: 7, event_high_water: 7, scanned_events: 7 });
    expect(() => parseRunReport(wire, reportRun)).toThrow('invalid_run_report');
  });
});
