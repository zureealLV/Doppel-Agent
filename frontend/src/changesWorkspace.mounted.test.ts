// Construction definitions FIRST; unexecuted until S9. Synthetic Vue host,
// real compiled SFCs/controllers/watchers; NOT DOM/WebView2/OS/HTTP acceptance.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChangesWorkspace from "./components/ChangesWorkspace.vue";
import { changesApi } from "./changesApi";
import { verificationApi } from "./verificationApi";
import { inverseApi } from "./inverseApi";
import { ChangesController } from "./changes";
import { InverseReviewController } from "./inverseReview";
import { VerificationReviewController } from "./verificationReview";
import type { ChangesDesktopApi, GitStatus, MetadataAuthorization, PatchEvidence, PatchRow } from "./changesTypes";
import type { VerificationReview } from "./verificationTypes";
import { button, checkLabel, click, flushVue, mountVirtual, VirtualNode } from "./testSupport/virtualHost";

const run = "a".repeat(32), other = "e".repeat(32), patchId = "d".repeat(32), reviewId = "f".repeat(32), hash = "b".repeat(64);
const call = "actual-patch-call";
function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: unknown) => void;
  return { promise: new Promise<T>((yes, no) => { resolve = yes; reject = no; }), resolve, reject }; }
function status(): GitStatus { return { available: false, reason: "not_git_repository", repository_clean: null,
  agent_attribution: "not_inferred_from_repository_changes" }; }
function row(source = run): PatchRow { return { run_id: source, tool_call_id: call, tool_name: "propose_patch", operation_status: "completed",
  receipt: { schema: 1, patch_id: patchId, status: "applied", files: [{ path: "fixture.py", base_hash: "missing", base_mode: null,
    after_hash: "sha256:" + hash, after_mode: 420, outcome: "applied" }] }, confirmed_applied: true, preimage_state: "retained",
  created_at: "fixture", preimage_expired_at: null, evidence: "durable_patch_effect_not_current_git_ownership",
  source_run_quiescent: true, inverse_preparation_requires_fresh_file_check: true }; }
function evidence(source = run): PatchEvidence { const receipt = row(source).receipt;
  return { tool_name: "propose_patch", operation_status: "completed", confirmed_applied: true, receipt,
    result: { patch_id: patchId, changed_paths: ["fixture.py"], unified_diff: "fixture sensitive old text", patch_receipt: receipt },
    preimage_state: "retained", source: { run_id: source, tool_call_id: call }, source_run: { status: "completed", lease_active: false },
    evidence: "durable_patch_effect_not_current_git_ownership", output: { sensitive: true, globally_redacted: false, accepted_preimage_blobs_exported: false } }; }
function authorization(): MetadataAuthorization { return { active: false, grant_id: null, workspace: "D:/isolated-fixture", metadata_root: null,
  scope: "exact_linked_worktree_read_inspection_only", persistent: false, lifetime: "current_owner_until_revoke_binding_change_switch_or_close",
  command_or_workspace_write_grant: false }; }
function native(): ChangesDesktopApi { return { git_metadata_authorization: vi.fn(async () => ({ ok: true, authorization: authorization() })),
  git_metadata_authorize: vi.fn(async () => ({ ok: true, authorization: authorization() })),
  git_metadata_revoke: vi.fn(async () => ({ ok: true, authorization: authorization() })) }; }
