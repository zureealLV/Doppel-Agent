// Closed original frozen-tariff monetary projection. No IO/prices/FX/authority.
// Caller MUST first validate original ProviderReport; parent report parser owns
// detached deep-freeze/export. Hash syntax/equality is correlation, not source
// authentication or reconstruction of the private original profile binding.
import type { ProviderReport, ProviderSubtotal } from './providerReport';
import type { BillingTariff } from './billingTariff';
export const costReasonKeys = ['no_frozen_tariff', 'receipt_mismatch', 'source_not_eligible', 'usage_unknown', 'cache_partition_unknown'] as const;
export interface FrozenPrice extends Readonly<Omit<BillingTariff, 'source_reference' | 'rates'>> {
  readonly rates: Readonly<BillingTariff['rates']>;
  readonly frozen_date: string; readonly source_sha256: string; readonly binding_sha256: string; readonly tariff_sha256: string;
  readonly source_verified: false; readonly billing_complete: false; readonly account_cap_guaranteed: false;
}
export interface CostCurrency { readonly currency: BillingTariff['currency']; readonly amount: string;
  readonly known_units: number; readonly request_units: number; readonly logical_call_units: number; readonly tariff_sha256: string;
  readonly input_tokens: string; readonly output_tokens: string; readonly cached_input_tokens: string | null; readonly uncached_input_tokens: string | null }
export interface CostSubset { readonly selected_units: number; readonly request_units: number; readonly logical_call_units: number;
  readonly known_units: number; readonly unknown_units: number; readonly unknown_reasons: Readonly<Record<typeof costReasonKeys[number], number>>;
  readonly currencies: readonly CostCurrency[] }
export interface ProviderCost extends CostSubset { readonly version: 1; readonly policy: 'selected_units_original_frozen_tariff_no_fx';
  readonly state: 'unknown' | 'partial' | 'known_for_declared_observations'; readonly coverage: 'selected_recorded_prefix' | 'partial_recorded_prefix';
  readonly source_state: 'missing' | 'invalid' | 'omitted' | 'known'; readonly price_snapshot: FrozenPrice | null;
  readonly scopes: { readonly basis: 'recorded_outer_tags_not_reconstructed'; readonly root: CostSubset;
    readonly children_total: number; readonly children: readonly (CostSubset & { readonly subagent_id: string; readonly generation: number })[];
    readonly children_omitted: number; readonly other_children: CostSubset; readonly limit: 16; readonly truncated: boolean };
  readonly basis: 'declared_tariff_observed_usage_not_invoice'; readonly source_verified: false; readonly billing_complete: false; readonly account_cap_guaranteed: false }
const MAX = BigInt(Number.MAX_SAFE_INTEGER), LIMIT = 5000;
const subsetKeys = ['selected_units', 'request_units', 'logical_call_units', 'known_units', 'unknown_units', 'unknown_reasons', 'currencies'];
const currencyKeys = ['currency', 'amount', 'known_units', 'request_units', 'logical_call_units', 'tariff_sha256',
  'input_tokens', 'output_tokens', 'cached_input_tokens', 'uncached_input_tokens'];
