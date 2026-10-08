import { reactive } from "vue";
import { changesError, validEvidence } from "./changes";
import { runIdentifier } from "./changesApi";
import { inverseApi } from "./inverseApi";
import { frozenCopy, sameInverseSource, validInverseReview, validInverseSource } from "./inverseFraming";
import type { InverseApi, InverseDecisionBody, InverseIntent, InversePrepareBody, InverseReview, InverseSource } from "./inverseTypes";
import type { PatchEvidence } from "./changesTypes";

const unknownStates = ["applying", "indeterminate"];
const terminalStates = ["applied", "failed", "rejected", "expired"];
export function inverseSourceFromEvidence(evidence: PatchEvidence | null): InverseSource | null {
  if (!evidence?.confirmed_applied || !evidence.receipt || !validInverseSource({ ...evidence.source, patch_id: evidence.receipt.patch_id })
    || !validEvidence(evidence, evidence.source.run_id, evidence.source.tool_call_id)) return null;
  return { run_id: evidence.source.run_id, tool_call_id: evidence.source.tool_call_id, patch_id: evidence.receipt.patch_id };
}
function presentation(value: InverseReview): InverseReview {
  // Never retain unrecognized top-level/private proposal/source blobs for display.
  const { review_id, operation_kind, source, effect_tool_call_id, patch_id, created_at, expires_at, status,
    review, result, error_code, effect_replayed, decision_replayed, effect_evidence, lifecycle } = value;
  return frozenCopy({ review_id, operation_kind, source: { run_id: source.run_id, tool_call_id: source.tool_call_id, patch_id: source.patch_id },
    effect_tool_call_id, patch_id, created_at, expires_at, status, review, result, error_code, effect_replayed, decision_replayed, effect_evidence,
    ...(lifecycle ? { lifecycle: { stored_status: lifecycle.stored_status, effective_status: lifecycle.effective_status,
      pending_expired: lifecycle.pending_expired, approval_available: false as const, retry_available: false as const } } : {}) });
}

