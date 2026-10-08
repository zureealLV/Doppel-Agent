// S4 definitions; controller/SSR evidence is not native WebView acceptance.
import { describe, expect, it, vi } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import PlanPanel from "./components/PlanPanel.vue";
import TaskQueue from "./components/TaskQueue.vue";
import { WorkOrderController, WorkOrderHttpError, clonePlan, defaultExecution, latestAttempt, canRetry, taskReason, validatePlan,
  type WorkOrder, type WorkOrderApi, type WorkOrderPlan, type WorkOrderSelection } from "./workOrders";

function plan(): WorkOrderPlan { return { title: "fixture", tasks: [
  { id: "read", title: "read", prompt: "read source fixture", dependencies: [], access: "read", mode: "graph", profile_id: null },
  { id: "fix", title: "fix", prompt: "prepare fixture", dependencies: ["read"], access: "write", mode: "graph", profile_id: null },
] }; }
function order(revision = 1): WorkOrder {
  const definition = plan();
  return { work_order_id: "a".repeat(32), title: definition.title, status: "draft", active_revision: revision,
    created_at: "2026-10-05", updated_at: "2026-10-05", plan: definition,
    tasks: definition.tasks.map(({ id, ...task }) => ({ ...task, task_id: id, revision, execution_revision: revision, status: "pending", retry_requested: 0 })),
    attempts: [], execution: {}, dispatch_error: "" };
}
function api(): WorkOrderApi {
  let selection = { saved: false, work_order_id: null as string | null, run_id: null as string | null, revision: 0, request_key: null as string | null };
  return { list: vi.fn(async () => [order()]), get: vi.fn(async () => order()),
    selection: vi.fn(async () => ({ ...selection })),
    runSelection: vi.fn(async (id: string) => ({ saved: false, work_order_id: id, run_id: null })),
    saveSelection: vi.fn(async (id: string | null, runId: string | null, revision: number, key: string) => {
      if (selection.request_key === key && selection.work_order_id === id && selection.run_id === runId) return { ...selection };
      if (revision !== selection.revision) throw new WorkOrderHttpError(409, "selection revision changed");
      selection = { saved: true, work_order_id: id, run_id: runId, revision: revision + 1, request_key: key };
      return { ...selection };
    }),
    queue: vi.fn(async () => ({ items: [], total: 0, limit: 100, scheduler: { active: 0, queued: 0, max_active: 4, queue_capacity: 100 } })),
    create: vi.fn(async () => order()), revise: vi.fn(async () => order(2)), plan: vi.fn(async () => plan()),
    activate: vi.fn(async (_id: string, _revision: number, settings: ReturnType<typeof defaultExecution>) => {
      const id = settings.profile_id || "fixture-default";
      return { ...order(), status: "queued", execution: { ...settings, profile_id: id, profiles: { [id]: { id, provider: "mock", model: "mock" } } } };
    }),
    control: vi.fn(async () => ({ ...order(), status: "paused" })), retry: vi.fn(async () => ({ ...order(), status: "paused" })) };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  return { promise: new Promise<T>(r => { resolve = r; }), resolve: (value: T) => resolve(value) };
}

