// Construction definitions first. No execution/RED-GREEN until S9.
import { describe, expect, it, vi } from "vitest";
import { HttpError } from "./api";
import { VerificationReviewController } from "./verificationReview";
import { validVerificationReview, validVerificationPage } from "./verificationFraming";
import type { VerificationApi, VerificationReview, VerificationSource, VerificationDecisionBody, VerificationPage, VerificationSummary } from "./verificationTypes";
const run = "a".repeat(32), patch = "b".repeat(32), id = "c".repeat(32), planId = "d".repeat(64);
const source: VerificationSource = { run_id: run, tool_call_id: "patch-call", patch_id: patch };
function view(status: VerificationReview["status"] = "pending"): VerificationReview {
  const commands = [{ name: "unit", argv: ["python", "-m", "pytest", "tests/offline"], timeout_seconds: 120 },
    { name: "lint", argv: ["python", "-m", "ruff", "check", "."], timeout_seconds: 60 }];
  return { review_id: id, operation_id: "manual-verification:" + id, operation_kind: "manual_verification", source: { ...source },
    target: "current_workspace_not_original_patch_snapshot", plan: { schema: 1, plan_id: planId,
      source: { path: ".doppel/verification.json", config_hash: "e".repeat(64), snapshot_hash: "f".repeat(64), workspace_id: "1".repeat(64) },
      commands, max_output_bytes: 65536, stop_on_failure: true, operation_timeout_seconds: 600 },
    created_at: "2026-10-05T00:00:00+00:00", expires_at: "2026-10-05T00:15:00+00:00", status, error_code: null,
    success: status === "completed" ? true : null, steps: status === "completed" ? commands.map((command, index) => ({ index, status: "finished",
      result: { name: command.name, argv: [...command.argv], exit_code: 0, success: true, stdout: "fixture pass\n", stderr: "",
        duration_ms: 10, supervision: "windows_job", error: null } })) : [], has_unknown_command: false, decision_replayed: false,
    evidence: status === "completed" ? "sealed_verification_steps" : "not_completion_proof" };
}
function backend(): VerificationApi { return { prepare: vi.fn(async () => view()), read: vi.fn(async () => view()),
  list: vi.fn(async (): Promise<VerificationPage> => ({ items: [], has_more: false, next_after_id: null, budget_limited: false, decode_bytes: 0,
    sql_read_only: true, filesystem_zero_write_guarantee: false, order: "review_id_keyset_not_chronological" })),
  decide: vi.fn(async (_run: string, _id: string, body: VerificationDecisionBody) => view(body.action === "approve" ? "completed" : "rejected")),
  cancel: vi.fn(async () => view("cancelled")), reconcile: vi.fn(async () => view("indeterminate")) }; }
function controller(api = backend()) { const c = new VerificationReviewController(api, () => id, () => Date.parse("2026-10-05T00:01:00Z"));
  c.selectRun(run); c.selectSource(source); c.activate(true); return c; }
function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: unknown) => void;
  return { promise: new Promise<T>((yes, no) => { resolve = yes; reject = no; }), resolve, reject }; }
function summary(reviewId = id): VerificationSummary {
  const record = view("completed"), { plan: _plan, steps: _steps, decision_replayed: _replayed, ...item } = record;
  return { ...item, review_id: reviewId, operation_id: "manual-verification:" + reviewId,
    lifecycle: { stored_status: "completed", effective_status: "completed", pending_expired: false, pending_unexpired: false,
      approval_available: false, retry_available: false, planned_commands: 2, sealed_steps: 2, remaining_commands: 0,
      outcome_unknown: false, all_planned_attempts_sealed: true, commands_completed: true },
    provenance: { source_registration: "registered_run", source_run: { status: "completed", lease_active: false },
      patch_success: "not_inferred_from_verification", plan: "stored_exact_review_not_current_config_validation",
      output: "stored_sealed_attempts_not_whole_project_acceptance", cross_database_snapshot: "not_atomic" } };
}
function page(item = summary(), more = false): VerificationPage { return { items: [item], has_more: more, next_after_id: more ? item.review_id : null,
  budget_limited: more, decode_bytes: 2048, sql_read_only: true, filesystem_zero_write_guarantee: false, order: "review_id_keyset_not_chronological" }; }

