// Explicit closed v1/v2/v3/v4 allowlists. No import IO/general error-body echo.
import { validateReportEvidence, type ReportEvidence } from './runReportEvidence';
import { validateProviderReport, type ProviderReport } from './providerReport';
import { validateProviderCost, type ProviderCost } from './providerCost';
export const REPORT_RESPONSE_BYTES = 2 * 1024 * 1024;
const LIMIT = 5000, MAX = Number.MAX_SAFE_INTEGER;
export interface ReportSubtotal { readonly known_model_events: number; readonly input_tokens: number | null;
  readonly output_tokens: number | null; readonly subtotal_overflow: boolean }
export interface HistoricalCost { readonly state: "unknown"; readonly amount: null; readonly currency: null; readonly price_snapshot: null;
  readonly reason: "no_frozen_billing_price_receipt"; readonly account_cap_guaranteed: false }
export interface RunReport {
  readonly version: 1 | 2 | 3 | 4; readonly run_id: string;
  readonly evidence?: ReportEvidence; // v2/v3/v4 REQUIRED by validator, v1 MUST omit
  readonly usage_basis?: 'provider_usage_only_not_compat_model_events'; // v3/v4 required, v1/v2 forbidden
  readonly provider_usage?: ProviderReport; // v3/v4 required, not an additive charge layer
  readonly source: { readonly status: string; readonly mode: "legacy" | "graph" | "deep"; readonly lease_active: boolean };
  readonly snapshot: { readonly event_total: number; readonly event_high_water: number; readonly scanned_events: number;
    readonly limit: 5000; readonly truncated: boolean; readonly omitted_usage_rows: number;
    readonly consistency: "same_runtime_database_read_transaction" };
  readonly redaction: { readonly policy: "allowlisted_identifiers_enums_numeric_counters_only" | "allowlisted_identifiers_enums_numeric_counters_and_tariff_contract_only"; readonly free_text_exported: false;
    readonly anonymous: false; readonly original_evidence_modified: false };
  readonly usage: { readonly state: "unknown" | "partial" | "known_for_recorded_model_events";
    readonly observed: ReportSubtotal; readonly root: ReportSubtotal;
    readonly children: readonly (ReportSubtotal & { readonly subagent_id: string; readonly generation: number })[];
    readonly unknown_model_events: number; readonly unclassified_usage_envelopes: number; readonly billing_complete: false;
    readonly old_child_attribution: "missing_tags_not_reconstructed"; readonly estimated_tokens_as_actual: false };
  readonly cost: HistoricalCost | ProviderCost; // v4 closed projection ONLY; historical versions MUST keep unknown shape.
  readonly recorded_effect_counts: Readonly<Record<"graph.tool_finished" | "deep.tool_finished" | "mcp.tool_executed" | "patch.applied", number>>;
  readonly physical_drain_verified: false; readonly task_quality_scored: false;
  readonly query: { readonly sql_read_only: true; readonly filesystem_zero_write_guarantee: false; readonly decoded_bytes: number };
  readonly service: { readonly read_only: true; readonly owner_held: true; readonly failed_close_diagnostic: boolean;
    readonly execution_admission: "quarantined" | "closed_failed" | "not_checked_by_read" };
}
function reject(): never { throw new Error("invalid_run_report"); }
function check(condition: unknown): asserts condition { if (!condition) reject(); }
function object(value: unknown, keys: string[]): Record<string, unknown> {
  check(value !== null && typeof value === "object" && !Array.isArray(value));
  check(Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
  const row = value as Record<string, unknown>, actual = Reflect.ownKeys(row);
  check(actual.length === keys.length && actual.every(key => typeof key === "string" && keys.includes(key)));
  check(keys.every(key => { const descriptor = Object.getOwnPropertyDescriptor(row, key); return descriptor?.enumerable && "value" in descriptor; }));
  return row;
}
function array(value: unknown, max: number): unknown[] {
  check(Array.isArray(value) && Object.getPrototypeOf(value) === Array.prototype);
  const length = Object.getOwnPropertyDescriptor(value, 'length');
  check(length && 'value' in length && Number.isInteger(length.value) && length.value >= 0 && length.value <= max);
  check(Reflect.ownKeys(value).length === length.value + 1);
  for (let index = 0; index < length.value; index++) {
    const item = Object.getOwnPropertyDescriptor(value, String(index)); check(item?.enumerable && 'value' in item);
  }
  return value;
}
const integer = (value: unknown, max = MAX): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= max;
export const reportIdentifier = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{32}$/.test(value);
export function reportFilename(run: string): string { check(reportIdentifier(run)); return `doppel-report-${run}.json`; }
const subtotalKeys = ["known_model_events", "input_tokens", "output_tokens", "subtotal_overflow"];
function subtotal(value: unknown, child = false): ReportSubtotal {
  const row = object(value, child ? [...subtotalKeys, "subagent_id", "generation"] : subtotalKeys);
  check(integer(row.known_model_events, LIMIT) && typeof row.subtotal_overflow === "boolean");
  if (row.known_model_events === 0) check(row.input_tokens === null && row.output_tokens === null && row.subtotal_overflow === false);
  else if (row.subtotal_overflow) check(row.known_model_events >= 2 && row.input_tokens === null && row.output_tokens === null);
  else { check(integer(row.input_tokens) && integer(row.output_tokens)); check(BigInt(row.input_tokens) + BigInt(row.output_tokens) <= BigInt(MAX)); }
  return row as unknown as ReportSubtotal;
}
function freeze<T>(value: T): T {
  if (value !== null && typeof value === "object") { for (const item of Object.values(value)) freeze(item); Object.freeze(value); }
  return value;
}
export function parseRunReport(value: unknown, run: string): RunReport {
  check(reportIdentifier(run));
  check(value !== null && typeof value === 'object' && !Array.isArray(value));
  const versionDescriptor = Object.getOwnPropertyDescriptor(value, 'version');
  check(versionDescriptor && 'value' in versionDescriptor && (versionDescriptor.value === 1 || versionDescriptor.value === 2 || versionDescriptor.value === 3 || versionDescriptor.value === 4));
  const report = object(value, ["version", "run_id", "source", "snapshot", "redaction", "usage", "cost", "recorded_effect_counts",
    "physical_drain_verified", "task_quality_scored", "query", "service", ...(versionDescriptor.value >= 2 ? ['evidence'] : []),
    ...(versionDescriptor.value >= 3 ? ['usage_basis', 'provider_usage'] : [])]);
  check(report.run_id === run && report.physical_drain_verified === false && report.task_quality_scored === false);
  if (report.version === 2 || report.version === 3 || report.version === 4) validateReportEvidence(report.evidence);
  const source = object(report.source, ["status", "mode", "lease_active"]);
  check(typeof source.status === "string" && ["queued", "running", "interrupted", "completed", "failed", "cancelled", "interrupted_expired"].includes(source.status)
    && typeof source.mode === "string" && ["legacy", "graph", "deep"].includes(source.mode) && typeof source.lease_active === "boolean");
  const snapshot = object(report.snapshot, ["event_total", "event_high_water", "scanned_events", "limit", "truncated", "omitted_usage_rows", "consistency"]);
  check(integer(snapshot.event_total) && integer(snapshot.event_high_water) && integer(snapshot.scanned_events, LIMIT)
    && snapshot.limit === LIMIT && typeof snapshot.truncated === "boolean" && integer(snapshot.omitted_usage_rows, snapshot.scanned_events)
    && snapshot.consistency === "same_runtime_database_read_transaction");
  check(snapshot.scanned_events === Math.min(snapshot.event_total, LIMIT) && snapshot.truncated === (snapshot.event_total > LIMIT)
    && (snapshot.event_total === 0 ? snapshot.event_high_water === 0 : snapshot.event_high_water >= snapshot.event_total));
  const redaction = object(report.redaction, ["policy", "free_text_exported", "anonymous", "original_evidence_modified"]);
  check(redaction.policy === (report.version === 4 ? "allowlisted_identifiers_enums_numeric_counters_and_tariff_contract_only" : "allowlisted_identifiers_enums_numeric_counters_only") && redaction.free_text_exported === false
    && redaction.anonymous === false && redaction.original_evidence_modified === false);
  const usage = object(report.usage, ["state", "observed", "root", "children", "unknown_model_events", "unclassified_usage_envelopes", "billing_complete", "old_child_attribution", "estimated_tokens_as_actual"]);
  check(integer(usage.unknown_model_events, snapshot.scanned_events) && usage.unclassified_usage_envelopes === snapshot.omitted_usage_rows
    && usage.billing_complete === false && usage.old_child_attribution === "missing_tags_not_reconstructed" && usage.estimated_tokens_as_actual === false);
  const legacyChildren = array(usage.children, LIMIT);
  const observed = subtotal(usage.observed), root = subtotal(usage.root), parts: ReportSubtotal[] = [root], seen = new Set<string>();
  for (const item of legacyChildren) {
    const row = object(item, [...subtotalKeys, "subagent_id", "generation"]);
    check(reportIdentifier(row.subagent_id) && integer(row.generation) && row.generation >= 1);
    const key = `${row.subagent_id}:${row.generation}`; check(!seen.has(key)); seen.add(key);
    const part = subtotal(row, true); check(part.known_model_events > 0); parts.push(part);
  }
  check(parts.reduce((sum, row) => sum + row.known_model_events, 0) === observed.known_model_events);
  if (parts.some(row => row.subtotal_overflow)) check(observed.subtotal_overflow);
  else {
    const input = parts.reduce((sum, row) => sum + BigInt(row.input_tokens ?? 0), 0n);
    const output = parts.reduce((sum, row) => sum + BigInt(row.output_tokens ?? 0), 0n);
    check(observed.subtotal_overflow === (input + output > BigInt(MAX)));
    if (observed.known_model_events && !observed.subtotal_overflow) check(BigInt(observed.input_tokens!) === input && BigInt(observed.output_tokens!) === output);
  }
  const partial = usage.unknown_model_events > 0 || snapshot.omitted_usage_rows > 0 || snapshot.truncated || observed.subtotal_overflow;
  check(usage.state === (observed.known_model_events === 0 ? "unknown" : partial ? "partial" : "known_for_recorded_model_events"));
  let provider: ProviderReport | undefined;
  if (report.version === 3 || report.version === 4) {
    check(report.usage_basis === 'provider_usage_only_not_compat_model_events');
    provider = validateProviderReport(report.provider_usage, {
      scanned_events: snapshot.scanned_events, event_high_water: snapshot.event_high_water,
      omitted_usage_rows: snapshot.omitted_usage_rows, truncated: snapshot.truncated,
    }, { observed, unknown_model_events: usage.unknown_model_events });
  }
  if (report.version === 4) {
    check(provider); validateProviderCost(report.cost, provider); // SAME original selected prefix/scopes, never samples or current prices.
  } else {
    const cost = object(report.cost, ["state", "amount", "currency", "price_snapshot", "reason", "account_cap_guaranteed"]);
    check(cost.state === "unknown" && cost.amount === null && cost.currency === null && cost.price_snapshot === null
      && cost.reason === "no_frozen_billing_price_receipt" && cost.account_cap_guaranteed === false);
  }
  const counts = object(report.recorded_effect_counts, ["graph.tool_finished", "deep.tool_finished", "mcp.tool_executed", "patch.applied"]);
  check(Object.values(counts).every(count => integer(count, snapshot.scanned_events as number)));
  check(Object.values(counts).reduce<number>((sum, count) => sum + (count as number), 0) + observed.known_model_events
    + usage.unknown_model_events + snapshot.omitted_usage_rows <= snapshot.scanned_events);
  const query = object(report.query, ["sql_read_only", "filesystem_zero_write_guarantee", "decoded_bytes"]);
  check(query.sql_read_only === true && query.filesystem_zero_write_guarantee === false && integer(query.decoded_bytes, 8 * 1024 * 1024));
  const service = object(report.service, ["read_only", "owner_held", "failed_close_diagnostic", "execution_admission"]);
  check(service.read_only === true && service.owner_held === true && typeof service.failed_close_diagnostic === "boolean"
    && typeof service.execution_admission === "string"
    && (service.execution_admission === 'quarantined'
      || service.execution_admission === (service.failed_close_diagnostic ? 'closed_failed' : 'not_checked_by_read')));
  // No reference to injected mutable reply remains. Closed schema contains no text to scrub.
  return freeze(JSON.parse(JSON.stringify(report)) as RunReport);
}
async function fetchReport(run: string, download: boolean): Promise<RunReport> {
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined; let done = false;
  try {
    check(reportIdentifier(run));
    const response = await fetch(`/api/v1/reports/runs/${run}${download ? "/download" : ""}`, {
      method: "GET", cache: "no-store", redirect: "error", credentials: "same-origin", headers: { Accept: "application/json" },
    }); // original promise; no timeout abort/retry/new request on hide or close
    reader = response.body?.getReader();
    check(response.ok && !response.redirected && reader && /^application\/json(?:\s*;|$)/i.test(response.headers.get("Content-Type") ?? "")
      && response.headers.get("Cache-Control") === "no-store");
    if (download) check(response.headers.get("Content-Disposition") === `attachment; filename="${reportFilename(run)}"`);
    const length = response.headers.get("Content-Length");
    if (length !== null) check(/^\d+$/.test(length) && integer(Number(length), REPORT_RESPONSE_BYTES));
    const chunks: Uint8Array[] = []; let bytes = 0;
    while (true) {
      const chunk = await reader.read(); if (chunk.done) { done = true; break; }
      bytes += chunk.value.byteLength; check(bytes <= REPORT_RESPONSE_BYTES); chunks.push(chunk.value);
    }
    const buffer = new Uint8Array(bytes); let offset = 0;
    for (const chunk of chunks) { buffer.set(chunk, offset); offset += chunk.byteLength; }
    return parseRunReport(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(buffer)), run);
  } catch { throw new Error("report_request_failed"); }
  finally { if (reader) { if (!done) await reader.cancel().catch(() => {}); reader.releaseLock(); } }
}
async function saveReport(text: string, filename: string): Promise<void> {
  // Local explicit browser download request only. Not proof of file completion or
  // native host capability; no remote link, arbitrary path, storage or native chooser.
  const match = /^doppel-report-([0-9a-f]{32})\.json$/.exec(filename);
  check(match && new TextEncoder().encode(text).length <= REPORT_RESPONSE_BYTES);
  const report = parseRunReport(JSON.parse(text), match[1]!);
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
  let link: HTMLAnchorElement | undefined;
  try {
    link = document.createElement("a");
    link.href = url; link.download = filename; link.hidden = true; document.body.appendChild(link); link.click();
    // This tick only releases our object URL; it does not wait for the browser's
    // unobservable download manager/file writer. Promise is still joined by root.
    await new Promise<void>(resolve => window.setTimeout(resolve, 0));
  } finally { try { link?.remove(); } finally { URL.revokeObjectURL(url); } }
}
export interface RunReportTransport { read(run: string): Promise<unknown>; download(run: string): Promise<unknown>;
  save(text: string, filename: string): Promise<void> }
export const runReportApi: RunReportTransport = { read: run => fetchReport(run, false), download: run => fetchReport(run, true), save: saveReport };
