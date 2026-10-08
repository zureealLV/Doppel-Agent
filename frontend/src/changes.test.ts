// Construction definitions only. No executed TDD/native/HTTP acceptance is claimed.
import { describe, expect, it, vi } from "vitest";
import { ChangesController } from "./changes";
import { HttpError } from "./api";
import type { ChangesApi, DiffSelection, GitDiff, GitStatus, GitAvailableStatus, MetadataAuthorization, ChangesDesktopApi, PatchRow, PatchEvidence } from "./changesTypes";

const run = "a".repeat(32), call = "actual-tool-call", fp = "b".repeat(64);
function status(): GitAvailableStatus {
  return { available: true, reason: null, head_id: "c".repeat(40), head_ref: "refs/heads/main", object_format: "sha1", ref_storage: "files",
    index_sha256: fp, refs_sha256: fp, linked_worktree: false, rows: [{ path: "src/<not-markup>.py", head: null, index: null,
      stages: [], staged: "none", worktree: "untracked", worktree_sha256: fp, worktree_blob_oid: "c".repeat(40), worktree_mode: "100644",
      mode_comparison: "not_applied_on_windows", reason: null, agent_generated: null }], excluded_count: 2, unknown_count: 0,
    walked_entries: 5, coverage_reasons: [], policy_sha256: fp, comparison: "raw_workspace_no_filters_or_eol_conversion",
    repository_clean: null, agent_attribution: "not_inferred_from_repository_changes", fingerprint: fp, captured_at: "fixture" };
}
function patch(): PatchRow {
  return { run_id: run, tool_call_id: call, tool_name: "propose_patch", operation_status: "completed", created_at: "fixture",
    receipt: { schema: 1, patch_id: "d".repeat(32), status: "applied", files: [{ path: "other.py", base_hash: "missing", base_mode: null,
      after_hash: "sha256:" + fp, after_mode: 420, outcome: "applied" }] }, preimage_state: "retained", preimage_expired_at: null,
    confirmed_applied: true, evidence: "durable_patch_effect_not_current_git_ownership", source_run_quiescent: true,
    inverse_preparation_requires_fresh_file_check: true };
}
function api(): ChangesApi {
  return { status: vi.fn(async () => status()), diff: vi.fn(async (selection: DiffSelection): Promise<GitDiff> => ({ available: true, reason: null, ...selection,
    fingerprint: selection.expected_fingerprint, before_sha256: null, after_sha256: fp, before_exists: false, after_exists: true,
    before_mode: null, after_mode: "100644", text: "<script>literal diff</script>", comparison: "raw_workspace_no_filters_or_eol_conversion", agent_generated: null })),
    patches: vi.fn(async () => [patch()]), evidence: vi.fn(async () => ({ tool_name: "propose_patch", operation_status: "completed",
      confirmed_applied: true, receipt: patch().receipt, result: { patch_id: patch().receipt.patch_id, changed_paths: ["other.py"],
        unified_diff: "<script>stored secret fixture</script>", patch_receipt: patch().receipt }, preimage_state: "retained",
      source: { run_id: run, tool_call_id: call }, source_run: { status: "completed", lease_active: false },
      evidence: "durable_patch_effect_not_current_git_ownership", output: { sensitive: true, globally_redacted: false, accepted_preimage_blobs_exported: false } } satisfies PatchEvidence)) };
}
function deferred<T>() { let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  return { promise: new Promise<T>((yes, no) => { resolve = yes; reject = no; }), resolve, reject }; }
function authorization(active = false): MetadataAuthorization {
  return { active, grant_id: active ? "e".repeat(32) : null, workspace: "D:/fixture", metadata_root: active ? "D:/metadata" : null,
    scope: "exact_linked_worktree_read_inspection_only", persistent: false, lifetime: "current_owner_until_revoke_binding_change_switch_or_close",
    command_or_workspace_write_grant: false };
}
function native(): ChangesDesktopApi { return { git_metadata_authorization: vi.fn(async () => ({ ok: true, authorization: authorization() })),
  git_metadata_authorize: vi.fn(async () => ({ ok: true, authorization: authorization(true) })),
  git_metadata_revoke: vi.fn(async () => ({ ok: true, authorization: authorization() })) }; }

