import { reactive } from "vue";
import type { ContextDescriptor, PrepareContext, Effort, Permissions, RunMode } from "./types";

export interface PlanTask {
  id: string; title: string; prompt: string; dependencies: string[];
  access: "read" | "write"; mode: RunMode; profile_id: string | null;
}
export interface WorkOrderPlan { title: string; tasks: PlanTask[] }
export interface WorkOrderSummary {
  work_order_id: string; title: string; status: string; active_revision: number; created_at: string; updated_at: string;
}
export interface WorkOrderTask extends Omit<PlanTask, "id"> {
  task_id: string; revision: number; execution_revision: number; status: string; retry_requested: number;
}
export interface WorkOrderAttempt {
  attempt_id: string; revision: number; task_id: string; attempt_number: number; run_id: string | null;
  status: string; runtime_status: string | null; runtime_lease_active: number | null;
}
export interface ExecutionSettings { profile_id: string | null; effort: Effort; deadline_seconds: number; permissions: Permissions; context?: ContextDescriptor }
export interface ContextBinding {
  revision: number; bound_revision: number; descriptor: ContextDescriptor; captured_at: string; context_sha256: string;
  context_bytes: number; estimated_tokens: number; estimate_method: string; actual_tokens: null;
}
export interface FrozenProfile { id: string; provider: string; model: string; base_url?: string; implementation?: string }
export interface WorkOrder extends WorkOrderSummary {
  plan: WorkOrderPlan; tasks: WorkOrderTask[]; attempts: WorkOrderAttempt[]; dispatch_error: string;
  execution: Partial<ExecutionSettings> & { profiles?: Record<string, FrozenProfile> };
  context_bindings?: ContextBinding[];
}
export interface QueueItem {
  run_id: string; conversation_id: string | null; mode: RunMode; status: string; lease_active: boolean;
  title: string; work_order_id: string | null; task_id: string | null;
}
export interface QueueSnapshot {
  items: QueueItem[]; total: number; limit: number;
  scheduler: { active: number; queued: number; max_active: number; queue_capacity: number };
}
export interface WorkOrderRunSelection { saved: boolean; work_order_id: string | null; run_id: string | null }
export interface WorkOrderSelection extends WorkOrderRunSelection { revision: number; request_key: string | null }
const base = "/api/v1/work-orders";
const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
export class WorkOrderHttpError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: { ...(init?.body ? { "Content-Type": "application/json", "X-Doppel-UI": "1" } : {}), ...init?.headers } });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: unknown };
    throw new WorkOrderHttpError(response.status, typeof payload.detail === "string" ? payload.detail : `工作单请求未完成（HTTP ${response.status}）。`);
  }
  return response.json() as Promise<T>;
}
const post = <T>(url: string, body: unknown) => request<T>(url, { method: "POST", body: JSON.stringify(body) });
export const workOrderApi = {
  list: (beforeId?: string, search = "") => request<WorkOrderSummary[]>(`${base}?limit=50&search=${encodeURIComponent(search)}${beforeId ? `&before_id=${encodeURIComponent(beforeId)}` : ""}`),
  get: (id: string) => request<WorkOrder>(path(id)),
  queue: () => request<QueueSnapshot>(`${base}/queue`),
  selection: () => request<WorkOrderSelection>(`${base}/selection`),
  runSelection: (id: string) => request<WorkOrderRunSelection>(`${path(id)}/selection`),
  saveSelection: (id: string | null, runId: string | null, revision: number, key: string) => post<WorkOrderSelection>(`${base}/selection`, {
    work_order_id: id, run_id: runId, expected_revision: revision, idempotency_key: key,
  }),
  create: (plan: WorkOrderPlan, key: string) => post<WorkOrder>(base, { plan, idempotency_key: key }),
  revise: (id: string, revision: number, plan: WorkOrderPlan, context?: ContextDescriptor) => request<WorkOrder>(`${path(id)}/plan`, {
    method: "PUT", body: JSON.stringify({ expected_revision: revision, plan, ...(context === undefined ? {} : { context }) }),
  }),
  plan: (id: string, revision: number) => request<WorkOrderPlan>(`${path(id)}/plans/${revision}`),
  activate: (id: string, revision: number, settings: ExecutionSettings) => post<WorkOrder>(`${path(id)}/activate`, { expected_revision: revision, settings }),
  control: (id: string, revision: number, action: "pause" | "resume" | "cancel") => post<WorkOrder>(`${path(id)}/control`, { expected_revision: revision, action }),
  retry: (id: string, revision: number, taskId: string, attemptId: string) => post<WorkOrder>(`${path(id)}/tasks/${encodeURIComponent(taskId)}/retry`, {
    expected_revision: revision, expected_attempt_id: attemptId,
  }),
};
export type WorkOrderApi = typeof workOrderApi;
export const clonePlan = (plan: WorkOrderPlan): WorkOrderPlan => JSON.parse(JSON.stringify(plan)) as WorkOrderPlan;
export function emptyPlan(): WorkOrderPlan {
  return { title: "新工作单", tasks: [{ id: "task-1", title: "理解项目", prompt: "", dependencies: [], access: "read", mode: "graph", profile_id: null }] };
}
export function defaultExecution(): ExecutionSettings {
  return { profile_id: null, effort: "balanced", deadline_seconds: 600,
    permissions: { workspace_write: false, command_execute: false, mcp_execute: false, delegate: false } };
}
export function validatePlan(plan: WorkOrderPlan): string {
  if (!plan.title.trim() || plan.title.length > 200 || plan.title.includes("\0")) return "工作单标题需要 1–200 个字符。";
  if (!plan.tasks.length || plan.tasks.length > 64) return "计划需要 1–64 个节点。";
  const ids = new Set(plan.tasks.map(t => t.id));
  if (ids.size !== plan.tasks.length) return "节点 ID 不可重复。";
  let bytes = 0;
  for (const task of plan.tasks) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(task.id)) return "节点 ID 只能用字母、数字、连字符或下划线（最多 64 字符）。";
    if (!task.title.trim() || task.title.length > 200 || task.title.includes("\0")) return "节点标题需要 1–200 个字符。";
    if (!task.prompt.trim() || task.prompt.length > 100000 || task.prompt.includes("\0")) return "每个节点需要 1–100000 个字符的任务说明。";
    bytes += new TextEncoder().encode(task.prompt.trim()).length;
    if (!(["read", "write"] as string[]).includes(task.access) || !(["legacy", "graph", "deep"] as string[]).includes(task.mode)) return "节点访问类型或运行模式无效。";
    if (task.profile_id !== null && (!task.profile_id.trim() || task.profile_id.length > 128 || task.profile_id.includes("\0"))) return "模型档案 ID 无效。";
    if (new Set(task.dependencies).size !== task.dependencies.length || task.dependencies.some(id => id === task.id || !ids.has(id))) return "依赖不能重复、指向自己或未知节点。";
  }
  if (bytes > 512 * 1024) return "计划任务说明的 UTF-8 总大小超过 512 KiB。";
  const complete = new Set<string>();
  while (complete.size < ids.size) {
    const ready = plan.tasks.filter(task => !complete.has(task.id) && task.dependencies.every(id => complete.has(id)));
    if (!ready.length) return "计划存在循环依赖。";
    ready.forEach(task => complete.add(task.id));
  }
  return "";
}
const normalized = (plan: WorkOrderPlan): string => JSON.stringify({ title: plan.title.trim(), tasks: plan.tasks.map(t => ({
  id: t.id, title: t.title.trim(), prompt: t.prompt.trim(), dependencies: t.dependencies,
  access: t.access, mode: t.mode, profile_id: t.profile_id?.trim() || null,
})) });
const liveAttempts = new Set(["reserved", "accepted", "running", "awaiting_approval", "interrupted"]);
export function latestAttempt(order: WorkOrder, task: WorkOrderTask): WorkOrderAttempt | undefined {
  return order.attempts.filter(a => a.task_id === task.task_id && a.revision === task.execution_revision)
    .sort((a, b) => b.attempt_number - a.attempt_number)[0];
}
export function canRetry(order: WorkOrder, task: WorkOrderTask): boolean {
  const attempt = latestAttempt(order, task);
  return !["succeeded", "cancelled", "draft"].includes(order.status) && task.status === "failed"
    && !!attempt && attempt.attempt_number < 3 && !order.attempts.some(a => liveAttempts.has(a.status))
    && !attempt.runtime_lease_active;
}
export function taskReason(order: WorkOrder, task: WorkOrderTask): string {
  const attempt = latestAttempt(order, task);
  if (attempt?.runtime_lease_active && ["completed", "failed", "cancelled", "interrupted_expired"].includes(attempt.runtime_status || "")) return "运行已报告终态；资源尚未排空，不解锁后继。";
  if (task.status === "awaiting_approval") return "等待该 run 的独立工具审批；计划批准不是副作用批准。";
  if (task.status === "blocked") return "前置节点失败或取消；请先处理对应失败节点。";
  if (task.retry_requested) return "已申请重试，等待显式恢复工作单。";
  if (task.status === "pending") {
    const missing = task.dependencies.filter(id => order.tasks.find(t => t.task_id === id)?.status !== "succeeded");
    if (missing.length) return `等待前置节点：${missing.join("、")}`;
    if (order.status === "draft") return "仅保存草稿；尚未批准执行。";
    if (order.status === "paused") return "工作单已暂停；不会派发新节点。";
    return "依赖已满足，等待现有队列容量或当前节点排空。";
  }
  return "";
}
export const orderStatus: Record<string, string> = { draft: "草稿", queued: "等待派发", running: "执行中", paused: "已暂停", succeeded: "执行完成（未等于验证通过）", failed: "失败", cancelled: "已取消", pending: "未派发", dispatching: "接收/排队中", awaiting_approval: "等待审批", blocked: "依赖阻塞", reserved: "待接收", accepted: "已接收", interrupted: "中断", completed: "运行完成" };
export const dispatchErrors: Record<string, string> = { admission_rejected: "派发被拒绝", admission_failed: "接收结果不确定，保留原派发 key", admission_not_persisted: "未找到持久接收记录", reconciliation_failed: "持久记录对账冲突", runtime_cancel_failed: "运行取消未完成，可显式重试取消" };

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const descriptorKey = (value?: ContextDescriptor): string => value ? JSON.stringify({ manifest_id: value.manifest_id,
  notes: value.notes.map(ref => ({ note_id: ref.note_id, revision: ref.revision })) }) : "absent";