function review(status: VerificationReview["status"] = "pending"): VerificationReview {
  const command = { name: "unit", argv: ["python", "-m", "pytest", "isolated-fixture"], timeout_seconds: 120 };
  return { review_id: reviewId, operation_id: "manual-verification:" + reviewId, operation_kind: "manual_verification",
    source: { run_id: run, tool_call_id: call, patch_id: patchId }, target: "current_workspace_not_original_patch_snapshot",
    plan: { schema: 1, plan_id: hash, source: { path: ".doppel/verification.json", config_hash: hash, snapshot_hash: hash, workspace_id: hash },
      commands: [command], max_output_bytes: 65536, stop_on_failure: true, operation_timeout_seconds: 600 },
    created_at: "2026-10-05T00:00:00+00:00", expires_at: "2026-10-05T00:15:00+00:00", status, error_code: null,
    success: status === "completed" ? true : null, steps: status === "completed" ? [{ index: 0, status: "finished",
      result: { name: command.name, argv: [...command.argv], exit_code: 0, success: true, stdout: "fixture sealed result", stderr: "",
        duration_ms: 1, supervision: "windows_job", error: null } }] : [], has_unknown_command: false, decision_replayed: false,
    evidence: status === "completed" ? "sealed_verification_steps" : "not_completion_proof" };
}
interface WorkspaceExposed { prepareClose(): Promise<void>; finishClose(): void; canChangeSource(): boolean }
let changes!: ChangesController, inverse!: InverseReviewController, verification!: VerificationReviewController;
let windowFixture: EventTarget & { pywebview?: { api: ChangesDesktopApi } };
const mounts: Array<{ unmount(): void }> = [];
function mount(props = { active: true, blocked: false, sourceRunId: run }) {
  const mounted = mountVirtual<WorkspaceExposed>(ChangesWorkspace, props); mounts.push(mounted); return mounted;
}
async function selectEvidence(root: VirtualNode) { await click(button(root, patchId)); await flushVue(); }
async function prepare(root: VirtualNode) {
  await checkLabel(root, "我确认该来源与命令名称范围");
  await checkLabel(root, "仅为准备请求提供新的 command_execute");
  await checkLabel(root, "仅为准备请求提供新的 workspace_write");
  await click(button(root, "准备新的精确命令预览")); await flushVue();
}
async function decisionGrants(root: VirtualNode) {
  await checkLabel(root, "我只决定上述精确 review/plan");
  await checkLabel(root, "为本次执行重新提供新的 command_execute");
  await checkLabel(root, "为本次执行重新提供新的 workspace_write");
}

