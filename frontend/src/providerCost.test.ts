// B2b5c definitions FIRST, ALL UNRUN. Pure declarations, not API/native/invoices.
import { expect, it } from 'vitest';
import { validateProviderCost } from './providerCost';
import { costFixture, costSubset, frozenPriceFixture } from './testSupport/providerCostFixture';
import { parseRunReport } from './runReportApi';
import { reportRun } from './testSupport/runReportFixture';
import { providerSubtotal, reportV3Fixture } from './testSupport/providerReportFixture';
import type { ProviderReport } from './providerReport';

function provider(): ProviderReport { return parseRunReport(reportV3Fixture(), reportRun).provider_usage!; }
type Mutable<T> = T extends readonly (infer U)[] ? Mutable<U>[] : T extends object ? { -readonly [K in keyof T]: Mutable<T[K]> } : T;
function mutableProvider(): Mutable<ProviderReport> { return structuredClone(provider()) as Mutable<ProviderReport>; }

it('checks exact priced root subset without adding compatibility or authenticating hash/source', () => {
  const cost = validateProviderCost(costFixture(), provider());
  expect(cost.currencies[0]?.amount).toBe('0.00001625');
  expect(cost.scopes.root.currencies[0]?.amount).toBe('0.00001625');
  expect(cost.source_verified).toBe(false); expect(cost.billing_complete).toBe(false);
  expect(cost.account_cap_guaranteed).toBe(false);
  expect(JSON.stringify(cost)).not.toMatch(/source_reference|fixture-model|api_key/);
});

it('keeps price-unknown known usage and empty root distinct from zero-priced observations', () => {
  const value = costFixture();
  Object.assign(value, { ...costSubset(false, true), state: 'unknown', source_state: 'missing', price_snapshot: null });
  value.scopes.root = costSubset(false, true);
  expect(validateProviderCost(value, provider()).unknown_units).toBe(1);
  const zero = costFixture(), p = mutableProvider();
  Object.assign(p.selected, { input_tokens: 0, output_tokens: 0, total_tokens: 0 }); p.scopes.root = { ...p.selected };
  for (const subset of [zero, zero.scopes.root]) Object.assign(subset.currencies[0]!, { input_tokens: '0', output_tokens: '0', amount: '0' });
  expect(validateProviderCost(zero, p).currencies[0]?.amount).toBe('0');
});

it('validates actual cache partition and charges inclusive output once, rejects absent/inconsistent partition', () => {
  const value = costFixture(); value.price_snapshot = frozenPriceFixture(true);
  for (const subset of [value, value.scopes.root]) Object.assign(subset.currencies[0]!, {
    amount: '0.00002', cached_input_tokens: '2', uncached_input_tokens: '5' });
  expect(validateProviderCost(value, provider()).currencies[0]?.amount).toBe('0.00002');
  value.scopes.root.currencies[0]!.uncached_input_tokens = null;
  expect(() => validateProviderCost(value, provider())).toThrow('invalid_run_report');
});

it('retains bounded exact BigInt witnesses beyond safe integers and partial overflow coverage', () => {
  const value = costFixture(), p = mutableProvider();
  Object.assign(p.selected, { known_units: 2, request_units: 2, input_tokens: null, output_tokens: null,
    total_tokens: null, subtotal_overflow: true }); p.scopes.root = { ...p.selected }; p.state = 'partial';
  Object.assign(value, { selected_units: 2, request_units: 2, known_units: 2,
    state: 'partial', coverage: 'partial_recorded_prefix' });
  Object.assign(value.scopes.root, { selected_units: 2, request_units: 2, known_units: 2 });
  for (const subset of [value, value.scopes.root]) Object.assign(subset.currencies[0]!, {
    known_units: 2, request_units: 2, input_tokens: '18014398509481982', output_tokens: '0', amount: '22517998136.8524775' });
  expect(validateProviderCost(value, p).currencies[0]?.input_tokens).toBe('18014398509481982');
  value.currencies[0]!.input_tokens = '18014398509481983';
  expect(() => validateProviderCost(value, p)).toThrow('invalid_run_report');
});

