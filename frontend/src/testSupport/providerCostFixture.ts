// Independent fictional declaration/numeric projection ONLY; no API/native proof.
import type { FrozenPrice, ProviderCost, CostSubset } from '../providerCost';
import { providerSubtotal, receiptSample, receiptUsage, reportV3Fixture } from './providerReportFixture';
import { reportRun } from './runReportFixture';
type Mutable<T> = T extends readonly (infer U)[] ? Mutable<U>[] : T extends object ? { -readonly [K in keyof T]: Mutable<T[K]> } : T;

export function frozenPriceFixture(partition = false): Mutable<FrozenPrice> {
  // Deliberate opaque correlation hashes, NOT backend fingerprint fixtures.
  // Frontend cannot authenticate the original profile/private reference.
  return { version: 1, currency: 'CNY', effective_date: '2026-10-01', frozen_date: '2026-10-06',
    source_kind: 'offline_fixture', source_sha256: 'a'.repeat(64), binding_sha256: 'b'.repeat(64),
    tariff_sha256: 'c'.repeat(64), unit_tokens: 1000000,
    billing_basis: partition ? 'cache_partition_output_inclusive' : 'input_output_inclusive',
    reasoning_basis: 'included_in_output', rates: { input: partition ? null : '1.25',
      cached_input: partition ? '0.5' : null, uncached_input: partition ? '2' : null, output: partition ? '3' : '2.5' },
    source_verified: false, billing_complete: false, account_cap_guaranteed: false };
}

export function costSubset(known = false, unknown = false): Mutable<CostSubset> {
  return { selected_units: Number(known) + Number(unknown), request_units: Number(known) + Number(unknown),
    logical_call_units: 0, known_units: Number(known), unknown_units: Number(unknown),
    unknown_reasons: { no_frozen_tariff: Number(unknown), receipt_mismatch: 0,
      source_not_eligible: 0, usage_unknown: 0, cache_partition_unknown: 0 },
    currencies: known ? [{ currency: 'CNY', amount: '0.00001625', known_units: 1, request_units: 1,
      logical_call_units: 0, tariff_sha256: 'c'.repeat(64), input_tokens: '7', output_tokens: '3',
      cached_input_tokens: null, uncached_input_tokens: null }] : [] };
}

export function costFixture(): Mutable<ProviderCost> {
  return { version: 1, policy: 'selected_units_original_frozen_tariff_no_fx',
    state: 'known_for_declared_observations', coverage: 'selected_recorded_prefix', source_state: 'known',
    ...costSubset(true), price_snapshot: frozenPriceFixture(),
    scopes: { basis: 'recorded_outer_tags_not_reconstructed', root: costSubset(true), children_total: 0,
      children: [], children_omitted: 0, other_children: costSubset(), limit: 16, truncated: false },
    basis: 'declared_tariff_observed_usage_not_invoice', source_verified: false,
    billing_complete: false, account_cap_guaranteed: false };
}

export function reportV4Fixture(run = reportRun) {
  const old = reportV3Fixture(run);
  for (const sample of old.provider_usage.samples.items) if (sample.receipt_version !== null) sample.receipt_version = 3;
  return { ...old, version: 4, redaction: { ...old.redaction,
    policy: 'allowlisted_identifiers_enums_numeric_counters_and_tariff_contract_only' }, cost: costFixture() };
}

function logicalCost() {
  const part = costSubset(true); part.request_units = 0; part.logical_call_units = 1;
  Object.assign(part.currencies[0]!, { request_units: 0, logical_call_units: 1 }); return part;
}