/** Manual inverse only. Verification has its own reviewed argv operation; never inherit its grant/evidence. */
export class InverseReviewController {
  readonly state = reactive({ active: false, closing: false, busy: false, reading: false, error: "", uncertain: false,
    source: null as InverseSource | null, review: null as InverseReview | null, intent: null as InverseIntent | null,
    armed: false, effectEpoch: 0, exitAcknowledged: false });
  private disposed = false;
  private flight?: Promise<void>;
  private readFlight?: Promise<void>;
  private abort?: AbortController;
  private epoch = 0;
  private resumeRequested = false;
  private anchor = "";
  constructor(private readonly api: InverseApi = inverseApi,
    private readonly operationId: () => string = () => crypto.randomUUID().replaceAll("-", ""),
    private readonly now: () => number = () => Date.now()) {}
  get selectionLocked(): boolean { return this.state.busy || this.state.reading || this.state.closing || this.state.uncertain || !!this.state.review && unknownStates.includes(this.state.review.status); }
  private canAct(): boolean { return !this.disposed && this.state.active && !this.state.closing && !this.flight && !this.readFlight; }
  private disarm(): void { this.anchor = ""; this.state.armed = false; }
  private pending(view = this.state.review): view is InverseReview {
    return !!view && view.status === "pending" && view.lifecycle?.pending_expired !== true && Date.parse(view.expires_at) > this.now();
  }
  private signature(view: InverseReview): string {
    return JSON.stringify({ source: view.source, review_id: view.review_id, patch_id: view.patch_id, created_at: view.created_at, expires_at: view.expires_at, review: view.review });
  }
  activate(active: boolean): void {
    if (this.disposed) return;
    this.state.active = active;
    if (!active) { this.disarm(); this.epoch++; this.abort?.abort(); }
    // Reentry does not refresh/reprepare/reconcile/grant or repeat a consumed decision.
  }
  selectSource(source: InverseSource | null): boolean {
    if (this.disposed || this.selectionLocked) return false;
    if (source && !validInverseSource(source)) return false;
    if (source && this.state.source && sameInverseSource(source, this.state.source)) return true;
    this.epoch++; this.disarm(); this.state.source = source ? frozenCopy({ run_id: source.run_id, tool_call_id: source.tool_call_id, patch_id: source.patch_id }) : null;
    this.state.review = null; this.state.intent = null; this.state.error = ""; this.state.exitAcknowledged = false; return true;
  }
  private publish(value: InverseReview): void {
    const previous = this.state.review;
    if (previous?.review_id === value.review_id && (this.signature(previous) !== this.signature(value)
      || previous.status !== "pending" && value.status === "pending"
      || terminalStates.includes(previous.status) && previous.status !== value.status)) throw new Error("inverse_immutable_review_unavailable");
    this.state.review = presentation(value); this.disarm();
  }
  async prepare(confirmed: boolean, write: boolean): Promise<void> {
    if (!this.canAct() || this.state.uncertain || this.state.review
      || confirmed !== true || write !== true || !validInverseSource(this.state.source)) return;
    const source = this.state.source;
    let reviewId: string;
    try { reviewId = this.operationId(); } catch { this.state.error = "无法产生安全的原始 operation ID；没有发送。"; return; }
    if (!runIdentifier(reviewId)) { this.state.error = "无法产生安全的原始 operation ID；没有发送。"; return; }
    const body: InversePrepareBody = { confirmed: true, source_tool_call_id: source.tool_call_id, source_patch_id: source.patch_id,
      operation_id: reviewId, workspace_write: true };
    this.state.review = null; this.disarm();
    const intent: InverseIntent = frozenCopy({ kind: "prepare", source, review_id: reviewId, patch_id: null, body });
    await this.dispatch(intent, () => this.api.prepare(source.run_id, frozenCopy(body)), true);
  }
  discardPreview(confirmed: boolean): void {
    if (!this.canAct() || this.selectionLocked || confirmed !== true) return;
    this.disarm(); this.state.review = null; this.state.intent = null; this.state.exitAcknowledged = false;
    // UI selection only; no server rejection/deletion/expiry, no erase or effect absence claim.
  }
  armStored(confirmed: boolean): void {
    if (!this.canAct() || confirmed !== true || this.state.uncertain || !this.pending()) return;
    this.anchor = this.signature(this.state.review!); this.state.armed = true;
  }
  async decide(action: "approve" | "reject", reviewId: string, patchId: string, confirmed: boolean, freshWrite: boolean): Promise<void> {
    const view = this.state.review;
    if (!this.canAct() || this.state.uncertain || confirmed !== true || !this.pending(view) || view.review_id !== reviewId || view.patch_id !== patchId
      || !["approve", "reject"].includes(action) || action === "approve" && (freshWrite !== true || !this.state.armed || this.anchor !== this.signature(view))) return;
    const body: InverseDecisionBody = { confirmed: true, patch_id: patchId, action, workspace_write: action === "approve" && freshWrite === true };
    const intent: InverseIntent = frozenCopy({ kind: action, source: view.source, review_id: reviewId, patch_id: patchId, body });
    this.disarm(); if (action === "approve") this.state.effectEpoch++;
    await this.dispatch(intent, () => this.api.decide(view.source.run_id, reviewId, frozenCopy(body)), false);
  }
  private dispatch(intent: InverseIntent, send: () => Promise<InverseReview>, freshPrepare: boolean): Promise<void> {
    this.state.busy = true; this.state.intent = intent; this.state.error = ""; this.state.exitAcknowledged = false;
    const work = (async () => {
      try {
        const reply = await send();
        if (!validInverseReview(reply, intent.source, intent.review_id, intent.patch_id)
          || intent.kind === "approve" && !["applied", "failed", "indeterminate"].includes(reply.status)
          || intent.kind === "reject" && reply.status !== "rejected") throw new Error("invalid_inverse_acknowledgement");
        if (this.disposed) return;
        this.publish(reply); this.state.intent = null; this.state.uncertain = false;
        // Preparation consent is not write approval. Fresh prepare pins the displayed preview,
        // but the later decision still requires a NEW explicit confirmation/write grant.
        if (freshPrepare && !this.state.closing && this.state.active && this.pending()) { this.anchor = this.signature(this.state.review!); this.state.armed = true; }
      } catch (error) {
        if (!this.disposed) { this.state.uncertain = true; this.disarm(); this.state.error = "原请求结果未知；保留 ID 与精确 payload，不据 HTTP 失败推断没执行。" + changesError(error); }
      }
    })().finally(() => { this.flight = undefined; this.state.busy = false; this.maybeResume(); });
    this.flight = work; return work;
  }
  async retryPrepare(confirmed: boolean): Promise<void> {
    const intent = this.state.intent;
    if (!this.canAct() || confirmed !== true || !this.state.uncertain || intent?.kind !== "prepare") return;
    // Prepare replay cannot apply a patch. Never mint an ID, re-read source, refresh TTL or inherit new checkbox values.
    const body = intent.body as InversePrepareBody;
    await this.dispatch(intent, () => this.api.prepare(intent.source.run_id, frozenCopy(body)), false);
  }
  recover(): Promise<void> {
    const intent = this.state.intent, view = this.state.review;
    if (intent) return this.read(intent.source, intent.review_id, intent.patch_id, intent);
    if (view) return this.read(view.source, view.review_id, view.patch_id, null);
    return Promise.resolve();
  }
  readExisting(reviewId: string): Promise<void> {
    if (this.selectionLocked || this.state.intent || !validInverseSource(this.state.source) || !runIdentifier(reviewId)) return Promise.resolve();
    return this.read(this.state.source, reviewId, null, null);
  }
  private read(source: InverseSource, reviewId: string, patchId: string | null, recovering: InverseIntent | null): Promise<void> {
    if (!this.canAct()) return Promise.resolve();
    this.disarm(); this.state.reading = true; this.state.error = "";
    const epoch = ++this.epoch, abort = new AbortController(); this.abort = abort;
    const work = (async () => {
      try {
        const reply = await this.api.read(source.run_id, reviewId, abort.signal);
        if (epoch !== this.epoch || abort.signal.aborted || this.disposed || this.state.closing) return;
        if (!validInverseReview(reply, source, reviewId, patchId)) throw new Error("inverse_read_scope_unavailable");
        this.publish(reply); this.state.exitAcknowledged = false;
        // GET never proves a pending/applying decision absent. Indeterminate is consumed unknown,
        // not a fresh approve; only explicit metadata reconciliation may narrow that evidence.
        if (recovering && (recovering.kind === "prepare" || terminalStates.includes(reply.status))) { this.state.intent = null; this.state.uncertain = false; }
      } catch (error) { if (epoch === this.epoch && !this.disposed && !this.state.closing) this.state.error = changesError(error); }
    })().finally(() => { this.readFlight = undefined; this.abort = undefined; this.state.reading = false; this.maybeResume(); });
    this.readFlight = work; return work;
  }
  async reconcile(confirmed: boolean): Promise<void> {
    const view = this.state.review;
    if (!this.canAct() || confirmed !== true || !view || !unknownStates.includes(view.status)) return;
    // A lost decision stays retained separately; reconcile is metadata-only, never its replay.
    const original = this.state.intent;
    const intent: InverseIntent = frozenCopy({ kind: "reconcile", source: view.source, review_id: view.review_id, patch_id: view.patch_id, body: { confirmed: true } });
    await this.dispatch(intent, () => this.api.reconcile(view.source.run_id, view.review_id, { confirmed: true }), false);
    if (!this.disposed && this.state.review && unknownStates.includes(this.state.review.status) && original) {
      this.state.intent = original; this.state.uncertain = true;
    }
  }
  acknowledgeExit(confirmed: boolean): void {
    if (!this.canAct() || confirmed !== true || !(this.state.uncertain || this.state.review && unknownStates.includes(this.state.review.status))) return;
    this.state.exitAcknowledged = true; this.disarm();
    // Narrows only exit blocking. It does NOT forget, persist/erase UI data, reset unknown,
    // allow new previews/decisions, cancel IO or claim the original operation absent.
  }
  private maybeResume(): void { if (this.resumeRequested && !this.disposed && !this.flight && !this.readFlight) { this.state.closing = false; this.resumeRequested = false; } }
  async prepareClose(): Promise<void> {
    this.state.closing = true; this.resumeRequested = false; this.disarm(); this.epoch++; this.abort?.abort();
    await Promise.all([...(this.flight ? [this.flight] : []), ...(this.readFlight ? [this.readFlight] : [])]);
    if ((this.state.uncertain || this.state.review && unknownStates.includes(this.state.review.status)) && !this.state.exitAcknowledged) throw new Error("逆向操作结果未知；请只读恢复／元数据对账，或明确知悉保留原 ID 后退出。不能重跑。" );
    this.maybeResume();
  }
  finishClose(): void { this.resumeRequested = true; this.maybeResume(); }
  dispose(): void { this.disposed = true; this.disarm(); this.epoch++; this.abort?.abort(); this.state.active = false; this.state.closing = true; }
}