describe("Changes read controller construction, not Git/native proof", () => {
  it("can reenter presentation without auto Git/native IO while another reviewed operation is locked", async () => {
    const backend = api(), controller = new ChangesController(backend); await controller.activate(true, false);
    expect(controller.state.active).toBe(true); expect(backend.status).not.toHaveBeenCalled();
    await controller.refresh(); expect(backend.status).toHaveBeenCalledTimes(1);
  });
  it("starts without fake status/cleanliness, reads only on entry and never infers receipt authorship from Git", async () => {
    const backend = api(), controller = new ChangesController(backend);
    expect(controller.state.status).toBeNull(); expect(backend.status).not.toHaveBeenCalled();
    controller.setSource(run); await controller.activate(true);
    expect(controller.state.patches[0]?.receipt.files[0]?.path).toBe("other.py");
    expect(controller.state.status?.repository_clean).toBeNull();
    expect(controller.state.sourceRunId).toBe(run);
  });
  it("pins admitted path/plane/stage/fingerprint, refuses arbitrary paths/stages and clears diff before refresh", async () => {
    const backend = api(), controller = new ChangesController(backend); await controller.activate(true);
    await controller.selectDiff("outside.py", "combined", null); expect(backend.diff).not.toHaveBeenCalled();
    await controller.selectDiff(status().rows[0]!.path, "staged", 2); expect(backend.diff).not.toHaveBeenCalled();
    await controller.selectDiff(status().rows[0]!.path, "combined", null);
    expect(vi.mocked(backend.diff).mock.calls[0]![0]).toEqual({ path: status().rows[0]!.path, plane: "combined", conflict_stage: null, expected_fingerprint: fp });
    const wait = deferred<GitStatus>(); vi.mocked(backend.status).mockReturnValueOnce(wait.promise);
    const refresh = controller.refresh(); expect(controller.state.diff).toBeNull(); expect(controller.state.status).toBeNull();
    wait.resolve(status()); await refresh;
  });
  it("unavailable and stale fingerprints do not become clean/empty-success or auto-refresh/retry", async () => {
    const backend = api(), controller = new ChangesController(backend); await controller.activate(true);
    vi.mocked(backend.diff).mockResolvedValueOnce({ available: false, reason: "stale_git_inspection", repository_clean: null, agent_attribution: "not_inferred_from_repository_changes" });
    await controller.selectDiff(status().rows[0]!.path, "worktree", null);
    expect(controller.state.stale).toBe(true); expect(controller.state.diff?.available).toBe(false);
    expect(backend.status).toHaveBeenCalledTimes(1); expect(backend.diff).toHaveBeenCalledTimes(1);
    vi.mocked(backend.status).mockResolvedValueOnce({ available: false, reason: "git_inspection_unavailable", repository_clean: null, agent_attribution: "not_inferred_from_repository_changes" });
    await controller.refresh(); expect(controller.state.status?.available).toBe(false); expect(controller.state.diff).toBeNull();
  });
  it("fences inactive/source-replaced late responses and does not persist sensitive evidence", async () => {
    const backend = api(), controller = new ChangesController(backend); await controller.activate(true); controller.setSource(run); await controller.loadPatches();
    const wait = deferred<PatchEvidence>(); vi.mocked(backend.evidence).mockReturnValueOnce(wait.promise);
    const read = controller.openEvidence(call); const old = await api().evidence(run, call);
    controller.setSource("f".repeat(32)); wait.resolve(old); await read;
    expect(controller.state.evidence).toBeNull(); expect(controller.state.patches).toEqual([]);
    const late = deferred<GitStatus>(); vi.mocked(backend.status).mockReturnValueOnce(late.promise);
    const refresh = controller.refresh(); await controller.activate(false); late.resolve(status()); await refresh;
    expect(controller.state.status).toBeNull(); expect(controller.state.stale).toBe(true);
  });
  it("rejects wrong-scope patch details and keeps only fixed errors, never raw provider/path messages", async () => {
    const backend = api(), controller = new ChangesController(backend); controller.setSource(run); await controller.activate(true);
    vi.mocked(backend.evidence).mockResolvedValueOnce({ ...await api().evidence(run, call), source: { run_id: "f".repeat(32), tool_call_id: call } });
    await controller.openEvidence(call); expect(controller.state.evidence).toBeNull();
    vi.mocked(backend.status).mockRejectedValueOnce(new HttpError(503, "SECRET-D:/private")); await controller.refresh();
    expect(controller.state.error).not.toContain("SECRET"); expect(controller.state.error).not.toContain("private");
  });
  it("keeps scoped historical reads usable when Git is unavailable, not falsely empty on query failure", async () => {
    const backend = api(), controller = new ChangesController(backend); controller.setSource(run);
    vi.mocked(backend.status).mockResolvedValueOnce({ available: false, reason: "git_executable_unavailable", repository_clean: null, agent_attribution: "not_inferred_from_repository_changes" });
    await controller.activate(true); expect(controller.state.status?.available).toBe(false); expect(controller.state.patchesLoaded).toBe(true);
    await controller.openEvidence(call); expect(controller.state.evidence?.confirmed_applied).toBe(true);
    vi.mocked(backend.patches).mockRejectedValueOnce(new HttpError(404, "raw-private-source")); await controller.loadPatches();
    expect(controller.state.patchesLoaded).toBe(false); expect(controller.state.evidenceError).not.toContain("raw-private-source");
  });
  it("rejects a mismatched fingerprint and actual conflict selection requires an admitted stage", async () => {
    const backend = api(), controller = new ChangesController(backend);
    const conflicted = status(); conflicted.rows[0]!.staged = "unmerged";
    conflicted.rows[0]!.stages = [{ path: conflicted.rows[0]!.path, mode: "100644", object_id: "c".repeat(40), kind: "blob", stage: 2 }];
    vi.mocked(backend.status).mockResolvedValueOnce(conflicted); await controller.activate(true);
    await controller.selectDiff(conflicted.rows[0]!.path, "worktree", null); expect(backend.diff).not.toHaveBeenCalled();
    await controller.selectDiff(conflicted.rows[0]!.path, "worktree", 2); expect(backend.diff).toHaveBeenCalledTimes(1);
    const wrong = await api().diff({ path: conflicted.rows[0]!.path, plane: "combined", conflict_stage: null, expected_fingerprint: "f".repeat(64) });
    vi.mocked(backend.diff).mockResolvedValueOnce(wrong); await controller.selectDiff(conflicted.rows[0]!.path, "combined", null);
    expect(controller.state.diff).toBeNull(); expect(controller.state.error).not.toBe(""); expect(controller.state.stale).toBe(true);
  });
  it("compares valid stored receipts structurally without JSON member-order assumptions and rejects exported preimages", async () => {
    const backend = api(), controller = new ChangesController(backend); controller.setSource(run); await controller.activate(true);
    const detail = await api().evidence(run, call), receipt = detail.receipt!;
    detail.result!.patch_receipt = { files: receipt.files.map(file => ({ outcome: file.outcome, after_mode: file.after_mode, path: file.path,
      base_hash: file.base_hash, base_mode: file.base_mode, after_hash: file.after_hash })), status: receipt.status, patch_id: receipt.patch_id, schema: 1 };
    vi.mocked(backend.evidence).mockResolvedValueOnce(detail); await controller.openEvidence(call); expect(controller.state.evidence).not.toBeNull();
    const privateDetail = await api().evidence(run, call);
    Object.assign(privateDetail.receipt!.files[0]!, { before_content: "must-not-export" });
    vi.mocked(backend.evidence).mockResolvedValueOnce(privateDetail); await controller.openEvidence(call); expect(controller.state.evidence).toBeNull();
  });
  it("aborts read presentation on close but waits its original promise and does not claim backend command drain", async () => {
    const backend = api(), controller = new ChangesController(backend); await controller.activate(true);
    const wait = deferred<GitStatus>(); vi.mocked(backend.status).mockReturnValueOnce(wait.promise);
    const read = controller.refresh(); const signal = vi.mocked(backend.status).mock.calls.at(-1)![0]!;
    let closed = false; const close = controller.prepareClose().then(() => { closed = true; });
    expect(signal.aborted).toBe(true); expect(closed).toBe(false);
    controller.finishClose(); expect(controller.state.closing).toBe(true);
    wait.resolve(status()); await read; await close; expect(controller.state.status).toBeNull(); expect(controller.state.closing).toBe(false);
  });
});

