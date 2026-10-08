import { reactive } from "vue";

import { nativeWorkspaceApi, workspaceApi } from "./workspaceApi";
import type { RunMode, RunRequest, PrepareContext } from "./types";
import { RunSubmissionController } from "./runSubmission";
import type { NativeConversation, NativeConversationSummary } from "./workspaceTypes";
import type { ChatSubmission, Conversation, ConversationGroup, ConversationSummary, LegacyApproval, LegacyEvent,
  PersistentRun, ProfileForm, ProfileConfiguration, PublicSettings } from "./workspaceTypes";
import { tariffPatch } from './billingTariff';

export function safeMarkdown(value: string): string {
  const entities: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const escaped = value.replace(/[&<>"']/g, (char) => entities[char]!);
  // Process fenced segments independently; user text cannot collide with a
  // placeholder token and raw HTML/URLs never become active markup.
  return escaped.split(/(```[\s\S]*?```)/g).map((part) => {
    if (part.startsWith("```") && part.endsWith("```")) {
      return `<pre><code>${part.slice(3, -3).replace(/^\w+\n/, "")}</code></pre>`;
    }
    return part.replace(/^### (.+)$/gm, "<h3>$1</h3>").replace(/^## (.+)$/gm, "<h2>$1</h2>")
      .replace(/^# (.+)$/gm, "<h1>$1</h1>").replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
  }).join("");
}

export function searchIndex(index: number, delta: number, count: number): number {
  return count > 0 ? ((index + delta) % count + count) % count : 0;
}

export function browserStorage(): Storage | undefined {
  try { return typeof window === "undefined" ? undefined : window.localStorage; } catch { return undefined; }
}

export function profileConfiguration(form: ProfileForm, purpose: 'save' | 'probe' | 'forget_key' = 'save'): ProfileConfiguration {
  if (![form.input_price, form.output_price].every((price) => Number.isFinite(price) && price >= 0)) {
    throw new Error("模型单价必须是有限的非负数。");
  }
  if (form.preset !== "mock") {
    const url = new URL(form.base_url);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error("API 地址必须是无凭据、查询和片段的 HTTP(S) URL。");
    }
    if (!form.model.trim()) throw new Error("模型名称不能为空。");
  }
  return { name: purpose === 'forget_key' ? form.name : form.name.trim(), preset: form.preset,
    model: purpose === 'forget_key' ? form.model : form.model.trim(),
    base_url: purpose === 'forget_key' ? form.base_url : form.base_url.trim(),
    input_price: form.input_price, output_price: form.output_price, api_key: purpose === 'forget_key' ? '' : form.api_key,
    provider: form.preset === "mock" ? "mock" : "openai", ...(purpose === 'save' ? tariffPatch(form.billing_tariff_edit) : {}) };
}

export class WorkspaceController {
  readonly state = reactive({
    workspace: "", settings: null as PublicSettings | null, groups: [] as ConversationGroup[],
    conversations: [] as ConversationSummary[], current: null as Conversation | null,
    archived: false, groupId: null as string | null, profileId: "", nextMode: "agent" as "agent" | "review",
    loading: false, opening: false, transition: false, submitting: false, error: "",
    activeRun: null as PersistentRun | null, events: [] as LegacyEvent[], tasks: [] as Array<Record<string, unknown>>,
    approvals: [] as LegacyApproval[], searchResults: [] as ConversationSummary[], searching: false,
  });
  private selectionEpoch = 0;
  private searchEpoch = 0;
  private listEpoch = 0;
  private disposed = false;
  private polling = false;
  private pendingOpens = 0;
  private selectionWrites: Promise<void> = Promise.resolve();
  private selectionRevision = 0;
  private unsavedSelection: { revision: number; id: string | null } | undefined;

  constructor(private readonly storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">) {}

  private remember(key: string, value: string): void { try { this.storage?.setItem(key, value); } catch { /* UI state still owns accepted work. */ } }
  private recalled(key: string): string | null { try { return this.storage?.getItem(key) ?? null; } catch { return null; } }
  private forget(key: string): void { try { this.storage?.removeItem(key); } catch { /* Storage is optional, not a run-acceptance gate. */ } }

  clearSelection(): void {
    this.selectionEpoch++; this.state.current = null; this.forget("doppel-conversation");
    this.persistSelection(null);
  }

  private persistSelection(id: string | null): void {
    const pending = { revision: ++this.selectionRevision, id };
    this.unsavedSelection = pending;
    this.selectionWrites = this.selectionWrites.then(async () => {
      try {
        await workspaceApi.saveSelection(pending.id);
        if (this.unsavedSelection?.revision === pending.revision) this.unsavedSelection = undefined;
      } catch {
        if (!this.disposed && this.unsavedSelection?.revision === pending.revision) {
          this.state.error = "Legacy 历史选择保存失败；切换项目之前请重试。";
        }
      }
    });
  }

  async flushSelection(): Promise<void> {
    let pending: Promise<void>;
    do { pending = this.selectionWrites; await pending; } while (pending !== this.selectionWrites);
    if (this.unsavedSelection) {
      this.persistSelection(this.unsavedSelection.id);
      do { pending = this.selectionWrites; await pending; } while (pending !== this.selectionWrites);
    }
    if (this.unsavedSelection) throw new Error("Legacy 历史选择尚未保存，未切换项目。");
  }

  async prepareClose(): Promise<void> {
    if (this.state.loading || this.state.opening || this.state.transition || this.state.submitting) {
      throw new Error("Legacy 导航或任务接收仍在进行，请稍后重试。");
    }
    // An accepted Legacy run is drained by the owning kernel, not replayed here.
    await this.flushSelection();
  }

  get busy(): boolean {
    return this.state.loading || this.state.opening || this.state.submitting || this.state.transition || !!this.state.activeRun;
  }

  report(error: unknown): void {
    if (!this.disposed) this.state.error = error instanceof Error ? error.message : String(error);
  }

  async initialize(): Promise<void> {
    this.state.loading = true;
    const epoch = this.selectionEpoch;
    try {
      const [health, settings, selection] = await Promise.all([
        workspaceApi.health(), workspaceApi.settings(), workspaceApi.selection(),
      ]);
      if (this.disposed || epoch !== this.selectionEpoch) return;
      this.state.workspace = health.workspace;
      this.updateSettings(settings);
      await this.refreshList();
      if (this.disposed || epoch !== this.selectionEpoch) return;
      // Browser storage is origin-scoped and ports change when kernels switch.
      // Do not import an unscoped browser ID into another project's database.
      const saved = selection.conversation_id;
      if (saved) {
        await this.open(saved);
      } else if (!selection.saved && this.state.conversations[0]) await this.open(this.state.conversations[0].id);
      const runId = this.recalled("doppel.vue.chat.run");
      if (runId) {
        try {
          const run = await workspaceApi.run(runId);
          if (!this.disposed && !["completed", "failed"].includes(run.status)) this.state.activeRun = run;
          else this.forget("doppel.vue.chat.run");
        } catch { this.forget("doppel.vue.chat.run"); }
      }
    } catch (error) { this.report(error); }
    finally { if (!this.disposed) this.state.loading = false; }
  }

  updateSettings(settings: PublicSettings): void {
    this.state.settings = settings; // public API deliberately omits all key bytes
    if (!settings.profiles.some((profile) => profile.id === this.state.profileId)) this.state.profileId = settings.active_profile_id;
  }

  async refreshList(): Promise<void> {
    const epoch = ++this.listEpoch;
    const [conversations, groups] = await Promise.all([workspaceApi.conversations(this.state.archived), workspaceApi.groups()]);
    if (this.disposed || epoch !== this.listEpoch) return;
    this.state.conversations = conversations;
    this.state.groups = groups;
    if (this.state.groupId && !groups.some((group) => group.id === this.state.groupId)) this.state.groupId = null;
  }

  async open(id: string): Promise<void> {
    const epoch = ++this.selectionEpoch;
    this.pendingOpens++;
    this.state.opening = true;
    try {
      const conversation = await workspaceApi.conversation(id);
      if (this.disposed || epoch !== this.selectionEpoch) return;
      this.select(conversation);
      this.state.nextMode = "agent";
    } finally {
      this.pendingOpens--;
      if (!this.disposed) this.state.opening = this.pendingOpens > 0;
    }
  }

  private select(conversation: Conversation): void {
    this.state.current = conversation;
    this.remember("doppel-conversation", conversation.id);
    this.persistSelection(conversation.id);
    const wanted = conversation.profile_id || this.state.settings?.active_profile_id;
    if (wanted && this.state.settings?.profiles.some((profile) => profile.id === wanted)) this.state.profileId = wanted;
  }

  async prepareDraft(mode: "agent" | "review"): Promise<void> {
    if (this.busy) return;
    this.state.transition = true;
    const epoch = ++this.selectionEpoch;
    const profileId = this.state.profileId;
    try {
      let conversation = await workspaceApi.draft(mode);
      if (this.disposed || epoch !== this.selectionEpoch) return;
      if (profileId) conversation = await workspaceApi.setProfile(conversation.id, profileId);
      if (this.disposed || epoch !== this.selectionEpoch) return;
      this.state.archived = false;
      this.state.groupId = null;
      this.select(conversation);
      this.state.nextMode = mode;
      await this.refreshList();
    } finally { if (!this.disposed) this.state.transition = false; }
  }

  async search(query: string): Promise<void> {
    const epoch = ++this.searchEpoch;
    this.state.searching = !!query.trim();
    this.state.searchResults = [];
    if (!query.trim()) return;
    try {
      const results = await workspaceApi.search(query.trim());
      if (!this.disposed && epoch === this.searchEpoch) this.state.searchResults = results;
    } finally { if (!this.disposed && epoch === this.searchEpoch) this.state.searching = false; }
  }

  async chooseProfile(profileId: string): Promise<void> {
    const current = this.state.current;
    this.state.profileId = profileId;
    if (current) {
      await workspaceApi.setProfile(current.id, profileId);
      if (this.state.current?.id === current.id) this.state.current.profile_id = profileId;
    }
  }

  async submit(request: ChatSubmission): Promise<void> {
    if (this.busy || !request.prompt.trim() || !this.state.profileId || this.state.current?.archived) return;
    this.state.submitting = true;
    const profileId = this.state.profileId;
    try {
      if (!this.state.current) {
        let draft = await workspaceApi.draft("agent");
        if (this.disposed) return;
        // A new Legacy draft has no profile. Bind the user's choice before
        // select() can replace it with the global default or any run starts.
        draft = await workspaceApi.setProfile(draft.id, profileId);
        if (this.disposed) return;
        this.select(draft);
      }
      const conversation = this.state.current!;
      const mode = this.state.nextMode;
      const accepted = await workspaceApi.start({ prompt: request.prompt.trim(), mode, conversation_id: conversation.id,
        config: { profile_id: profileId }, effort: request.effort, allow_write: request.permissions.workspace_write,
        allow_command: request.permissions.command_execute, allow_mcp: request.permissions.mcp_execute, allow_delegate: request.permissions.delegate });
      // Persist the accepted run ID even if this view was closed while posting.
      // Reopening the workspace can observe it instead of resubmitting a task.
      this.remember("doppel.vue.chat.run", accepted.run_id);
      if (this.disposed) return;
      this.state.nextMode = "agent";
      this.state.activeRun = { ...accepted, status: "queued", prompt: request.prompt.trim() };
      this.state.events = [];
      this.state.tasks = [];
      this.state.approvals = [];
    } finally { if (!this.disposed) this.state.submitting = false; }
  }

  async refreshRun(): Promise<void> {
    const active = this.state.activeRun;
    if (!active || this.polling || this.disposed) return;
    this.polling = true;
    try {
      const [run, events, tasks, approvals] = await Promise.all([workspaceApi.run(active.run_id), workspaceApi.events(active.run_id),
        workspaceApi.tasks(active.run_id), workspaceApi.approvals(active.run_id)]);
      if (this.disposed || this.state.activeRun?.run_id !== active.run_id) return;
      this.state.events = events;
      this.state.tasks = tasks;
      this.state.approvals = approvals;
      this.state.activeRun = run;
      if (["completed", "failed"].includes(run.status)) {
        this.state.activeRun = null;
        this.forget("doppel.vue.chat.run");
        if (run.conversation_id && this.state.current?.id === run.conversation_id) await this.open(run.conversation_id);
        await this.refreshList();
      }
    } finally { this.polling = false; }
  }

  async decide(approvalId: string, allow: boolean): Promise<void> {
    const runId = this.state.activeRun?.run_id;
    if (!runId) return;
    await workspaceApi.decide(runId, approvalId, allow);
    await this.refreshRun();
  }

  dispose(): void {
    this.disposed = true;
    this.selectionEpoch++;
    this.searchEpoch++;
    this.listEpoch++;
  }
}

// Native history is never inferred from or migrated out of the Legacy store.
export class NativeWorkspaceController {
  readonly state = reactive({ workspace: "", settings: null as PublicSettings | null,
    groups: [] as ConversationGroup[], conversations: [] as NativeConversationSummary[],
    current: null as NativeConversation | null, archived: false, groupId: null as string | null,
    profileId: "", mode: "graph" as RunMode, loading: false, opening: false, transition: false, submitting: false,
    error: "", selectionError: "", selectionSaving: false, closing: false,
    selectedRunId: "", searchResults: [] as NativeConversationSummary[], searching: false });
  private selectionEpoch = 0;
  private listEpoch = 0;
  private searchEpoch = 0;
  private disposed = false;
  private polling = false;
  private readonly admission = new RunSubmissionController(
    (request: RunRequest) => nativeWorkspaceApi.start(request.conversation_id!, request),
    (reply, request) => { if (reply.conversation_id !== request.conversation_id) throw new Error("运行接受回复会话身份不匹配；保留原请求。"); });
  private selectionWrites: Promise<void> = Promise.resolve();
  private selectionWriteEpoch = 0;
  constructor(private readonly storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">,
    private readonly prepareContext?: PrepareContext) {}
  private remember(key: string, value: string): void { try { this.storage?.setItem(key, value); } catch { /* Optional. */ } }
  private recalled(key: string): string | null { try { return this.storage?.getItem(key) ?? null; } catch { return null; } }
  get submissionUncertain(): boolean { return this.admission.state.uncertain; }
  get busy(): boolean { return this.state.closing || this.state.loading || this.state.opening || this.state.transition || this.state.submitting || this.submissionUncertain || !!this.state.current?.active_run_id; }
  report(error: unknown): void { if (!this.disposed) this.state.error = error instanceof Error ? error.message : String(error); }
  clearSelection(): void {
    if (this.state.closing || this.state.submitting || this.submissionUncertain) return;
    this.selectionEpoch++; this.state.current = null; this.state.selectedRunId = "";
    this.remember("doppel.native.conversation", "");
    this.persistSelection(null, null);
  }
  private persistSelection(conversationId: string | null, runId: string | null): void {
    const epoch = ++this.selectionWriteEpoch;
    this.state.selectionSaving = true;
    this.state.selectionError = "";
    // Ordered writes ensure an older selection cannot arrive after a newer click.
    // IDs are the only payload; this does not accept/replay a runtime turn.
    this.selectionWrites = this.selectionWrites.then(async () => {
      try { await nativeWorkspaceApi.saveSelection(conversationId, runId); }
      catch { if (!this.disposed && epoch === this.selectionWriteEpoch) this.state.selectionError = "历史选择保存失败；下次打开可能无法恢复，请重新选择。"; }
      finally { if (!this.disposed && epoch === this.selectionWriteEpoch) this.state.selectionSaving = false; }
    });
  }
  async flushSelection(): Promise<void> {
    // A write may be added while the previous snapshot is draining.
    let pending: Promise<void>;
    do { pending = this.selectionWrites; await pending; } while (pending !== this.selectionWrites);
  }
  async prepareClose(): Promise<void> {
    // Awaiting a run's actual execution is deliberately NOT a close prerequisite:
    // service shutdown retains its existing owned cancellation/drain behavior.
    this.admission.prepareClose();
    if (this.state.loading || this.state.opening || this.state.transition || this.state.submitting) {
      throw new Error("navigation or acceptance is still pending");
    }
    this.state.closing = true;
    await this.flushSelection();
    if (this.state.selectionError) throw new Error("navigation preference was not saved");
  }
  finishClose(): void { this.state.closing = false; }
  updateSettings(settings: PublicSettings): void {
    this.state.settings = settings;
    const wanted = this.state.current?.profile_id || this.state.profileId || settings.active_profile_id;
    // A deleted named profile is not silently replaced on an existing conversation.
    this.state.profileId = settings.profiles.some(p => p.id === wanted) ? wanted : "";
  }
  async initialize(): Promise<void> {
    this.state.loading = true;
    const epoch = this.selectionEpoch;
    try {
      const [health, settings, selection] = await Promise.all([
        workspaceApi.health(), workspaceApi.settings(), nativeWorkspaceApi.selection(),
      ]);
      if (this.disposed || epoch !== this.selectionEpoch) return;
      this.state.workspace = health.workspace;
      this.updateSettings(settings);
      await this.refreshList();
      if (this.disposed || epoch !== this.selectionEpoch) return;
      // Server preferences survive the desktop's changing origin/port. Browser
      // IDs are only a one-time fallback for workspaces without a saved choice.
      const saved = selection.saved ? selection.conversation_id : this.recalled("doppel.native.conversation");
      if (saved) {
        try { await this.open(saved); }
        catch (error) { this.remember("doppel.native.conversation", ""); if (selection.saved) this.report(error); }
      }
      if (!selection.saved && !this.state.current && this.state.conversations[0]) await this.open(this.state.conversations[0].id);
    } catch (error) { this.report(error); }
    finally { if (!this.disposed) this.state.loading = false; }
  }
  async refreshList(): Promise<void> {
    const epoch = ++this.listEpoch;
    const [rows, groups] = await Promise.all([nativeWorkspaceApi.conversations(this.state.archived), nativeWorkspaceApi.groups()]);
    if (this.disposed || epoch !== this.listEpoch) return;
    this.state.conversations = rows; this.state.groups = groups;
    if (!groups.some(g => g.id === this.state.groupId)) this.state.groupId = null;
  }
  private select(conversation: NativeConversation): void {
    this.state.current = conversation; this.state.mode = conversation.mode;
    this.state.archived = Boolean(conversation.archived);
    this.remember("doppel.native.conversation", conversation.id);
    const remembered = conversation.selected_run_id !== undefined ? conversation.selected_run_id
      : this.recalled(`doppel.native.run.${conversation.id}`);
    const preferred = conversation.selected_run_id === null ? ""
      : conversation.runs.some(r => r.run_id === remembered) ? remembered! : conversation.runs.at(-1)?.run_id || "";
    this.selectRun(conversation.active_run_id || preferred);
    this.updateSettings(this.state.settings!);
  }
  selectRun(runId: string): void {
    if (this.state.closing) return;
    if (runId && !this.state.current?.runs.some(r => r.run_id === runId)) return;
    this.state.selectedRunId = runId;
    if (this.state.current) {
      this.remember(`doppel.native.run.${this.state.current.id}`, runId);
      this.persistSelection(this.state.current.id, runId || null);
    }
  }
  async open(id: string): Promise<void> {
    if (this.state.closing || this.state.submitting || this.state.transition || this.submissionUncertain) return;
    const epoch = ++this.selectionEpoch;
    const archived = this.state.archived;
    this.state.opening = true;
    try {
      const conversation = await nativeWorkspaceApi.conversation(id);
      if (this.disposed || epoch !== this.selectionEpoch) return;
      this.select(conversation);
      if (archived !== this.state.archived) await this.refreshList();
      await this.flushSelection();
    } finally { if (!this.disposed && epoch === this.selectionEpoch) this.state.opening = false; }
  }
  async prepareDraft(review = false): Promise<void> {
    if (this.busy || !this.state.profileId) return;
    this.state.transition = true;
    const epoch = ++this.selectionEpoch;
    try {
      const conversation = await nativeWorkspaceApi.draft(this.state.mode, this.state.profileId, review ? "代码审查" : "新对话");
      if (this.disposed || epoch !== this.selectionEpoch) return;
      this.state.groupId = null; this.select(conversation);
      await this.flushSelection(); await this.refreshList();
    } finally { if (!this.disposed) this.state.transition = false; }
  }
  async chooseProfile(profileId: string): Promise<void> {
    if (this.busy) return;
    if (!this.state.settings?.profiles.some(p => p.id === profileId)) throw new Error("模型档案不存在。");
    const current = this.state.current;
    if (current?.archived) return;
    const epoch = this.selectionEpoch;
    this.state.transition = true;
    try {
      if (current) {
        const updated = await nativeWorkspaceApi.update(current.id, { profile_id: profileId });
        if (this.disposed || epoch !== this.selectionEpoch) return;
        this.state.current = updated;
      }
      this.state.profileId = profileId;
    } finally { if (!this.disposed) this.state.transition = false; }
  }
  async search(query: string): Promise<void> {
    const epoch = ++this.searchEpoch;
    this.state.searchResults = []; this.state.searching = !!query.trim();
    if (!query.trim()) return;
    try {
      const rows = await nativeWorkspaceApi.search(query.trim());
      if (!this.disposed && epoch === this.searchEpoch) this.state.searchResults = rows;
    } finally { if (!this.disposed && epoch === this.searchEpoch) this.state.searching = false; }
  }
  async submit(request: ChatSubmission, includeContext = false): Promise<void> {
    if (this.busy || !request.prompt.trim() || !this.state.profileId || this.state.current?.archived) return;
    this.state.submitting = true;
    ++this.selectionEpoch;
    const draftInput: ChatSubmission = JSON.parse(JSON.stringify(request)) as ChatSubmission;
    const profileId = this.state.profileId, mode = this.state.mode;
    try {
      if (includeContext && !this.prepareContext) throw new Error("上下文面板未就绪，不能绑定输入。");
      const context = includeContext ? await this.prepareContext!(null) : undefined;
      if (this.disposed) return;
      if (!this.state.current) {
        const draft = await nativeWorkspaceApi.draft(mode, profileId);
        if (this.disposed) return;
        this.select(draft);
      }
      const current = this.state.current!;
      const body = { ...draftInput, prompt: draftInput.prompt.trim(), mode: current.mode, conversation_id: current.id,
        profile_id: profileId, deadline_seconds: 600, ...(context === undefined ? {} : { context }) };
      const accepted = await this.admission.submit(body);
      if (accepted) await this.ownAccepted(accepted);
    } finally { if (!this.disposed) this.state.submitting = false; }
  }
  async retrySubmission(): Promise<void> {
    if (!this.submissionUncertain || this.disposed || this.state.submitting || this.state.closing || this.state.loading || this.state.opening || this.state.transition) return;
    this.state.submitting = true;
    try { const accepted = await this.admission.retry(); if (accepted) await this.ownAccepted(accepted); }
    finally { if (!this.disposed) this.state.submitting = false; }
  }
  private async ownAccepted(accepted: { run_id: string; conversation_id: string; status: string }): Promise<void> {
    if (this.disposed) return;
    const current = this.state.current;
    if (!current || current.id !== accepted.conversation_id) throw new Error("已接受运行所属会话不在当前选择中；不能投影到另一会话。");
    this.remember("doppel.native.conversation", current.id);
    this.remember(`doppel.native.run.${current.id}`, accepted.run_id);
    this.persistSelection(current.id, accepted.run_id);
    // Own acceptance before projection I/O; a later read error isn't a lost POST.
    const active = ["queued", "running", "interrupted"].includes(accepted.status);
    current.active_run_id = active ? accepted.run_id : null;
    if (!current.runs.some(run => run.run_id === accepted.run_id)) current.runs.push({ run_id: accepted.run_id, mode: current.mode, status: accepted.status, lease_active: active ? 1 : 0 });
    this.state.selectedRunId = accepted.run_id;
    await this.refreshRun();
  }
  async refreshRun(): Promise<void> {
    const current = this.state.current;
    if (!current || this.polling || this.disposed || this.state.closing || this.state.transition) return;
    const epoch = this.selectionEpoch;
    this.polling = true;
    try {
      const next = await nativeWorkspaceApi.conversation(current.id);
      if (this.disposed || epoch !== this.selectionEpoch) return;
      const changed = JSON.stringify(current) !== JSON.stringify(next);
      this.state.current = next;
      if (!next.runs.some(r => r.run_id === this.state.selectedRunId)) this.selectRun(next.active_run_id || next.runs.at(-1)?.run_id || "");
      if (changed) await this.refreshList();
    } finally { this.polling = false; }
  }
  dispose(): void { this.disposed = true; this.selectionEpoch++; this.listEpoch++; this.searchEpoch++; }
}
