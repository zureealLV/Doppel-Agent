// Source definitions first; execution/RED-GREEN/native acceptance deferred to S9.
import { describe, expect, it, vi } from "vitest";
import { HttpError } from "./api";
import { InverseReviewController } from "./inverseReview";
import { validInverseReview } from "./inverseFraming";
import type { InverseApi, InverseDecisionBody, InverseReview, InverseSource } from "./inverseTypes";
const run = "a".repeat(32), original = "b".repeat(32), reviewId = "c".repeat(32), inverse = "d".repeat(32);
const source: InverseSource = { run_id: run, tool_call_id: "patch-call", patch_id: original };
function view(status: InverseReview["status"] = "pending"): InverseReview {
  const projection = { unified_diff: "--- a/source.py\n+++ b/source.py\n@@ -1 +1 @@\n-user edit\n+accepted preimage\n",
    files: [{ path: "source.py", base_hash: "sha256:" + "e".repeat(64), base_mode: 420,
      target_hash: "sha256:" + "f".repeat(64), target_mode: 420, action: "restore" as const }] };
  return { review_id: reviewId, operation_kind: "manual_inverse", source: { ...source }, effect_tool_call_id: "manual-inverse:" + reviewId,
    patch_id: inverse, created_at: "2026-10-05T00:00:00+00:00", expires_at: "2026-10-05T00:15:00+00:00", status,
    review: projection, result: status === "applied" ? { patch_id: inverse, changed_paths: ["source.py"], unified_diff: projection.unified_diff,
      patch_receipt: { schema: 1, patch_id: inverse, status: "applied", files: [{ path: "source.py", base_hash: projection.files[0]!.base_hash,
        base_mode: 420, after_hash: projection.files[0]!.target_hash, after_mode: 420, outcome: "applied" }] },
      receipt_source: { run_id: run, tool_call_id: "manual-inverse:" + reviewId, origin: "manual_inverse", durability: "sealed_tool_ledger",
        source_tool_call_id: source.tool_call_id, source_patch_id: original } } : null,
    error_code: null, effect_replayed: false, decision_replayed: false,
    effect_evidence: status === "applied" ? "sealed_patch_ledger" : status === "applying" || status === "failed" || status === "indeterminate" ? "requires_patch_ledger_inspection" : "review_not_applied" };
}
function api(): InverseApi { return { prepare: vi.fn(async () => view()), read: vi.fn(async () => view()),
  decide: vi.fn(async (_run: string, _review: string, body: InverseDecisionBody) => view(body.action === "approve" ? "applied" : "rejected")), reconcile: vi.fn(async () => view("indeterminate")) }; }
function controller(backend = api()) { return new InverseReviewController(backend, () => reviewId, () => Date.parse("2026-10-05T00:01:00Z")); }
function deferred<T>() { let resolve!: (v: T) => void; return { promise: new Promise<T>(r => { resolve = r; }), resolve }; }

