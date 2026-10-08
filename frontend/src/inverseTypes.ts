import type { PatchReceipt } from "./changesTypes";
export interface InverseSource { run_id: string; tool_call_id: string; patch_id: string }
export interface InverseProjection {
  unified_diff: string;
  files: Array<{ path: string; base_hash: string; base_mode: number | null; target_hash: string; target_mode: number | null; action: "restore" | "delete" }>;
}
export type InverseStatus = "pending" | "applying" | "applied" | "failed" | "rejected" | "expired" | "indeterminate";
export interface InverseReview {
  review_id: string; operation_kind: "manual_inverse"; source: InverseSource; effect_tool_call_id: string; patch_id: string;
  created_at: string; expires_at: string; status: InverseStatus; review: InverseProjection;
  result: { patch_id: string; changed_paths: string[]; unified_diff: string; patch_receipt: PatchReceipt;
    receipt_source: { run_id: string; tool_call_id: string; origin: "manual_inverse"; durability: "sealed_tool_ledger";
      source_tool_call_id: string; source_patch_id: string } } | null;
  error_code: "patch_operation_failed" | "patch_outcome_indeterminate" | null;
  effect_replayed: boolean; decision_replayed: boolean;
  effect_evidence: "sealed_patch_ledger" | "requires_patch_ledger_inspection" | "review_not_applied";
  lifecycle?: { stored_status: InverseStatus; effective_status: InverseStatus; pending_expired: boolean;
    approval_available: false; retry_available: false };
}
export interface InversePrepareBody { confirmed: boolean; source_tool_call_id: string; source_patch_id: string; operation_id: string; workspace_write: boolean }
export interface InverseDecisionBody { confirmed: boolean; patch_id: string; action: "approve" | "reject"; workspace_write: boolean }
export interface InverseApi {
  prepare: (run: string, body: InversePrepareBody) => Promise<InverseReview>;
  read: (run: string, reviewId: string, signal?: AbortSignal) => Promise<InverseReview>;
  decide: (run: string, reviewId: string, body: InverseDecisionBody) => Promise<InverseReview>;
  reconcile: (run: string, reviewId: string, body: { confirmed: boolean }) => Promise<InverseReview>;
}
export interface InverseIntent {
  kind: "prepare" | "approve" | "reject" | "reconcile"; source: InverseSource; review_id: string; patch_id: string | null;
  // Frozen original JSON projection, not copied from mutable grants or a later preview.
  body: InversePrepareBody | InverseDecisionBody | { confirmed: boolean };
}
