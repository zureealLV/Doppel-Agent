// Numeric wire oracle only, not actual ledger or native/command proof.
import { reportFixture, reportRun } from './runReportFixture';
export function evidenceSection<T>(items: T[]) {
  return { state: 'known', total: items.length, scanned: items.length, emitted: items.length, omitted: 0,
    limit: 16, truncated: false, items, reason: null as string | null };
}
export function reportEvidenceFixture() {
  return {
    patches: evidenceSection([{ tool_call_sha256: '1'.repeat(64), operation: 'propose_patch', status: 'completed', patch_id: 'b'.repeat(32),
      receipt_status: 'applied', confirmed_applied: true, files: 1, preimage_state: 'retained' }]),
    inverses: evidenceSection([{ review_id: 'd'.repeat(32), source_tool_call_sha256: '1'.repeat(64), source_patch_id: 'b'.repeat(32), patch_id: 'e'.repeat(32),
      effect_tool_call_sha256: '2'.repeat(64), status: 'pending', files: 1, source_receipt_confirmed: true,
      source_receipt_state: 'confirmed', effect_receipt_state: 'not_confirmed', outcome_unknown: false, pending_expired: false }]),
    verifications: evidenceSection([{ review_id: 'f'.repeat(32), source_tool_call_sha256: '1'.repeat(64), source_patch_id: 'b'.repeat(32), plan_id: '3'.repeat(64),
      status: 'completed', source_receipt_confirmed: true, source_receipt_state: 'confirmed', planned_commands: 2, sealed_steps: 1, exited_commands: 1,
      failed_attempts: 1, remaining_commands: 1, has_unknown_command: false, attempts_success: false, all_commands_exited: false,
      steps: [{ index: 0, status: 'finished', exit_code: 1, success: false, failure: null, duration_ms: 1 }],
      target: 'current_workspace_not_original_patch_snapshot', project_acceptance: false, pending_expired: false }]),
    work_orders: evidenceSection([{ attempt_id: '4'.repeat(32), work_order_id: '5'.repeat(32), task_id_sha256: '6'.repeat(64),
      execution_revision: 1, active_plan_revision: 2, attempt_number: 1, attempt_status: 'succeeded', order_status: 'paused', dispatch_state: 'admitted' }]),
    snapshot_consistency: 'independent_read_transactions_not_atomic_with_event_snapshot',
    tool_call_identity: 'domain_separated_sha256_not_raw_id_or_approval_authority', reserved_body_bytes: 1024, decode_limit_bytes: 8 * 1024 * 1024,
    sql_read_only: true, filesystem_zero_write_guarantee: false, current_git_ownership_proven: false, project_acceptance_proven: false,
    physical_drain_verified: false, approval_granted: false, automatic_retry_allowed: false,
  };
}
export function reportV2Fixture(run = reportRun) { return { ...reportFixture(run), version: 2, evidence: reportEvidenceFixture() }; }
