import { reactive } from "vue";
import { changesError } from "./changes";
import { runIdentifier } from "./changesApi";
import { frozenCopy } from "./inverseFraming";
import { verificationApi, validVerificationNames } from "./verificationApi";
import { sameVerificationSource, validVerificationPage, validVerificationReview, validVerificationSource, verificationPresentation, verificationUnknown } from "./verificationFraming";
import type { VerificationApi, VerificationControlIntent, VerificationDecisionBody, VerificationIntent, VerificationPage,
  VerificationPrepareBody, VerificationReview, VerificationSource, VerificationSummary } from "./verificationTypes";

const terminals = ["completed", "failed", "cancelled", "rejected", "expired"];
/** Manual configured verification only. Does not reuse inverse grants, execute arbitrary argv or repair unknowns. */
export class VerificationReviewController {
  readonly state = reactive({ active: false, closing: false, busy: false, controlling: false, reading: false,
    runId: "", source: null as VerificationSource | null, review: null as VerificationReview | null,
    intent: null as VerificationIntent | null, controlIntents: [] as VerificationControlIntent[], uncertain: false, controlUncertain: false,
    armed: false, exitAcknowledged: false, effectEpoch: 0, error: "", historyError: "",
    history: null as VerificationPage | null, historyStale: true, historyCursor: "", historyDepth: 0 });
  private disposed = false;
  private flight?: Promise<void>;
  private controlFlight?: Promise<void>;
  private readFlight?: Promise<void>;
  private abort?: AbortController;
  private readEpoch = 0;
  private anchor = "";
  private resumeRequested = false;
  private cursors = [""];
  constructor(private readonly api: VerificationApi = verificationApi,
    private readonly operationId: () => string = () => crypto.randomUUID().replaceAll("-", ""),
    private readonly now: () => number = () => Date.now()) {}
  get outcomeUnknown(): boolean { return this.state.uncertain || this.state.controlUncertain || verificationUnknown(this.state.review); }
  get selectionLocked(): boolean { return this.state.busy || this.state.controlling || this.state.reading || this.state.closing || this.outcomeUnknown; }
  get canCancel(): boolean {
    const view = this.state.review;
    // After a timed-out close hook, only exact narrowing cancel may still reach the original live task.
    return !this.disposed && this.state.active && (!this.state.closing || this.resumeRequested) && !this.controlFlight && !this.state.controlUncertain
      && this.state.controlIntents.length < 16 && !!view && (this.state.intent?.kind === "approve" && !!this.flight
        || !this.flight && ["pending", "running", "indeterminate"].includes(view.status));
  }
  get canReconcile(): boolean { return this.canAct() && !!this.state.review && ["running", "indeterminate"].includes(this.state.review.status)
    && this.state.controlIntents.length < 16; }
  get canRecover(): boolean { return this.canRead() && (!!this.state.review || !!this.state.intent)
    && (!this.flight || this.state.intent?.kind !== "prepare"); }
  private canRead(): boolean { return !this.disposed && this.state.active && !this.state.closing && !this.readFlight && !this.controlFlight; }
  private canAct(): boolean { return this.canRead() && !this.flight; }
  private disarm(): void { this.anchor = ""; this.state.armed = false; }
  private fenceRead(): void { this.readEpoch++; this.abort?.abort(); }
  private knownTerminal(): boolean { return !!this.state.review && terminals.includes(this.state.review.status) && !verificationUnknown(this.state.review); }
  private settleProof(): void {
    if (this.knownTerminal()) {
      this.state.uncertain = false; this.state.controlUncertain = false;
      if (!this.flight) this.state.intent = null;
    }
  }
  private pending(view = this.state.review): view is VerificationReview { return !!view && view.status === "pending"
    && view.lifecycle?.pending_expired !== true && Date.parse(view.expires_at) > this.now(); }
  private signature(view: VerificationReview): string { return JSON.stringify({ source: view.source, review_id: view.review_id,
    created_at: view.created_at, expires_at: view.expires_at, plan: view.plan }); }
  activate(active: boolean): void {
    if (this.disposed) return; this.state.active = active;
    if (!active) { this.disarm(); this.fenceRead(); }
    // No automatic history/read/config refresh/permission/reconciliation on entry.
  }
  selectRun(run: string): boolean {
    if (this.disposed || this.selectionLocked || run !== "" && !runIdentifier(run)) return false;
    if (run === this.state.runId) return true;
    this.clearSelection(); this.state.runId = run; this.state.source = null; this.state.history = null;
    this.state.historyStale = true; this.state.historyError = ""; this.state.historyCursor = ""; this.state.historyDepth = 0; this.cursors = [""]; return true;
  }
  selectSource(source: VerificationSource | null): boolean {
    if (this.disposed || this.selectionLocked || source && (!validVerificationSource(source) || source.run_id !== this.state.runId)) return false;
    if (source && this.state.source && sameVerificationSource(source, this.state.source)) return true;
    this.clearSelection(); this.state.source = source ? frozenCopy({ run_id: source.run_id, tool_call_id: source.tool_call_id, patch_id: source.patch_id }) : null; return true;
  }
  private clearSelection(): void {
    this.fenceRead(); this.disarm(); this.state.review = null; this.state.intent = null; this.state.controlIntents = [];
    this.state.uncertain = false; this.state.controlUncertain = false; this.state.exitAcknowledged = false; this.state.error = "";
  }
  discardPreview(confirmed: boolean): void { if (this.canAct() && !this.selectionLocked && confirmed === true) this.clearSelection(); }
  private accept(reply: VerificationReview, source: VerificationSource | null, reviewId: string, planId: string | null): void {
    const previous = this.state.review;
    if (!validVerificationReview(reply, this.state.runId, reviewId, source, planId)
      || previous?.review_id === reviewId && (this.signature(previous) !== this.signature(reply)
        || previous.status !== "pending" && reply.status === "pending"
        || terminals.includes(previous.status) && previous.status !== reply.status)) throw new Error("verification_evidence_scope_unavailable");
    this.state.review = verificationPresentation(reply); this.state.exitAcknowledged = false; this.disarm();
  }
  async prepare(names: string[] | null, confirmed: boolean, command: boolean, write: boolean): Promise<void> {
    if (!this.canAct() || this.outcomeUnknown || this.state.review || confirmed !== true || command !== true || write !== true
      || !validVerificationSource(this.state.source) || !validVerificationNames(names)) return;
    let reviewId: string;
    try { reviewId = this.operationId(); } catch { this.state.error = "无法生成安全原 operation ID；没有发送。"; return; }
    if (!runIdentifier(reviewId)) { this.state.error = "无法生成安全原 operation ID；没有发送。"; return; }
    const source = this.state.source;
    const body: VerificationPrepareBody = frozenCopy({ confirmed: true, source_tool_call_id: source.tool_call_id,
      source_patch_id: source.patch_id, operation_id: reviewId, names, command_execute: true, workspace_write: true });
    const intent: VerificationIntent = frozenCopy({ kind: "prepare", source, review_id: reviewId, plan_id: null, body });
    await this.dispatch(intent, () => this.api.prepare(source.run_id, frozenCopy(body)), true);
  }
  armStored(confirmed: boolean): void { if (this.canAct() && confirmed === true && !this.outcomeUnknown && this.pending()) {
    this.anchor = this.signature(this.state.review!); this.state.armed = true;
  } }
  async decide(action: "approve" | "reject", reviewId: string, planId: string, confirmed: boolean, newCommand: boolean, newWrite: boolean): Promise<void> {
    const view = this.state.review;
    if (!this.canAct() || this.outcomeUnknown || confirmed !== true || !this.pending(view) || view.review_id !== reviewId || view.plan.plan_id !== planId
      || !["approve", "reject"].includes(action) || action === "approve" && (!this.state.armed || this.anchor !== this.signature(view) || newCommand !== true || newWrite !== true)) return;
    const body: VerificationDecisionBody = { confirmed: true, plan_id: planId, action,
      command_execute: action === "approve" && newCommand === true, workspace_write: action === "approve" && newWrite === true };
    const intent: VerificationIntent = frozenCopy({ kind: action, source: view.source, review_id: reviewId, plan_id: planId, body });
    this.disarm(); if (action === "approve") { this.state.effectEpoch++; this.state.historyStale = true; }
    await this.dispatch(intent, () => this.api.decide(view.source.run_id, reviewId, frozenCopy(body)), false);
  }
  private dispatch(intent: VerificationIntent, send: () => Promise<VerificationReview>, freshPrepare: boolean): Promise<void> {
    this.fenceRead(); this.disarm(); this.state.busy = true; this.state.intent = intent; this.state.error = ""; this.state.exitAcknowledged = false;
    const work = (async () => {
      try {
        const reply = await send();
        if (this.disposed) return;
        if (intent.kind === "prepare" && (intent.body as VerificationPrepareBody).names !== null
          && JSON.stringify(reply.plan?.commands.map(command => command.name)) !== JSON.stringify((intent.body as VerificationPrepareBody).names)
          || intent.kind === "approve" && !["completed", "failed", "cancelled", "indeterminate"].includes(reply.status)
          || intent.kind === "reject" && reply.status !== "rejected") throw new Error("verification_acknowledgement_unavailable");
        this.fenceRead(); this.accept(reply, intent.source, intent.review_id, intent.plan_id); this.state.uncertain = false;
        if (freshPrepare && !this.state.closing && this.state.active && !this.outcomeUnknown && this.pending()) { this.anchor = this.signature(this.state.review!); this.state.armed = true; }
        // A known prepare ACK is not execution approval; a consumed unknown ACK remains locked by its actual evidence.
        if (!verificationUnknown(reply)) this.state.intent = null;
      } catch (error) {
        if (!this.disposed) {
          // A newer exact terminal GET/cancel witness can settle this original decision,
          // but it cannot settle a DIFFERENT lost cancel/reconcile transport acknowledgement.
          this.state.uncertain = !this.knownTerminal(); this.disarm();
          this.state.error = this.state.uncertain ? "原验证请求结果未知；保留 ID/精确 payload，不据 HTTP 失败判断没执行。" + changesError(error)
            : "原决定回复丢失，但已有同一 operation 的明确终态证据；不重发决定，也不据此推断其他控制回执。";
        }
      }
    })().finally(() => { this.flight = undefined; this.state.busy = false;
      if (this.knownTerminal() && !this.state.uncertain) this.state.intent = null;
      this.maybeResume(); });
    this.flight = work; return work;
  }
  async retryPrepare(confirmed: boolean): Promise<void> {
    const intent = this.state.intent;
    if (!this.canAct() || confirmed !== true || !this.state.uncertain || this.state.controlUncertain || intent?.kind !== "prepare") return;
    await this.dispatch(intent, () => this.api.prepare(intent.source.run_id, frozenCopy(intent.body as VerificationPrepareBody)), false);
  }
  recover(): Promise<void> {
    if (!this.canRecover) return Promise.resolve();
    const intent = this.state.intent, view = this.state.review;
    return intent ? this.read(intent.review_id, intent.source, intent.plan_id, intent) : view ? this.read(view.review_id, view.source, view.plan.plan_id, null) : Promise.resolve();
  }
  readExisting(reviewId: string): Promise<void> {
    if (!this.canAct() || this.selectionLocked || !runIdentifier(reviewId) || !runIdentifier(this.state.runId)) return Promise.resolve();
    const seen = this.state.history?.items.find(item => item.review_id === reviewId);
    return this.read(reviewId, seen?.source ?? null, null, null, seen);
  }
  readHistory(item: VerificationSummary): Promise<void> {
    if (!this.canAct() || this.selectionLocked || this.state.historyStale || !this.state.history?.items.some(row => row.review_id === item.review_id
      && sameVerificationSource(row.source, item.source))) return Promise.resolve();
    return this.read(item.review_id, item.source, null, null, item);
  }
  private read(id: string, source: VerificationSource | null, planId: string | null, recovering: VerificationIntent | null, seen?: VerificationSummary): Promise<void> {
    if (!this.canRead()) return Promise.resolve();
    this.disarm(); this.state.reading = true; this.state.error = "";
    const epoch = ++this.readEpoch, abort = new AbortController(); this.abort = abort;
    const work = (async () => {
      try {
        const reply = await this.api.read(this.state.runId, id, abort.signal);
        if (epoch !== this.readEpoch || abort.signal.aborted || this.disposed || this.state.closing) return;
        if (recovering?.kind === "prepare" && (recovering.body as VerificationPrepareBody).names !== null
          && JSON.stringify(reply.plan?.commands.map(command => command.name)) !== JSON.stringify((recovering.body as VerificationPrepareBody).names)) throw new Error("verification_selection_scope_unavailable");
        if (seen && (seen.status !== "pending" && reply.status === "pending"
          || terminals.includes(seen.status) && reply.status !== seen.status)) throw new Error("verification_consumed_history_unavailable");
        this.accept(reply, source, id, planId);
        if (recovering?.kind === "prepare") { this.state.uncertain = false; if (!this.flight) this.state.intent = null; }
        this.settleProof(); // Pending/running/404 never clears consumed unknown; terminal evidence never grants again.
      } catch (error) { if (epoch === this.readEpoch && !this.disposed && !this.state.closing) this.state.error = changesError(error); }
    })().finally(() => { this.readFlight = undefined; this.abort = undefined; this.state.reading = false; this.maybeResume(); });
    this.readFlight = work; return work;
  }
  cancel(confirmed: boolean): Promise<void> {
    if (confirmed !== true || !this.canCancel) return Promise.resolve();
    const view = this.state.review!;
    const intent: VerificationControlIntent = frozenCopy({ kind: "cancel", source: view.source, review_id: view.review_id,
      plan_id: view.plan.plan_id, body: { confirmed: true, plan_id: view.plan.plan_id } });
    return this.control(intent, () => this.api.cancel(view.source.run_id, view.review_id, { confirmed: true, plan_id: view.plan.plan_id }));
  }
  reconcile(confirmed: boolean): Promise<void> {
    if (confirmed !== true || !this.canReconcile) return Promise.resolve();
    const view = this.state.review!;
    const intent: VerificationControlIntent = frozenCopy({ kind: "reconcile", source: view.source, review_id: view.review_id,
      plan_id: view.plan.plan_id, body: { confirmed: true } });
    return this.control(intent, () => this.api.reconcile(view.source.run_id, view.review_id, { confirmed: true }));
  }
  private control(intent: VerificationControlIntent, send: () => Promise<VerificationReview>): Promise<void> {
    this.fenceRead(); this.disarm(); this.state.controlling = true; this.state.controlIntents.push(intent);
    this.state.exitAcknowledged = false; this.state.error = ""; this.state.historyStale = true;
    const work = (async () => {
      try {
        const reply = await send();
        if (this.disposed) return;
        if (["pending", "running"].includes(reply.status)) throw new Error("verification_control_not_settled");
        this.fenceRead(); this.accept(reply, intent.source, intent.review_id, intent.plan_id);
        this.state.controlUncertain = verificationUnknown(reply); this.settleProof();
      } catch (error) {
        if (!this.disposed) { this.state.controlUncertain = true; this.state.error = "原控制请求/排空回执未知；精确 ID/payload 保留，不自动重复或宣称已停止。" + changesError(error); }
      }
    })().finally(() => { this.controlFlight = undefined; this.state.controlling = false; this.maybeResume(); });
    this.controlFlight = work; return work;
  }
  loadHistory(): Promise<void> { return this.history([""]); }
  nextHistory(): Promise<void> {
    const next = this.state.history?.next_after_id;
    if (this.state.historyStale || !this.state.history?.has_more || !next) return Promise.resolve();
    if (this.cursors.length >= 64) { this.state.historyError = "本次导航达到 64 页保留上限；可显式重新读取首屏，非总记录数上限。"; return Promise.resolve(); }
    return this.history([...this.cursors, next]);
  }
  previousHistory(): Promise<void> { return this.cursors.length > 1 ? this.history(this.cursors.slice(0, -1)) : Promise.resolve(); }
  private history(trail: string[]): Promise<void> {
    if (!this.canAct() || this.selectionLocked || !runIdentifier(this.state.runId)) return Promise.resolve();
    this.disarm(); this.state.reading = true; this.state.historyStale = true; this.state.historyError = "";
    const run = this.state.runId, after = trail.at(-1)!, epoch = ++this.readEpoch, abort = new AbortController(); this.abort = abort;
    const work = (async () => {
      try {
        const page = await this.api.list(run, after, abort.signal);
        if (epoch !== this.readEpoch || abort.signal.aborted || this.disposed || this.state.closing) return;
        if (!validVerificationPage(page, run, after)) throw new Error("verification_history_unavailable");
        // Summaries contain no argv/stdout; refuse private extra fields rather than retaining them.
        this.state.history = frozenCopy({ items: page.items.map(item => ({ ...verificationPresentationSummary(item) })), has_more: page.has_more,
          next_after_id: page.next_after_id, budget_limited: page.budget_limited, decode_bytes: page.decode_bytes,
          sql_read_only: true as const, filesystem_zero_write_guarantee: false as const, order: "review_id_keyset_not_chronological" as const });
        this.state.historyStale = false; this.cursors = trail; this.state.historyCursor = after; this.state.historyDepth = trail.length - 1;
      } catch (error) { if (epoch === this.readEpoch && !this.disposed && !this.state.closing) this.state.historyError = changesError(error); }
    })().finally(() => { this.readFlight = undefined; this.abort = undefined; this.state.reading = false; this.maybeResume(); });
    this.readFlight = work; return work;
  }
  acknowledgeExit(confirmed: boolean): void { if (this.canAct() && confirmed === true && this.outcomeUnknown) {
    this.state.exitAcknowledged = true; this.disarm(); // Exit only; no forget, new grants, cancel or absence proof.
  } }
  private maybeResume(): void { if (this.resumeRequested && !this.disposed && !this.flight && !this.controlFlight && !this.readFlight) {
    this.state.closing = false; this.resumeRequested = false;
  } }
  async prepareClose(): Promise<void> {
    this.state.closing = true; this.resumeRequested = false; this.disarm(); this.fenceRead();
    while (this.flight || this.controlFlight || this.readFlight) await Promise.all([this.flight, this.controlFlight, this.readFlight].filter((flight): flight is Promise<void> => !!flight));
    if (this.outcomeUnknown && !this.state.exitAcknowledged) throw new Error("验证结果/原排空回执未知；只读恢复或显式记录原 IDs 后退出，不能重跑。" );
    this.maybeResume();
  }
  finishClose(): void { this.resumeRequested = true; this.maybeResume(); }
  dispose(): void { this.disposed = true; this.disarm(); this.fenceRead(); this.state.active = false; this.state.closing = true; }
}
function verificationPresentationSummary(item: VerificationSummary): VerificationSummary {
  const { review_id, operation_id, operation_kind, source, target, created_at, expires_at, status, error_code, success, has_unknown_command, evidence, lifecycle, provenance } = item;
  return { review_id, operation_id, operation_kind, source: { run_id: source.run_id, tool_call_id: source.tool_call_id, patch_id: source.patch_id },
    target, created_at, expires_at, status, error_code, success, has_unknown_command, evidence,
    ...(lifecycle ? { lifecycle: { stored_status: lifecycle.stored_status, effective_status: lifecycle.effective_status, pending_expired: lifecycle.pending_expired,
      pending_unexpired: lifecycle.pending_unexpired, approval_available: false as const, retry_available: false as const, planned_commands: lifecycle.planned_commands,
      sealed_steps: lifecycle.sealed_steps, remaining_commands: lifecycle.remaining_commands, outcome_unknown: lifecycle.outcome_unknown,
      all_planned_attempts_sealed: lifecycle.all_planned_attempts_sealed, commands_completed: lifecycle.commands_completed } } : {}),
    ...(provenance ? { provenance: { source_registration: provenance.source_registration,
      source_run: { status: provenance.source_run.status, lease_active: provenance.source_run.lease_active }, patch_success: provenance.patch_success,
      plan: provenance.plan, output: provenance.output, cross_database_snapshot: provenance.cross_database_snapshot } } : {}) };
}