beforeEach(() => {
  vi.stubGlobal("Document", class Document {}); vi.stubGlobal("ShadowRoot", class ShadowRoot {});
  windowFixture = new EventTarget(); vi.stubGlobal("window", windowFixture);
  vi.stubGlobal("crypto", { randomUUID: () => reviewId });
  vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-05T00:01:00Z"));
  vi.spyOn(changesApi, "status").mockImplementation(async () => status());
  vi.spyOn(changesApi, "patches").mockImplementation(async source => [row(source)]);
  vi.spyOn(changesApi, "evidence").mockImplementation(async source => evidence(source));
  vi.spyOn(changesApi, "diff").mockRejectedValue(new Error("unexpected diff in fixture"));
  vi.spyOn(verificationApi, "prepare").mockImplementation(async () => review());
  vi.spyOn(verificationApi, "decide").mockImplementation(async () => review("completed"));
  vi.spyOn(verificationApi, "read").mockImplementation(async () => review());
  vi.spyOn(verificationApi, "cancel").mockImplementation(async () => review("cancelled"));
  vi.spyOn(verificationApi, "reconcile").mockRejectedValue(new Error("unexpected reconcile"));
  vi.spyOn(verificationApi, "list").mockRejectedValue(new Error("unexpected history polling"));
  for (const method of ["prepare", "read", "decide", "reconcile"] as const)
    vi.spyOn(inverseApi, method).mockRejectedValue(new Error("unexpected inverse request"));
  const changesActivate = ChangesController.prototype.activate;
  vi.spyOn(ChangesController.prototype, "activate").mockImplementation(function (this: ChangesController, active, refresh) {
    changes = this; return changesActivate.call(this, active, refresh);
  });
  const inverseActivate = InverseReviewController.prototype.activate;
  vi.spyOn(InverseReviewController.prototype, "activate").mockImplementation(function (this: InverseReviewController, active) {
    inverse = this; return inverseActivate.call(this, active);
  });
  const verificationActivate = VerificationReviewController.prototype.activate;
  vi.spyOn(VerificationReviewController.prototype, "activate").mockImplementation(function (this: VerificationReviewController, active) {
    verification = this; return verificationActivate.call(this, active);
  });
});
afterEach(() => { for (const mounted of mounts.splice(0)) mounted.unmount(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Changes mounted source/lifecycle construction — virtual host only", () => {
  it("with an already-ready host initializes Git then scoped patches before the original native read", async () => {
    const host = native(), wait = deferred<Awaited<ReturnType<ChangesDesktopApi["git_metadata_authorization"]>>>();
    vi.mocked(host.git_metadata_authorization).mockReturnValueOnce(wait.promise); windowFixture.pywebview = { api: host };
    const mounted = mount(); await flushVue();
    expect(changesApi.status).toHaveBeenCalledTimes(1); expect(changesApi.patches).toHaveBeenCalledTimes(1);
    expect(changes.state.patchesLoaded).toBe(true); expect(host.git_metadata_authorization).toHaveBeenCalledTimes(1);
    expect(host.git_metadata_authorize).not.toHaveBeenCalled(); expect(host.git_metadata_revoke).not.toHaveBeenCalled();
    expect(button(mounted.root, "读取该来源补丁").props.disabled).toBe(true);
    wait.resolve({ ok: true, authorization: authorization() }); await flushVue(); expect(changes.state.authorization?.active).toBe(false);
  });
  it("late bridge-ready during initial Git reading does not preempt or abandon the ordered source load", async () => {
    const wait = deferred<GitStatus>(); vi.mocked(changesApi.status).mockReturnValueOnce(wait.promise);
    mount(); await flushVue(); const host = native(); windowFixture.pywebview = { api: host };
    windowFixture.dispatchEvent(new Event("pywebviewready")); await flushVue();
    expect(host.git_metadata_authorization).not.toHaveBeenCalled(); expect(changesApi.patches).not.toHaveBeenCalled();
    wait.resolve(status()); await flushVue(); expect(changesApi.patches).toHaveBeenCalledTimes(1);
    expect(host.git_metadata_authorization).toHaveBeenCalledTimes(1); expect(host.git_metadata_authorize).not.toHaveBeenCalled();
  });
  it("mounting blocked starts no Git/patch/native read; unblocking activates the original ordered chain", async () => {
    const host = native(); windowFixture.pywebview = { api: host };
    const mounted = mount({ active: true, blocked: true, sourceRunId: run }); await flushVue();
    expect(changesApi.status).not.toHaveBeenCalled(); expect(changesApi.patches).not.toHaveBeenCalled();
    expect(host.git_metadata_authorization).not.toHaveBeenCalled(); expect(verification.state.active).toBe(false);
    mounted.props.blocked = false; await flushVue(); expect(changesApi.status).toHaveBeenCalledTimes(1);
    expect(changesApi.patches).toHaveBeenCalledTimes(1); expect(host.git_metadata_authorization).toHaveBeenCalledTimes(1);
  });
  it("blocking the page fences an original read and its continuation, without claiming server drain", async () => {
    const wait = deferred<GitStatus>(); vi.mocked(changesApi.status).mockReturnValueOnce(wait.promise);
    const mounted = mount(); await flushVue(); const originalSignal = vi.mocked(changesApi.status).mock.calls[0]![0]!;
    mounted.props.blocked = true; await flushVue(); expect(originalSignal.aborted).toBe(true);
    wait.resolve(status()); await flushVue(); expect(changes.state.status).toBeNull(); expect(changesApi.patches).not.toHaveBeenCalled();
    mounted.props.blocked = false; await flushVue(); expect(changesApi.status).toHaveBeenCalledTimes(2);
    expect(changesApi.patches).toHaveBeenCalledTimes(1);
  });
  it("late evidence for a replaced scope cannot become either review's source", async () => {
    const mounted = mount(); await flushVue(); const wait = deferred<PatchEvidence>(); vi.mocked(changesApi.evidence).mockReturnValueOnce(wait.promise);
    const original = Promise.resolve(click(button(mounted.root, patchId))); await flushVue();
    mounted.props.sourceRunId = other; await flushVue(); wait.resolve(evidence()); await original; await flushVue();
    expect(changes.state.sourceRunId).toBe(other); expect(changes.state.evidence).toBeNull();
    expect(inverse.state.source).toBeNull(); expect(verification.state.source).toBeNull();
    expect(verification.state.runId).toBe(other); expect(verificationApi.prepare).not.toHaveBeenCalled();
  });
  it("incoming source while transition-blocked cannot replace evidence, and is adopted only after unblocking", async () => {
    const mounted = mount(); await flushVue(); await selectEvidence(mounted.root);
    mounted.props.blocked = true; await flushVue(); mounted.props.sourceRunId = other; await flushVue();
    expect(changes.state.sourceRunId).toBe(run); expect(verification.state.runId).toBe(run);
    expect(verification.state.source?.patch_id).toBe(patchId);
    const reads = vi.mocked(changesApi.patches).mock.calls.length;
    mounted.props.blocked = false; await flushVue(); expect(changes.state.sourceRunId).toBe(other);
    expect(verification.state.runId).toBe(other); expect(verification.state.source).toBeNull();
    expect(changesApi.patches).toHaveBeenCalledTimes(reads + 1);
  });
  it("one actual framed patch evidence feeds both separate controllers; private/wrong-scope evidence feeds neither", async () => {
    const mounted = mount(); await flushVue(); await selectEvidence(mounted.root);
    const expected = { run_id: run, tool_call_id: call, patch_id: patchId };
    expect(inverse.state.source).toEqual(expected); expect(verification.state.source).toEqual(expected);
    mounted.props.sourceRunId = other; await flushVue();
    const privateEvidence = evidence(other); Object.assign(privateEvidence.receipt!.files[0]!, { before_content: "must not export" });
    vi.mocked(changesApi.evidence).mockResolvedValueOnce(privateEvidence); await selectEvidence(mounted.root);
    expect(inverse.state.source).toBeNull(); expect(verification.state.source).toBeNull();
    vi.mocked(changesApi.evidence).mockResolvedValueOnce(evidence(run)); await selectEvidence(mounted.root);
    expect(changes.state.evidence).toBeNull(); expect(verificationApi.prepare).not.toHaveBeenCalled();
  });
  it("synthetic mounted checkbox inputs require independent new decision grants; blocked inputs consume old consent", async () => {
    const mounted = mount(); await flushVue(); await selectEvidence(mounted.root); await prepare(mounted.root);
    expect(verificationApi.prepare).toHaveBeenCalledWith(run, { confirmed: true, source_tool_call_id: call, source_patch_id: patchId,
      operation_id: reviewId, names: null, command_execute: true, workspace_write: true });
    expect(button(mounted.root, "批准执行这份精确计划").props.disabled).toBe(true);
    await decisionGrants(mounted.root); expect(button(mounted.root, "批准执行这份精确计划").props.disabled).toBe(false);
    mounted.props.blocked = true; await flushVue(); mounted.props.blocked = false; await flushVue();
    expect(button(mounted.root, "批准执行这份精确计划").props.disabled).toBe(true);
    expect(verification.state.armed).toBe(false); expect(verificationApi.decide).not.toHaveBeenCalled();
    expect(inverseApi.prepare).not.toHaveBeenCalled(); // Verification does not inherit or send an inverse grant.
  });
  it("a lost approve locks incoming source and cross-panel/native/Git controls; reentry does not retry/read/grant", async () => {
    const host = native(); windowFixture.pywebview = { api: host };
    const mounted = mount(); await flushVue(); await selectEvidence(mounted.root); await prepare(mounted.root); await decisionGrants(mounted.root);
    vi.mocked(verificationApi.decide).mockRejectedValueOnce(new Error("SECRET actual command may have run"));
    await click(button(mounted.root, "批准执行这份精确计划")); await flushVue();
    const originalIntent = verification.state.intent, statusReads = vi.mocked(changesApi.status).mock.calls.length;
    const nativeReads = vi.mocked(host.git_metadata_authorization).mock.calls.length;
    mounted.props.sourceRunId = other; await flushVue(); expect(changes.state.sourceRunId).toBe(run);
    expect(verification.state.intent).toEqual(originalIntent); expect(mounted.exposed().canChangeSource()).toBe(false);
    for (const label of ["显式刷新 Git 快照", "只读当前原生授权", "原生选择并确认授权", "读取该来源补丁"])
      expect(button(mounted.root, label).props.disabled).toBe(true);
    expect(changes.state.stale).toBe(true); expect(changes.state.status).toBeNull();
    mounted.props.active = false; await flushVue(); mounted.props.active = true; await flushVue();
    expect(changesApi.status).toHaveBeenCalledTimes(statusReads); expect(host.git_metadata_authorization).toHaveBeenCalledTimes(nativeReads);
    expect(verificationApi.decide).toHaveBeenCalledTimes(1); expect(verificationApi.read).not.toHaveBeenCalled();
    expect(verificationApi.list).not.toHaveBeenCalled(); expect(host.git_metadata_authorize).not.toHaveBeenCalled();
    expect(verification.state.error).not.toContain("SECRET"); expect(inverseApi.prepare).not.toHaveBeenCalled();
  });
  it("the mounted cancel control reaches the original live approval while close still joins original promises", async () => {
    const mounted = mount(); await flushVue(); await selectEvidence(mounted.root); await prepare(mounted.root); await decisionGrants(mounted.root);
    const approve = deferred<VerificationReview>(), cancel = deferred<VerificationReview>();
    vi.mocked(verificationApi.decide).mockReturnValueOnce(approve.promise); vi.mocked(verificationApi.cancel).mockReturnValueOnce(cancel.promise);
    const originalApproval = Promise.resolve(click(button(mounted.root, "批准执行这份精确计划"))); await flushVue();
    // Use the live panel's actual compiler handler, not a simulated OS click.
    await checkLabel(mounted.root, "我明确取消同一 review/plan");
    const cancelButton = button(mounted.root, "取消原操作并等待排空回执");
    const originalCancel = Promise.resolve(click(cancelButton)); await flushVue();
    expect(verificationApi.cancel).toHaveBeenCalledWith(run, reviewId, { confirmed: true, plan_id: hash });
    let finished = false; const close = mounted.exposed().prepareClose().then(() => { finished = true; }); await flushVue();
    expect(finished).toBe(false); approve.resolve(review("completed")); await originalApproval; await flushVue();
    expect(finished).toBe(false); cancel.resolve(review("completed")); await originalCancel; await close;
    expect(verificationApi.decide).toHaveBeenCalledTimes(1); expect(verificationApi.cancel).toHaveBeenCalledTimes(1);
    expect(changes.state.closing).toBe(true); mounted.exposed().finishClose(); await flushVue(); expect(changes.state.closing).toBe(false);
  });
  it("a superseded nested close hook cannot reach the later verification/read barriers", async () => {
    const mounted = mount(); await flushVue(); const first = deferred<void>();
    vi.spyOn(inverse, "prepareClose").mockReturnValueOnce(first.promise);
    const verificationClose = vi.spyOn(verification, "prepareClose"), changesClose = vi.spyOn(changes, "prepareClose");
    const close = mounted.exposed().prepareClose().then(() => null, error => error); await flushVue();
    mounted.exposed().finishClose(); first.resolve(); const result = await close;
    expect(result).toBeInstanceOf(Error); expect(verificationClose).not.toHaveBeenCalled(); expect(changesClose).not.toHaveBeenCalled();
  });
  it("unmount/fresh project mount has no inherited review/grants/native descriptor; late old prepare cannot publish", async () => {
    const mounted = mount(); await flushVue(); await selectEvidence(mounted.root); const wait = deferred<VerificationReview>();
    vi.mocked(verificationApi.prepare).mockReturnValueOnce(wait.promise); const old = verification;
    // Controller dispatch here isolates old-flight fencing; checkbox dispatch is covered above.
    const request = old.prepare(null, true, true, true); await flushVue(); mounted.unmount();
    const fresh = mount({ active: true, blocked: false, sourceRunId: other }); await flushVue();
    expect(verification).not.toBe(old); expect(verification.state.runId).toBe(other); expect(verification.state.intent).toBeNull();
    expect(verification.state.source).toBeNull(); expect(verification.state.review).toBeNull(); expect(changes.state.authorization).toBeNull();
    wait.resolve(review()); await request; await flushVue(); expect(old.state.review).toBeNull();
    expect(verification.state.review).toBeNull(); expect(fresh.root.textContent).not.toContain(reviewId);
    expect(verificationApi.decide).not.toHaveBeenCalled(); // No assertion about physical old backend drain.
  });
});
