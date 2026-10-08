export interface VerificationSource { run_id: string; tool_call_id: string; patch_id: string }
export type VerificationStatus = "pending" | "running" | "completed" | "failed" | "cancelled" | "rejected" | "expired" | "indeterminate";
export interface VerificationCommand { name: string; argv: string[]; timeout_seconds: number }
export interface VerificationPlan {
  schema: 1; plan_id: string; source: { path: string; config_hash: string; snapshot_hash: string; workspace_id: string };
  commands: VerificationCommand[]; max_output_bytes: number; stop_on_failure: boolean; operation_timeout_seconds: number;
}
export interface VerificationResult {
  name: string; argv: string[]; exit_code: number | null; success: boolean; stdout: string; stderr: string;
  duration_ms: number; supervision: string;
  error: "verification_timeout" | "verification_output_limit" | "verification_supervision_unavailable" | null;
}
export interface VerificationLifecycle {
  stored_status: VerificationStatus; effective_status: VerificationStatus; pending_expired: boolean; pending_unexpired: boolean;
  approval_available: false; retry_available: false; planned_commands: number; sealed_steps: number; remaining_commands: number;
  outcome_unknown: boolean; all_planned_attempts_sealed: boolean; commands_completed: boolean;
}
export interface VerificationProvenance {
  source_registration: "registered_run"; source_run: { status: string; lease_active: boolean };
  patch_success: "not_inferred_from_verification"; plan: "stored_exact_review_not_current_config_validation";
  output: "stored_sealed_attempts_not_whole_project_acceptance"; cross_database_snapshot: "not_atomic";
}
export interface VerificationSummary {
  review_id: string; operation_id: string; operation_kind: "manual_verification"; source: VerificationSource;
  target: "current_workspace_not_original_patch_snapshot"; created_at: string; expires_at: string; status: VerificationStatus;
  error_code: "verification_review_stale" | "verification_cancelled" | "verification_deadline_exceeded" | "verification_outcome_indeterminate" | "verification_evidence_budget" | null;
  success: boolean | null; has_unknown_command: boolean; evidence: "sealed_verification_steps" | "not_completion_proof";
  lifecycle?: VerificationLifecycle; provenance?: VerificationProvenance;
}
export interface VerificationReview extends VerificationSummary {
  plan: VerificationPlan; steps: Array<{ index: number; status: "running" | "finished"; result: VerificationResult | null }>;
  decision_replayed: boolean;
}
export interface VerificationPage {
  items: VerificationSummary[]; has_more: boolean; next_after_id: string | null; budget_limited: boolean; decode_bytes: number;
  sql_read_only: true; filesystem_zero_write_guarantee: false; order: "review_id_keyset_not_chronological";
}
export interface VerificationPrepareBody {
  confirmed: boolean; source_tool_call_id: string; source_patch_id: string; operation_id: string;
  names: string[] | null; command_execute: boolean; workspace_write: boolean;
}
export interface VerificationDecisionBody { confirmed: boolean; plan_id: string; action: "approve" | "reject"; command_execute: boolean; workspace_write: boolean }
export interface VerificationControlBody { confirmed: boolean; plan_id: string }
export interface VerificationApi {
  prepare: (run: string, body: VerificationPrepareBody) => Promise<VerificationReview>;
  list: (run: string, afterId: string, signal?: AbortSignal) => Promise<VerificationPage>;
  read: (run: string, reviewId: string, signal?: AbortSignal) => Promise<VerificationReview>;
  decide: (run: string, reviewId: string, body: VerificationDecisionBody) => Promise<VerificationReview>;
  cancel: (run: string, reviewId: string, body: VerificationControlBody) => Promise<VerificationReview>;
  reconcile: (run: string, reviewId: string, body: { confirmed: boolean }) => Promise<VerificationReview>;
}
export interface VerificationIntent {
  kind: "prepare" | "approve" | "reject"; source: VerificationSource; review_id: string; plan_id: string | null;
  body: VerificationPrepareBody | VerificationDecisionBody;
}
export interface VerificationControlIntent {
  kind: "cancel" | "reconcile"; source: VerificationSource; review_id: string; plan_id: string;
  body: VerificationControlBody | { confirmed: boolean };
}