describe("Changes zero-argument native metadata controls construction", () => {
  it("readiness is not a grant; browser/missing host cannot authorize; actual bridge calls have no JS path/bool", async () => {
    const controller = new ChangesController(api()), host = native();
    await controller.authorize(); expect(controller.state.authorization).toBeNull();
    controller.attach(host); expect(controller.state.authorization).toBeNull(); await controller.activate(true);
    await controller.authorize(); expect(host.git_metadata_authorize).toHaveBeenCalledWith();
    expect(controller.state.authorization?.active).toBe(true); expect(controller.state.authorization?.persistent).toBe(false);
  });
  it("lost grant reply is unknown, prevents repeated grant/revoke and recovers only by read, never auto-grants", async () => {
    const controller = new ChangesController(api()), host = native(); controller.attach(host); await controller.activate(true);
    vi.mocked(host.git_metadata_authorize).mockRejectedValueOnce(new Error("secret native exception")); await controller.authorize();
    expect(controller.state.authorizationUnknown).toBe(true); await controller.authorize(); await controller.revoke();
    expect(host.git_metadata_authorize).toHaveBeenCalledTimes(1); expect(host.git_metadata_revoke).not.toHaveBeenCalled();
    await controller.refreshAuthorization(); expect(controller.state.authorizationUnknown).toBe(false);
  });
  it("native cancellation does not manufacture revocation; malformed/read-failed authority stays unknown", async () => {
    const controller = new ChangesController(api()), host = native(); controller.attach(host); await controller.activate(true);
    vi.mocked(host.git_metadata_authorize).mockResolvedValueOnce({ ok: true, cancelled: true }); await controller.authorize();
    expect(controller.state.authorization).toBeNull(); expect(controller.state.authorizationUnknown).toBe(true);
    vi.mocked(host.git_metadata_authorization).mockRejectedValueOnce(new Error("native key secret")); await controller.refreshAuthorization();
    expect(controller.state.authorizationUnknown).toBe(true); expect(controller.state.nativeError).not.toContain("key secret");
    vi.mocked(host.git_metadata_authorization).mockResolvedValueOnce({ ok: true, authorization: { ...authorization(true), persistent: true } as unknown as MetadataAuthorization });
    await controller.refreshAuthorization(); expect(controller.state.authorization).toBeNull();
  });
  it("close drains the original non-abortable dialog; preparation timeout recovery cannot reopen controls early", async () => {
    const controller = new ChangesController(api()), host = native(); controller.attach(host); await controller.activate(true);
    const wait = deferred<{ ok: boolean; authorization: MetadataAuthorization }>(); vi.mocked(host.git_metadata_authorize).mockReturnValueOnce(wait.promise);
    const grant = controller.authorize(); let drained = false; const close = controller.prepareClose().then(() => { drained = true; });
    controller.finishClose(); expect(controller.state.closing).toBe(true); expect(drained).toBe(false);
    wait.resolve({ ok: true, authorization: authorization(true) }); await grant; await close;
    expect(controller.state.authorization).toBeNull(); expect(controller.state.authorizationUnknown).toBe(true);
    expect(controller.state.closing).toBe(false); await controller.refreshAuthorization();
    expect(host.git_metadata_authorize).toHaveBeenCalledTimes(1);
  });
  it("bridge replacement or page departure fences late grants and requires reading the current host", async () => {
    const controller = new ChangesController(api()), host = native(); controller.attach(host); await controller.activate(true);
    const wait = deferred<{ ok: boolean; authorization: MetadataAuthorization }>(); vi.mocked(host.git_metadata_authorize).mockReturnValueOnce(wait.promise);
    const grant = controller.authorize(); controller.attach(native()); wait.resolve({ ok: true, authorization: authorization(true) }); await grant;
    expect(controller.state.authorization).toBeNull(); expect(controller.state.authorizationUnknown).toBe(true);
    await controller.refreshAuthorization(); expect(controller.state.authorizationUnknown).toBe(false);
  });
});
