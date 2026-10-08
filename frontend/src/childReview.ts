import { reactive, type InjectionKey } from "vue";
import { parseChildAdmission, parseChildCancel, parseChildHistory, parseChildSnapshot, reviewPrompt, subagentReviewApi,
  type ChildAdmission, type ChildCancelReceipt, type ChildHistory, type ChildRecord, type ChildSnapshot,
  type ReviewedCancelBody, type ReviewedFollowBody, type ReviewedSpawnBody } from "./subagentReviewApi";

export const openChildReviewKey: InjectionKey<(parent: string) => void> = Symbol("root-owned-child-review");
export type ChildAction = "spawn" | "follow_up" | "cancel";
export interface ChildIntent {
  readonly action: ChildAction; readonly parent_run_id: string; readonly subagent_id: string | null;
  readonly expected_generation: number | null; readonly prompt: string | null;
  readonly body: Readonly<ReviewedSpawnBody | ReviewedFollowBody | ReviewedCancelBody>;
}
type Receipt = ChildAdmission | ChildCancelReceipt;
type Drafts = { spawn: string; follow: Record<string, { generation: number; text: string }> };
function copy<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T; }

// One App-owned controller, not one per short-lived inspector. Only drafts for
// up to 32 nonempty draft sources remain in page memory; no new scheduler/storage.
export class ChildReviewController {
  readonly state = reactive({ parentRunId: "", selectedChildId: "", active: false, closing: false, busy: false,
    error: "", sourceNotice: "", snapshot: null as ChildSnapshot | null, page: null as ChildHistory | null,
    spawnDraft: "", followDraft: "", draftGeneration: null as number | null, hasDrafts: false,
    action: "spawn" as ChildAction, review: null as ChildIntent | null, confirmed: false,
    intent: null as ChildIntent | null, lastReceipt: null as { intent: ChildIntent; receipt: Receipt } | null,
    uncertain: false, deferredAcknowledgement: false, exitAcknowledged: false, draftExitAcknowledged: false });
  private readonly drafts = new Map<string, Drafts>();
  private flight: Promise<void> | undefined;
  private deferred: Receipt | null = null;
  private consent = "";
  private epoch = 0;
  private closeEpoch = 0;
  private disposed = false;
  constructor(private readonly api: typeof subagentReviewApi = subagentReviewApi) {}
  get selectionLocked(): boolean { return this.state.closing || this.state.busy || this.state.intent !== null; }
  get hasDrafts(): boolean { return this.state.hasDrafts; }
  get unloadBlocked(): boolean {
    return this.state.busy || this.state.uncertain && !this.state.exitAcknowledged || this.hasDrafts && !this.state.draftExitAcknowledged;
  }
  get selectedChild(): ChildRecord | null {
    return this.state.snapshot?.items.find(item => item.subagent_id === this.state.selectedChildId) ?? null;
  }
  private canRead(): boolean { return !this.disposed && this.state.active && !this.selectionLocked && !this.flight; }
  private disarm(): void { this.state.review = null; this.state.confirmed = false; this.consent = ""; }
  activate(active: boolean): void {
    if (active !== this.state.active) { this.epoch++; this.disarm(); }
    this.state.active = active && !this.disposed && !this.state.closing;
  }
  private saveDrafts(): void {
    const parent = this.state.parentRunId;
    if (!parent) return;
    const previous = this.drafts.get(parent) ?? { spawn: "", follow: {} };
    previous.spawn = this.state.spawnDraft;
    if (this.state.selectedChildId && this.state.draftGeneration !== null)
      previous.follow[this.state.selectedChildId] = { generation: this.state.draftGeneration, text: this.state.followDraft };
    for (const [child, draft] of Object.entries(previous.follow)) if (!draft.text) delete previous.follow[child];
    if (previous.spawn || Object.values(previous.follow).some(draft => !!draft.text)) this.drafts.set(parent, previous);
    else this.drafts.delete(parent); // empty visits/bindings consume no retained draft slot
    this.state.hasDrafts = [...this.drafts.values()].some(item => !!item.spawn || Object.values(item.follow).some(draft => !!draft.text));
  }
  selectSource(parent: string): boolean {
    if (this.disposed) return false;
    if (parent === this.state.parentRunId && parent) { this.state.sourceNotice = ""; return true; }
    if (!this.canRead() || !/^[0-9a-f]{32}$/.test(parent)) {
      this.state.sourceNotice = "子任务来源已固定或不可用；没有切换、读取或重发原请求。"; return false;
    }
    this.saveDrafts(); this.epoch++; this.disarm();
    this.state.parentRunId = parent; this.state.selectedChildId = "";
    this.state.spawnDraft = this.drafts.get(parent)?.spawn ?? ""; this.state.followDraft = ""; this.state.draftGeneration = null;
    this.state.snapshot = null; this.state.page = null; this.state.lastReceipt = null; this.state.error = ""; this.state.sourceNotice = "";
    this.state.action = "spawn"; this.saveDrafts(); return true;
  }
  selectChild(child: string): boolean {
    if (!this.canRead()) return false;
    const record = this.state.snapshot?.items.find(item => item.subagent_id === child);
    if (!record) return false;
    if (child === this.state.selectedChildId) return true; // no implicit generation rebinding
    this.saveDrafts(); this.epoch++; this.disarm(); this.state.selectedChildId = child; this.state.page = null;
    const saved = this.drafts.get(this.state.parentRunId)?.follow[child];
    this.state.followDraft = saved?.text ?? ""; this.state.draftGeneration = saved?.generation ?? record.generation;
    this.saveDrafts(); return true;
  }
  adoptChildGeneration(): void {
    if (!this.canRead() || !this.selectedChild) return;
    this.disarm(); this.state.page = null; this.state.draftGeneration = this.selectedChild.generation; this.saveDrafts();
  }
  setDraft(kind: "spawn" | "follow_up", text: string): void {
    if (!this.canRead() || !this.state.parentRunId || typeof text !== "string" || text.length > 8000
      || kind === "follow_up" && !this.selectedChild) return;
    if (text && !this.drafts.has(this.state.parentRunId) && this.drafts.size >= 32) {
      this.state.sourceNotice = "本页已保留 32 个非空来源草稿；仍可读取新来源，先明确清空不需要的旧草稿再写新草稿。"; return;
    }
    this.state.sourceNotice = "";
    this.disarm(); this.state.draftExitAcknowledged = false;
    if (kind === "spawn") this.state.spawnDraft = text; else this.state.followDraft = text;
    this.saveDrafts();
  }
  chooseAction(action: ChildAction): void {
    if (!this.canRead() || !["spawn", "follow_up", "cancel"].includes(action)) return;
    if (action !== this.state.action) { this.disarm(); this.state.action = action; }
  }
  private read<T>(send: () => Promise<T>, publish: (value: T) => void): Promise<void> {
    if (!this.canRead() || !this.state.parentRunId) return Promise.resolve();
    this.disarm(); this.state.busy = true; this.state.error = ""; const epoch = ++this.epoch;
    const work = Promise.resolve().then(send).then(value => {
      if (!this.disposed && this.state.active && !this.state.closing && epoch === this.epoch) publish(value);
    }).catch(() => {
      if (!this.disposed && this.state.active && !this.state.closing && epoch === this.epoch)
        this.state.error = "子任务读取未完成；未启动、取消、追问或认定旧确认请求成功。";
    }).finally(() => { if (this.flight === work) { this.flight = undefined; this.state.busy = false; } });
    this.flight = work; return work;
  }
  readSnapshot(): Promise<void> {
    const parent = this.state.parentRunId;
    return this.read(() => this.api.snapshot(parent), value => {
      this.state.snapshot = copy(parseChildSnapshot(value, parent)); this.state.page = null;
      if (!this.selectedChild) { this.saveDrafts(); this.state.selectedChildId = ""; this.state.followDraft = ""; this.state.draftGeneration = null; }
      // A newer row never rebinds an older draft. Explicit adopt is required.
    });
  }
  readHistory(offset: number): Promise<void> {
    const record = this.selectedChild, parent = this.state.parentRunId;
    if (!record || !this.canRead() || !Number.isSafeInteger(offset) || offset < 0 || offset > record.history_total) return Promise.resolve();
    const child = record.subagent_id, generation = record.generation; this.state.page = null;
    return this.read(() => this.api.history(parent, child, generation, offset, 16), value => {
      this.state.page = copy(parseChildHistory(value, parent, child, generation, offset, 16));
    });
  }
  private makeIntent(): ChildIntent {
    const snapshot = this.state.snapshot, record = this.selectedChild, parent = this.state.parentRunId, action = this.state.action;
    if (!snapshot || snapshot.parent_run_id !== parent || snapshot.service.failed_close_diagnostic
      || snapshot.service.execution_admission !== "not_checked_by_read"
        && !(action === 'cancel' && snapshot.service.execution_admission === 'quarantined')) throw new Error("child_review_metadata_required");
    let body: ReviewedSpawnBody | ReviewedFollowBody | ReviewedCancelBody;
    let prompt: string | null = null, generation: number | null = null, child: string | null = null;
    if (action === "spawn") {
      if (snapshot.total >= snapshot.limits.lifetime_per_parent) throw new Error("child_review_lifetime_capacity");
      prompt = reviewPrompt(this.state.spawnDraft); body = Object.freeze({ confirmed: true, prompt });
    } else {
      if (!record) throw new Error("child_review_selection_required");
      child = record.subagent_id; generation = record.generation;
      if (action === "follow_up") {
        if (record.status !== "completed" || this.state.draftGeneration !== generation) throw new Error("child_review_draft_generation_changed");
        prompt = reviewPrompt(this.state.followDraft); body = Object.freeze({ confirmed: true, prompt, expected_generation: generation });
      } else {
        if (!["queued", "running"].includes(record.status)) throw new Error("child_review_cancel_not_active");
        body = Object.freeze({ confirmed: true, expected_generation: generation });
      }
    }
    if (new TextEncoder().encode(JSON.stringify(body)).length > 16384
      || action === "follow_up" && generation === Number.MAX_SAFE_INTEGER) throw new Error("child_review_body_budget");
    return Object.freeze({ action, parent_run_id: parent, subagent_id: child, expected_generation: generation, prompt, body });
  }
  reviewAction(): boolean {
    if (!this.canRead()) return false;
    this.disarm(); this.state.error = "";
    try { this.state.review = this.makeIntent(); return true; }
    catch { this.state.error = "无法准备本次动作：先读取原父任务目录，核对状态、累计额度、草稿与原 generation。"; return false; }
  }
  private signature(): string { try { return JSON.stringify(this.makeIntent()); } catch { return ""; } }
  confirmReview(confirmed: boolean): void {
    if (!this.canRead() || !this.state.review) return;
    const signature = this.signature();
    this.consent = confirmed === true && signature === JSON.stringify(this.state.review) ? signature : "";
    this.state.confirmed = !!this.consent;
  }
  private validateReceipt(reply: Receipt, intent: ChildIntent): Receipt {
    return intent.action === "cancel" ? parseChildCancel(reply, intent.parent_run_id, intent.subagent_id!, intent.expected_generation!)
      : parseChildAdmission(reply, intent.parent_run_id, intent.prompt!, intent.subagent_id ?? undefined, intent.expected_generation ?? undefined);
  }
  private publishReceipt(reply: Receipt): void {
    const intent = this.state.intent;
    if (!intent) throw new Error("child_review_original_intent_required");
    this.state.lastReceipt = copy({ intent, receipt: this.validateReceipt(reply, intent) });
    this.state.snapshot = null; this.state.page = null; // explicit fresh read required, not current-state/drain receipt
    this.state.intent = null; this.state.uncertain = false; this.state.exitAcknowledged = false;
    this.state.deferredAcknowledgement = false; this.deferred = null; this.disarm();
    // Keep raw drafts and their original generation even after a known ACK.
  }
  dispatch(): Promise<void> {
    if (!this.canRead() || !this.state.review || !this.state.confirmed || !this.consent
      || this.consent !== this.signature() || this.consent !== JSON.stringify(this.state.review)) return Promise.resolve();
    const intent = this.state.review;
    this.disarm(); this.state.intent = intent; this.state.busy = true; this.state.error = "";
    this.state.lastReceipt = null; this.state.exitAcknowledged = false; const epoch = ++this.epoch;
    const send = (): Promise<Receipt> => intent.action === "spawn" ? this.api.spawn(intent.parent_run_id, intent.body as ReviewedSpawnBody)
      : intent.action === "follow_up" ? this.api.followUp(intent.parent_run_id, intent.subagent_id!, intent.body as ReviewedFollowBody)
      : this.api.cancel(intent.parent_run_id, intent.subagent_id!, intent.body as ReviewedCancelBody);
    const work = Promise.resolve().then(send).then(reply => {
      const receipt = copy(this.validateReceipt(reply, intent));
      if (this.disposed) return;
      if (epoch === this.epoch && this.state.active && !this.state.closing) this.publishReceipt(receipt);
      else { this.deferred = receipt; this.state.deferredAcknowledgement = true; }
    }).catch(() => {
      if (this.disposed) return;
      this.state.uncertain = true;
      this.state.error = "子任务操作回复未知；固定原父任务、子任务、generation 和确认请求，不自动重试或用列表猜测成功。";
    }).finally(() => { if (this.flight === work) { this.flight = undefined; this.state.busy = false; } });
    this.flight = work; return work;
  }
  showOriginalReceipt(): void {
    if (this.disposed || !this.state.active || this.state.closing || this.flight || this.state.uncertain || !this.deferred) return;
    this.publishReceipt(this.deferred); // explicit local adoption only, no fetch/effect
  }
  acknowledgeExit(confirmed: boolean): void {
    if (this.disposed || !this.state.active || this.state.closing || this.flight || !this.state.uncertain) return;
    this.state.exitAcknowledged = confirmed === true; this.disarm(); // narrows exit, never source/action unlock
  }
  acknowledgeDraftExit(confirmed: boolean): void {
    if (this.disposed || !this.state.active || this.state.closing || this.flight) return;
    this.state.draftExitAcknowledged = confirmed === true;
  }
  async prepareClose(): Promise<void> {
    const epoch = ++this.closeEpoch; this.state.closing = true; this.state.active = false; this.disarm(); this.epoch++;
    const original = this.flight; if (original) await original;
    if (this.disposed || epoch !== this.closeEpoch) throw new Error("子任务准备已被替代；不得继续旧主机切换。");
    if (this.state.uncertain && !this.state.exitAcknowledged) throw new Error("子任务回复未知；请明确知悉保留原请求后退出，不能重跑。");
    if (this.hasDrafts && !this.state.draftExitAcknowledged) throw new Error("子任务草稿仅在本页内存；请明确知悉关闭或切项目会丢失全部草稿。");
    // Original host service/manager owns actual child/SQLite/OS drain; not this ACK.
  }
  finishClose(): void { this.closeEpoch++; this.epoch++; this.disarm(); this.state.closing = false; }
  dispose(): void { this.disposed = true; this.closeEpoch++; this.epoch++; this.disarm(); this.state.active = false; this.state.closing = true; }
}