describe("manual verification review construction", () => {
  it("pins the full immutable configured argv/plan and requires independent prepare and new approve grants", async () => {
    const api = backend(), c = controller(api);
    await c.prepare(null, true, false, true); await c.prepare(null, false, true, true); expect(api.prepare).not.toHaveBeenCalled();
    await c.prepare(null, true, true, true); expect(api.prepare).toHaveBeenCalledWith(run, { confirmed: true, source_tool_call_id: source.tool_call_id,
      source_patch_id: patch, operation_id: id, names: null, command_execute: true, workspace_write: true });
    expect(Object.isFrozen(c.state.review?.plan.commands[0]?.argv)).toBe(true);
    await c.decide("approve", id, planId, true, true, false); expect(api.decide).not.toHaveBeenCalled();
    await c.decide("approve", id, "e".repeat(64), true, true, true); expect(api.decide).not.toHaveBeenCalled();
    await c.decide("approve", id, planId, true, true, true); expect(api.decide).toHaveBeenCalledTimes(1);
    expect(c.state.review?.success).toBe(true); expect(c.state.effectEpoch).toBe(1);
    await c.decide("approve", id, planId, true, true, true); expect(api.decide).toHaveBeenCalledTimes(1);
  });
  it("checks explicit name selection, rejects empty/duplicate/oversized or injected argv, and never hashes with JS serialization", async () => {
    const api = backend(), c = controller(api); await c.prepare([], true, true, true); await c.prepare(["unit", "unit"], true, true, true);
    await c.prepare(["界".repeat(43)], true, true, true); expect(api.prepare).not.toHaveBeenCalled();
    vi.mocked(api.prepare).mockResolvedValueOnce(view()); await c.prepare(["lint", "unit"], true, true, true);
    expect(c.state.review).toBeNull(); expect(c.state.uncertain).toBe(true);
    const privatePlan = view(); Object.assign(privatePlan.plan, { executable: "unreviewed" });
    expect(validVerificationReview(privatePlan, run, id)).toBe(false);
    const number = view(); expect(validVerificationReview(number, run, id)).toBe(true); // 600.0 is 600 after JSON parsing; server checks canonical hash.
  });
  it("lost prepare keeps original names/ID/payload through 404 and only explicit original prepare replay; recovered preview disarms", async () => {
    const api = backend(), c = controller(api); vi.mocked(api.prepare).mockRejectedValueOnce(new Error("secret path"));
    await c.prepare(null, true, true, true); const intent = c.state.intent;
    c.selectRun("e".repeat(32)); expect(c.state.runId).toBe(run); await c.prepare(["unit"], true, true, true);
    expect(api.prepare).toHaveBeenCalledTimes(1); vi.mocked(api.read).mockRejectedValueOnce(new HttpError(404, "private"));
    await c.recover(); expect(c.state.intent).toEqual(intent); expect(c.state.uncertain).toBe(true);
    await c.retryPrepare(false); expect(api.prepare).toHaveBeenCalledTimes(1); await c.retryPrepare(true);
    expect(vi.mocked(api.prepare).mock.calls[1]).toEqual(vi.mocked(api.prepare).mock.calls[0]); expect(c.state.armed).toBe(false);
    await c.decide("approve", id, planId, true, true, true); expect(api.decide).not.toHaveBeenCalled();
    c.armStored(true); await c.decide("approve", id, planId, true, true, true); expect(api.decide).toHaveBeenCalledTimes(1);
  });
  it("pending GET cannot make a lost approve safe to resend; exact consumed operation stays sticky until terminal evidence", async () => {
    const api = backend(), c = controller(api); await c.prepare(null, true, true, true);
    vi.mocked(api.decide).mockRejectedValueOnce(new HttpError(503, "actual command ran")); await c.decide("approve", id, planId, true, true, true);
    const intent = c.state.intent; await c.recover(); expect(c.state.intent).toEqual(intent); expect(c.state.uncertain).toBe(true);
    c.armStored(true); await c.decide("approve", id, planId, true, true, true); expect(api.decide).toHaveBeenCalledTimes(1);
    vi.mocked(api.read).mockResolvedValueOnce(view("completed")); await c.recover(); expect(c.state.uncertain).toBe(false);
    expect(c.state.review?.status).toBe("completed"); expect(c.state.armed).toBe(false);
  });
  it("lost selected-name prepare refuses a different name order on GET and never arms mismatched config", async () => {
    const api = backend(), c = controller(api); vi.mocked(api.prepare).mockRejectedValueOnce(new Error("lost prepare"));
    await c.prepare(["lint", "unit"], true, true, true); const intent = c.state.intent; await c.recover();
    expect(c.state.intent).toEqual(intent); expect(c.state.uncertain).toBe(true); expect(c.state.review).toBeNull();
    expect(c.state.armed).toBe(false);
  });
  it("a completed main ACK cannot erase a separate lost cancel/drain acknowledgement; only an explicit current read narrows it", async () => {
    const api = backend(), c = controller(api); await c.prepare(null, true, true, true);
    const operation = deferred<VerificationReview>(); vi.mocked(api.decide).mockReturnValueOnce(operation.promise);
    const decision = c.decide("approve", id, planId, true, true, true);
    vi.mocked(api.cancel).mockRejectedValueOnce(new HttpError(503, "control failed after command completed")); await c.cancel(true);
    operation.resolve(view("completed")); await decision; expect(c.state.controlUncertain).toBe(true);
    expect(c.selectionLocked).toBe(true); expect(c.state.controlIntents[0]?.kind).toBe("cancel");
    vi.mocked(api.read).mockResolvedValueOnce(view("completed")); await c.recover(); expect(c.state.controlUncertain).toBe(false);
    expect(c.state.armed).toBe(false); expect(api.cancel).toHaveBeenCalledTimes(1);
  });
  it("timed-out close resumption admits only explicit exact cancellation while still joining original handles", async () => {
    const api = backend(), c = controller(api); await c.prepare(null, true, true, true);
    const operation = deferred<VerificationReview>(); vi.mocked(api.decide).mockReturnValueOnce(operation.promise);
    const decision = c.decide("approve", id, planId, true, true, true), close = c.prepareClose();
    expect(c.canCancel).toBe(false); c.finishClose(); expect(c.canCancel).toBe(true);
    await c.cancel(true); expect(c.state.closing).toBe(true); expect(c.state.busy).toBe(true);
    await c.prepare(null, true, true, true); expect(api.prepare).toHaveBeenCalledTimes(1);
    operation.reject(new HttpError(503, "cancel interrupted original")); await decision; await close;
    expect(c.state.closing).toBe(false); expect(c.state.review?.status).toBe("cancelled");
  });
  it("cancel can reach original live decision without abort/retry; both original promises must settle before close unfreezes", async () => {
    const api = backend(), c = controller(api); await c.prepare(null, true, true, true);
    const operation = deferred<VerificationReview>(), cancellation = deferred<VerificationReview>();
    vi.mocked(api.decide).mockReturnValueOnce(operation.promise); vi.mocked(api.cancel).mockReturnValueOnce(cancellation.promise);
    const decision = c.decide("approve", id, planId, true, true, true); expect(c.canCancel).toBe(true);
    const cancel = c.cancel(true); expect(api.cancel).toHaveBeenCalledWith(run, id, { confirmed: true, plan_id: planId });
    const close = c.prepareClose(); c.finishClose(); cancellation.resolve(view("cancelled")); await cancel;
    expect(c.state.closing).toBe(true); expect(c.state.busy).toBe(true);
    operation.reject(new HttpError(503, "cancel reached original task")); await decision; await close;
    expect(c.state.closing).toBe(false); expect(c.state.busy).toBe(false); expect(api.decide).toHaveBeenCalledTimes(1);
  });
  it("late pre-cancel GET cannot regress terminal evidence; cancel error retains original control payload, not permission to execute", async () => {
    const api = backend(), c = controller(api); await c.prepare(null, true, true, true);
    const read = deferred<VerificationReview>(); vi.mocked(api.read).mockReturnValueOnce(read.promise); const reading = c.recover();
    // Cancel fences but joins the original diagnostic read; no retrying any effect.
    await c.cancel(true); read.resolve(view()); await reading; expect(c.state.review?.status).toBe("cancelled");
    expect(c.state.armed).toBe(false);
    c.discardPreview(true); await c.prepare(null, true, true, true);
    vi.mocked(api.cancel).mockRejectedValueOnce(new HttpError(503, "lost drain receipt")); await c.cancel(true);
    expect(c.state.controlUncertain).toBe(true); expect(c.state.controlIntents[0]?.body).toEqual({ confirmed: true, plan_id: planId });
    await c.cancel(true); expect(api.cancel).toHaveBeenCalledTimes(2); // one prior known cancel, one lost; no repeat of lost cancel.
    await c.recover(); expect(c.state.controlUncertain).toBe(true); expect(c.state.armed).toBe(false);
  });
  it("stop-on-failure completed prefix is failed verification, not all planned attempts or patch/project success", () => {
    const prefix = view("completed"); prefix.steps = prefix.steps.slice(0, 1); prefix.steps[0]!.result!.exit_code = 1;
    prefix.steps[0]!.result!.success = false; prefix.success = false;
    expect(validVerificationReview(prefix, run, id)).toBe(true);
    prefix.success = true; expect(validVerificationReview(prefix, run, id)).toBe(false);
    const unknown = view("cancelled"); unknown.steps = [{ index: 0, status: "running", result: null }]; unknown.has_unknown_command = true;
    expect(validVerificationReview(unknown, run, id)).toBe(true);
  });
  it("cancelled with unsealed command stays unknown; explicit reconcile is metadata only, no rerun even after cancel", async () => {
    const api = backend(), c = controller(api); await c.prepare(null, true, true, true);
    const unknown = view("cancelled"); unknown.steps = [{ index: 0, status: "running", result: null }]; unknown.has_unknown_command = true;
    vi.mocked(api.cancel).mockResolvedValueOnce(unknown); await c.cancel(true); expect(c.selectionLocked).toBe(true);
    const close = c.prepareClose(); await expect(close).rejects.toThrow("未知"); c.finishClose(); c.acknowledgeExit(true);
    await c.prepareClose(); c.finishClose(); expect(c.selectionLocked).toBe(true); expect(c.state.exitAcknowledged).toBe(true);
    await c.decide("approve", id, planId, true, true, true); expect(api.decide).not.toHaveBeenCalled();
  });
  it("reconcile retains unknown control request and never changes config/executes; terminal stored GET is not a new grant", async () => {
    const api = backend(), c = controller(api); await c.prepare(null, true, true, true);
    vi.mocked(api.decide).mockRejectedValueOnce(new Error("lost")); await c.decide("approve", id, planId, true, true, true);
    vi.mocked(api.read).mockResolvedValueOnce(view("indeterminate")); await c.recover(); await c.reconcile(false);
    expect(api.reconcile).not.toHaveBeenCalled(); await c.reconcile(true);
    expect(api.reconcile).toHaveBeenCalledWith(run, id, { confirmed: true }); expect(c.selectionLocked).toBe(true);
    expect(c.state.intent?.kind).toBe("approve"); expect(api.prepare).toHaveBeenCalledTimes(1);
  });
  it("history is keyset scoped, bounded, readonly and not chronological; malformed or changed immutable plan refuses details", async () => {
    const api = backend(), c = controller(api); await c.loadHistory(); expect(api.list).toHaveBeenCalledWith(run, "", expect.any(AbortSignal));
    expect(c.state.history?.order).toBe("review_id_keyset_not_chronological"); expect(api.prepare).not.toHaveBeenCalled();
    const malformed = { items: [], has_more: true, next_after_id: id, budget_limited: false, decode_bytes: 0,
      sql_read_only: true, filesystem_zero_write_guarantee: false, order: "review_id_keyset_not_chronological" } as VerificationPage;
    expect(validVerificationPage(malformed, run, "")).toBe(false);
    await c.prepare(null, true, true, true); const changed = view(); changed.plan.commands[0]!.argv.push("--changed");
    vi.mocked(api.read).mockResolvedValueOnce(changed); await c.recover(); expect(c.state.error).not.toBe("");
    expect(c.state.review?.plan.commands[0]?.argv).not.toContain("--changed"); expect(c.state.armed).toBe(false);
  });
  it("pages advance/back with original review-ID cursors; summaries neither export argv nor bypass exact detail/grants", async () => {
    const api = backend(), c = controller(api), second = "e".repeat(32);
    vi.mocked(api.list).mockResolvedValueOnce(page(summary(), true)); await c.loadHistory();
    expect(c.state.historyStale).toBe(false); expect(c.state.history?.budget_limited).toBe(true);
    vi.mocked(api.list).mockResolvedValueOnce(page(summary(second))); await c.nextHistory();
    expect(vi.mocked(api.list).mock.calls[1]?.slice(0, 2)).toEqual([run, id]); expect(c.state.historyDepth).toBe(1);
    vi.mocked(api.list).mockResolvedValueOnce(page()); await c.previousHistory();
    expect(c.state.historyDepth).toBe(0); expect(vi.mocked(api.list).mock.calls[2]?.[1]).toBe("");
    vi.mocked(api.read).mockResolvedValueOnce(view("completed"));
    await c.readHistory(c.state.history!.items[0]!); expect(api.read).toHaveBeenCalledWith(run, id, expect.any(AbortSignal));
    expect(c.state.armed).toBe(false); expect(api.prepare).not.toHaveBeenCalled(); expect(api.decide).not.toHaveBeenCalled();
    const privateSummary = summary(); Object.assign(privateSummary, { stdout: "private" }); expect(validVerificationPage(page(privateSummary), run, "")).toBe(false);
    const foreign = summary(); foreign.source.run_id = second; expect(validVerificationPage(page(foreign), run, "")).toBe(false);
    const falseDenominator = summary(); falseDenominator.lifecycle!.sealed_steps = 0; falseDenominator.lifecycle!.remaining_commands = 2;
    expect(validVerificationPage(page(falseDenominator), run, "")).toBe(false);
  });
  it("already-observed consumed review cannot regress to fresh pending through GET/history, even without a lost local intent", async () => {
    const api = backend(), c = controller(api); vi.mocked(api.read).mockResolvedValueOnce(view("running"));
    await c.readExisting(id); expect(c.state.review?.status).toBe("running"); await c.recover();
    expect(c.state.review?.status).toBe("running"); expect(c.selectionLocked).toBe(true); c.armStored(true);
    await c.decide("approve", id, planId, true, true, true); expect(api.decide).not.toHaveBeenCalled();
    const historyApi = backend(), history = controller(historyApi); vi.mocked(historyApi.list).mockResolvedValueOnce(page()); await history.loadHistory();
    await history.readHistory(history.state.history!.items[0]!); expect(history.state.review).toBeNull();
    expect(history.state.error).not.toBe(""); expect(history.state.armed).toBe(false);
  });
  it("refuses result/step discrepancies, private fields and unsealed fake success rather than rendering a claimed green result", () => {
    const mismatch = view("completed"); mismatch.steps[0]!.result!.argv.push("--unreviewed"); expect(validVerificationReview(mismatch, run, id)).toBe(false);
    const fake = view("completed"); fake.steps[0]!.result!.exit_code = null; expect(validVerificationReview(fake, run, id)).toBe(false);
    const privateResult = view("completed"); Object.assign(privateResult.steps[0]!.result!, { token: "private" }); expect(validVerificationReview(privateResult, run, id)).toBe(false);
    const unsealed = view("completed"); unsealed.steps[1] = { index: 1, status: "running", result: null }; unsealed.has_unknown_command = true;
    expect(validVerificationReview(unsealed, run, id)).toBe(false);
  });
  it("page departure fences late readonly replies but original approval reply remains owned; no automatic reentry reads/grants", async () => {
    const api = backend(), c = controller(api); await c.prepare(null, true, true, true);
    const reading = deferred<VerificationReview>(); vi.mocked(api.read).mockReturnValueOnce(reading.promise); const read = c.recover();
    c.activate(false); reading.resolve(view("completed")); await read; expect(c.state.review?.status).toBe("pending");
    c.activate(true); expect(api.read).toHaveBeenCalledTimes(1); c.armStored(true);
    const operation = deferred<VerificationReview>(); vi.mocked(api.decide).mockReturnValueOnce(operation.promise);
    const decision = c.decide("approve", id, planId, true, true, true); c.activate(false);
    operation.resolve(view("completed")); await decision; expect(c.state.review?.status).toBe("completed"); expect(c.state.armed).toBe(false);
  });
});