function check(value: unknown): asserts value { if (!value) throw new Error('invalid_run_report'); }
function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  check(value !== null && typeof value === 'object' && !Array.isArray(value));
  check(Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
  const actual = Reflect.ownKeys(value);
  check(actual.length === keys.length && actual.every(key => typeof key === 'string' && keys.includes(key)));
  for (const key of keys) { const descriptor = Object.getOwnPropertyDescriptor(value, key); check(descriptor?.enumerable && 'value' in descriptor); }
  return value as Record<string, unknown>;
}
function array(value: unknown, max: number): unknown[] {
  check(Array.isArray(value) && Object.getPrototypeOf(value) === Array.prototype);
  const descriptor = Object.getOwnPropertyDescriptor(value, 'length');
  check(descriptor && 'value' in descriptor && Number.isInteger(descriptor.value) && descriptor.value >= 0 && descriptor.value <= max);
  check(Reflect.ownKeys(value).length === descriptor.value + 1);
  for (let index = 0; index < descriptor.value; index++) {
    const item = Object.getOwnPropertyDescriptor(value, String(index)); check(item?.enumerable && 'value' in item);
  }
  return value;
}
const integer = (value: unknown, max = LIMIT): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= max;
const hash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
function date(value: unknown): string {
  check(typeof value === 'string' && /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value));
  const year = Number(value.slice(0, 4)), month = Number(value.slice(5, 7)), day = Number(value.slice(8));
  const instant = new Date(0); instant.setUTCFullYear(year, month - 1, day);
  check(year >= 1 && instant.getUTCFullYear() === year && instant.getUTCMonth() === month - 1 && instant.getUTCDate() === day);
  return value;
}
function rate(value: unknown): bigint {
  check(typeof value === 'string' && /^(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,8})?$/.test(value));
  check(!value.includes('.') || !value.endsWith('0'));
  const [whole, fraction = ''] = value.split('.'); return BigInt(whole!) * 100000000n + BigInt(fraction.padEnd(8, '0'));
}
function price(value: unknown): FrozenPrice {
  const row = object(value, ['version', 'currency', 'effective_date', 'frozen_date', 'source_kind', 'source_sha256', 'binding_sha256',
    'unit_tokens', 'billing_basis', 'reasoning_basis', 'rates', 'tariff_sha256', 'source_verified', 'billing_complete', 'account_cap_guaranteed']);
  check(row.version === 1 && row.unit_tokens === 1000000 && row.reasoning_basis === 'included_in_output');
  check(typeof row.currency === 'string' && ['CNY', 'USD', 'EUR', 'GBP', 'JPY'].includes(row.currency));
  check(typeof row.source_kind === 'string' && ['user_declared', 'offline_fixture', 'official_reference'].includes(row.source_kind));
  check(date(row.effective_date) <= date(row.frozen_date));
  check(hash(row.source_sha256) && hash(row.binding_sha256) && hash(row.tariff_sha256));
  check(row.source_verified === false && row.billing_complete === false && row.account_cap_guaranteed === false);
  const rates = object(row.rates, ['input', 'cached_input', 'uncached_input', 'output']); rate(rates.output);
  if (row.billing_basis === 'input_output_inclusive') {
    rate(rates.input); check(rates.cached_input === null && rates.uncached_input === null);
  } else {
    check(row.billing_basis === 'cache_partition_output_inclusive' && rates.input === null);
    rate(rates.cached_input); rate(rates.uncached_input);
  }
  return row as unknown as FrozenPrice;
}
function tokens(value: unknown): bigint {
  check(typeof value === 'string' && /^(?:0|[1-9][0-9]{0,19})$/.test(value)); return BigInt(value);
}
function amount(value: unknown): bigint {
  check(typeof value === 'string' && /^(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,14})?$/.test(value));
  check(!value.includes('.') || !value.endsWith('0'));
  const [whole, fraction = ''] = value.split('.'); return BigInt(whole!) * 100000000000000n + BigInt(fraction.padEnd(14, '0'));
}
function currency(value: unknown, frozen: FrozenPrice): CostCurrency {
  const row = object(value, currencyKeys);
  check(row.currency === frozen.currency && row.tariff_sha256 === frozen.tariff_sha256);
  check(integer(row.known_units) && row.known_units >= 1 && integer(row.request_units) && integer(row.logical_call_units));
  check(row.request_units + row.logical_call_units === row.known_units);
  const input = tokens(row.input_tokens), output = tokens(row.output_tokens);
  check(input + output <= BigInt(row.known_units) * MAX);
  let inputAmount: bigint;
  if (frozen.billing_basis === 'cache_partition_output_inclusive') {
    const hit = tokens(row.cached_input_tokens), miss = tokens(row.uncached_input_tokens); check(hit + miss === input);
    inputAmount = hit * rate(frozen.rates.cached_input) + miss * rate(frozen.rates.uncached_input);
  } else {
    check(row.cached_input_tokens === null && row.uncached_input_tokens === null); inputAmount = input * rate(frozen.rates.input);
  }
  check(amount(row.amount) === inputAmount + output * rate(frozen.rates.output)); // 1e14 exact; reasoning INCLUDED in output.
  return row as unknown as CostCurrency;
}
function subset(value: unknown, frozen: FrozenPrice | null, original: ProviderSubtotal, extra: readonly string[] = []): CostSubset {
  const row = object(value, [...subsetKeys, ...extra]);
  for (const key of subsetKeys.slice(0, 5)) check(integer(row[key]));
  const selected = row.selected_units as number, known = row.known_units as number;
  check(known + (row.unknown_units as number) === selected && (row.request_units as number) + (row.logical_call_units as number) === selected);
  check(selected === original.known_units + original.unknown_units && known <= original.known_units
    && row.request_units === original.request_units && row.logical_call_units === original.logical_call_units);
  const reasons = object(row.unknown_reasons, costReasonKeys);
  check(Object.values(reasons).every(v => integer(v, selected)));
  check(Object.values(reasons).reduce<number>((sum, v) => sum + (v as number), 0) === row.unknown_units);
  check((reasons.usage_unknown as number) <= original.unknown_units);
  if (!frozen) check(known === 0 && reasons.no_frozen_tariff === selected);
  if (!frozen || frozen.billing_basis !== 'cache_partition_output_inclusive') check(reasons.cache_partition_unknown === 0);
  const rows = array(row.currencies, 1);
  check(rows.length === (known ? 1 : 0));
  if (known) {
    check(frozen); const c = currency(rows[0], frozen);
    check(c.known_units === known && c.request_units <= (row.request_units as number) && c.logical_call_units <= (row.logical_call_units as number));
    if (!original.subtotal_overflow) {
      const input = tokens(c.input_tokens), output = tokens(c.output_tokens);
      check(original.input_tokens !== null && original.output_tokens !== null);
      check(input <= BigInt(original.input_tokens) && output <= BigInt(original.output_tokens));
      if (known === original.known_units) check(input === BigInt(original.input_tokens) && output === BigInt(original.output_tokens));
    }
  }
  return row as unknown as CostSubset;
}
function conserved(total: CostSubset, parts: readonly CostSubset[]): void {
  for (const key of ['selected_units', 'request_units', 'logical_call_units', 'known_units', 'unknown_units'] as const)
    check(parts.reduce((sum, p) => sum + p[key], 0) === total[key]);
  for (const reason of costReasonKeys) check(parts.reduce((sum, p) => sum + p.unknown_reasons[reason], 0) === total.unknown_reasons[reason]);
  const priced = parts.flatMap(p => p.currencies), target = total.currencies[0];
  if (!target) { check(priced.length === 0); return; }
  check(priced.every(p => p.currency === target.currency && p.tariff_sha256 === target.tariff_sha256));
  check(priced.reduce((sum, p) => sum + amount(p.amount), 0n) === amount(target.amount));
  for (const key of ['known_units', 'request_units', 'logical_call_units'] as const)
    check(priced.reduce((sum, p) => sum + p[key], 0) === target[key]);
  for (const key of ['input_tokens', 'output_tokens', 'cached_input_tokens', 'uncached_input_tokens'] as const) {
    if (target[key] === null) check(priced.every(p => p[key] === null));
    else check(priced.reduce((sum, p) => sum + tokens(p[key]), 0n) === tokens(target[key]));
  }
}
export function validateProviderCost(value: unknown, provider: ProviderReport): ProviderCost {
  const extra = ['version', 'policy', 'state', 'coverage', 'source_state', 'price_snapshot', 'scopes',
    'basis', 'source_verified', 'billing_complete', 'account_cap_guaranteed'];
  const row = object(value, [...subsetKeys, ...extra]);
  check(row.version === 1 && row.policy === 'selected_units_original_frozen_tariff_no_fx'
    && row.basis === 'declared_tariff_observed_usage_not_invoice');
  check(row.source_verified === false && row.billing_complete === false && row.account_cap_guaranteed === false);
  check(typeof row.source_state === 'string' && ['known', 'missing', 'invalid', 'omitted'].includes(row.source_state));
  check((row.source_state === 'known') === (row.price_snapshot !== null));
  const frozen = row.price_snapshot === null ? null : price(row.price_snapshot);
  const total = subset(row, frozen, provider.selected, extra), uncertain = provider.state !== 'known_for_selected_receipts';
  check(row.coverage === (uncertain ? 'partial_recorded_prefix' : 'selected_recorded_prefix'));
  check(row.state === (!total.known_units ? 'unknown' : total.unknown_units || uncertain ? 'partial' : 'known_for_declared_observations'));
  const scopes = object(row.scopes, ['basis', 'root', 'children_total', 'children', 'children_omitted', 'other_children', 'limit', 'truncated']);
  check(scopes.basis === 'recorded_outer_tags_not_reconstructed' && scopes.limit === 16
    && typeof scopes.truncated === 'boolean' && scopes.truncated === provider.scopes.truncated
    && integer(scopes.children_total) && scopes.children_total === provider.scopes.children_total
    && integer(scopes.children_omitted) && scopes.children_omitted === provider.scopes.children_omitted);
  const children = array(scopes.children, 16); check(children.length === provider.scopes.children.length);
  const parts = [subset(scopes.root, frozen, provider.scopes.root)];
  children.forEach((value, index) => {
    const child = object(value, [...subsetKeys, 'subagent_id', 'generation']), original = provider.scopes.children[index]!;
    check(child.subagent_id === original.subagent_id && integer(child.generation, Number.MAX_SAFE_INTEGER) && child.generation >= 1
      && child.generation === original.generation);
    parts.push(subset(child, frozen, original, ['subagent_id', 'generation']));
  });
  parts.push(subset(scopes.other_children, frozen, provider.scopes.other_children)); conserved(total, parts);
  return row as unknown as ProviderCost;
}
