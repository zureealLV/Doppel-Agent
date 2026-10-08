import { reactive } from "vue";
import { HttpError } from "./api";
import { changesApi, PATCH_PAGE_SIZE, inspectionFingerprint, relativeChangesPath, runIdentifier, toolCallIdentifier, validDiffSelection } from "./changesApi";
import type { ChangesApi, ChangesDesktopApi, ConflictStage, DiffPlane, DiffSelection, GitStatus, GitDiff, MetadataAuthorization, PatchReceipt, PatchRow, PatchEvidence } from "./changesTypes";

const comparison = "raw_workspace_no_filters_or_eol_conversion";
const attribution = "durable_patch_effect_not_current_git_ownership";
const nullableHash = (value: unknown) => value === null || typeof value === "string" && inspectionFingerprint(value);
const nullableGitMode = (value: unknown) => value === null || typeof value === "string" && /^[0-7]{6}$/.test(value);
const sameKeys = (value: object, keys: string[]) => Object.keys(value).sort().join("|") === keys.sort().join("|");
const hashOrMissing = (value: unknown) => typeof value === "string" && (value === "missing" || /^sha256:[0-9a-f]{64}$/.test(value));
const mode = (value: unknown) => value === null || typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 0o777;
function validReceipt(value: PatchReceipt | null): value is PatchReceipt {
  return !!value && sameKeys(value, ["schema", "patch_id", "status", "files"]) && value.schema === 1
    && runIdentifier(value.patch_id) && ["prepared", "applied", "failed"].includes(value.status)
    && Array.isArray(value.files) && value.files.length > 0 && value.files.length <= 32
    && value.files.every(file => file && sameKeys(file, ["path", "base_hash", "base_mode", "after_hash", "after_mode", "outcome"])
      && relativeChangesPath(file.path) && hashOrMissing(file.base_hash) && hashOrMissing(file.after_hash)
      && mode(file.base_mode) && mode(file.after_mode) && (file.base_hash === "missing") === (file.base_mode === null)
      && (file.after_hash === "missing") === (file.after_mode === null)
      && ["pending", "applied", "unchanged", "rolled_back", "preserved", "unknown"].includes(file.outcome)
      && (value.status !== "applied" || file.outcome === "applied") && (value.status !== "prepared" || file.outcome === "pending")
      && (value.status !== "failed" || file.outcome !== "pending"))
    && new Set(value.files.map(file => file.path.toLowerCase())).size === value.files.length;
}
function validPatch(row: PatchRow, run: string): boolean {
  return !!row && row.run_id === run && toolCallIdentifier(row.tool_call_id) && validReceipt(row.receipt)
    && ["propose_patch", "inverse_patch"].includes(row.tool_name) && ["running", "completed", "failed"].includes(row.operation_status)
    && ["retained", "expired"].includes(row.preimage_state) && row.evidence === attribution
    && row.confirmed_applied === (row.operation_status === "completed" && row.receipt.status === "applied")
    && typeof row.created_at === "string" && row.created_at.length <= 64
    && (row.preimage_expired_at === null || typeof row.preimage_expired_at === "string" && row.preimage_expired_at.length <= 64)
    && (row.preimage_state === "expired") === (row.preimage_expired_at !== null)
    && typeof row.source_run_quiescent === "boolean" && row.inverse_preparation_requires_fresh_file_check === true;
}
function sameReceipt(left: PatchReceipt, right: PatchReceipt): boolean {
  return left.patch_id === right.patch_id && left.schema === right.schema && left.status === right.status
    && left.files.length === right.files.length && left.files.every((file, index) => {
      const other = right.files[index]; return !!other && file.path === other.path && file.base_hash === other.base_hash
        && file.base_mode === other.base_mode && file.after_hash === other.after_hash && file.after_mode === other.after_mode && file.outcome === other.outcome;
    });
}
export function validEvidence(value: PatchEvidence, run: string, call: string): boolean {
  if (!value || value.source?.run_id !== run || value.source?.tool_call_id !== call || value.evidence !== attribution
    || !["propose_patch", "inverse_patch"].includes(value.tool_name) || !["running", "completed", "failed"].includes(value.operation_status)
    || !["retained", "expired"].includes(value.preimage_state) || typeof value.source_run?.status !== "string"
    || typeof value.source_run?.lease_active !== "boolean" || value.output?.sensitive !== true || value.output?.globally_redacted !== false
    || value.output?.accepted_preimage_blobs_exported !== false || value.receipt !== null && !validReceipt(value.receipt)) return false;
  const confirmed = value.operation_status === "completed" && value.receipt?.status === "applied";
  if (value.confirmed_applied !== confirmed) return false;
  if (!confirmed) return value.result === null;
  const result = value.result, receipt = value.receipt;
  if (!receipt) return false;
  return !!result && sameKeys(result, ["patch_id", "changed_paths", "unified_diff", "patch_receipt"])
    && result.patch_id === receipt.patch_id && validReceipt(result.patch_receipt)
    && sameReceipt(result.patch_receipt, receipt)
    && Array.isArray(result.changed_paths) && JSON.stringify(result.changed_paths) === JSON.stringify(receipt.files.map(file => file.path))
    && typeof result.unified_diff === "string" && new TextEncoder().encode(result.unified_diff).length <= 4 * 1024 * 1024;
}
function validStatus(value: GitStatus): boolean {
  if (!value || value.repository_clean !== null || value.agent_attribution !== "not_inferred_from_repository_changes") return false;
  if (value.available === false) return typeof value.reason === "string";
  return value.available === true && value.comparison === comparison && inspectionFingerprint(value.fingerprint)
    && typeof value.captured_at === "string" && value.captured_at.length <= 64 && (value.head_ref === null || typeof value.head_ref === "string" && value.head_ref.length <= 4096)
    && (value.head_id === null || typeof value.head_id === "string" && /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(value.head_id))
    && nullableHash(value.index_sha256) && nullableHash(value.refs_sha256) && inspectionFingerprint(value.policy_sha256)
    && typeof value.linked_worktree === "boolean" && [null, "sha1", "sha256"].includes(value.object_format) && ["files", "reftable"].includes(value.ref_storage)
    && Array.isArray(value.rows) && value.rows.length <= 1000
    && value.rows.every(row => row && relativeChangesPath(row.path) && Array.isArray(row.stages) && row.stages.length <= 3
      && row.stages.every(entry => entry && [1, 2, 3].includes(entry.stage)) && row.agent_generated === null
      && typeof row.staged === "string" && typeof row.worktree === "string")
    && [value.excluded_count, value.unknown_count, value.walked_entries].every(n => Number.isInteger(n) && n >= 0)
    && Array.isArray(value.coverage_reasons) && value.coverage_reasons.length <= 64 && value.coverage_reasons.every(reason => typeof reason === "string");
}
function validDiff(value: GitDiff, selection: DiffSelection): boolean {
  if (!value) return false;
  if (value.available === false) return value.repository_clean === null && typeof value.reason === "string";
  return value.available === true && value.path === selection.path && value.plane === selection.plane
    && value.conflict_stage === selection.conflict_stage && value.fingerprint === selection.expected_fingerprint
    && value.comparison === comparison && value.agent_generated === null && typeof value.text === "string"
    && typeof value.before_exists === "boolean" && typeof value.after_exists === "boolean"
    && nullableHash(value.before_sha256) && nullableHash(value.after_sha256) && nullableGitMode(value.before_mode) && nullableGitMode(value.after_mode)
    && value.before_exists === (value.before_sha256 !== null) && value.after_exists === (value.after_sha256 !== null)
    && new TextEncoder().encode(value.text).length <= 256 * 1024;
}
function validAuthorization(value: MetadataAuthorization | undefined): value is MetadataAuthorization {
  return !!value && typeof value.active === "boolean" && typeof value.workspace === "string" && value.workspace.length > 0
    && (value.active ? typeof value.grant_id === "string" && runIdentifier(value.grant_id)
      && typeof value.metadata_root === "string" && value.metadata_root.length > 0 : value.grant_id === null && value.metadata_root === null)
    && value.scope === "exact_linked_worktree_read_inspection_only" && value.persistent === false
    && value.lifetime === "current_owner_until_revoke_binding_change_switch_or_close" && value.command_or_workspace_write_grant === false;
}
export function changesError(error: unknown): string {
  if (error instanceof HttpError) {
    if (error.status === 409) return "快照／来源状态已变化；请显式刷新，不自动重试操作。";
    if (error.status === 404) return "来源或证据不存在／不在当前项目注册范围；不是空仓库证明。";
    if (error.status === 403) return "当前权限或原生来源边界拒绝读取。";
  }
  return "无法读取当前范围的变更证据；未推断为空、clean 或成功。";
}

