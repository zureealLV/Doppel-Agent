/** Actual S6 read projections; no source Git ownership or execution grants inferred. */
export type DiffPlane = "staged" | "worktree" | "combined";
export type ConflictStage = 1 | 2 | 3 | null;
export interface GitEntry { path: string; mode: string; object_id: string; kind: string; stage: number }
export interface GitRow {
  path: string; head: GitEntry | null; index: GitEntry | null; stages: GitEntry[]; staged: string; worktree: string;
  worktree_sha256: string | null; worktree_blob_oid: string | null; worktree_mode: string | null;
  mode_comparison: string; reason: string | null; agent_generated: null;
}
export interface GitUnavailable {
  available: false; reason: string; repository_clean: null; agent_attribution: "not_inferred_from_repository_changes";
}
export interface GitAvailableStatus {
  available: true; reason: null; head_id: string | null; head_ref: string | null; object_format: string | null; ref_storage: string;
  index_sha256: string | null; refs_sha256: string | null; linked_worktree: boolean; rows: GitRow[]; excluded_count: number;
  unknown_count: number; walked_entries: number; coverage_reasons: string[]; policy_sha256: string;
  comparison: "raw_workspace_no_filters_or_eol_conversion"; repository_clean: null;
  agent_attribution: "not_inferred_from_repository_changes"; fingerprint: string; captured_at: string;
}
export type GitStatus = GitAvailableStatus | GitUnavailable;
export interface DiffSelection { path: string; plane: DiffPlane; conflict_stage: ConflictStage; expected_fingerprint: string }
export interface GitDiffAvailable {
  available: true; reason: null; path: string; plane: DiffPlane; conflict_stage: ConflictStage; fingerprint: string;
  before_sha256: string | null; after_sha256: string | null; before_exists: boolean; after_exists: boolean;
  before_mode: string | null; after_mode: string | null; text: string;
  comparison: "raw_workspace_no_filters_or_eol_conversion"; agent_generated: null;
}
export type GitDiff = GitDiffAvailable | GitUnavailable;
export interface PatchReceipt {
  schema: 1; patch_id: string; status: "prepared" | "applied" | "failed";
  files: Array<{ path: string; base_hash: string; base_mode: number | null; after_hash: string; after_mode: number | null;
    outcome: "pending" | "applied" | "unchanged" | "rolled_back" | "preserved" | "unknown" }>;
}
export interface PatchRow {
  run_id: string; tool_call_id: string; tool_name: "propose_patch" | "inverse_patch"; operation_status: "running" | "completed" | "failed";
  created_at: string; receipt: PatchReceipt; preimage_state: "retained" | "expired"; preimage_expired_at: string | null;
  confirmed_applied: boolean; evidence: "durable_patch_effect_not_current_git_ownership"; source_run_quiescent: boolean;
  inverse_preparation_requires_fresh_file_check: true;
}
export interface PatchEvidence {
  tool_name: PatchRow["tool_name"]; operation_status: PatchRow["operation_status"]; confirmed_applied: boolean;
  receipt: PatchReceipt | null; result: { patch_id: string; changed_paths: string[]; unified_diff: string; patch_receipt: PatchReceipt } | null;
  preimage_state: "retained" | "expired"; source: { run_id: string; tool_call_id: string };
  source_run: { status: string; lease_active: boolean }; evidence: "durable_patch_effect_not_current_git_ownership";
  output: { sensitive: true; globally_redacted: false; accepted_preimage_blobs_exported: false };
}
export interface MetadataAuthorization {
  active: boolean; grant_id: string | null; workspace: string; metadata_root: string | null;
  scope: "exact_linked_worktree_read_inspection_only"; persistent: false;
  lifetime: "current_owner_until_revoke_binding_change_switch_or_close"; command_or_workspace_write_grant: false;
}
export interface MetadataResult { ok: boolean; cancelled?: boolean; authorization?: MetadataAuthorization; error?: string }
export interface ChangesDesktopApi {
  git_metadata_authorize: () => Promise<MetadataResult>;
  git_metadata_authorization: () => Promise<MetadataResult>;
  git_metadata_revoke: () => Promise<MetadataResult>;
}
export interface ChangesApi {
  status: (signal?: AbortSignal) => Promise<GitStatus>;
  diff: (selection: DiffSelection, signal?: AbortSignal) => Promise<GitDiff>;
  patches: (run: string, offset?: number, signal?: AbortSignal) => Promise<PatchRow[]>;
  evidence: (run: string, call: string, signal?: AbortSignal) => Promise<PatchEvidence>;
}
