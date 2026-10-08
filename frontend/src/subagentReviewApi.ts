import { request } from "./api";

export interface ReviewedSpawnBody { readonly confirmed: true; readonly prompt: string }
export interface ReviewedFollowBody extends ReviewedSpawnBody { readonly expected_generation: number }
export interface ReviewedCancelBody { readonly confirmed: true; readonly expected_generation: number }
export interface ChildRecord {
  subagent_id: string; parent_run_id: string; generation: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  prompt: string; answer: string; created_at: string; updated_at: string; error_present: boolean;
  history: Array<{ prompt: string; answer: string }>;
  history_total: number; history_offset: number; history_limit: number; history_truncated: boolean;
}
interface OutputFlags { sensitive: true; globally_redacted: false; text_trusted: false }
interface HeldRead {
  read_only: true; owner_held: true; failed_close_diagnostic: boolean;
  execution_admission: "quarantined" | "closed_failed" | "not_checked_by_read";
}
export interface ChildSnapshot {
  parent_run_id: string; items: ChildRecord[]; total: number; limit: 100; truncated: boolean;
  counts: { lifetime_for_parent: number; durable_active_for_parent: number };
  capabilities: { workspace_read: true; workspace_write: false; command_execute: false; mcp_execute: false; delegate: false };
  child_mode: "graph"; profile_inheritance: "parent_snapshot" | "parent_profile_resolution";
  limits: { lifetime_per_parent: number; global_active: number; global_queue: number };
  scheduler_observation: { scope: "original_scheduler_in_memory"; active: number; queued: number };
  service: HeldRead; physical_drain_verified: false; output: OutputFlags;
}
export interface ChildHistory extends ChildRecord { service: HeldRead; physical_drain_verified: false; output: OutputFlags }
export interface ChildAdmission {
  parent_run_id: string; reviewed_generation: number | null; record: ChildRecord;
  admission_acknowledged: true; completion_verified: false; physical_drain_verified: false; output: OutputFlags;
}
export interface ChildCancelReceipt {
  parent_run_id: string; subagent_id: string; reviewed_generation: number;
  cancel_requested: boolean; physical_drain_verified: false;
}

const RECORD_KEYS = ["subagent_id", "parent_run_id", "status", "prompt", "answer", "generation", "created_at", "updated_at",
  "history", "history_total", "history_offset", "history_limit", "history_truncated", "error_present"];
const INVALID_REQUEST = "invalid_subagent_review_request", INVALID_RESPONSE = "invalid_subagent_review_response";
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function keys(value: Record<string, unknown>, expected: string[]): boolean {
  return Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key));
}
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}
function id(value: unknown): value is string { return typeof value === "string" && /^[0-9a-f]{32}$/.test(value); }
function text(value: unknown, limit: number): value is string {
  if (typeof value !== "string" || value.length > limit * 2) return false;
  const characters = Array.from(value);
  return characters.length <= limit && characters.every(character =>
    character.length !== 1 || character.charCodeAt(0) < 0xd800 || character.charCodeAt(0) > 0xdfff);
}
function flags(value: unknown): boolean {
  return object(value) && keys(value, ["sensitive", "globally_redacted", "text_trusted"])
    && value.sensitive === true && value.globally_redacted === false && value.text_trusted === false;
}
function held(value: unknown): boolean {
  return object(value) && keys(value, ["read_only", "owner_held", "failed_close_diagnostic", "execution_admission"])
    && value.read_only === true && value.owner_held === true && typeof value.failed_close_diagnostic === "boolean"
    && (value.execution_admission === "quarantined"
      || value.execution_admission === (value.failed_close_diagnostic ? "closed_failed" : "not_checked_by_read"));
}
function invalidResponse(): never { throw new Error(INVALID_RESPONSE); }
function invalidRequest(): never { throw new Error(INVALID_REQUEST); }
function scope(parent: string, child?: string): string {
  if (!id(parent) || child !== undefined && !id(child)) invalidRequest();
  return `/api/v1/subagent-review/runs/${parent}${child === undefined ? "" : `/${child}`}`;
}

// Explicit draft normalization, NOT an implicit rewrite of an already confirmed
// request. Match original Python str.strip(), including NEL and U+001C..001F,
// excluding BOM. The controller must retain its raw draft and review this result.
export function reviewPrompt(value: string): string {
  if (typeof value !== "string") invalidRequest();
  const stripped = value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu, "");
  if (!text(stripped, 4000) || !stripped.length) invalidRequest();
  return stripped;
}