describe("work-order domain mirror", () => {
  it("does not accept empty prompts, duplicate IDs, dangling/self/cyclic dependencies or oversized UTF8", () => {
    expect(validatePlan(plan())).toBe("");
    const invalid = plan(); invalid.tasks[0]!.prompt = " "; expect(validatePlan(invalid)).not.toBe("");
    invalid.tasks[0]!.prompt = "fixture"; invalid.tasks[1]!.id = "read"; expect(validatePlan(invalid)).toContain("重复");
    const cycle = plan(); cycle.tasks[0]!.dependencies = ["fix"]; expect(validatePlan(cycle)).toContain("循环");
    cycle.tasks[0]!.dependencies = ["unknown"]; expect(validatePlan(cycle)).toContain("未知");
    cycle.tasks[0]!.dependencies = ["read"]; expect(validatePlan(cycle)).toContain("自己");
    const large = plan(); large.tasks[0]!.prompt = "汉".repeat(90000); large.tasks[1]!.prompt = "汉".repeat(90000);
    expect(validatePlan(large)).toContain("512 KiB");
  });
  it("retries only a failed latest bound attempt, without live leases or fourth tries", () => {
    const record = order(2); record.status = "failed";
    const task = record.tasks[0]!; task.status = "failed"; task.execution_revision = 1;
    record.attempts.push({ attempt_id: "one", revision: 1, task_id: task.task_id, attempt_number: 1, run_id: "run-one", status: "failed", runtime_status: "failed", runtime_lease_active: 0 });
    expect(latestAttempt(record, task)?.attempt_id).toBe("one"); expect(canRetry(record, task)).toBe(true);
    record.attempts[0]!.runtime_lease_active = 1; expect(canRetry(record, task)).toBe(false);
    expect(taskReason(record, task)).toContain("尚未排空");
    record.attempts[0]!.runtime_lease_active = 0; record.attempts[0]!.attempt_number = 3; expect(canRetry(record, task)).toBe(false);
    record.attempts[0]!.attempt_number = 2; record.attempts[0]!.status = "awaiting_approval"; expect(canRetry(record, task)).toBe(false);
  });
});