const bindingAt = (record: WorkOrder, revision: number): ContextBinding | null => record.context_bindings?.find(binding => binding.revision === revision) || null;
const bindingKey = (value: ContextBinding | null): string => value ? JSON.stringify({ bound_revision: value.bound_revision,
  descriptor: descriptorKey(value.descriptor), captured_at: value.captured_at, hash: value.context_sha256, bytes: value.context_bytes,
  estimate: value.estimated_tokens, method: value.estimate_method }) : "absent";
type SaveIntent = { kind: "save"; plan: WorkOrderPlan; id: string | null; revision: number; key: string;
  rebind: boolean; context?: ContextDescriptor; carried: ContextBinding | null; prepared: boolean; sent: boolean };
type ActivationIntent = { kind: "activate"; id: string; revision: number; settings: ExecutionSettings; includeContext: boolean; prepared: boolean; sent: boolean };
type MutationIntent = SaveIntent | ActivationIntent;
type SelectionIntent = { id: string | null; runId: string | null; key: string; revision?: number };
export class WorkOrderController {
  readonly state = reactive({ orders: [] as WorkOrderSummary[], selected: null as WorkOrder | null,
    draft: emptyPlan(), baseRevision: 0, dirty: false, busy: false, loading: false, closing: false,
    uncertain: false, uncertainOperation: null as "save" | "activate" | null, conflict: false, rebindContext: false, error: "", more: false, listLoading: false, query: "", appliedQuery: "", capped: false, queue: null as QueueSnapshot | null, queueError: "",
    history: null as { revision: number; plan: WorkOrderPlan } | null,
    runId: "", rememberedRunId: null as string | null, transientRun: false,
    restoring: false, restoreError: "", selectionPending: false, selectionError: "" });
  private pending: SaveIntent | null = null;
  private activation: ActivationIntent | null = null;
  private flight: Promise<void> | null = null;
  private epoch = 0;
  private disposed = false;
  private refreshing = false;
  private listing = false;
  private pendingOpens = 0;
  private selectionWrites: Promise<void> = Promise.resolve();
  private pendingSelection: SelectionIntent | null = null;
  constructor(private readonly api: WorkOrderApi = workOrderApi, private readonly prepareContext?: PrepareContext) {}
  get hasEdits(): boolean { return this.state.dirty || this.state.rebindContext; }
  private report(error: unknown): void { this.state.error = error instanceof Error ? error.message : "工作单操作未完成，请刷新后重试。"; }
  private accept(record: WorkOrder, preserveHistory = false): void {
    if (this.state.selected?.work_order_id !== record.work_order_id) this.applyRun(null);
    this.state.selected = record; this.state.draft = clonePlan(record.plan); this.state.baseRevision = record.active_revision;
    this.state.dirty = false; this.state.rebindContext = false; this.state.uncertain = false; this.state.uncertainOperation = null;
    this.state.conflict = false; this.pending = null; this.activation = null; if (!preserveHistory) this.state.history = null;
  }
  private navigationBlocked(): boolean { return this.state.restoring || !!this.state.restoreError; }
  private applyRun(id: string | null): void {
    this.state.runId = id || ""; this.state.rememberedRunId = id; this.state.transientRun = false;
  }
  private associated(record: WorkOrder, id: string | null): string | null {
    if (id && !record.attempts.some(attempt => attempt.run_id === id)) throw new Error("保存的运行不属于该工作单；恢复未完成，请重试。");
    return id;
  }
  async initialize(): Promise<void> {
    if (this.disposed || this.state.restoring || this.state.closing || this.state.busy || this.state.loading
      || this.hasEdits || this.state.uncertain || this.state.selectionPending) return;
    const epoch = ++this.epoch;
    this.state.restoring = true; this.state.restoreError = ""; ++this.pendingOpens;
    try {
      const saved = await this.api.selection();
      if (saved.run_id && !saved.work_order_id) throw new Error("工作单选择记录不完整；恢复未完成。");
      const record = saved.work_order_id ? await this.api.get(saved.work_order_id) : null;
      if (record && record.work_order_id !== saved.work_order_id) throw new Error("恢复的工作单 ID 不匹配。");
      const run = record ? this.associated(record, saved.run_id) : null;
      if (this.disposed || epoch !== this.epoch) return;
      if (record) this.accept(record);
      else { this.state.selected = null; this.state.draft = emptyPlan(); this.state.baseRevision = 0; this.state.history = null; }
      this.applyRun(run);
      // No POST, admission, activation or recent-list fallback during recovery.
    } catch (error) {
      if (!this.disposed && epoch === this.epoch) this.state.restoreError = error instanceof Error ? error.message : "项目选择恢复失败；请重试，不覆盖未知记录。";
    } finally { --this.pendingOpens; if (epoch === this.epoch) this.state.restoring = false; }
    if (!this.disposed) { await this.list(); await this.refresh(); }
  }
  private rememberSelection(id: string | null, runId: string | null): void {
    const intent: SelectionIntent = { id, runId, key: crypto.randomUUID() };
    this.pendingSelection = intent; this.state.selectionPending = true; this.state.selectionError = "";
    this.selectionWrites = this.selectionWrites.then(() => this.writeSelection(intent));
  }
  private async writeSelection(intent: SelectionIntent, recover = false): Promise<void> {
    try {
      if (intent.revision === undefined || recover) {
        const current = await this.api.selection();
        if (intent.revision !== undefined && current.revision < intent.revision) throw new Error("项目选择版本倒退；保存未确认，请先处理项目库恢复。");
        if (recover && intent.revision !== undefined && current.revision > intent.revision
          && current.work_order_id === intent.id && current.run_id === intent.runId) {
          this.confirmSelection(intent); return;
        }
        if (intent.revision !== undefined && current.revision !== intent.revision) {
          // Metadata-only CAS rebase: never alter a plan-save or runtime admission key.
          intent.key = crypto.randomUUID();
        }
        intent.revision = current.revision;
      }
      const reply = await this.api.saveSelection(intent.id, intent.runId, intent.revision, intent.key);
      if (reply.work_order_id !== intent.id || reply.run_id !== intent.runId || reply.revision !== intent.revision + 1
        || reply.request_key !== intent.key) throw new Error("选择保存回复不匹配，等待重新确认。");
      this.confirmSelection(intent);
    } catch (error) {
      if (this.pendingSelection === intent && !this.disposed) this.state.selectionError = error instanceof Error ? error.message : "项目选择未保存；请重试。";
    }
  }
  private confirmSelection(intent: SelectionIntent): void {
    if (this.pendingSelection !== intent || this.disposed) return;
    this.pendingSelection = null; this.state.selectionPending = false; this.state.selectionError = "";
  }
  async flushSelection(): Promise<void> {
    // Await the stable tail: a later queued selection must not be dropped by an older ACK.
    let tail: Promise<void>;
    do { tail = this.selectionWrites; await tail; } while (tail !== this.selectionWrites);
    const intent = this.pendingSelection;
    if (intent) {
      this.selectionWrites = this.selectionWrites.then(() => this.writeSelection(intent, true));
      do { tail = this.selectionWrites; await tail; } while (tail !== this.selectionWrites);
    }
    if (this.pendingSelection) throw new Error(this.state.selectionError || "工作单选择尚未确认保存，不能关闭或切换项目。");
  }
  async retrySelection(): Promise<void> { try { await this.flushSelection(); } catch { /* Visible selectionError remains; do not fake success. */ } }
  openRun(id: string): void {
    if (this.disposed || this.state.busy || this.state.loading || this.state.closing || this.navigationBlocked()) return;
    const order = this.state.selected;
    if (order?.attempts.some(attempt => attempt.run_id === id)) {
      this.applyRun(id); this.rememberSelection(order.work_order_id, id);
    } else { this.state.runId = id; this.state.transientRun = true; }
  }
  hideRun(): void {
    if (this.disposed || this.state.busy || this.state.loading || this.state.closing || this.navigationBlocked()) return;
    if (this.state.transientRun) { this.applyRun(this.state.rememberedRunId); return; }
    this.applyRun(null); this.rememberSelection(this.state.selected?.work_order_id ?? null, null);
  }
  update(plan: WorkOrderPlan): void {
    if (this.state.busy || this.state.closing || this.state.uncertain || this.state.loading || this.disposed || this.navigationBlocked()) return;
    this.state.draft = clonePlan(plan); this.state.dirty = !this.state.selected || normalized(plan) !== normalized(this.state.selected.plan);
  }
  setContextRebind(value: boolean): void {
    if (this.state.busy || this.state.closing || this.state.uncertain || this.state.loading || this.disposed || this.navigationBlocked()) return;
    if (!value) { this.state.rebindContext = false; return; }
    const order = this.state.selected;
    if (!order || ["draft", "succeeded", "cancelled"].includes(order.status)
      || !order.plan.tasks.some(task => !order.attempts.some(attempt => attempt.task_id === task.id))) return;
    this.state.rebindContext = value;
  }
  fresh(): void {
    if (this.disposed || this.state.loading || this.navigationBlocked()) return;
    if (this.hasEdits || this.state.busy || this.state.uncertain || this.state.closing) { this.state.error = "请先保存或显式放弃当前编辑。"; return; }
    ++this.epoch; this.state.selected = null; this.state.draft = emptyPlan(); this.state.baseRevision = 0; this.state.history = null; this.state.error = "";
    this.applyRun(null); this.rememberSelection(null, null);
  }
  discard(): void {
    if (this.state.busy || this.state.closing || this.state.uncertain || this.state.loading || this.disposed || this.navigationBlocked()) return;
    if (this.state.selected) this.accept(this.state.selected);
    else { this.state.draft = emptyPlan(); this.state.dirty = false; }
    this.state.error = "";
  }
  async select(id: string): Promise<void> {
    if (this.disposed || this.navigationBlocked()) return;
    if (this.hasEdits || this.state.busy || this.state.closing || this.state.uncertain) { this.state.error = "请先保存或显式放弃当前编辑。"; return; }
    const epoch = ++this.epoch;
    this.state.loading = true; this.state.error = ""; ++this.pendingOpens;
    try {
      const record = await this.api.get(id), saved = await this.api.runSelection(id);
      if (record.work_order_id !== id || saved.work_order_id !== id) throw new Error("工作单选择回复不匹配。");
      const run = this.associated(record, saved.run_id);
      if (!this.disposed && epoch === this.epoch) { this.accept(record); this.applyRun(run); this.rememberSelection(id, run); }
    }
    catch (e) { if (epoch === this.epoch) this.report(e); }
    finally { --this.pendingOpens; if (epoch === this.epoch) this.state.loading = false; }
  }
  async list(more = false): Promise<void> {
    if (this.disposed || this.state.closing || this.listing) return;
    this.listing = true;
    this.state.listLoading = true;
    const query = more ? this.state.appliedQuery : this.state.query.trim().slice(0, 200);
    try {
      const rows = await this.api.list(more ? this.state.orders.at(-1)?.work_order_id : undefined, query);
      if (this.disposed) return;
      const combined = more ? [...this.state.orders, ...rows.filter(row => !this.state.orders.some(old => old.work_order_id === row.work_order_id))] : rows;
      this.state.orders = combined.slice(0, 250); this.state.appliedQuery = query;
      this.state.capped = combined.length >= 250;
      this.state.more = rows.length === 50 && !this.state.capped;
    } catch (e) { this.report(e); }
    finally { this.listing = false; this.state.listLoading = false; }
  }
  async refresh(): Promise<void> {
    if (this.disposed || this.state.closing || this.state.busy || this.state.loading || this.refreshing || this.navigationBlocked()) return;
    this.refreshing = true;
    const id = this.state.selected?.work_order_id, epoch = this.epoch;
    try {
      try { const queue = await this.api.queue(); if (!this.disposed) { this.state.queue = queue; this.state.queueError = ""; } }
      catch { this.state.queueError = "队列快照读取失败；下面可能是旧状态。"; }
      if (id) {
        const record = await this.api.get(id);
        if (this.disposed || epoch !== this.epoch || this.state.selected?.work_order_id !== id) return;
        if (record.work_order_id !== id) throw new Error("工作单刷新回复身份不匹配。");
        const intent = this.pending || this.activation;
        if (intent && this.matches(record, intent)) { this.accept(record); this.state.error = ""; }
        else if (this.hasEdits || this.state.uncertain) {
          this.state.selected = record;
          if (intent) this.state.conflict = this.isConflict(record, intent);
        }
        else this.accept(record, true);
        this.state.orders = this.state.orders.map(row => row.work_order_id === id ? {
          ...row, title: record.title, status: record.status, active_revision: record.active_revision, updated_at: record.updated_at,
        } : row);
      }
    } catch (e) { if (epoch === this.epoch) this.report(e); }
    finally { this.refreshing = false; }
  }
  private matches(record: WorkOrder, intent: MutationIntent): boolean {
    if (!intent.prepared || !intent.sent || intent.id && record.work_order_id !== intent.id) return false;
    if (intent.kind === "save") {
      if (normalized(record.plan) !== normalized(intent.plan) || intent.id && record.active_revision !== intent.revision + 1) return false;
      if (!intent.id) return record.active_revision >= 1;
      const actual = bindingAt(record, record.active_revision);
      if (intent.rebind) return !!actual && actual.bound_revision === intent.revision + 1
        && descriptorKey(actual.descriptor) === descriptorKey(intent.context)
        && descriptorKey(record.execution.context) === descriptorKey(intent.context);
      return bindingKey(actual) === bindingKey(intent.carried)
        && descriptorKey(record.execution.context) === descriptorKey(intent.carried?.descriptor);
    }
    if (record.active_revision !== intent.revision || record.status === "draft") return false;
    const expected = intent.settings, actual = record.execution;
    if (actual.effort !== expected.effort || actual.deadline_seconds !== expected.deadline_seconds
      || expected.profile_id !== null && actual.profile_id !== expected.profile_id
      || typeof actual.profile_id !== "string" || !actual.profile_id || !actual.profiles?.[actual.profile_id]
      || !actual.permissions || (Object.keys(expected.permissions) as Array<keyof Permissions>).some(key => actual.permissions![key] !== expected.permissions[key])
      || descriptorKey(actual.context) !== descriptorKey(expected.context)) return false;
    const binding = bindingAt(record, intent.revision);
    return expected.context ? !!binding && binding.bound_revision === intent.revision
      && descriptorKey(binding.descriptor) === descriptorKey(expected.context) : binding === null;
  }
  private isConflict(record: WorkOrder, intent: MutationIntent): boolean {
    return record.work_order_id === intent.id && !this.matches(record, intent)
      && (record.active_revision > intent.revision || intent.kind === "activate" && record.status !== "draft");
  }
  private async mutate(operation: () => Promise<WorkOrder>, intent?: MutationIntent): Promise<void> {
    if (this.state.busy || this.state.closing || this.disposed || this.state.loading || this.navigationBlocked()) return;
    ++this.epoch; this.state.busy = true; this.state.error = "";
    const recovering = this.state.uncertain;
    const flight = (async () => {
      try { const record = await operation(); if (!this.disposed) {
        if (intent && !this.matches(record, intent)) throw new Error("保存/激活回复的身份、版本、配置或上下文绑定不匹配；保留原请求，需刷新确认。");
        const changed = this.state.selected?.work_order_id !== record.work_order_id;
        this.accept(record); if (changed) this.rememberSelection(record.work_order_id, null); await this.list();
      } }
      catch (e) { if (!this.disposed) { this.report(e); if (intent) {
        const refused = !recovering && e instanceof WorkOrderHttpError && [400, 404, 409, 422].includes(e.status);
        this.state.uncertain = intent.sent && !refused;
        this.state.uncertainOperation = this.state.uncertain ? intent.kind : null;
        if (!this.state.uncertain) { this.pending = null; this.activation = null; this.state.conflict = false; }
      } } }
      finally { this.state.busy = false; }
    })();
    this.flight = flight;
    await flight;
    if (this.flight === flight) this.flight = null;
  }
  async save(): Promise<void> {
    if (this.state.busy || this.state.closing || this.state.loading || this.disposed || this.activation || this.navigationBlocked()) return;
    if (!this.pending) {
      const error = validatePlan(this.state.draft);
      if (error) { this.state.error = error; return; }
      if (this.state.selected && this.state.baseRevision !== this.state.selected.active_revision) { this.state.error = "计划版本已变；编辑仍保留，请放弃并重新载入后再编辑。"; return; }
      const order = this.state.selected;
      this.pending = { kind: "save", plan: clonePlan(this.state.draft), id: order?.work_order_id ?? null,
        revision: this.state.baseRevision, key: crypto.randomUUID(), rebind: this.state.rebindContext,
        carried: order ? clone(bindingAt(order, this.state.baseRevision)) : null, prepared: !this.state.rebindContext, sent: false };
    }
    const intent = this.pending;
    await this.mutate(async () => {
      if (!intent.prepared) {
        if (!intent.id || !this.prepareContext) throw new Error("目标工作单上下文未就绪；尚未保存新版本。");
        intent.context = clone(await this.prepareContext(intent.id)); intent.prepared = true;
      }
      if (this.disposed || this.state.closing && !intent.sent) throw new Error("工作区正在关闭；准备中的新版本没有提交。");
      intent.sent = true;
      return intent.id ? intent.context === undefined ? this.api.revise(intent.id, intent.revision, clonePlan(intent.plan))
        : this.api.revise(intent.id, intent.revision, clonePlan(intent.plan), clone(intent.context)) : this.api.create(clonePlan(intent.plan), intent.key);
    }, intent);
  }
  async reloadAfterUncertain(): Promise<void> {
    const intent = this.pending || this.activation, epoch = this.epoch;
    if (!intent?.id || this.state.busy || this.state.loading || this.state.closing || this.disposed || this.navigationBlocked()) {
      if (intent && !intent.id) this.state.error = "创建回复未确认，请重试相同保存以取得原工作单 ID。";
      return;
    }
    ++this.pendingOpens; this.state.loading = true;
    try {
      const record = await this.api.get(intent.id);
      if (this.disposed || this.epoch !== epoch || (this.pending || this.activation) !== intent) return;
      if (record.work_order_id !== intent.id) throw new Error("确认回复工作单身份不匹配。");
      if (this.matches(record, intent)) { this.accept(record); this.state.error = ""; }
      else {
        this.state.selected = record; this.state.conflict = this.isConflict(record, intent);
        this.state.error = this.state.conflict ? "服务端已有不同的固定版本/配置；旧请求仍不能判定为成功。可只读采用服务端版本并停止本地重试，不会再次激活。"
          : "尚无匹配的固定版本/上下文回执；保持原请求，可显式重试。";
      }
    } catch (e) { this.report(e); }
    finally { --this.pendingOpens; if (epoch === this.epoch) this.state.loading = false; }
  }
  async activate(settings: ExecutionSettings, includeContext = false): Promise<void> {
    const order = this.state.selected;
    if (!order || this.state.busy || this.state.loading || this.state.closing || this.disposed || this.navigationBlocked()
      || this.hasEdits || this.state.uncertain || order.status !== "draft" || this.pending || this.activation) return;
    const options = clone(settings); delete options.context;
    this.activation = { kind: "activate", id: order.work_order_id, revision: order.active_revision, settings: options,
      includeContext, prepared: !includeContext, sent: false };
    await this.dispatchActivation(this.activation);
  }
  private async dispatchActivation(intent: ActivationIntent): Promise<void> {
    await this.mutate(async () => {
      if (!intent.prepared) {
        if (!this.prepareContext) throw new Error("工作单上下文尚未就绪；没有激活。");
        intent.settings.context = clone(await this.prepareContext(intent.id)); intent.prepared = true;
      }
      if (this.disposed || this.state.closing && !intent.sent) throw new Error("工作区正在关闭；准备中的激活没有提交。");
      intent.sent = true;
      return this.api.activate(intent.id, intent.revision, clone(intent.settings));
    }, intent);
  }
  async retryPending(): Promise<void> {
    if (!this.state.uncertain) return;
    if (this.activation) await this.dispatchActivation(this.activation);
    else if (this.pending) await this.save();
  }
  async adoptServerVersion(confirmed: boolean): Promise<void> {
    const intent = this.pending || this.activation, epoch = this.epoch;
    if (!confirmed || !this.state.uncertain || !this.state.conflict || !intent?.id || this.state.busy || this.state.loading || this.state.closing || this.disposed) return;
    ++this.pendingOpens; this.state.loading = true;
    try {
      const record = await this.api.get(intent.id);
      if (this.disposed || epoch !== this.epoch || (this.pending || this.activation) !== intent) return;
      if (!this.isConflict(record, intent)) throw new Error("未确认稳定的版本/执行配置冲突；不能丢弃未知请求。");
      this.accept(record);
      this.state.error = "已明确停止本地旧请求重试并采用服务端版本；这不是旧请求成功/未执行的判断，没有再次激活或重绑。";
    } catch (error) { this.report(error); }
    finally { --this.pendingOpens; if (epoch === this.epoch) this.state.loading = false; }
  }
  async control(action: "pause" | "resume" | "cancel"): Promise<void> {
    const order = this.state.selected;
    if (!order || this.hasEdits || this.state.uncertain) return;
    await this.mutate(() => this.api.control(order.work_order_id, order.active_revision, action));
  }
  async retry(taskId: string): Promise<void> {
    const order = this.state.selected, task = order?.tasks.find(t => t.task_id === taskId);
    if (!order || !task || !canRetry(order, task) || this.hasEdits || this.state.uncertain) return;
    const attempt = latestAttempt(order, task)!;
    await this.mutate(() => this.api.retry(order.work_order_id, order.active_revision, taskId, attempt.attempt_id));
  }
  async history(revision: number): Promise<void> {
    const order = this.state.selected, epoch = this.epoch;
    if (!order || this.state.busy || this.state.closing || this.disposed) return;
    try { const plan = await this.api.plan(order.work_order_id, revision); if (!this.disposed && epoch === this.epoch) this.state.history = { revision, plan }; }
    catch (e) { this.report(e); }
  }
  async prepareClose(): Promise<void> {
    this.state.closing = true;
    if (this.navigationBlocked() || this.pendingOpens) throw new Error("工作单选择仍在恢复或载入；请等待或重试后再关闭/切换项目。");
    if (this.flight) await this.flight;
    if (this.hasEdits || this.state.uncertain) throw new Error("工作单存在未保存或未确认的编辑/激活/上下文重绑；请保存、刷新确认或显式放弃后再关闭/切换项目。");
    await this.flushSelection();
  }
  finishClose(): void { this.state.closing = false; }
  dispose(): void { this.disposed = true; ++this.epoch; }
}
