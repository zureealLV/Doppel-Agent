// B2b4b closed numeric journal projection only. No IO, prices or action authority.
export const providerCountKeys = ['call_rows', 'request_rows', 'call_ids', 'calls_matched', 'calls_unsettled',
  'call_invalid_ids', 'call_duplicate_ids', 'request_ids', 'requests_matched', 'requests_unsettled',
  'request_invalid_ids', 'request_duplicate_ids', 'requests_linked', 'request_orphans', 'request_ordinal_collisions',
  'request_ordinal_gaps', 'summary_mismatches', 'historical_calls_with_requests', 'call_returned', 'call_failed',
  'call_cancelled', 'call_method_entered', 'request_returned', 'request_failed', 'request_cancelled',
  'request_method_entered', 'callbacks', 'callbacks_matched', 'callbacks_unmatched', 'callbacks_invalid_usage',
  'callbacks_invalid_scope', 'invalid_scope_frames', 'invalid_frames', 'unclassified_rows', 'unselected_known_receipts'] as const;
export type ProviderCounts = Readonly<Record<typeof providerCountKeys[number], number>>;
export interface ProviderSubtotal { readonly known_units: number; readonly unknown_units: number; readonly request_units: number;
  readonly logical_call_units: number; readonly input_tokens: number | null; readonly output_tokens: number | null;
  readonly total_tokens: number | null; readonly subtotal_overflow: boolean }
export interface ProviderUsagePair { readonly input_tokens: number; readonly output_tokens: number; readonly total_tokens: number }
export interface ProviderUsageDetails { readonly version: 1; readonly cached_input_tokens: number | null;
  readonly uncached_input_tokens: number | null; readonly reasoning_output_tokens: number | null;
  readonly cache_state: 'known' | 'partial' | 'unknown' | 'invalid'; readonly reasoning_state: 'known' | 'unknown' | 'invalid' }
export interface ProviderSample { readonly kind: 'request' | 'logical_call' | 'compatibility_callback'; readonly seq: number;
  readonly receipt_version: 1 | 2 | 3 | null; readonly call_id: string | null; readonly attempt_id: string | null;
  readonly attempt_index: number | null; readonly engine: 'legacy' | 'graph' | 'deep' | null;
  readonly actor: 'runtime_model' | 'deep_builtin_investigator' | 'deep_builtin_verifier' | null;
  readonly scope: { readonly kind: 'root' } | { readonly kind: 'child'; readonly subagent_id: string; readonly generation: number };
  readonly outcome: 'returned' | 'failed' | 'cancelled' | null; readonly method_entered: boolean | null;
  readonly usage: ProviderUsagePair | null; readonly usage_details: ProviderUsageDetails; readonly selected: boolean;
  readonly callback_linkage: 'matched' | 'unmatched' | null }
export interface ProviderReport { readonly version: 1; readonly policy: 'linked_requests_else_logical_calls_no_callback_addition';
  readonly state: 'unknown' | 'partial' | 'known_for_selected_receipts'; readonly selected: ProviderSubtotal;
  readonly compatibility: ProviderSubtotal; readonly counts: ProviderCounts;
  readonly scopes: { readonly basis: 'recorded_outer_tags_not_reconstructed'; readonly root: ProviderSubtotal;
    readonly children_total: number; readonly children: readonly (ProviderSubtotal & { readonly subagent_id: string; readonly generation: number })[];
    readonly children_omitted: number; readonly other_children: ProviderSubtotal; readonly limit: 16; readonly truncated: boolean };
  readonly samples: { readonly total: number; readonly emitted: number; readonly omitted: number; readonly limit: 16;
    readonly truncated: boolean; readonly items: readonly ProviderSample[] };
  readonly billing_complete: false; readonly account_cap_guaranteed: false; readonly wire_request_coverage: 'unknown' }