describe("work-order controller ownership and edit boundaries", () => {
  it("saving and reading create no automatic activation or grants", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    controller.update(plan()); await controller.save();
    expect(backend.create).toHaveBeenCalledTimes(1); expect(backend.activate).not.toHaveBeenCalled();
    expect(controller.state.selected?.status).toBe("draft");
    await controller.activate(defaultExecution()); expect(backend.activate).toHaveBeenCalledWith("a".repeat(32), 1, defaultExecution());
    expect(Object.values(defaultExecution().permissions).every(v => v === false)).toBe(true);
  });
  it("unknown create result retains the same immutable payload/key and blocks close/new edits", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    vi.mocked(backend.create).mockRejectedValueOnce(new Error("offline"));
    controller.update(plan()); await controller.save();
    const first = vi.mocked(backend.create).mock.calls[0]!;
    const edited = plan(); edited.title = "must not replace unknown request"; controller.update(edited);
    expect(controller.state.draft.title).toBe("fixture"); expect(controller.state.uncertain).toBe(true);
    await expect(controller.prepareClose()).rejects.toThrow("未确认"); controller.finishClose();
    await controller.save(); expect(vi.mocked(backend.create).mock.calls[1]).toEqual(first);
    expect(controller.state.uncertain).toBe(false); expect(controller.state.dirty).toBe(false);
  });
  it("a known HTTP conflict retains editing but permits explicit discard", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    await controller.select("a".repeat(32)); const edit = plan(); edit.title = "edited"; controller.update(edit);
    vi.mocked(backend.revise).mockRejectedValueOnce(new WorkOrderHttpError(409, "revision changed"));
    await controller.save(); expect(controller.state.dirty).toBe(true); expect(controller.state.uncertain).toBe(false);
    controller.discard(); expect(controller.state.draft.title).toBe("fixture");
  });
  it("close drains an in-flight save before deciding whether unsaved state remains", async () => {
    const backend = api(), controller = new WorkOrderController(backend), gate = deferred<WorkOrder>();
    vi.mocked(backend.create).mockReturnValueOnce(gate.promise);
    controller.update(plan()); const saving = controller.save(); const closing = controller.prepareClose();
    let closed = false; void closing.then(() => { closed = true; }); await Promise.resolve(); expect(closed).toBe(false);
    gate.resolve(order()); await saving; await closing;
    expect(controller.state.closing).toBe(true); controller.finishClose(); expect(controller.state.closing).toBe(false);
  });
  it("polling never erases dirty edits or pretends a conflicting revision was saved", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    await controller.select("a".repeat(32)); const edit = plan(); edit.title = "my edit"; controller.update(edit);
    const other = order(2); other.plan.title = "another controller";
    vi.mocked(backend.get).mockResolvedValue(other); await controller.refresh();
    expect(controller.state.draft.title).toBe("my edit"); expect(controller.state.baseRevision).toBe(1);
    await controller.save(); expect(backend.revise).not.toHaveBeenCalled();
    controller.discard(); expect(controller.state.draft.title).toBe("another controller"); expect(controller.state.baseRevision).toBe(2);
  });
  it("a lost edit response clears only after exact revision/plan reconciliation", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    await controller.select("a".repeat(32)); const edit = plan(); edit.title = "saved edit"; controller.update(edit);
    vi.mocked(backend.revise).mockRejectedValueOnce(new Error("lost response")); await controller.save();
    const accepted = order(2); accepted.plan = clonePlan(edit);
    vi.mocked(backend.get).mockResolvedValue(accepted); await controller.reloadAfterUncertain();
    expect(controller.state.dirty).toBe(false); expect(controller.state.uncertain).toBe(false);
    expect(controller.state.baseRevision).toBe(2);
  });
  it("a failed confirmation read does not unfreeze an unknown save", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    await controller.select("a".repeat(32)); const edit = plan(); edit.title = "edit"; controller.update(edit);
    vi.mocked(backend.revise).mockRejectedValueOnce(new Error("lost reply")); await controller.save();
    vi.mocked(backend.get).mockRejectedValueOnce(new Error("read unavailable")); await controller.reloadAfterUncertain();
    expect(controller.state.uncertain).toBe(true); expect(controller.state.error).toBe("read unavailable");
  });
  it("late selection reads cannot overwrite a later selected work order", async () => {
    const backend = api(), controller = new WorkOrderController(backend), stale = deferred<WorkOrder>();
    vi.mocked(backend.get).mockReturnValueOnce(stale.promise);
    const first = controller.select("a".repeat(32)); const secondOrder = { ...order(), work_order_id: "b".repeat(32) };
    vi.mocked(backend.get).mockResolvedValueOnce(secondOrder); await controller.select(secondOrder.work_order_id);
    stale.resolve(order()); await first; expect(controller.state.selected?.work_order_id).toBe(secondOrder.work_order_id);
  });
  it("pausing/cancelling cannot discard unsaved edits, and history remains read-only across polls", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    await controller.select("a".repeat(32)); await controller.history(1); await controller.refresh();
    expect(controller.state.history?.revision).toBe(1);
    const edit = plan(); edit.title = "unsaved"; controller.update(edit);
    await controller.control("cancel"); expect(backend.control).not.toHaveBeenCalled();
    controller.fresh(); expect(controller.state.dirty).toBe(true);
  });
  it("caps visible summaries and keeps pagination on the submitted search, not later keystrokes", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    let page = 0;
    vi.mocked(backend.list).mockImplementation(async () => Array.from({ length: 50 }, (_, index) => ({
      ...order(), work_order_id: (++page * 100 + index).toString(16).padStart(32, "0"),
    })));
    controller.state.query = "older"; await controller.list(); controller.state.query = "not submitted";
    for (let index = 0; index < 4; index++) await controller.list(true);
    expect(controller.state.orders).toHaveLength(250); expect(controller.state.capped).toBe(true);
    expect(controller.state.more).toBe(false); expect(vi.mocked(backend.list).mock.calls.every(call => call[1] === "older")).toBe(true);
  });
});