function promptBody(value: unknown, follow = false): ReviewedSpawnBody | ReviewedFollowBody {
  if (!object(value) || !keys(value, ["confirmed", "prompt", ...(follow ? ["expected_generation"] : [])])
    || value.confirmed !== true || !text(value.prompt, 4000) || reviewPrompt(value.prompt) !== value.prompt
    || follow && !integer(value.expected_generation, 1, Number.MAX_SAFE_INTEGER - 1)) invalidRequest();
  // Copy and freeze original accepted intent before any await. Caller mutation
  // cannot alter later receipt attribution or the serialized request.
  return follow ? Object.freeze({ confirmed: true, prompt: value.prompt, expected_generation: value.expected_generation as number })
    : Object.freeze({ confirmed: true, prompt: value.prompt });
}
function cancelBody(value: unknown): ReviewedCancelBody {
  if (!object(value) || !keys(value, ["confirmed", "expected_generation"]) || value.confirmed !== true
    || !integer(value.expected_generation, 1)) invalidRequest();
  return Object.freeze({ confirmed: true, expected_generation: value.expected_generation });
}
function serialize(body: ReviewedSpawnBody | ReviewedFollowBody | ReviewedCancelBody): string {
  const encoded = JSON.stringify(body);
  if (new TextEncoder().encode(encoded).length > 16384) invalidRequest();
  return encoded;
}

function parseRecord(value: unknown, parent: string, extraKeys: string[] = []): ChildRecord {
  if (!object(value) || !keys(value, [...RECORD_KEYS, ...extraKeys]) || !id(value.subagent_id) || value.parent_run_id !== parent
    || !integer(value.generation, 1) || typeof value.status !== "string"
    || !["queued", "running", "completed", "failed", "cancelled"].includes(value.status)
    || !text(value.prompt, 4000) || !text(value.answer, 8000) || !text(value.created_at, 64) || !value.created_at
    || !text(value.updated_at, 64) || !value.updated_at || typeof value.error_present !== "boolean"
    || !integer(value.history_total) || value.history_total !== value.generation - 1 || !integer(value.history_offset, 0, value.history_total)
    || !integer(value.history_limit, 1, 16) || !Array.isArray(value.history)
    || value.history.length !== Math.min(value.history_limit, value.history_total - value.history_offset)
    || value.history_truncated !== (value.history_offset > 0 || value.history_offset + value.history_limit < value.history_total)) invalidResponse();
  for (const turn of value.history) {
    if (!object(turn) || !keys(turn, ["prompt", "answer"]) || !text(turn.prompt, 4000) || !text(turn.answer, 8000)) invalidResponse();
  }
  return value as unknown as ChildRecord;
}

export function parseChildSnapshot(value: unknown, parent: string): ChildSnapshot {
  if (!id(parent) || !object(value) || !keys(value, ["parent_run_id", "items", "total", "limit", "truncated", "counts",
    "capabilities", "child_mode", "profile_inheritance", "service", "limits", "scheduler_observation", "physical_drain_verified", "output"])
    || value.parent_run_id !== parent || !integer(value.total) || value.limit !== 100
    || value.truncated !== (value.total > 100) || !Array.isArray(value.items) || value.items.length !== Math.min(100, value.total)
    || !object(value.counts) || !keys(value.counts, ["lifetime_for_parent", "durable_active_for_parent"])
    || value.counts.lifetime_for_parent !== value.total || !integer(value.counts.durable_active_for_parent, 0, value.total)
    || !object(value.capabilities) || !keys(value.capabilities, ["workspace_read", "workspace_write", "command_execute", "mcp_execute", "delegate"])
    || value.capabilities.workspace_read !== true || value.capabilities.workspace_write !== false
    || value.capabilities.command_execute !== false || value.capabilities.mcp_execute !== false || value.capabilities.delegate !== false
    || value.child_mode !== "graph" || (value.profile_inheritance !== "parent_snapshot" && value.profile_inheritance !== "parent_profile_resolution")
    || !held(value.service) || value.physical_drain_verified !== false || !flags(value.output)
    || !object(value.limits) || !keys(value.limits, ["lifetime_per_parent", "global_active", "global_queue"])
    || !integer(value.limits.lifetime_per_parent, 1) || !integer(value.limits.global_active, 1) || !integer(value.limits.global_queue, 1)
    || !object(value.scheduler_observation) || !keys(value.scheduler_observation, ["scope", "active", "queued"])
    || value.scheduler_observation.scope !== "original_scheduler_in_memory"
    || !integer(value.scheduler_observation.active, 0, value.limits.global_active)
    || !integer(value.scheduler_observation.queued, 0, value.limits.global_queue)) invalidResponse();
  const ids = new Set<string>();
  let shownActive = 0;
  for (const item of value.items) {
    const record = parseRecord(item, parent);
    if (ids.has(record.subagent_id) || record.history_limit !== 16 || record.history_offset !== Math.max(0, record.history_total - 16)) invalidResponse();
    ids.add(record.subagent_id);
    if (record.status === "queued" || record.status === "running") shownActive++;
  }
  if (value.counts.durable_active_for_parent < shownActive
    || value.truncated === false && value.counts.durable_active_for_parent !== shownActive) invalidResponse();
  return value as unknown as ChildSnapshot;
}