describe("exact manual inverse controller construction", () => {
  it("does not refresh immutable stored inverse projection or regress consumed status to pending through a later GET", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source); await c.prepare(true, true);
    const changed = view(); changed.review.files[0]!.base_hash = "sha256:" + "1".repeat(64);
    vi.mocked(backend.read).mockResolvedValueOnce(changed); await c.recover();
    expect(c.state.review?.review.files[0]?.base_hash).toBe("sha256:" + "e".repeat(64)); expect(c.state.armed).toBe(false);
    vi.mocked(backend.read).mockResolvedValueOnce(view("indeterminate")); await c.recover(); await c.recover();
    expect(c.state.review?.status).toBe("indeterminate"); expect(c.selectionLocked).toBe(true);
    c.armStored(true); await c.decide("approve", reviewId, inverse, true, true); expect(backend.decide).not.toHaveBeenCalled();
  });
  it("refuses mismatched applied origin/after hashes and private preimage fields, not a success label from status alone", () => {
    const wrongOrigin = view("applied"); wrongOrigin.result!.receipt_source.source_patch_id = "f".repeat(32);
    expect(validInverseReview(wrongOrigin, source, reviewId, inverse)).toBe(false);
    const wrongHash = view("applied"); wrongHash.result!.patch_receipt.files[0]!.after_hash = "sha256:" + "a".repeat(64);
    expect(validInverseReview(wrongHash, source, reviewId, inverse)).toBe(false);
    const privatePreview = view(); Object.assign(privatePreview.review.files[0]!, { before_content: "private" });
    expect(validInverseReview(privatePreview, source, reviewId)).toBe(false);
  });
  it("requires explicit discard of a known unsent preview before creating a different operation; no server erase", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source); await c.prepare(true, true);
    await c.prepare(true, true); expect(backend.prepare).toHaveBeenCalledTimes(1);
    c.discardPreview(false); expect(c.state.review).not.toBeNull(); c.discardPreview(true); expect(c.state.review).toBeNull();
    expect(backend.decide).not.toHaveBeenCalled(); expect(backend.reconcile).not.toHaveBeenCalled();
  });
  it("requires separate explicit prepare and fresh decision grants, pins immutable preview and never sends argv/raw preimages", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source);
    await c.prepare(false, true); await c.prepare(true, false); expect(backend.prepare).not.toHaveBeenCalled();
    await c.prepare(true, true); expect(backend.prepare).toHaveBeenCalledWith(run, { confirmed: true, source_tool_call_id: "patch-call", source_patch_id: original,
      operation_id: reviewId, workspace_write: true }); expect(Object.isFrozen(c.state.review?.review.files)).toBe(true);
    await c.decide("approve", reviewId, inverse, true, false); expect(backend.decide).not.toHaveBeenCalled();
    await c.decide("approve", "e".repeat(32), inverse, true, true); expect(backend.decide).not.toHaveBeenCalled();
    await c.decide("approve", reviewId, inverse, true, true);
    expect(backend.decide).toHaveBeenCalledWith(run, reviewId, { confirmed: true, patch_id: inverse, action: "approve", workspace_write: true });
    expect(c.state.review?.status).toBe("applied"); expect(c.state.effectEpoch).toBe(1);
    await c.decide("approve", reviewId, inverse, true, true); expect(backend.decide).toHaveBeenCalledTimes(1);
  });
  it("lost prepare retains exact ID/body and refuses new source/new preview; GET never prepares/approves and exact prepare retry is explicit", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source);
    vi.mocked(backend.prepare).mockRejectedValueOnce(new Error("secret transport")); await c.prepare(true, true);
    const intent = c.state.intent; expect(intent?.kind).toBe("prepare"); expect(c.state.uncertain).toBe(true);
    c.selectSource({ ...source, run_id: "f".repeat(32) }); expect(c.state.source?.run_id).toBe(run);
    await c.prepare(true, true); expect(backend.prepare).toHaveBeenCalledTimes(1);
    vi.mocked(backend.read).mockRejectedValueOnce(new HttpError(404, "not persisted yet")); await c.recover();
    expect(c.state.intent).toEqual(intent); expect(c.state.uncertain).toBe(true);
    await c.retryPrepare(false); expect(backend.prepare).toHaveBeenCalledTimes(1);
    await c.retryPrepare(true); expect(backend.prepare).toHaveBeenCalledTimes(2);
    expect(vi.mocked(backend.prepare).mock.calls[1]).toEqual(vi.mocked(backend.prepare).mock.calls[0]);
    expect(c.state.armed).toBe(false); expect(c.state.error).not.toContain("secret");
  });
  it("lost consumed decision is not safe to repeat even if GET is pending; terminal read resolves acknowledgement, not new permission", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source); await c.prepare(true, true);
    vi.mocked(backend.decide).mockRejectedValueOnce(new HttpError(503, "after actual effect")); await c.decide("approve", reviewId, inverse, true, true);
    const intent = c.state.intent; await c.recover(); expect(c.state.intent).toEqual(intent); expect(c.state.armed).toBe(false);
    c.armStored(true); await c.decide("approve", reviewId, inverse, true, true); expect(backend.decide).toHaveBeenCalledTimes(1);
    vi.mocked(backend.read).mockResolvedValueOnce(view("applied")); await c.recover();
    expect(c.state.uncertain).toBe(false); expect(c.state.review?.status).toBe("applied"); expect(c.state.armed).toBe(false);
  });
  it("GET recovery from prepare leaves approval disarmed; requires explicit stored-review confirmation with new write grant", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source);
    vi.mocked(backend.prepare).mockRejectedValueOnce(new Error("lost")); await c.prepare(true, true); await c.recover();
    expect(c.state.uncertain).toBe(false); expect(c.state.armed).toBe(false); expect(backend.decide).not.toHaveBeenCalled();
    await c.decide("approve", reviewId, inverse, true, true); expect(backend.decide).not.toHaveBeenCalled();
    c.armStored(true); await c.decide("approve", reviewId, inverse, true, true); expect(backend.decide).toHaveBeenCalledTimes(1);
  });
  it("indeterminate only offers explicit metadata reconcile, never effect replay; failure stays separate from partial patch effect", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source); await c.prepare(true, true);
    vi.mocked(backend.decide).mockRejectedValueOnce(new Error("lost")); await c.decide("approve", reviewId, inverse, true, true);
    vi.mocked(backend.read).mockResolvedValueOnce(view("indeterminate")); await c.recover();
    await c.reconcile(false); expect(backend.reconcile).not.toHaveBeenCalled(); await c.reconcile(true);
    expect(backend.reconcile).toHaveBeenCalledWith(run, reviewId, { confirmed: true });
    expect(c.selectionLocked).toBe(true); expect(backend.decide).toHaveBeenCalledTimes(1);
    vi.mocked(backend.read).mockResolvedValueOnce(view("failed")); await c.recover(); expect(c.state.review?.status).toBe("failed");
    expect(c.state.armed).toBe(false); expect(c.selectionLocked).toBe(false);
  });
  it("expiry/wrong source/malformed private preview refuses approval and never echoes exceptions", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source);
    vi.mocked(backend.prepare).mockResolvedValueOnce({ ...view(), source: { ...source, patch_id: "f".repeat(32) } }); await c.prepare(true, true);
    expect(c.state.review).toBeNull(); expect(c.state.uncertain).toBe(true); expect(c.state.error).not.toBe("");
    const expired = { ...view(), lifecycle: { stored_status: "pending", effective_status: "expired", pending_expired: true, approval_available: false, retry_available: false } } as InverseReview;
    vi.mocked(backend.read).mockResolvedValueOnce(expired); await c.recover(); c.armStored(true); expect(c.state.armed).toBe(false);
  });
  it("page departure disarms reads but retains the original owned effect reply; close cannot reopen before original flight settles", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source); await c.prepare(true, true);
    const wait = deferred<InverseReview>(); vi.mocked(backend.decide).mockReturnValueOnce(wait.promise);
    const effect = c.decide("approve", reviewId, inverse, true, true); c.activate(false);
    const close = c.prepareClose(); c.finishClose(); expect(c.state.closing).toBe(true);
    wait.resolve(view("applied")); await effect; await close; expect(c.state.review?.status).toBe("applied"); expect(c.state.closing).toBe(false);
  });
  it("page departure suppresses a late read, keeps the previous immutable preview and never re-arms on entry", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source); await c.prepare(true, true);
    const previous = c.state.review, wait = deferred<InverseReview>(); vi.mocked(backend.read).mockReturnValueOnce(wait.promise);
    const read = c.recover(); c.activate(false); wait.resolve(view("applied")); await read;
    expect(c.state.review).toBe(previous); expect(c.state.armed).toBe(false); c.activate(true);
    expect(backend.read).toHaveBeenCalledTimes(1); expect(backend.decide).not.toHaveBeenCalled();
  });
  it("unknown blocks close unless explicitly acknowledged; acknowledgement does not clear intent or allow another effect", async () => {
    const backend = api(), c = controller(backend); c.activate(true); c.selectSource(source);
    vi.mocked(backend.prepare).mockRejectedValueOnce(new Error("lost")); await c.prepare(true, true);
    await expect(c.prepareClose()).rejects.toThrow("未知"); c.finishClose(); const intent = c.state.intent;
    c.acknowledgeExit(true); await c.prepareClose(); expect(c.state.intent).toEqual(intent); expect(c.state.uncertain).toBe(true);
    c.finishClose(); await c.prepare(true, true); expect(backend.prepare).toHaveBeenCalledTimes(1);
  });
});