describe("project-backed work-order selection definitions, not restart/native proof", () => {
  const oid = "a".repeat(32), rid = "c".repeat(32);
  function withRun(): WorkOrder {
    const record = order();
    record.attempts.push({ attempt_id: "attempt", revision: 1, task_id: "read", attempt_number: 1,
      run_id: rid, status: "accepted", runtime_status: "queued", runtime_lease_active: 1 });
    return record;
  }
  it("restores an order outside the recent page and its actual attempt run without any POST or activation", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    vi.mocked(backend.selection).mockResolvedValue({ saved: true, work_order_id: oid, run_id: rid, revision: 7, request_key: "saved-key" });
    vi.mocked(backend.get).mockResolvedValue(withRun()); vi.mocked(backend.list).mockResolvedValue([]);
    await controller.initialize();
    expect(controller.state.selected?.work_order_id).toBe(oid); expect(controller.state.runId).toBe(rid);
    expect(controller.state.orders).toEqual([]); expect(backend.saveSelection).not.toHaveBeenCalled();
    expect(backend.activate).not.toHaveBeenCalled(); expect(backend.control).not.toHaveBeenCalled(); expect(backend.retry).not.toHaveBeenCalled();
  });
  it("never-saved and explicit cleared records both avoid silently selecting the first recent row", async () => {
    for (const saved of [false, true]) {
      const backend = api(), controller = new WorkOrderController(backend);
      vi.mocked(backend.selection).mockResolvedValue({ saved, work_order_id: null, run_id: null, revision: saved ? 3 : 0, request_key: saved ? "clear-key" : null });
      await controller.initialize(); expect(controller.state.selected).toBeNull();
      expect(controller.state.orders).toHaveLength(1); expect(backend.saveSelection).not.toHaveBeenCalled();
    }
  });
  it("failed restore blocks edits and close, preserves the unknown record, and manual retry performs reads only", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    vi.mocked(backend.selection).mockRejectedValueOnce(new Error("offline restore"));
    await controller.initialize(); controller.update(plan()); controller.fresh(); await controller.select(oid); await controller.save();
    expect(controller.state.restoreError).toBe("offline restore"); expect(controller.state.dirty).toBe(false);
    expect(backend.create).not.toHaveBeenCalled(); expect(backend.saveSelection).not.toHaveBeenCalled();
    await expect(controller.prepareClose()).rejects.toThrow("恢复"); controller.finishClose();
    await controller.initialize(); expect(controller.state.restoreError).toBe("");
    expect(backend.saveSelection).not.toHaveBeenCalled();
  });
  it("pending restore/older open reads cannot be silently dropped by close or replace the later selection", async () => {
    const backend = api(), controller = new WorkOrderController(backend), older = deferred<WorkOrder>();
    vi.mocked(backend.get).mockReturnValueOnce(older.promise);
    const first = controller.select(oid), other = { ...order(), work_order_id: "b".repeat(32) };
    vi.mocked(backend.get).mockResolvedValueOnce(other); await controller.select(other.work_order_id);
    await expect(controller.prepareClose()).rejects.toThrow("载入"); controller.finishClose();
    older.resolve(order()); await first; await controller.flushSelection();
    expect(controller.state.selected?.work_order_id).toBe(other.work_order_id);
    expect((await backend.selection()).work_order_id).toBe(other.work_order_id);
  });
  it("select reads that order's remembered run; unrelated global runs stay transient and cannot corrupt that association", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    vi.mocked(backend.get).mockResolvedValue(withRun());
    vi.mocked(backend.runSelection).mockResolvedValue({ saved: true, work_order_id: oid, run_id: rid });
    await controller.select(oid); await controller.flushSelection();
    const calls = vi.mocked(backend.saveSelection).mock.calls.length;
    controller.openRun("d".repeat(32)); expect(controller.state.transientRun).toBe(true);
    expect(controller.state.rememberedRunId).toBe(rid); expect(vi.mocked(backend.saveSelection).mock.calls).toHaveLength(calls);
    controller.hideRun(); expect(controller.state.runId).toBe(rid); expect(controller.state.transientRun).toBe(false);
    controller.hideRun(); await controller.flushSelection();
    expect(controller.state.runId).toBe(""); expect((await backend.selection()).run_id).toBeNull();
  });
  it("opening a historical associated run persists its ID; polling/control on the same order do not reset it or write selection", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    vi.mocked(backend.get).mockResolvedValue(withRun()); await controller.select(oid); await controller.flushSelection();
    controller.openRun(rid); await controller.flushSelection(); const calls = vi.mocked(backend.saveSelection).mock.calls.length;
    vi.mocked(backend.control).mockResolvedValueOnce({ ...withRun(), status: "paused" });
    await controller.refresh(); await controller.control("pause");
    expect(controller.state.runId).toBe(rid); expect(vi.mocked(backend.saveSelection).mock.calls).toHaveLength(calls);
    expect((await backend.selection()).run_id).toBe(rid);
  });
  it("serializes navigation writes, ignores older ACK for the pending flag, and close awaits the latest tail", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    const original = vi.mocked(backend.saveSelection).getMockImplementation()!, gate = deferred<WorkOrderSelection>(), started = deferred<WorkOrderSelection>();
    vi.mocked(backend.saveSelection).mockImplementationOnce(async (...args) => {
      const reply = await original(...args); started.resolve(reply); return gate.promise;
    });
    await controller.select(oid); const firstReply = await started.promise;
    const other = { ...order(), work_order_id: "b".repeat(32) };
    vi.mocked(backend.get).mockResolvedValueOnce(other); await controller.select(other.work_order_id);
    expect(backend.saveSelection).toHaveBeenCalledTimes(1); expect(controller.state.selectionPending).toBe(true);
    const closing = controller.prepareClose(); gate.resolve(firstReply); await closing;
    expect(controller.state.selectionPending).toBe(false);
    expect((await backend.selection()).work_order_id).toBe(other.work_order_id);
    expect(vi.mocked(backend.saveSelection).mock.calls.map(call => call[2])).toEqual([0, 1]);
  });
  it("confirms a committed lost reply by canonical revision and pair without issuing a second write", async () => {
    const backend = api(), controller = new WorkOrderController(backend), original = vi.mocked(backend.saveSelection).getMockImplementation()!;
    vi.mocked(backend.saveSelection).mockImplementationOnce(async (...args) => { await original(...args); throw new Error("lost ACK"); });
    await controller.select(oid); await controller.flushSelection();
    expect(controller.state.selectionPending).toBe(false); expect(backend.saveSelection).toHaveBeenCalledTimes(1);
    expect(controller.state.selectionError).toBe("");
  });
  it("a noncommitted failure blocks close; retry keeps the exact pair/key/CAS while that revision is unchanged", async () => {
    const backend = api(), controller = new WorkOrderController(backend), original = vi.mocked(backend.saveSelection).getMockImplementation()!;
    vi.mocked(backend.saveSelection).mockRejectedValue(new Error("write unavailable"));
    await controller.select(oid); await expect(controller.prepareClose()).rejects.toThrow("write unavailable");
    controller.finishClose(); const first = vi.mocked(backend.saveSelection).mock.calls[0]!;
    expect(controller.state.selectionPending).toBe(true); expect(controller.state.selected?.work_order_id).toBe(oid);
    vi.mocked(backend.saveSelection).mockImplementation(original); await controller.retrySelection();
    expect(controller.state.selectionPending).toBe(false); expect(vi.mocked(backend.saveSelection).mock.calls.at(-1)).toEqual(first);
  });
  it("rebases only a failed metadata key after another durable version; a delayed old CAS cannot overwrite it", async () => {
    const backend = api(), controller = new WorkOrderController(backend), original = vi.mocked(backend.saveSelection).getMockImplementation()!;
    const attempted = deferred<void>();
    vi.mocked(backend.saveSelection).mockImplementationOnce(async () => { attempted.resolve(undefined); throw new Error("HTTP failed before late commit"); });
    await controller.select(oid);
    await attempted.promise;
    const other = "b".repeat(32);
    await original(other, null, 0, "external-navigation-key");
    await controller.flushSelection();
    const calls = vi.mocked(backend.saveSelection).mock.calls, late = calls[0]!, latest = calls.at(-1)!;
    expect(latest[2]).toBe(1); expect(latest[3]).not.toBe(late[3]);
    await expect(original(...late)).rejects.toThrow("revision changed");
    expect((await backend.selection()).work_order_id).toBe(oid);
  });
  it("failed canonical confirmation cannot clear pending state or let close through", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    const attempted = deferred<void>();
    vi.mocked(backend.saveSelection).mockImplementation(async () => { attempted.resolve(undefined); throw new Error("lost reply"); });
    await controller.select(oid); await attempted.promise;
    vi.mocked(backend.selection).mockRejectedValue(new Error("confirmation offline"));
    await expect(controller.prepareClose()).rejects.toThrow("confirmation offline");
    expect(controller.state.selectionPending).toBe(true); controller.finishClose();
  });
  it("foreign restore run and regressed metadata revision remain fail-closed", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    vi.mocked(backend.selection).mockResolvedValueOnce({ saved: true, work_order_id: oid, run_id: rid, revision: 4, request_key: "foreign-run-key" });
    await controller.initialize(); expect(controller.state.restoreError).toContain("不属于");
    expect(backend.saveSelection).not.toHaveBeenCalled();
    await controller.initialize();
    vi.mocked(backend.selection).mockResolvedValueOnce({ saved: true, work_order_id: null, run_id: null, revision: 9, request_key: "old-version-key" });
    const attempted = deferred<void>();
    vi.mocked(backend.saveSelection).mockImplementationOnce(async () => { attempted.resolve(undefined); throw new Error("lost reply"); });
    await controller.select(oid); await attempted.promise;
    await expect(controller.prepareClose()).rejects.toThrow("版本倒退");
    expect(controller.state.selectionPending).toBe(true);
    expect(backend.saveSelection).toHaveBeenCalledTimes(1); controller.finishClose();
  });
  it("new draft clears the global selection explicitly, and dispose discards late restore reads without POST", async () => {
    const backend = api(), controller = new WorkOrderController(backend);
    await controller.select(oid); controller.fresh(); await controller.flushSelection();
    expect((await backend.selection()).work_order_id).toBeNull();
    const next = new WorkOrderController(backend), gate = deferred<WorkOrderSelection>();
    vi.mocked(backend.selection).mockReturnValueOnce(gate.promise);
    const restoring = next.initialize(), writes = vi.mocked(backend.saveSelection).mock.calls.length;
    next.dispose(); gate.resolve({ saved: true, work_order_id: oid, run_id: null, revision: 9, request_key: "late-key" });
    await restoring; expect(next.state.selected).toBeNull(); expect(vi.mocked(backend.saveSelection).mock.calls).toHaveLength(writes);
  });
});

