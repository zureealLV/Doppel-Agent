// Explicit v2 receipt allowlist. Raw IDs, paths, dates, plans and output omitted.
export interface ReportSection<T> { readonly state: 'known' | 'partial' | 'unknown'; readonly total: number | null;
  readonly scanned: number; readonly emitted: number; readonly omitted: number; readonly limit: 16;
  readonly truncated: boolean | null; readonly items: readonly T[]; readonly reason: 'unavailable' | 'missing_tables' | 'incomplete_evidence' | null }
type ReceiptState = 'confirmed' | 'not_confirmed' | 'unavailable';
export interface ReportPatch { readonly tool_call_sha256: string; readonly operation: 'propose_patch' | 'inverse_patch';
  readonly status: 'running' | 'completed' | 'failed'; readonly patch_id: string | null;
  readonly receipt_status: 'prepared' | 'applied' | 'failed' | null; readonly confirmed_applied: boolean;
  readonly files: number | null; readonly preimage_state: 'retained' | 'expired' | 'unknown' }
export interface ReportInverse { readonly review_id: string; readonly source_tool_call_sha256: string; readonly source_patch_id: string;
  readonly patch_id: string; readonly effect_tool_call_sha256: string; readonly status: string; readonly files: number;
  readonly source_receipt_confirmed: boolean; readonly source_receipt_state: ReceiptState; readonly effect_receipt_state: ReceiptState; readonly outcome_unknown: boolean;
  readonly pending_expired: boolean }
export interface ReportVerificationStep { readonly index: number; readonly status: 'running' | 'finished'; readonly exit_code: number | null;
  readonly success: boolean | null; readonly failure: 'verification_timeout' | 'verification_output_limit' | 'verification_supervision_unavailable' | null;
  readonly duration_ms: number | null }
export interface ReportVerification { readonly review_id: string; readonly source_tool_call_sha256: string; readonly source_patch_id: string;
  readonly plan_id: string; readonly status: string; readonly source_receipt_confirmed: boolean; readonly source_receipt_state: ReceiptState;
  readonly planned_commands: number; readonly sealed_steps: number; readonly exited_commands: number; readonly failed_attempts: number;
  readonly remaining_commands: number; readonly has_unknown_command: boolean; readonly attempts_success: boolean | null;
  readonly all_commands_exited: boolean; readonly steps: readonly ReportVerificationStep[];
  readonly target: 'current_workspace_not_original_patch_snapshot'; readonly project_acceptance: false; readonly pending_expired: boolean }
export interface ReportWorkOrder { readonly attempt_id: string; readonly work_order_id: string; readonly task_id_sha256: string;
  readonly execution_revision: number; readonly active_plan_revision: number; readonly attempt_number: number;
  readonly attempt_status: string; readonly order_status: string; readonly dispatch_state: 'pending' | 'admitted' | 'abandoned' | null }
export interface ReportEvidence { readonly patches: ReportSection<ReportPatch>; readonly inverses: ReportSection<ReportInverse>;
  readonly verifications: ReportSection<ReportVerification>; readonly work_orders: ReportSection<ReportWorkOrder>;
  readonly snapshot_consistency: 'independent_read_transactions_not_atomic_with_event_snapshot';
  readonly tool_call_identity: 'domain_separated_sha256_not_raw_id_or_approval_authority';
  readonly reserved_body_bytes: number; readonly decode_limit_bytes: 8388608; readonly sql_read_only: true;
  readonly filesystem_zero_write_guarantee: false; readonly current_git_ownership_proven: false; readonly project_acceptance_proven: false;
  readonly physical_drain_verified: false; readonly approval_granted: false; readonly automatic_retry_allowed: false }
