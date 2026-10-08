export type RunMode = "legacy" | "graph" | "deep";
export type Effort = "quick" | "balanced" | "deep";

export interface Permissions {
  workspace_write: boolean;
  command_execute: boolean;
  mcp_execute: boolean;
  delegate: boolean;
}

export interface RunRequest {
  context?: ContextDescriptor;
  conversation_id?: string;
  idempotency_key?: string;
  prompt: string;
  mode: RunMode;
  effort: Effort;
  deadline_seconds: number;
  profile_id?: string;
  permissions: Permissions;
}

export interface ContextDescriptor { manifest_id: string | null; notes: Array<{ note_id: string; revision: number }> }
export type PrepareContext = (scope: string | null) => Promise<ContextDescriptor>;
export interface InputFileEntry {
  index: number; path: string; start_line: number; requested_end_line: number | null; end_line: number; total_lines: number;
  file_sha256: string; content_sha256: string; captured_at: string; text: string; bytes: number; truncation_reasons: string[];
}
export interface InputSnapshot {
  version: 1; scope_work_order_id: string | null; captured_at: string; rendered_context: string;
  context_sha256: string; context_bytes: number; estimated_tokens: number; estimate_method: string; actual_tokens: null;
  manifest: { manifest_id: string; created_at: string; entries: InputFileEntry[]; budget_bytes: number; total_bytes: number } | null;
  notes: Array<{ note_id: string; revision: number; scope_work_order_id: string | null; kind: string; title: string;
    body: string; body_sha256: string; confirmed_at: string; sources: Array<Record<string, unknown>> }>;
  predecessors: Array<{ work_order_id: string; task_id: string; execution_revision: number; attempt_id: string; run_id: string;
    status: string; lease_drained: boolean; result_finished_at: string; result_bytes: number; result_sha256: string;
    text: string; included_bytes: number; included_sha256: string; truncation_reasons: string[] }>;
}

export interface InterruptValue {
  tool_calls?: Array<Record<string, unknown>>;
  action_requests?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface InterruptRecord {
  id: string;
  value: InterruptValue;
}

export interface RunRecord {
  input_snapshot?: InputSnapshot | null;
  conversation_id?: string | null;
  lease_active?: boolean;
  run_id: string;
  thread_id: string;
  status: string;
  mode: RunMode;
  request: RunRequest;
  answer: string;
  error: string;
  metadata: {
    interrupts?: InterruptRecord[];
    [key: string]: unknown;
  };
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested: boolean;
}

export interface RuntimeEvent {
  seq: number;
  run_id: string;
  thread_id: string;
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface SubagentRecord {
  subagent_id: string;
  parent_run_id: string;
  status: string;
  prompt: string;
  history: Array<{ prompt: string; answer: string }>;
  answer: string;
  error: string;
  generation: number;
  created_at: string;
  updated_at: string;
}