export function reportV4ChildrenFixture() {
  // SAME denominator pattern as disposable original API definition: root
  // request/call/callback5 +17 child pairs +1 pending child start =40 events.
  const wire = reportV4Fixture(), p = wire.provider_usage, cost = wire.cost;
  Object.assign(wire.snapshot, { event_total: 40, event_high_water: 40, scanned_events: 40 });
  p.state = 'partial';
  p.selected = { ...providerSubtotal(true), known_units: 18, unknown_units: 1, request_units: 1, logical_call_units: 18,
    input_tokens: 126, output_tokens: 54, total_tokens: 180 };
  Object.assign(p.counts, { call_rows: 37, call_ids: 19, calls_matched: 18, calls_unsettled: 1, call_returned: 18, call_method_entered: 18 });
  p.scopes.children = Array.from({ length: 16 }, (_, index) => ({ ...providerSubtotal(true, 'logical_call'),
    subagent_id: (index + 1).toString(16).padStart(32, '0'), generation: 2 }));
  Object.assign(p.scopes, { children_total: 18, children_omitted: 2, truncated: true,
    other_children: { ...providerSubtotal(true, 'logical_call'), unknown_units: 1, logical_call_units: 2 } });
  const childSamples = Array.from({ length: 13 }, (_, index) => {
    const sample = receiptSample('logical_call', 7 + 2 * index);
    Object.assign(sample, { receipt_version: 3, selected: true, call_id: (index + 100).toString(16).padStart(32, '0') });
    Object.assign(sample.scope, { kind: 'child', subagent_id: (index + 1).toString(16).padStart(32, '0'), generation: 2 });
    return sample;
  });
  Object.assign(p.samples, { total: 20, emitted: 16, omitted: 4, truncated: true, items: [...p.samples.items, ...childSamples] });
  Object.assign(cost, { selected_units: 19, known_units: 18, unknown_units: 1, request_units: 1, logical_call_units: 18,
    state: 'partial', coverage: 'partial_recorded_prefix' });
  cost.unknown_reasons.source_not_eligible = 1;
  Object.assign(cost.currencies[0]!, { amount: '0.0002925', known_units: 18, request_units: 1, logical_call_units: 17,
    input_tokens: '126', output_tokens: '54' });
  const other = logicalCost(); other.selected_units = 2; other.logical_call_units = 2; other.unknown_units = 1;
  other.unknown_reasons.source_not_eligible = 1;
  Object.assign(cost.scopes, { children_total: 18, children_omitted: 2, truncated: true, other_children: other,
    children: p.scopes.children.map(child => ({ ...logicalCost(), subagent_id: child.subagent_id, generation: child.generation })) });
  return wire;
}

export function reportV4OverflowFixture() {
  const wire = reportV4Fixture(), p = wire.provider_usage, cost = wire.cost, max = Number.MAX_SAFE_INTEGER;
  Object.assign(wire.snapshot, { event_total: 7, event_high_water: 7, scanned_events: 7 });
  for (const part of [wire.usage.root, wire.usage.observed]) Object.assign(part, { input_tokens: max, output_tokens: 0 });
  p.state = 'partial'; p.selected = { ...providerSubtotal(true), known_units: 2, request_units: 2,
    input_tokens: null, output_tokens: null, total_tokens: null, subtotal_overflow: true }; p.scopes.root = { ...p.selected };
  Object.assign(p.counts, { request_rows: 4, request_ids: 2, requests_matched: 2, requests_linked: 2,
    request_failed: 1, request_method_entered: 2 });
  const failed = receiptSample('request', 3), returned = receiptSample('request', 5);
  Object.assign(failed, { outcome: 'failed' }); Object.assign(returned, { attempt_id: 'e'.repeat(32), attempt_index: 2 });
  const call = receiptSample('logical_call', 6), callback = receiptSample('compatibility_callback', 7);
  const items = [failed, returned, call, callback];
  for (const sample of items) { sample.usage = receiptUsage(max, 0); if (sample.receipt_version !== null) sample.receipt_version = 3; }
  Object.assign(p.samples, { total: 4, emitted: 4, items });
  Object.assign(cost, { selected_units: 2, request_units: 2, known_units: 2, state: 'partial', coverage: 'partial_recorded_prefix' });
  Object.assign(cost.scopes.root, { selected_units: 2, request_units: 2, known_units: 2 });
  for (const part of [cost, cost.scopes.root]) Object.assign(part.currencies[0]!, { known_units: 2, request_units: 2,
    input_tokens: '18014398509481982', output_tokens: '0', amount: '22517998136.8524775' });
  return wire;
}