const MAX = Number.MAX_SAFE_INTEGER;
function check(value: unknown): asserts value { if (!value) throw new Error('invalid_run_report'); }
function object(value: unknown, keys: string[]): Record<string, unknown> {
  check(value !== null && typeof value === 'object' && !Array.isArray(value));
  check(Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
  const row = value as Record<string, unknown>, actual = Reflect.ownKeys(row);
  check(actual.length === keys.length && actual.every(key => typeof key === 'string' && keys.includes(key)));
  check(keys.every(key => { const descriptor = Object.getOwnPropertyDescriptor(row, key); return descriptor?.enumerable && 'value' in descriptor; }));
  return row;
}
const integer = (value: unknown, max = MAX): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= max;
const signed = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value);
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{32}$/.test(value);
const hash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const member = (value: unknown, values: string[]): value is string => typeof value === 'string' && values.includes(value);
function source(row: Record<string, unknown>): void {
  check(hash(row.source_tool_call_sha256) && uuid(row.source_patch_id) && member(row.source_receipt_state, ['confirmed', 'not_confirmed', 'unavailable'])
    && row.source_receipt_confirmed === (row.source_receipt_state === 'confirmed'));
}
function patch(value: unknown): Record<string, unknown> {
  const row = object(value, ['tool_call_sha256', 'operation', 'status', 'patch_id', 'receipt_status', 'confirmed_applied', 'files', 'preimage_state']);
  check(hash(row.tool_call_sha256) && member(row.operation, ['propose_patch', 'inverse_patch']) && member(row.status, ['running', 'completed', 'failed'])
    && typeof row.confirmed_applied === 'boolean');
  if (row.patch_id === null) check(row.receipt_status === null && row.files === null && row.confirmed_applied === false && row.preimage_state === 'unknown');
  else check(uuid(row.patch_id) && member(row.receipt_status, ['prepared', 'applied', 'failed']) && integer(row.files, 32) && row.files >= 1
    && member(row.preimage_state, ['retained', 'expired']) && row.confirmed_applied === (row.status === 'completed' && row.receipt_status === 'applied'));
  return row;
}
function inverse(value: unknown): Record<string, unknown> {
  const row = object(value, ['review_id', 'source_tool_call_sha256', 'source_patch_id', 'patch_id', 'effect_tool_call_sha256', 'status', 'files',
    'source_receipt_confirmed', 'source_receipt_state', 'effect_receipt_state', 'outcome_unknown', 'pending_expired']); source(row);
  check(uuid(row.review_id) && uuid(row.patch_id) && hash(row.effect_tool_call_sha256) && integer(row.files, 32) && row.files >= 1
    && member(row.status, ['pending', 'applying', 'applied', 'failed', 'rejected', 'expired', 'indeterminate'])
    && member(row.effect_receipt_state, ['confirmed', 'not_confirmed', 'unavailable']) && row.outcome_unknown === ['applying', 'failed', 'indeterminate'].includes(row.status)
    && typeof row.pending_expired === 'boolean' && (row.status === 'pending' || row.pending_expired === false));
  return row;
}
function verification(value: unknown): Record<string, unknown> {
  const row = object(value, ['review_id', 'source_tool_call_sha256', 'source_patch_id', 'plan_id', 'status', 'source_receipt_confirmed', 'source_receipt_state',
    'planned_commands', 'sealed_steps', 'exited_commands', 'failed_attempts', 'remaining_commands', 'has_unknown_command', 'attempts_success',
    'all_commands_exited', 'steps', 'target', 'project_acceptance', 'pending_expired']); source(row);
  check(uuid(row.review_id) && hash(row.plan_id) && member(row.status, ['pending', 'running', 'completed', 'failed', 'cancelled', 'rejected', 'expired', 'indeterminate'])
    && integer(row.planned_commands, 16) && row.planned_commands >= 1 && Array.isArray(row.steps) && row.steps.length <= row.planned_commands
    && row.target === 'current_workspace_not_original_patch_snapshot' && row.project_acceptance === false
    && typeof row.pending_expired === 'boolean' && (row.status === 'pending' || row.pending_expired === false));
  let sealed = 0, exited = 0, failed = 0, unknown = false;
  for (const [index, value] of row.steps.entries()) {
    const step = object(value, ['index', 'status', 'exit_code', 'success', 'failure', 'duration_ms']);
    check(step.index === index && member(step.status, ['running', 'finished']));
    if (step.status === 'running') {
      check(index === row.steps.length - 1 && step.exit_code === null && step.success === null && step.failure === null && step.duration_ms === null); unknown = true;
    } else {
      sealed++; check(integer(step.duration_ms, 86400000));
      if (step.exit_code === null) check(step.success === false && member(step.failure, ['verification_timeout', 'verification_output_limit', 'verification_supervision_unavailable']));
      else { check(signed(step.exit_code) && step.success === (step.exit_code === 0) && step.failure === null); exited++; }
      if (step.success === false) failed++;
    }
  }
  check(row.sealed_steps === sealed && row.exited_commands === exited && row.failed_attempts === failed && row.remaining_commands === row.planned_commands - sealed
    && row.has_unknown_command === unknown && row.all_commands_exited === (exited === row.planned_commands));
  if (['pending', 'rejected', 'expired'].includes(row.status)) check(row.steps.length === 0);
  if (row.status === 'completed') {
    check(!unknown && sealed > 0 && (sealed === row.planned_commands || failed > 0 && row.steps[row.steps.length - 1].success === false)
      && row.attempts_success === (failed === 0));
  } else check(row.attempts_success === null);
  return row;
}
function workOrder(value: unknown): Record<string, unknown> {
  const row = object(value, ['attempt_id', 'work_order_id', 'task_id_sha256', 'execution_revision', 'active_plan_revision', 'attempt_number', 'attempt_status', 'order_status', 'dispatch_state']);
  check(uuid(row.attempt_id) && uuid(row.work_order_id) && hash(row.task_id_sha256) && integer(row.execution_revision) && row.execution_revision >= 1
    && integer(row.active_plan_revision) && row.active_plan_revision >= row.execution_revision && integer(row.attempt_number, 3) && row.attempt_number >= 1
    && member(row.attempt_status, ['reserved', 'accepted', 'running', 'awaiting_approval', 'succeeded', 'failed', 'cancelled', 'interrupted'])
    && member(row.order_status, ['draft', 'queued', 'running', 'paused', 'succeeded', 'failed', 'cancelled'])
    && (row.dispatch_state === null || member(row.dispatch_state, ['pending', 'admitted', 'abandoned'])));
  return row;
}
function section(value: unknown, validate: (value: unknown) => Record<string, unknown>, identity: string): void {
  const row = object(value, ['state', 'total', 'scanned', 'emitted', 'omitted', 'limit', 'truncated', 'items', 'reason']);
  check(row.limit === 16 && Array.isArray(row.items) && row.items.length <= 16);
  if (row.state === 'unknown') {
    check(row.total === null && row.scanned === 0 && row.emitted === 0 && row.omitted === 0 && row.truncated === null && row.items.length === 0
      && member(row.reason, ['unavailable', 'missing_tables'])); return;
  }
  check(integer(row.total) && integer(row.scanned, 16) && row.scanned === Math.min(row.total, 16)
    && integer(row.emitted, 16) && row.emitted === row.items.length && integer(row.omitted, 16) && row.omitted === row.scanned - row.items.length
    && typeof row.truncated === 'boolean' && row.truncated === (row.total > row.scanned));
  const seen = new Set<unknown>(); let dependentUnknown = false;
  for (const value of row.items) {
    const item = validate(value); check(!seen.has(item[identity])); seen.add(item[identity]);
    dependentUnknown ||= item.source_receipt_state === 'unavailable' || item.effect_receipt_state === 'unavailable' || 'dispatch_state' in item && item.dispatch_state === null;
  }
  const partial = row.omitted > 0 || row.truncated || dependentUnknown;
  check(row.state === (partial ? 'partial' : 'known') && row.reason === (partial ? 'incomplete_evidence' : null));
}
export function validateReportEvidence(value: unknown): void {
  const row = object(value, ['patches', 'inverses', 'verifications', 'work_orders', 'snapshot_consistency', 'tool_call_identity', 'reserved_body_bytes', 'decode_limit_bytes',
    'sql_read_only', 'filesystem_zero_write_guarantee', 'current_git_ownership_proven', 'project_acceptance_proven', 'physical_drain_verified', 'approval_granted', 'automatic_retry_allowed']);
  check(row.snapshot_consistency === 'independent_read_transactions_not_atomic_with_event_snapshot'
    && row.tool_call_identity === 'domain_separated_sha256_not_raw_id_or_approval_authority' && integer(row.reserved_body_bytes, 8388608) && row.decode_limit_bytes === 8388608
    && row.sql_read_only === true && row.filesystem_zero_write_guarantee === false && row.current_git_ownership_proven === false && row.project_acceptance_proven === false
    && row.physical_drain_verified === false && row.approval_granted === false && row.automatic_retry_allowed === false);
  section(row.patches, patch, 'tool_call_sha256'); section(row.inverses, inverse, 'review_id');
  section(row.verifications, verification, 'review_id'); section(row.work_orders, workOrder, 'attempt_id');
}