export function parseChildHistory(value: unknown, parent: string, child: string, generation: number, offset: number, limit: number): ChildHistory {
  const record = parseRecord(value, parent, ["service", "physical_drain_verified", "output"]);
  if (!id(parent) || !id(child) || !integer(generation, 1) || !integer(offset) || !integer(limit, 1, 16)
    || record.subagent_id !== child || record.generation !== generation || record.history_offset !== offset || record.history_limit !== limit
    || !object(value) || !held(value.service) || value.physical_drain_verified !== false || !flags(value.output)) invalidResponse();
  return value as unknown as ChildHistory;
}

export function parseChildAdmission(value: unknown, parent: string, prompt: string, child?: string, generation?: number): ChildAdmission {
  if (!id(parent) || !text(prompt, 4000) || (child === undefined) !== (generation === undefined)
    || child !== undefined && (!id(child) || !integer(generation, 1, Number.MAX_SAFE_INTEGER - 1))
    || !object(value) || !keys(value, ["parent_run_id", "reviewed_generation", "record", "admission_acknowledged",
      "completion_verified", "physical_drain_verified", "output"])
    || value.parent_run_id !== parent || value.reviewed_generation !== (generation ?? null)
    || value.admission_acknowledged !== true || value.completion_verified !== false || value.physical_drain_verified !== false
    || !flags(value.output)) invalidResponse();
  const record = parseRecord(value.record, parent);
  if (record.prompt !== prompt || record.generation !== (generation === undefined ? 1 : generation + 1)
    || child !== undefined && record.subagent_id !== child || record.status !== "queued" || record.answer !== "" || record.error_present
    || record.history_limit !== 16 || record.history_offset !== Math.max(0, record.history_total - 16)
    || generation === undefined && record.history_total !== 0) invalidResponse();
  return value as unknown as ChildAdmission;
}

export function parseChildCancel(value: unknown, parent: string, child: string, generation: number): ChildCancelReceipt {
  if (!id(parent) || !id(child) || !integer(generation, 1) || !object(value)
    || !keys(value, ["parent_run_id", "subagent_id", "reviewed_generation", "cancel_requested", "physical_drain_verified"])
    || value.parent_run_id !== parent || value.subagent_id !== child || value.reviewed_generation !== generation
    || typeof value.cancel_requested !== "boolean" || value.physical_drain_verified !== false) invalidResponse();
  return value as unknown as ChildCancelReceipt;
}

async function send(path: string, body?: string): Promise<unknown> {
  try {
    return await request<unknown>(path, { cache: "no-store", ...(body === undefined ? {} : { method: "POST", body }) });
  } catch {
    // Do not surface arbitrary HTTP/connector/private exception messages. The
    // controller must retain every dispatched failed POST as unknown, even 4xx.
    throw new Error("subagent_review_request_failed");
  }
}

// No initialization/polling, compatibility endpoints, implicit confirmation,
// retries, abort/timer, browser storage, provider calls or source reconciliation.
// This is transport only; B3 must own original promises/drafts/unknown receipts
// across hide/source change/close. Generation pinning is NOT request idempotency.
export const subagentReviewApi = {
  async snapshot(parent: string): Promise<ChildSnapshot> {
    return parseChildSnapshot(await send(scope(parent)), parent);
  },
  async history(parent: string, child: string, generation: number, offset: number, limit = 16): Promise<ChildHistory> {
    const base = scope(parent, child);
    if (!integer(generation, 1) || !integer(offset) || !integer(limit, 1, 16)) invalidRequest();
    return parseChildHistory(await send(`${base}/history?expected_generation=${generation}&offset=${offset}&limit=${limit}`),
      parent, child, generation, offset, limit);
  },
  async spawn(parent: string, body: ReviewedSpawnBody): Promise<ChildAdmission> {
    const base = scope(parent), original = promptBody(body);
    return parseChildAdmission(await send(`${base}/spawn`, serialize(original)), parent, original.prompt);
  },
  async followUp(parent: string, child: string, body: ReviewedFollowBody): Promise<ChildAdmission> {
    const base = scope(parent, child), original = promptBody(body, true) as ReviewedFollowBody;
    return parseChildAdmission(await send(`${base}/follow-ups`, serialize(original)), parent, original.prompt, child, original.expected_generation);
  },
  async cancel(parent: string, child: string, body: ReviewedCancelBody): Promise<ChildCancelReceipt> {
    const base = scope(parent, child), original = cancelBody(body);
    return parseChildCancel(await send(`${base}/cancel`, serialize(original)), parent, child, original.expected_generation);
  },
};