/** Memory-only sensitive presentation. Abort fences replies, not proof of server/OS drain. */
export class ChangesController {
  readonly state = reactive({ active: false, closing: false, reading: false, nativeBusy: false, ready: false,
    status: null as GitStatus | null, diff: null as GitDiff | null, selected: null as DiffSelection | null, stale: false, error: "",
    sourceRunId: "", patches: [] as PatchRow[], patchesLoaded: false, patchOffset: 0, patchesMore: false,
    evidence: null as PatchEvidence | null, evidenceError: "", authorization: null as MetadataAuthorization | null,
    authorizationUnknown: false, nativeError: "" });
  private disposed = false;
  private generation = 0;
  private activity = 0;
  private nativeEpoch = 0;
  private host?: ChangesDesktopApi;
  private reads = new Map<Promise<void>, AbortController>();
  private nativeFlight?: Promise<void>;
  private resumeRequested = false;
  constructor(private readonly api: ChangesApi = changesApi) {}
  get busy(): boolean { return this.state.reading || this.state.nativeBusy || this.state.closing; }
  invalidateSnapshot(): void {
    this.invalidate(); this.state.status = null; this.state.diff = null; this.state.selected = null; this.state.stale = true;
    // Called when a separately reviewed effect may have started, never a claim it completed.
  }
  private canRead(): boolean { return !this.disposed && this.state.active && !this.state.closing && !this.nativeFlight; }
  private invalidate(): void { this.generation++; for (const abort of this.reads.values()) abort.abort(); this.state.reading = false; }
  private maybeResume(): void {
    if (this.resumeRequested && !this.disposed && !this.nativeFlight && !this.reads.size) { this.state.closing = false; this.resumeRequested = false; }
  }
  private read(operation: (signal: AbortSignal) => Promise<void>): Promise<void> {
    if (!this.canRead()) return Promise.resolve();
    this.invalidate(); const generation = this.generation, abort = new AbortController(); this.state.reading = true;
    const work = (async () => { await operation(abort.signal); })().catch(error => {
      if (generation === this.generation && !this.disposed && !this.state.closing) {
        this.state.error = changesError(error); if (error instanceof HttpError && error.status === 409) this.state.stale = true;
      }
    }).finally(() => {
      this.reads.delete(work); if (generation === this.generation) this.state.reading = false; this.maybeResume();
    });
    this.reads.set(work, abort); return work;
  }
  async activate(active: boolean, refreshOnEntry = true): Promise<void> {
    if (this.disposed) return;
    this.state.active = active; const activity = ++this.activity;
    if (!active) { this.invalidate(); this.state.stale = true; this.nativeEpoch++; return; }
    if (!refreshOnEntry) return;
    await this.refresh(); if (activity !== this.activity || !this.canRead()) return;
    if (this.state.sourceRunId) await this.loadPatches();
    if (activity === this.activity) await this.refreshAuthorization();
  }
  refresh(): Promise<void> {
    if (!this.canRead()) return Promise.resolve();
    this.state.status = null; this.state.diff = null; this.state.selected = null; this.state.stale = false; this.state.error = "";
    const generation = this.generation + 1;
    return this.read(async signal => {
      const value = await this.api.status(signal);
      if (generation !== this.generation || signal.aborted) return;
      if (!validStatus(value)) throw new Error("invalid_changes_status"); this.state.status = value;
    });
  }
  selectDiff(path: string, plane: DiffPlane, conflictStage: ConflictStage): Promise<void> {
    const status = this.state.status;
    if (!this.canRead() || !status?.available || this.state.stale) return Promise.resolve();
    const row = status.rows.find(item => item.path === path);
    const selection: DiffSelection = { path, plane, conflict_stage: conflictStage, expected_fingerprint: status.fingerprint };
    if (!row || !validDiffSelection(selection) || conflictStage !== null && !row.stages.some(entry => entry.stage === conflictStage)
      || row.staged === "unmerged" && plane !== "combined" && conflictStage === null) return Promise.resolve();
    this.state.selected = selection; this.state.diff = null; this.state.error = ""; const generation = this.generation + 1;
    return this.read(async signal => {
      const value = await this.api.diff(selection, signal); if (generation !== this.generation || signal.aborted) return;
      if (!validDiff(value, selection)) { this.state.stale = true; throw new Error("invalid_changes_diff"); }
      this.state.diff = value;
      if (!value.available && value.reason === "stale_git_inspection") this.state.stale = true;
    });
  }
  setSource(run: string): void {
    if (this.disposed || this.state.closing) return;
    this.invalidate(); this.state.sourceRunId = runIdentifier(run) ? run : "";
    this.state.patches = []; this.state.patchesLoaded = false; this.state.patchOffset = 0; this.state.patchesMore = false;
    this.state.evidence = null; this.state.evidenceError = run && !runIdentifier(run) ? "来源必须是当前项目已注册的精确 run ID。" : "";
  }
  loadPatches(offset = 0): Promise<void> {
    const run = this.state.sourceRunId;
    if (!this.canRead() || !runIdentifier(run) || !Number.isInteger(offset) || offset < 0 || offset > 100000) return Promise.resolve();
    this.state.patches = []; this.state.patchesLoaded = false; this.state.patchesMore = false; this.state.evidence = null;
    this.state.evidenceError = ""; const generation = this.generation + 1;
    return this.read(async signal => {
      try {
        const rows = await this.api.patches(run, offset, signal); if (generation !== this.generation || signal.aborted || run !== this.state.sourceRunId) return;
        if (!Array.isArray(rows) || rows.length > PATCH_PAGE_SIZE || !rows.every(row => validPatch(row, run))
          || new Set(rows.map(row => row.tool_call_id)).size !== rows.length) throw new Error("invalid_patch_page");
        this.state.patches = rows; this.state.patchOffset = offset; this.state.patchesLoaded = true; this.state.patchesMore = rows.length === PATCH_PAGE_SIZE;
      } catch (error) { if (generation === this.generation && !signal.aborted) this.state.evidenceError = changesError(error); }
    });
  }
  openEvidence(call: string): Promise<void> {
    const run = this.state.sourceRunId;
    if (!this.canRead() || !runIdentifier(run) || !this.state.patches.some(row => row.tool_call_id === call)) return Promise.resolve();
    this.state.evidence = null; this.state.evidenceError = ""; const generation = this.generation + 1;
    return this.read(async signal => {
      try {
        const value = await this.api.evidence(run, call, signal); if (generation !== this.generation || signal.aborted || run !== this.state.sourceRunId) return;
        if (!validEvidence(value, run, call)) throw new Error("invalid_patch_evidence"); this.state.evidence = value;
      } catch (error) { if (generation === this.generation && !signal.aborted) this.state.evidenceError = changesError(error); }
    });
  }
  attach(host?: Partial<ChangesDesktopApi>): void {
    const next = host && [host.git_metadata_authorize, host.git_metadata_authorization, host.git_metadata_revoke].every(fn => typeof fn === "function")
      ? host as ChangesDesktopApi : undefined;
    if (next === this.host) return;
    this.nativeEpoch++; if (this.nativeFlight || this.state.authorization?.active) this.state.authorizationUnknown = true;
    this.host = next; this.state.ready = !!next; this.state.authorization = null;
  }
  private native(action: "read" | "grant" | "revoke"): Promise<void> {
    if (!this.host || !this.canRead() || this.state.reading || action !== "read" && this.state.authorizationUnknown) return Promise.resolve();
    const host = this.host, epoch = this.nativeEpoch;
    this.state.nativeBusy = true; this.state.nativeError = "";
    if (action !== "read") { this.state.authorization = null; this.state.status = null; this.state.diff = null; this.state.selected = null; this.state.stale = true; }
    const work = (async () => {
      try {
        // Zero arguments, no web path/confirmation/authority transport, no timeout race or auto-retry.
        const result = await (action === "read" ? host.git_metadata_authorization() : action === "grant" ? host.git_metadata_authorize() : host.git_metadata_revoke());
        if (epoch !== this.nativeEpoch || host !== this.host || this.disposed || this.state.closing || !this.state.active) {
          if (action !== "read") this.state.authorizationUnknown = true; return;
        }
        if (result.ok === true && result.cancelled === true && action === "grant") {
          this.state.authorizationUnknown = true; this.state.nativeError = "已取消此次原生选择／确认；已有授权未推断为无，请只读当前授权。"; return;
        }
        if (result.ok !== true || !validAuthorization(result.authorization)) throw new Error("native_git_metadata_unavailable");
        this.state.authorization = result.authorization; this.state.authorizationUnknown = false;
      } catch {
        this.state.authorizationUnknown = true;
        if (epoch === this.nativeEpoch && !this.disposed && !this.state.closing) {
          this.state.authorization = null; this.state.nativeError = "原生授权回复不可用；结果未知时只能读取当前授权，不自动重复确认或撤销。";
        }
      }
    })().finally(() => { this.nativeFlight = undefined; this.state.nativeBusy = false; this.maybeResume(); });
    this.nativeFlight = work; return work;
  }
  refreshAuthorization(): Promise<void> { return this.native("read"); }
  authorize(): Promise<void> { return this.native("grant"); }
  revoke(): Promise<void> { return this.native("revoke"); }
  async prepareClose(): Promise<void> {
    this.state.closing = true; this.resumeRequested = false; this.nativeEpoch++; this.invalidate();
    this.state.status = null; this.state.diff = null; this.state.selected = null; this.state.evidence = null; this.state.patches = [];
    if (this.state.authorization?.active) this.state.authorizationUnknown = true;
    this.state.authorization = null;
    await Promise.all([...this.reads.keys(), ...(this.nativeFlight ? [this.nativeFlight] : [])]); this.maybeResume();
  }
  finishClose(): void { this.resumeRequested = true; this.maybeResume(); }
  dispose(): void {
    this.disposed = true; this.nativeEpoch++; this.invalidate(); this.state.closing = true;
    this.state.status = null; this.state.diff = null; this.state.evidence = null; this.state.patches = []; this.state.authorization = null;
    // Host owns actual effect/close drain. Unmount cannot claim native cancellation or revoke a grant.
  }
}
