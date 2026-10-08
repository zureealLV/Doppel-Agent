// B2b4b independent numeric wire fixtures only, not runtime/SQL/native evidence.
import { reportV2Fixture } from './reportEvidenceFixture';
import { reportRun } from './runReportFixture';
export const receiptCall = 'b'.repeat(32), receiptAttempt = 'd'.repeat(32);
export const receiptUsage = (input_tokens = 7, output_tokens = 3) => ({ input_tokens, output_tokens, total_tokens: input_tokens + output_tokens });
export function receiptDetails() { return { version: 1, cached_input_tokens: null as number | null,
  uncached_input_tokens: null as number | null, reasoning_output_tokens: null as number | null, cache_state: 'unknown', reasoning_state: 'unknown' }; }
export function providerSubtotal(known = false, kind = 'request', pair = receiptUsage()) {
  return { known_units: known ? 1 : 0, unknown_units: 0, request_units: known && kind === 'request' ? 1 : 0,
    logical_call_units: known && kind === 'logical_call' ? 1 : 0, input_tokens: known ? pair.input_tokens : null,
    output_tokens: known ? pair.output_tokens : null, total_tokens: known ? pair.total_tokens : null, subtotal_overflow: false };
}
export function receiptSample(kind = 'request', seq = 3) {
  const callback = kind === 'compatibility_callback', request = kind === 'request';
  return { kind, seq, receipt_version: callback ? null : 2, call_id: callback ? null : receiptCall,
    attempt_id: request ? receiptAttempt : null, attempt_index: request ? 1 : null, engine: callback ? null : 'graph',
    actor: callback ? null : 'runtime_model', scope: { kind: 'root' }, outcome: callback ? null : 'returned',
    method_entered: callback ? null : true, usage: receiptUsage() as ReturnType<typeof receiptUsage> | null,
    usage_details: receiptDetails(), selected: request, callback_linkage: callback ? 'matched' : null };
}
export function providerProjectionFixture() {
  return { version: 1, policy: 'linked_requests_else_logical_calls_no_callback_addition', state: 'unknown',
    selected: providerSubtotal(), compatibility: providerSubtotal(),
    counts: { call_rows: 0, request_rows: 0, call_ids: 0, calls_matched: 0, calls_unsettled: 0,
      call_invalid_ids: 0, call_duplicate_ids: 0, request_ids: 0, requests_matched: 0, requests_unsettled: 0,
      request_invalid_ids: 0, request_duplicate_ids: 0, requests_linked: 0, request_orphans: 0,
      request_ordinal_collisions: 0, request_ordinal_gaps: 0, summary_mismatches: 0, historical_calls_with_requests: 0,
      call_returned: 0, call_failed: 0, call_cancelled: 0, call_method_entered: 0,
      request_returned: 0, request_failed: 0, request_cancelled: 0, request_method_entered: 0,
      callbacks: 0, callbacks_matched: 0, callbacks_unmatched: 0, callbacks_invalid_usage: 0, callbacks_invalid_scope: 0,
      invalid_scope_frames: 0, invalid_frames: 0, unclassified_rows: 0, unselected_known_receipts: 0 },
    scopes: { basis: 'recorded_outer_tags_not_reconstructed', root: providerSubtotal(), children_total: 0,
      children: [] as (ReturnType<typeof providerSubtotal> & { subagent_id: string; generation: number })[],
      children_omitted: 0, other_children: providerSubtotal(), limit: 16, truncated: false },
    samples: { total: 0, emitted: 0, omitted: 0, limit: 16, truncated: false, items: [] as ReturnType<typeof receiptSample>[] },
    billing_complete: false, account_cap_guaranteed: false, wire_request_coverage: 'unknown' };
}
export function reportV3Fixture(run = reportRun) {
  const old = reportV2Fixture(run), provider = providerProjectionFixture();
  Object.assign(old.snapshot, { event_total: 5, event_high_water: 5, scanned_events: 5 });
  Object.assign(old.recorded_effect_counts, { 'graph.tool_finished': 0 });
  Object.assign(old.usage.observed, { input_tokens: 7, output_tokens: 3 });
  Object.assign(old.usage.root, { input_tokens: 7, output_tokens: 3 });
  provider.state = 'known_for_selected_receipts'; provider.selected = providerSubtotal(true); provider.scopes.root = providerSubtotal(true);
  Object.assign(provider.counts, { call_rows: 2, request_rows: 2, call_ids: 1, calls_matched: 1,
    request_ids: 1, requests_matched: 1, requests_linked: 1, call_returned: 1, request_returned: 1,
    call_method_entered: 1, request_method_entered: 1, callbacks: 1, callbacks_matched: 1, unselected_known_receipts: 1 });
  Object.assign(provider.samples, { total: 3, emitted: 3, items: [receiptSample(), receiptSample('logical_call', 4), receiptSample('compatibility_callback', 5)] });
  return { ...old, version: 3, usage_basis: 'provider_usage_only_not_compat_model_events', provider_usage: provider };
}