describe("plan and queue SSR contracts, not WebView proof", () => {
  it("escapes untrusted plan text and separates saving from permissioned execution", async () => {
    const record = order(); record.plan.title = "<script>fixture</script>";
    const html = await renderToString(createSSRApp(PlanPanel, { plan: record.plan, order: record, dirty: false, disabled: false, uncertain: false, profiles: [] }));
    expect(html).toContain("&lt;script&gt;"); expect(html).not.toContain("<script>fixture");
    expect(html).toContain("保存新计划版本"); expect(html).toContain("批准此版本并进入队列");
    expect(html).toContain("审批仍独立处理");
  });
  it("unknown occupancy is not zero and approval-pending is not completion", async () => {
    const record = order(); record.status = "running"; record.tasks[0]!.status = "awaiting_approval";
    record.attempts.push({ attempt_id: "one", revision: 1, task_id: "read", attempt_number: 1, run_id: "real-run", status: "awaiting_approval", runtime_status: "interrupted", runtime_lease_active: 1 });
    const html = await renderToString(createSSRApp(TaskQueue, { order: record, queue: null, queueError: "", disabled: false }));
    expect(html).toContain("不能把未知占用当作零"); expect(html).toContain("等待审批");
    expect(html).toContain("打开真实运行/审批"); expect(html).not.toContain("100%");
    expect(html).toContain("未验证"); expect(html).toContain("real-run");
  });
});
