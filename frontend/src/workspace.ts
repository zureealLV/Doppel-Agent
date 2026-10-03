import { reactive } from "vue";

import { workspaceApi } from "./workspaceApi";
import type { ChatSubmission, Conversation, ConversationGroup, ConversationSummary, LegacyApproval, LegacyEvent,
  PersistentRun, ProfileForm, PublicSettings } from "./workspaceTypes";

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

export function profileConfiguration(form: ProfileForm): ProfileForm & { provider: "mock" | "openai" } {
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
  return { ...form, name: form.name.trim(), model: form.model.trim(), base_url: form.base_url.trim(),
    provider: form.preset === "mock" ? "mock" : "openai" };
}

export class WorkspaceController {
  readonly state = reactive({
    workspace: "", settings: null as PublicSettings | null, groups: [] as ConversationGroup[],
    conversations: [] as ConversationSummary[], current: null as Conversation | null,
    archived: false, groupId: null as string | null, profileId: "", nextMode: "agent" as "agent" | "review",
    loading: false, transition: false, submitting: false, error: "",
    activeRun: null as PersistentRun | null, events: [] as LegacyEvent[], tasks: [] as Array<Record<string, unknown>>,
    approvals: [] as LegacyApproval[], searchResults: [] as ConversationSummary[], searching: false,
  });
  private selectionEpoch = 0;
  private searchEpoch = 0;
  private listEpoch = 0;
  private disposed = false;
  private polling = false;

  constructor(private readonly storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">) {}

  private remember(key: string, value: string): void { try { this.storage?.setItem(key, value); } catch { /* UI state still owns accepted work. */ } }
  private recalled(key: string): string | null { try { return this.storage?.getItem(key) ?? null; } catch { return null; } }
  private forget(key: string): void { try { this.storage?.removeItem(key); } catch { /* Storage is optional, not a run-acceptance gate. */ } }

  clearSelection(): void { this.selectionEpoch++; this.state.current = null; this.forget("doppel-conversation"); }

  get busy(): boolean { return this.state.submitting || this.state.transition || !!this.state.activeRun; }

  report(error: unknown): void {
    if (!this.disposed) this.state.error = error instanceof Error ? error.message : String(error);
  }

  async initialize(): Promise<void> {
    this.state.loading = true;
    try {
      const [health, settings] = await Promise.all([workspaceApi.health(), workspaceApi.settings()]);
      if (this.disposed) return;
      this.state.workspace = health.workspace;
      this.updateSettings(settings);
      await this.refreshList();
      if (this.disposed) return;
      const saved = this.recalled("doppel-conversation");
      if (saved) {
        try { await this.open(saved); } catch { this.forget("doppel-conversation"); }
      } else if (this.state.conversations[0]) await this.open(this.state.conversations[0].id);
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
    const conversation = await workspaceApi.conversation(id);
    if (this.disposed || epoch !== this.selectionEpoch) return;
    this.select(conversation);
    this.state.nextMode = "agent";
  }

  private select(conversation: Conversation): void {
    this.state.current = conversation;
    this.remember("doppel-conversation", conversation.id);
    const wanted = conversation.profile_id || this.state.settings?.active_profile_id;
    if (wanted && this.state.settings?.profiles.some((profile) => profile.id === wanted)) this.state.profileId = wanted;
  }

  async prepareDraft(mode: "agent" | "review"): Promise<void> {
    if (this.busy) return;
    this.state.transition = true;
    const epoch = ++this.selectionEpoch;
    try {
      const conversation = await workspaceApi.draft(mode);
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
    try {
      if (!this.state.current) {
        const draft = await workspaceApi.draft("agent");
        if (this.disposed) return;
        this.select(draft);
      }
      const conversation = this.state.current!;
      const profileId = this.state.profileId;
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
