import { request } from "./api";
import type { Conversation, ConversationGroup, ConversationSummary, LegacyApproval, LegacyEvent,
  PersistentRun, PersistentRunRequest, ProfileForm, PublicSettings } from "./workspaceTypes";

const id = encodeURIComponent;
const post = <T>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

// Intentionally retains the production persistent-chat API. These are not v1
// Graph/Deep conversations and must not be presented as native runtime parity.
export const workspaceApi = {
  health: () => request<{ status: string; workspace: string }>("/api/health"),
  settings: () => request<PublicSettings>("/api/settings"),
  saveProfile: (profileId: string, config: ProfileForm & { provider: string }, forgetKey = false) =>
    post<PublicSettings>("/api/settings", { profile_id: profileId, config, forget_key: forgetKey }),
  deleteProfile: (profileId: string) => post<PublicSettings>(`/api/settings/profiles/${id(profileId)}/delete`),
  probe: (config: ProfileForm & { provider: string; profile_id?: string }) => post<{ ok: boolean; reply: string }>("/api/probe", { config }),
  conversations: (archived = false) => request<ConversationSummary[]>(`/api/conversations?archived=${archived ? 1 : 0}`),
  conversation: (conversationId: string) => request<Conversation>(`/api/conversations/${id(conversationId)}`),
  draft: (mode: "agent" | "review") => post<Conversation>(`/api/conversations/${mode === "review" ? "review" : "new"}-draft`),
  search: (query: string) => request<ConversationSummary[]>(`/api/conversations/search?q=${encodeURIComponent(query)}`),
  rename: (conversationId: string, title: string) => post<Conversation>(`/api/conversations/${id(conversationId)}/rename`, { title }),
  archive: (conversationId: string, archived: boolean) => post<Conversation>(`/api/conversations/${id(conversationId)}/archive`, { archived }),
  delete: (conversationId: string) => post<{ ok: boolean }>(`/api/conversations/${id(conversationId)}/delete`),
  setGroup: (conversationId: string, groupId: string | null) => post<Conversation>(`/api/conversations/${id(conversationId)}/group`, { group_id: groupId }),
  setProfile: (conversationId: string, profileId: string) => post<Conversation>(`/api/conversations/${id(conversationId)}/profile`, { profile_id: profileId }),
  groups: () => request<ConversationGroup[]>("/api/groups"),
  createGroup: (name: string) => post<ConversationGroup>("/api/groups", { name }),
  renameGroup: (groupId: string, name: string) => post<ConversationGroup>(`/api/groups/${id(groupId)}/rename`, { name }),
  deleteGroup: (groupId: string) => post<{ ok: boolean }>(`/api/groups/${id(groupId)}/delete`),
  start: (body: PersistentRunRequest) => post<{ run_id: string; conversation_id: string }>("/api/runs", body),
  run: (runId: string) => request<PersistentRun>(`/api/runs/${id(runId)}`),
  events: (runId: string) => request<LegacyEvent[]>(`/api/runs/${id(runId)}/events`),
  tasks: (runId: string) => request<Array<Record<string, unknown>>>(`/api/runs/${id(runId)}/tasks`),
  approvals: (runId: string) => request<LegacyApproval[]>(`/api/runs/${id(runId)}/approvals`),
  decide: (runId: string, approvalId: string, allow: boolean) => post<{ ok: boolean }>(`/api/runs/${id(runId)}/approvals/${id(approvalId)}/decision`, { allow }),
};