it('matches original scope identities/generations and conserves first16 plus unknown other_children', () => {
  const value = costFixture(), p = mutableProvider();
  p.state = 'partial'; p.scopes.children_total = 18; p.scopes.children_omitted = 2; p.scopes.truncated = true;
  p.scopes.root = providerSubtotal();
  p.scopes.children = Array.from({ length: 16 }, (_, index) => ({ ...providerSubtotal(true),
    subagent_id: `${index + 1}`.padStart(32, '0'), generation: 2 }));
  p.scopes.other_children = { ...providerSubtotal(true), unknown_units: 1, request_units: 2 };
  p.selected = { ...providerSubtotal(true), known_units: 17, unknown_units: 1, request_units: 18,
    input_tokens: 119, output_tokens: 51, total_tokens: 170 };
  Object.assign(value, { selected_units: 18, request_units: 18, known_units: 17, unknown_units: 1,
    state: 'partial', coverage: 'partial_recorded_prefix' });
  value.unknown_reasons.source_not_eligible = 1;
  Object.assign(value.currencies[0]!, { amount: '0.00027625', known_units: 17, request_units: 17,
    input_tokens: '119', output_tokens: '51' });
  Object.assign(value.scopes, { root: costSubset(), children_total: 18, children_omitted: 2, truncated: true,
    children: p.scopes.children.map(child => ({ ...costSubset(true), subagent_id: child.subagent_id, generation: child.generation })),
    other_children: { ...costSubset(true), selected_units: 2, request_units: 2, unknown_units: 1 } });
  value.scopes.other_children.unknown_reasons.source_not_eligible = 1;
  expect(validateProviderCost(value, p).scopes.other_children.unknown_units).toBe(1);
  value.scopes.children[0]!.generation = 99;
  expect(() => validateProviderCost(value, p)).toThrow('invalid_run_report');
});

const malformed: Array<(v: ReturnType<typeof costFixture>) => unknown> = [
  v => Object.assign(v, { version: true }), v => Object.assign(v, { selected_units: 2 }),
  v => Object.assign(v, { state: 'known_for_declared_observations', unknown_units: 1 }),
  v => Object.assign(v, { source_verified: true }), v => Object.assign(v, { billing_complete: true }),
  v => Object.assign(v, { account_cap_guaranteed: true }), v => Object.assign(v, { source_state: 'invalid' }),
  v => Object.assign(v, { profile: 'PRIVATE' }), v => Object.assign(v.unknown_reasons, { arbitrary: 0 }),
  v => Object.assign(v.price_snapshot!, { source_reference: 'PRIVATE' }),
  v => Object.assign(v.price_snapshot!, { frozen_date: '2026-02-30' }),
  v => Object.assign(v.price_snapshot!, { effective_date: '2026-10-07' }),
  v => Object.assign(v.price_snapshot!, { rates: { input: '1.250', output: '2.5', cached_input: null, uncached_input: null } }),
  v => Object.assign(v.price_snapshot!, { source_sha256: 'bad' }),
  v => Object.assign(v.price_snapshot!, { source_kind: 'verified_official' }),
  v => Object.assign(v.price_snapshot!, { account_cap_guaranteed: true }),
  v => Object.assign(v.unknown_reasons, { usage_unknown: 1 }),
  v => Object.assign(v.currencies[0]!, { known_units: true }),
  v => Object.assign(v.currencies[0]!, { amount: 0.00001625 }),
  v => Object.assign(v.currencies[0]!, { amount: '0.000016250' }),
  v => Object.assign(v.currencies[0]!, { amount: '0.00001626' }),
  v => Object.assign(v.currencies[0]!, { input_tokens: '07' }),
  v => Object.assign(v.currencies[0]!, { input_tokens: '8', amount: '0.0000175' }),
  v => Object.assign(v.currencies[0]!, { input_tokens: '1e3' }),
  v => Object.assign(v.currencies[0]!, { cached_input_tokens: '0' }),
  v => Object.assign(v.currencies[0]!, { output_tokens: '9007199254740991' }),
  v => Object.assign(v.currencies[0]!, { currency: 'USD' }),
  v => Object.assign(v.currencies[0]!, { tariff_sha256: 'f'.repeat(64) }),
  v => v.currencies.push({ ...v.currencies[0]! }),
  v => Object.assign(v.scopes.root.currencies[0]!, { amount: '0.00001626' }),
  v => Object.assign(v.scopes, { children_total: 1 }),
  v => Object.assign(v.scopes.root, { unknown_units: 1 }),
  v => Object.assign(v.scopes, { truncated: true }),
];
it.each(malformed)('refuses private/malformed/numerically inconsistent monetary fields', mutate => {
  const value = costFixture(); mutate(value);
  expect(() => validateProviderCost(value, provider())).toThrow('invalid_run_report');
});

it('rejects getters/symbols/sparse or decorated currency arrays before executing private accessors', () => {
  for (const mutate of [
    (v: ReturnType<typeof costFixture>, invoke: () => never) => Object.defineProperty(v.price_snapshot!, 'rates', { enumerable: true, get: invoke }),
    (v: ReturnType<typeof costFixture>, invoke: () => never) => Object.defineProperty(v.currencies, '0', { enumerable: true, get: invoke }),
    (v: ReturnType<typeof costFixture>) => Object.assign(v.currencies, { private: 'SECRET' }),
    (v: ReturnType<typeof costFixture>) => delete v.currencies[0],
    (v: ReturnType<typeof costFixture>) => Object.assign(v, { [Symbol('secret')]: 'SECRET' }),
  ]) {
    const value = costFixture(); let invoked = false;
    mutate(value, () => { invoked = true; throw new Error('PRIVATE_GETTER'); });
    expect(() => validateProviderCost(value, provider())).toThrow('invalid_run_report'); expect(invoked).toBe(false);
  }
});
