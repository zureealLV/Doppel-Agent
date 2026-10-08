import { request } from "./api";
import type { RunMode, RunRequest } from "./types";
import type { NativeConversation, NativeConversationSummary, NativeWorkspaceSelection } from "./workspaceTypes";
import type { Conversation, ConversationGroup, ConversationSummary, LegacyApproval, LegacyEvent,
  PersistentRun, PersistentRunRequest, ProfileConfiguration, PublicSettings } from "./workspaceTypes";

const id = encodeURIComponent;
const post = <T>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

const patch = <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const conversationPath = (cid: string) => `/api/v1/conversations/${id(cid)}`;
export const nativeWorkspaceApi = {
  selection: () => request<NativeWorkspaceSelection>("/api/v1/workspace-selection"),
  saveSelection: (conversationId: string | null, runId: string | null) => request<NativeWorkspaceSelection>(
    "/api/v1/workspace-selection", { method: "PUT", body: JSON.stringify({ conversation_id: conversationId, run_id: runId }) }),
  conversations: (archived = false) => request<NativeConversationSummary[]>(`/api/v1/conversations?archived=${archived}`),
  conversation: (cid: string) => request<NativeConversation>(conversationPath(cid)),
  draft: (mode: RunMode, profileId: string, title = "新对话") => post<NativeConversation>("/api/v1/conversations", { mode, profile_id: profileId, title, draft: true }),
  search: (query: string) => request<NativeConversationSummary[]>(`/api/v1/conversations?q=${id(query)}`),
  update: (cid: string, body: { title?: string; archived?: boolean; group_id?: string | null; profile_id?: string }) => patch<NativeConversation>(conversationPath(cid), body),
  delete: (cid: string) => request<{ ok: boolean; audit_retained: boolean }>(conversationPath(cid), { method: "DELETE" }),
  groups: () => request<ConversationGroup[]>("/api/v1/conversation-groups"),
  createGroup: (name: string) => post<ConversationGroup>("/api/v1/conversation-groups", { name }),
  renameGroup: (gid: string, name: string) => patch<ConversationGroup>(`/api/v1/conversation-groups/${id(gid)}`, { name }),
  deleteGroup: (gid: string) => request<{ ok: boolean }>(`/api/v1/conversation-groups/${id(gid)}`, { method: "DELETE" }),
  start: (cid: string, body: RunRequest) => post<{ run_id: string; conversation_id: string; status: string }>(`${conversationPath(cid)}/runs`, body),
};

// Intentionally retains the production persistent-chat API. These are not v1
// Graph/Deep conversations and must not be presented as native runtime parity.
export const workspaceApi = {
  selection: () => request<{ saved: boolean; conversation_id: string | null }>("/api/workspace-selection"),
  saveSelection: (conversationId: string | null) => post<{ saved: boolean; conversation_id: string | null }>(
    "/api/workspace-selection", { conversation_id: conversationId }),
  health: () => request<{ status: string; workspace: string }>("/api/health"),
  settings: () => request<PublicSettings>("/api/settings"),
  saveProfile: (profileId: string, config: ProfileConfiguration, forgetKey = false) =>
    post<PublicSettings>("/api/settings", { profile_id: profileId, config, forget_key: forgetKey }),
  deleteProfile: (profileId: string) => post<PublicSettings>(`/api/settings/profiles/${id(profileId)}/delete`),
  probe: (config: ProfileConfiguration & { profile_id?: string }) => post<{ ok: boolean; reply: string }>("/api/probe", { config }),
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