interface Snapshot { scanned_events: number; event_high_water: number; omitted_usage_rows: number; truncated: boolean }
interface CompatibilityEvents { observed: { known_model_events: number; input_tokens: number | null;
  output_tokens: number | null; subtotal_overflow: boolean }; unknown_model_events: number }
const MAX = Number.MAX_SAFE_INTEGER, PREFIX = 16;
const subtotalKeys = ['known_units', 'unknown_units', 'request_units', 'logical_call_units', 'input_tokens', 'output_tokens', 'total_tokens', 'subtotal_overflow'];
function check(value: unknown): asserts value { if (!value) throw new Error('invalid_run_report'); }
function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  check(value !== null && typeof value === 'object' && !Array.isArray(value));
  check(Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
  const actual = Reflect.ownKeys(value);
  check(actual.length === keys.length && actual.every(key => typeof key === 'string' && keys.includes(key)));
  check(keys.every(key => { const d = Object.getOwnPropertyDescriptor(value, key); return d?.enumerable && 'value' in d; }));
  return value as Record<string, unknown>;
}
function array(value: unknown, max: number): unknown[] {
  check(Array.isArray(value) && Object.getPrototypeOf(value) === Array.prototype);
  const length = Object.getOwnPropertyDescriptor(value, 'length');
  check(length && 'value' in length && integer(length.value, max));
  check(Reflect.ownKeys(value).length === length.value + 1);
  for (let index = 0; index < length.value; index++) {
    const d = Object.getOwnPropertyDescriptor(value, String(index)); check(d?.enumerable && 'value' in d);
  }
  return value;
}
const integer = (value: unknown, max = MAX): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= max;
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{32}$/.test(value);
const member = (value: unknown, values: readonly string[]): value is string => typeof value === 'string' && values.includes(value);
function subtotal(value: unknown, max: number, extra: string[] = []): ProviderSubtotal {
  const row = object(value, [...subtotalKeys, ...extra]);
  check(['known_units', 'unknown_units', 'request_units', 'logical_call_units'].every(key => integer(row[key], max))
    && typeof row.subtotal_overflow === 'boolean');
  if (row.known_units === 0) check(row.input_tokens === null && row.output_tokens === null && row.total_tokens === null && !row.subtotal_overflow);
  else if (row.subtotal_overflow) check((row.known_units as number) >= 2 && row.input_tokens === null && row.output_tokens === null && row.total_tokens === null);
  else check(integer(row.input_tokens) && integer(row.output_tokens) && integer(row.total_tokens)
    && BigInt(row.input_tokens) + BigInt(row.output_tokens) === BigInt(row.total_tokens));
  return row as unknown as ProviderSubtotal;
}
function units(row: ProviderSubtotal): number { return row.known_units + row.unknown_units; }
function compareParts(total: ProviderSubtotal, parts: readonly ProviderSubtotal[]): void {
  check(parts.every(part => units(part) === part.request_units + part.logical_call_units));
  for (const key of ['known_units', 'unknown_units', 'request_units', 'logical_call_units'] as const)
    check(parts.reduce((sum, part) => sum + part[key], 0) === total[key]);
  if (parts.some(part => part.subtotal_overflow)) check(total.subtotal_overflow);
  else {
    const input = parts.reduce((sum, part) => sum + BigInt(part.input_tokens ?? 0), 0n);
    const output = parts.reduce((sum, part) => sum + BigInt(part.output_tokens ?? 0), 0n);
    check(total.subtotal_overflow === (input + output > BigInt(MAX)));
    if (total.known_units && !total.subtotal_overflow)
      check(BigInt(total.input_tokens!) === input && BigInt(total.output_tokens!) === output && BigInt(total.total_tokens!) === input + output);
  }
}
function pair(value: unknown): ProviderUsagePair | null {
  if (value === null) return null;
  const row = object(value, ['input_tokens', 'output_tokens', 'total_tokens']);
  check(integer(row.input_tokens) && integer(row.output_tokens) && integer(row.total_tokens)
    && BigInt(row.input_tokens) + BigInt(row.output_tokens) === BigInt(row.total_tokens));
  return row as unknown as ProviderUsagePair;
}
function details(value: unknown, usage: ProviderUsagePair | null, unknownOnly: boolean): void {
  const row = object(value, ['version', 'cached_input_tokens', 'uncached_input_tokens', 'reasoning_output_tokens', 'cache_state', 'reasoning_state']);
  check(row.version === 1 && member(row.cache_state, ['known', 'partial', 'unknown', 'invalid']) && member(row.reasoning_state, ['known', 'unknown', 'invalid']));
  if (!usage || unknownOnly) check(row.cache_state === 'unknown' && row.reasoning_state === 'unknown');
  if (row.cache_state === 'known' || row.cache_state === 'partial') {
    check(usage && (row.cached_input_tokens === null || integer(row.cached_input_tokens, usage.input_tokens))
      && (row.uncached_input_tokens === null || integer(row.uncached_input_tokens, usage.input_tokens)));
    if (row.cache_state === 'known') check(row.cached_input_tokens !== null && row.uncached_input_tokens !== null
      && BigInt(row.cached_input_tokens as number) + BigInt(row.uncached_input_tokens as number) === BigInt(usage.input_tokens));
    else check((row.cached_input_tokens === null) !== (row.uncached_input_tokens === null));
  } else check(row.cached_input_tokens === null && row.uncached_input_tokens === null);
  if (row.reasoning_state === 'known') check(usage && integer(row.reasoning_output_tokens, usage.output_tokens));
  else check(row.reasoning_output_tokens === null);
}
function sample(value: unknown, highWater: number): ProviderSample {
  const row = object(value, ['kind', 'seq', 'receipt_version', 'call_id', 'attempt_id', 'attempt_index', 'engine', 'actor', 'scope',
    'outcome', 'method_entered', 'usage', 'usage_details', 'selected', 'callback_linkage']);
  check(member(row.kind, ['request', 'logical_call', 'compatibility_callback']) && integer(row.seq, highWater) && row.seq > 0 && typeof row.selected === 'boolean');
  // Read scope discriminant via descriptor, never a getter before exact allowlist.
  check(row.scope !== null && typeof row.scope === 'object');
  const descriptor = Object.getOwnPropertyDescriptor(row.scope, 'kind'); check(descriptor && 'value' in descriptor);
  const scope = object(row.scope, descriptor.value === 'child' ? ['kind', 'subagent_id', 'generation'] : ['kind']);
  check(scope.kind === 'root' || scope.kind === 'child' && uuid(scope.subagent_id) && integer(scope.generation) && scope.generation > 0);
  const usage = pair(row.usage), callback = row.kind === 'compatibility_callback';
  if (callback) {
    check(row.receipt_version === null && row.call_id === null && row.attempt_id === null && row.attempt_index === null
      && row.engine === null && row.actor === null && row.outcome === null && row.method_entered === null && row.selected === false
      && member(row.callback_linkage, ['matched', 'unmatched']) && (row.callback_linkage !== 'matched' || usage !== null));
  } else {
    check((row.receipt_version === 1 || row.receipt_version === 2 || row.receipt_version === 3) && uuid(row.call_id)
      && member(row.engine, ['legacy', 'graph', 'deep']) && (row.actor === 'runtime_model'
        || row.engine === 'deep' && member(row.actor, ['deep_builtin_investigator', 'deep_builtin_verifier']))
      && member(row.outcome, ['returned', 'failed', 'cancelled']) && typeof row.method_entered === 'boolean' && row.callback_linkage === null);
    if (row.outcome === 'returned') check(row.method_entered === true);
    if (row.kind === 'request') {
      check(uuid(row.attempt_id) && integer(row.attempt_index) && row.attempt_index > 0);
      if (!row.method_entered) check(row.outcome === 'cancelled' && usage === null);
    } else check(row.attempt_id === null && row.attempt_index === null && (row.outcome === 'returned' || usage === null));
  }
  details(row.usage_details, usage, callback || row.kind === 'logical_call' || row.receipt_version === 1);
  return row as unknown as ProviderSample;
}
function compareObserved(total: ProviderSubtotal, rows: readonly ProviderSample[], complete: boolean, compatibility = false): void {
  const known = rows.filter(row => row.usage !== null), unknown = rows.length - known.length;
  check(known.length <= total.known_units && unknown <= total.unknown_units);
  check(rows.filter(row => row.kind === 'request').length <= total.request_units
    && rows.filter(row => row.kind === 'logical_call').length <= total.logical_call_units);
  if (complete) check(known.length === total.known_units && (!compatibility || unknown === total.unknown_units));
  const input = known.reduce((sum, row) => sum + BigInt(row.usage!.input_tokens), 0n);
  const output = known.reduce((sum, row) => sum + BigInt(row.usage!.output_tokens), 0n);
  if (known.length === total.known_units) {
    check(total.subtotal_overflow === (input + output > BigInt(MAX)));
    if (known.length && !total.subtotal_overflow) check(input === BigInt(total.input_tokens!) && output === BigInt(total.output_tokens!));
  } else if (total.known_units && !total.subtotal_overflow) check(input <= BigInt(total.input_tokens!) && output <= BigInt(total.output_tokens!));
}
export function validateProviderReport(value: unknown, snapshot: Snapshot, legacy: CompatibilityEvents): ProviderReport {
  const row = object(value, ['version', 'policy', 'state', 'selected', 'compatibility', 'counts', 'scopes', 'samples',
    'billing_complete', 'account_cap_guaranteed', 'wire_request_coverage']);
  check(row.version === 1 && row.policy === 'linked_requests_else_logical_calls_no_callback_addition'
    && member(row.state, ['unknown', 'partial', 'known_for_selected_receipts']) && row.billing_complete === false
    && row.account_cap_guaranteed === false && row.wire_request_coverage === 'unknown');
  const max = snapshot.scanned_events, rawCounts = object(row.counts, providerCountKeys);
  check(Object.values(rawCounts).every(value => integer(value, max)));
  const c = rawCounts as unknown as ProviderCounts;
  check(c.call_ids === c.calls_matched + c.calls_unsettled + c.call_invalid_ids + c.call_duplicate_ids
    && c.request_ids === c.requests_matched + c.requests_unsettled + c.request_invalid_ids + c.request_duplicate_ids);
  check(c.call_ids + c.calls_matched + c.call_duplicate_ids <= c.call_rows
    && c.request_ids + c.requests_matched + c.request_duplicate_ids <= c.request_rows
    && c.call_rows + c.request_rows + c.callbacks + c.unclassified_rows <= max
    && c.unclassified_rows === snapshot.omitted_usage_rows);
  check(c.call_returned + c.call_failed + c.call_cancelled === c.calls_matched && c.call_method_entered <= c.calls_matched
    && c.call_returned <= c.call_method_entered && c.request_returned + c.request_failed + c.request_cancelled === c.requests_matched
    && c.request_method_entered <= c.requests_matched && c.request_returned <= c.request_method_entered);
  check(c.requests_linked + c.request_orphans === c.request_ids && c.requests_linked <= c.requests_matched + c.requests_unsettled
    && c.summary_mismatches <= c.calls_matched && c.historical_calls_with_requests <= c.calls_matched
    && c.request_ordinal_gaps <= c.calls_matched && c.request_ordinal_collisions <= c.request_rows);
  check(c.callbacks_matched + c.callbacks_unmatched === c.callbacks && c.callbacks_invalid_scope <= c.callbacks_unmatched
    && c.callbacks_invalid_scope <= c.invalid_scope_frames && c.invalid_scope_frames <= c.call_rows + c.request_rows + c.callbacks
    && c.callbacks_invalid_usage <= c.callbacks_unmatched - c.callbacks_invalid_scope);
  const selected = subtotal(row.selected, max), compatibility = subtotal(row.compatibility, max);
  check(units(selected) <= max && units(selected) === selected.request_units + selected.logical_call_units
    && selected.request_units === c.requests_linked && selected.logical_call_units <= c.calls_matched + c.calls_unsettled
    && (!c.callbacks_matched || c.calls_matched > 0 && selected.known_units > 0));
  check(compatibility.request_units === 0 && compatibility.logical_call_units === 0
    && units(compatibility) === c.callbacks_unmatched - c.callbacks_invalid_scope && compatibility.unknown_units === c.callbacks_invalid_usage);
  check(legacy.observed.known_model_events === c.callbacks_matched + compatibility.known_units
    && legacy.unknown_model_events === c.callbacks_invalid_scope + c.callbacks_invalid_usage);
  const scopes = object(row.scopes, ['basis', 'root', 'children_total', 'children', 'children_omitted', 'other_children', 'limit', 'truncated']);
  check(scopes.basis === 'recorded_outer_tags_not_reconstructed' && scopes.limit === PREFIX && integer(scopes.children_total, units(selected)));
  const children = array(scopes.children, PREFIX), parts: ProviderSubtotal[] = [subtotal(scopes.root, max)], childKeys = new Set<string>();
  let previousChild: { id: string; generation: number } | undefined;
  for (const child of children) {
    const item = object(child, [...subtotalKeys, 'subagent_id', 'generation']);
    check(uuid(item.subagent_id) && integer(item.generation) && item.generation > 0);
    const key = `${item.subagent_id}:${item.generation}`; check(!childKeys.has(key)); childKeys.add(key);
    if (previousChild) check(previousChild.id < item.subagent_id || previousChild.id === item.subagent_id && previousChild.generation < item.generation);
    previousChild = { id: item.subagent_id, generation: item.generation };
    const part = subtotal(child, max, ['subagent_id', 'generation']); check(units(part) > 0); parts.push(part);
  }
  check(children.length === Math.min(scopes.children_total, PREFIX) && scopes.children_omitted === Math.max(0, scopes.children_total - PREFIX)
    && scopes.truncated === (scopes.children_total > PREFIX));
  const other = subtotal(scopes.other_children, max);
  check(scopes.children_omitted === 0 ? units(other) === 0 : units(other) >= (scopes.children_omitted as number));
  parts.push(other); compareParts(selected, parts);
  const samples = object(row.samples, ['total', 'emitted', 'omitted', 'limit', 'truncated', 'items']);
  check(integer(samples.total, c.call_rows + c.request_rows + c.callbacks) && samples.limit === PREFIX);
  const items = array(samples.items, PREFIX).map(item => sample(item, snapshot.event_high_water));
  check(samples.emitted === items.length && items.length === Math.min(samples.total, PREFIX)
    && samples.omitted === Math.max(0, samples.total - PREFIX) && samples.truncated === (samples.total > PREFIX));
  let previous = 0;
  for (const item of items) { check(item.seq > previous); previous = item.seq; }
  const chosen = items.filter(item => item.selected), callback = items.filter(item => item.kind === 'compatibility_callback');
  check(chosen.filter(item => item.kind === 'request').length <= selected.request_units
    && chosen.filter(item => item.kind === 'logical_call').length <= selected.logical_call_units);
  const matched = callback.filter(item => item.callback_linkage === 'matched'), unmatched = callback.filter(item => item.callback_linkage === 'unmatched');
  check(matched.length <= c.callbacks_matched && unmatched.length <= c.callbacks_unmatched - c.callbacks_invalid_scope);
  const unselectedKnown = items.filter(item => item.kind !== 'compatibility_callback' && !item.selected && item.usage !== null).length;
  check(unselectedKnown <= c.unselected_known_receipts && c.unselected_known_receipts <= (samples.total as number));
  if (!samples.truncated) check(matched.length === c.callbacks_matched && unmatched.length === c.callbacks_unmatched - c.callbacks_invalid_scope
    && unselectedKnown === c.unselected_known_receipts);
  // Ambiguity is preserved, not schema-erased; duplicated unselected rows are
  // allowed, but no selected ID/ordinal can charge an observed collision twice.
  for (const item of chosen) {
    if (item.kind === 'request') check(items.filter(other => other.kind === 'request' && (other.attempt_id === item.attempt_id
      || other.call_id === item.call_id && other.attempt_index === item.attempt_index)).length === 1);
    if (item.kind === 'logical_call') check(items.filter(other => other.kind === 'logical_call' && other.call_id === item.call_id).length === 1
      && !items.some(other => other.kind === 'request' && other.call_id === item.call_id));
    const scoped = item.scope.kind === 'root' ? parts[0]! : children.find(value => {
      const child = value as { subagent_id: string; generation: number };
      return item.scope.kind === 'child' && child.subagent_id === item.scope.subagent_id && child.generation === item.scope.generation;
    }) as ProviderSubtotal | undefined;
    if (scoped) check(units(scoped) > 0); else check((scopes.children_omitted as number) > 0);
  }
  compareObserved(parts[0]!, chosen.filter(item => item.scope.kind === 'root'), false);
  for (const value of children) {
    const child = value as ProviderSubtotal & { subagent_id: string; generation: number };
    compareObserved(child, chosen.filter(item => item.scope.kind === 'child' && item.scope.subagent_id === child.subagent_id
      && item.scope.generation === child.generation), false);
  }
  const unseen = chosen.filter(item => item.scope.kind === 'child' && !childKeys.has(`${item.scope.subagent_id}:${item.scope.generation}`));
  if (previousChild) for (const item of unseen) {
    check(item.scope.kind === 'child' && (previousChild.id < item.scope.subagent_id
      || previousChild.id === item.scope.subagent_id && previousChild.generation < item.scope.generation));
  }
  compareObserved(other, unseen, false);
  compareObserved(selected, chosen, !samples.truncated); compareObserved(compatibility, unmatched, !samples.truncated, true);
  const old: ProviderSubtotal = { known_units: legacy.observed.known_model_events, unknown_units: legacy.unknown_model_events,
    request_units: 0, logical_call_units: 0, input_tokens: legacy.observed.input_tokens, output_tokens: legacy.observed.output_tokens,
    total_tokens: legacy.observed.known_model_events && !legacy.observed.subtotal_overflow
      ? Number(BigInt(legacy.observed.input_tokens!) + BigInt(legacy.observed.output_tokens!)) : null,
    subtotal_overflow: legacy.observed.subtotal_overflow };
  compareObserved(old, callback, !samples.truncated); // compatible evidence checked, NEVER added to primary
  const uncertain = snapshot.truncated || snapshot.omitted_usage_rows > 0 || selected.unknown_units > 0 || selected.subtotal_overflow
    || ['calls_unsettled', 'call_invalid_ids', 'call_duplicate_ids', 'requests_unsettled', 'request_invalid_ids', 'request_duplicate_ids',
      'request_orphans', 'request_ordinal_collisions', 'request_ordinal_gaps', 'summary_mismatches', 'historical_calls_with_requests',
      'callbacks_unmatched', 'invalid_scope_frames', 'invalid_frames'].some(key => c[key as keyof ProviderCounts] > 0);
  check(row.state === (selected.known_units === 0 ? 'unknown' : uncertain ? 'partial' : 'known_for_selected_receipts'));
  return row as unknown as ProviderReport; // parent validator clones/deep-freezes AFTER all allowlists
}
