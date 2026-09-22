export type RunMode = "legacy" | "graph" | "deep";
export type Effort = "quick" | "balanced" | "deep";

export interface Permissions {
  workspace_write: boolean;
  command_execute: boolean;
  mcp_execute: boolean;
  delegate: boolean;
}

export interface RunRequest {
  prompt: string;
  mode: RunMode;
  effort: Effort;
  deadline_seconds: number;
  profile_id?: string;
  permissions: Permissions;
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
