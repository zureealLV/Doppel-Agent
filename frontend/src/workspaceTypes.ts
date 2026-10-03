import type { Effort, Permissions } from "./types";

export interface ConversationSummary {
  id: string;
  title: string;
  archived: number;
  group_id: string | null;
  group_name: string | null;
  profile_id: string | null;
  created_at: string;
  updated_at: string;
  preview?: string;
  message_count?: number;
}

export interface Conversation extends ConversationSummary {
  messages: Array<{ role: "user" | "assistant"; content: string; model?: string | null; run_id?: string | null }>;
}

export interface ConversationGroup { id: string; name: string; conversation_count: number }

export interface ModelProfile {
  id: string; name: string; provider: "mock" | "openai"; preset: string; model: string;
  base_url: string; input_price: number; output_price: number; api_key_saved: boolean;
}

export interface PublicSettings { active_profile_id: string; profiles: ModelProfile[]; key_protection: string }

export interface ProfileForm {
  name: string; preset: string; model: string; base_url: string;
  input_price: number; output_price: number; api_key: string;
}

export interface ChatSubmission { prompt: string; effort: Effort; permissions: Permissions }
export interface PersistentRunRequest {
  prompt: string; mode: "agent" | "review"; effort: Effort; conversation_id: string;
  config: { profile_id: string }; allow_write: boolean; allow_command: boolean; allow_mcp: boolean; allow_delegate: boolean;
}
export interface PersistentRun { run_id: string; conversation_id: string | null; status: string; answer?: string; prompt?: string }
export interface LegacyEvent { kind: string; payload: Record<string, unknown> }
export interface LegacyApproval { id: string; tool: string }
