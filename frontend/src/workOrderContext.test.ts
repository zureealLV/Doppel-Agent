// Source/controller/SSR definitions only; run at S9, not native/provider proof.
import { afterEach, describe, expect, it, vi } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import PlanPanel from "./components/PlanPanel.vue";
import ContextBindingReceipt from "./components/ContextBindingReceipt.vue";
import { defaultExecution, workOrderApi, WorkOrderController, WorkOrderHttpError,
  type ContextBinding, type WorkOrder, type WorkOrderApi, type WorkOrderPlan, type WorkOrderSelection } from "./workOrders";
import type { ContextDescriptor, PrepareContext } from "./types";

const oid = "a".repeat(32), clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const descriptor = (): ContextDescriptor => ({ manifest_id: "b".repeat(32), notes: [{ note_id: "c".repeat(32), revision: 1 }] });
function plan(): WorkOrderPlan { return { title: "fixture", tasks: [
  { id: "first", title: "first", prompt: "read fixture", dependencies: [], access: "read", mode: "graph", profile_id: null },
  { id: "future", title: "future", prompt: "future fixture", dependencies: ["first"], access: "read", mode: "deep", profile_id: null },
] }; }
function record(): WorkOrder { const definition = plan(); return { work_order_id: oid, title: "fixture", status: "draft", active_revision: 1,
  created_at: "fixture", updated_at: "fixture", plan: definition, attempts: [], execution: {}, dispatch_error: "", context_bindings: [],
  tasks: definition.tasks.map(({ id, ...task }) => ({ ...task, task_id: id, revision: 1, execution_revision: 1, status: "pending", retry_requested: 0 })) }; }
function binding(revision: number, context = descriptor()): ContextBinding { return { revision, bound_revision: revision, descriptor: clone(context),
  captured_at: `capture-${revision}`, context_sha256: "d".repeat(64), context_bytes: 128, estimated_tokens: 32, estimate_method: "utf8_bytes_div4", actual_tokens: null }; }
function harness(initial = record()) {
  const server = { record: clone(initial) };
  let selection: WorkOrderSelection = { saved: false, work_order_id: null, run_id: null, revision: 0, request_key: null };
  const backend: WorkOrderApi = {
    get: vi.fn(async () => clone(server.record)), list: vi.fn(async () => [clone(server.record)]),
    selection: vi.fn(async () => clone(selection)), runSelection: vi.fn(async id => ({ saved: false, work_order_id: id, run_id: null })),
    saveSelection: vi.fn(async (id, run, revision, key) => {
      selection = { saved: true, work_order_id: id, run_id: run, revision: revision + 1, request_key: key }; return clone(selection);
    }),
    queue: vi.fn(async () => ({ items: [], total: 0, limit: 100, scheduler: { active: 0, queued: 0, max_active: 4, queue_capacity: 100 } })),
    create: vi.fn(async () => clone(server.record)), plan: vi.fn(async () => clone(server.record.plan)),
    activate: vi.fn(async (_id, revision, settings) => {
      if (revision !== server.record.active_revision) throw new WorkOrderHttpError(409, "revision changed");
      const fixed = { ...clone(settings), profile_id: settings.profile_id || "mock", profiles: { mock: { id: "mock", provider: "mock", model: "mock" } } };
      if (server.record.status !== "draft") {
        if (JSON.stringify(fixed) !== JSON.stringify(server.record.execution)) throw new WorkOrderHttpError(409, "different execution");
        return clone(server.record);
      }
      server.record.status = "queued"; server.record.execution = fixed;
      server.record.context_bindings = settings.context ? [binding(revision, settings.context)] : [];
      return clone(server.record);
    }),
    revise: vi.fn<WorkOrderApi['revise']>(async (_id, revision, definition, context) => {
      if (revision !== server.record.active_revision) throw new WorkOrderHttpError(409, "revision changed");
      const previous = server.record, next = revision + 1, carried = previous.context_bindings?.find(b => b.revision === revision);
      server.record = { ...previous, active_revision: next, status: "paused", plan: clone(definition), title: definition.title,
        execution: context === undefined ? previous.execution : { ...previous.execution, context: clone(context) },
        context_bindings: context ? [binding(next, context)] : carried ? [{ ...clone(carried), revision: next }] : [],
        tasks: definition.tasks.map(({ id, ...task }) => ({ ...task, task_id: id, revision: next,
          execution_revision: previous.attempts.some(a => a.task_id === id) ? previous.tasks.find(t => t.task_id === id)!.execution_revision : next,
          status: previous.attempts.some(a => a.task_id === id) ? previous.tasks.find(t => t.task_id === id)!.status : "pending", retry_requested: 0 })) };
      for (const old of previous.context_bindings || []) {
        if (server.record.tasks.some(t => t.execution_revision === old.revision && previous.attempts.some(a => a.task_id === t.task_id))) server.record.context_bindings!.unshift(clone(old));
      }
      return clone(server.record);
    }),
    control: vi.fn(async () => clone(server.record)), retry: vi.fn(async () => clone(server.record)),
  };
  return { backend, server };
}
function deferred<T>() { let resolve!: (value: T) => void; return { promise: new Promise<T>(r => { resolve = r; }), resolve }; }
afterEach(() => vi.unstubAllGlobals());

describe("work-order explicit activation/context preparation definitions", () => {
  it("does not read panel context on default-off activation, even when a caller supplies a descriptor", async () => {
    const { backend } = harness(), prepare = vi.fn<PrepareContext>(async () => descriptor()), controller = new WorkOrderController(backend, prepare);
    await controller.select(oid); await controller.activate({ ...defaultExecution(), context: descriptor() });
    expect(prepare).not.toHaveBeenCalled(); expect(vi.mocked(backend.activate).mock.calls[0]![2]).not.toHaveProperty("context");
    expect(controller.state.selected?.status).toBe("queued"); expect(controller.state.uncertain).toBe(false);
  });
  it("waits for own-order context flush, freezes settings before await, and refuses navigation/new submits", async () => {
    const { backend } = harness(), gate = deferred<ContextDescriptor>(), prepare = vi.fn<PrepareContext>(() => gate.promise);
    const controller = new WorkOrderController(backend, prepare); await controller.select(oid);
    const settings = defaultExecution(), pending = controller.activate(settings, true);
    settings.permissions.command_execute = true; settings.effort = "deep";
    await controller.select("e".repeat(32)); await controller.activate(settings, true); controller.update({ ...plan(), title: "changed" });
    expect(backend.activate).not.toHaveBeenCalled(); expect(controller.state.selected?.work_order_id).toBe(oid);
    gate.resolve(descriptor()); await pending;
    expect(prepare).toHaveBeenCalledWith(oid); expect(backend.activate).toHaveBeenCalledTimes(1);
    expect(vi.mocked(backend.activate).mock.calls[0]![2]).toEqual({ ...defaultExecution(), context: descriptor() });
  });
  it("preparation failure sends no activation, remains known and allows deliberate retry", async () => {
    const { backend } = harness(), prepare = vi.fn<PrepareContext>().mockRejectedValueOnce(new Error("wrong namespace")).mockResolvedValueOnce(descriptor());
    const controller = new WorkOrderController(backend, prepare); await controller.select(oid);
    await controller.activate(defaultExecution(), true); expect(backend.activate).not.toHaveBeenCalled(); expect(controller.state.uncertain).toBe(false);
    await controller.activate(defaultExecution(), true); expect(backend.activate).toHaveBeenCalledTimes(1); expect(controller.state.error).toBe("");
  });
  it("keeps original settings/context across lost ACK and later 4xx, and recovers paused without reactivation", async () => {
    const { backend, server } = harness(), picked = descriptor(), prepare = vi.fn<PrepareContext>(async () => picked);
    const controller = new WorkOrderController(backend, prepare); await controller.select(oid);
    const activate = vi.mocked(backend.activate).getMockImplementation()!;
    vi.mocked(backend.activate).mockImplementationOnce(async (...args) => { await activate(...args); throw new Error("lost ACK"); })
      .mockRejectedValueOnce(new WorkOrderHttpError(409, "later refusal"));
    await controller.activate(defaultExecution(), true); picked.notes[0]!.revision = 9; picked.manifest_id = null;
    expect(controller.state.uncertainOperation).toBe("activate"); await expect(controller.prepareClose()).rejects.toThrow("未确认"); controller.finishClose();
    await controller.save(); await controller.activate({ ...defaultExecution(), effort: "deep" }, true); controller.discard();
    expect(backend.activate).toHaveBeenCalledTimes(1); await controller.retryPending(); expect(controller.state.uncertain).toBe(true);
    server.record.status = "paused"; await controller.retryPending();
    const calls = vi.mocked(backend.activate).mock.calls; expect(calls[1]).toEqual(calls[0]); expect(calls[2]).toEqual(calls[0]); expect(prepare).toHaveBeenCalledTimes(1);
    expect(controller.state.selected?.status).toBe("paused"); expect(controller.state.uncertain).toBe(false);
  });
  it("does not accept same-status polling with different grants; conflict adoption is explicit, fresh and read-only", async () => {
    const { backend, server } = harness(), controller = new WorkOrderController(backend); await controller.select(oid);
    vi.mocked(backend.activate).mockRejectedValueOnce(new Error("lost ACK")); await controller.activate(defaultExecution());
    server.record.status = "queued"; server.record.execution = { ...defaultExecution(), permissions: { ...defaultExecution().permissions, command_execute: true } };
    await controller.refresh(); expect(controller.state.uncertain).toBe(true); expect(controller.state.conflict).toBe(true);
    await controller.adoptServerVersion(false); expect(controller.state.uncertain).toBe(true);
    await controller.adoptServerVersion(true); expect(controller.state.uncertain).toBe(false); expect(controller.state.selected?.execution.permissions?.command_execute).toBe(true);
    expect(backend.activate).toHaveBeenCalledTimes(1); expect(backend.control).not.toHaveBeenCalled(); expect(controller.state.error).toContain("不是旧请求成功");
  });
});

describe("future-only revision binding and unknown reconciliation definitions", () => {
  it("permits context-only future revision and preserves begun execution binding without implicit rebind on later edits", async () => {
    const { backend, server } = harness(), prepare = vi.fn<PrepareContext>(async () => descriptor()), controller = new WorkOrderController(backend, prepare);
    await controller.select(oid); await controller.activate(defaultExecution(), true);
    server.record.attempts.push({ attempt_id: "old-attempt", revision: 1, task_id: "first", attempt_number: 1, run_id: "old-run", status: "running", runtime_status: "running", runtime_lease_active: 1 });
    server.record.tasks[0]!.status = "running"; await controller.refresh(); controller.setContextRebind(true);
    expect(controller.state.dirty).toBe(false); expect(controller.hasEdits).toBe(true);
    await controller.control("resume"); expect(backend.control).not.toHaveBeenCalled(); await expect(controller.prepareClose()).rejects.toThrow("未保存"); controller.finishClose();
    await controller.save(); expect(vi.mocked(backend.revise).mock.calls[0]).toEqual([oid, 1, plan(), descriptor()]);
    expect(controller.state.selected?.tasks.map(t => t.execution_revision)).toEqual([1, 2]); expect(controller.state.selected?.context_bindings?.map(b => b.bound_revision)).toEqual([1, 2]);
    const edit = plan(); edit.tasks[1]!.prompt = "only future definition changed"; controller.update(edit); await controller.save();
    expect(vi.mocked(backend.revise).mock.calls[1]).toHaveLength(3); expect(prepare).toHaveBeenCalledTimes(2); // activation + one explicit rebind
    expect(controller.state.selected?.context_bindings?.at(-1)?.bound_revision).toBe(2);
  });
  it("does not confuse identical plan/descriptor copied from an old binding with a newly confirmed rebind", async () => {
    const { backend, server } = harness(), picked = descriptor(), prepare = vi.fn<PrepareContext>(async () => picked), controller = new WorkOrderController(backend, prepare);
    await controller.select(oid); await controller.activate(defaultExecution(), true); controller.setContextRebind(true);
    vi.mocked(backend.revise).mockRejectedValueOnce(new Error("lost revision ACK")); await controller.save();
    picked.notes[0]!.revision = 10; server.record.active_revision = 2;
    server.record.tasks = server.record.tasks.map(task => ({ ...task, revision: 2, execution_revision: 2 }));
    server.record.context_bindings = [{ ...binding(1), revision: 2 }]; server.record.status = "paused";
    await controller.refresh(); await controller.reloadAfterUncertain(); expect(controller.state.uncertain).toBe(true); expect(controller.state.rebindContext).toBe(true);
    server.record.context_bindings = [binding(2)]; await controller.reloadAfterUncertain();
    expect(controller.state.uncertain).toBe(false); expect(controller.state.rebindContext).toBe(false); expect(backend.revise).toHaveBeenCalledTimes(1);
  });
  it("unknown future save retries the same plan/CAS/descriptor after later 4xx, not a changed panel", async () => {
    const initial = record(); initial.status = "paused"; initial.execution = defaultExecution();
    const { backend } = harness(initial), picked = descriptor(), prepare = vi.fn<PrepareContext>(async () => picked), controller = new WorkOrderController(backend, prepare);
    await controller.select(oid); controller.setContextRebind(true);
    vi.mocked(backend.revise).mockRejectedValueOnce(new Error("lost revision")).mockRejectedValueOnce(new WorkOrderHttpError(400, "later source stale"));
    await controller.save(); picked.notes[0]!.revision = 88; controller.setContextRebind(false); controller.update({ ...plan(), title: "new body" });
    await controller.retryPending(); expect(controller.state.uncertain).toBe(true); await controller.retryPending();
    const calls = vi.mocked(backend.revise).mock.calls; expect(calls[1]).toEqual(calls[0]); expect(calls[2]).toEqual(calls[0]); expect(prepare).toHaveBeenCalledTimes(1);
    expect(controller.state.uncertain).toBe(false); expect(controller.state.selected?.execution.context).toEqual(descriptor());
  });
  it("rebind preparation/first rejection never silently clears local intent or starts a run", async () => {
    const initial = record(); initial.status = "paused"; const { backend } = harness(initial);
    const prepare = vi.fn<PrepareContext>().mockRejectedValueOnce(new Error("unaccepted file edit")).mockResolvedValueOnce(descriptor());
    const controller = new WorkOrderController(backend, prepare); await controller.select(oid); controller.setContextRebind(true); await controller.save();
    expect(backend.revise).not.toHaveBeenCalled(); expect(controller.state.rebindContext).toBe(true); expect(controller.state.uncertain).toBe(false);
    vi.mocked(backend.revise).mockRejectedValueOnce(new WorkOrderHttpError(400, "context_file_stale")); await controller.save();
    expect(controller.state.uncertain).toBe(false); expect(controller.state.rebindContext).toBe(true);
    controller.discard(); expect(controller.hasEdits).toBe(false); expect(backend.activate).not.toHaveBeenCalled();
  });
  it("explicit empty rebind is a saved descriptor, not an omitted request or automatic latest note", async () => {
    const initial = record(); initial.status = "paused"; const { backend } = harness(initial), empty = { manifest_id: null, notes: [] };
    const prepare = vi.fn<PrepareContext>(async () => empty), controller = new WorkOrderController(backend, prepare);
    await controller.select(oid); controller.setContextRebind(true); await controller.save();
    expect(vi.mocked(backend.revise).mock.calls[0]![3]).toEqual(empty); expect(controller.state.selected?.context_bindings?.[0]?.descriptor).toEqual(empty);
  });
});

describe("binding UI/HTTP contract definitions, not rendered native acceptance", () => {
  it("separates carried/fresh execution provenance and estimates from current picks and actual usage", async () => {
    const html = await renderToString(createSSRApp(ContextBindingReceipt, { bindings: [binding(1), { ...binding(1), revision: 2 }], activeRevision: 2 }));
    for (const expected of ["不是当前面板选择", "已开始节点保留", "沿用 r1 固定快照", "不是实际用量", "d".repeat(64), "c".repeat(32)]) expect(html).toContain(expected);
    expect(html).not.toContain("hash 与本次检查一致"); expect(html).not.toContain("验证已通过");
  });
  it("offers explicit activation consent, future-only context-only revision and uncertain activation recovery", async () => {
    const initial = record();
    const draft = await renderToString(createSSRApp(PlanPanel, { plan: initial.plan, order: initial, dirty: false, disabled: false, uncertain: false, profiles: [] }));
    expect(draft).toContain("默认不绑定"); expect(draft).toContain("草稿保存不等于接受上下文");
    initial.status = "paused";
    const started = await renderToString(createSSRApp(PlanPanel, { plan: initial.plan, order: initial, dirty: false, rebindContext: true, disabled: false, uncertain: true, uncertainOperation: "activate", conflict: true, profiles: [] }));
    expect(started).toContain("仅用于尚未开始的节点"); expect(started).toContain("只改上下文"); expect(started).toContain("激活结果尚未确认");
    expect(started).toContain("采用服务端版本"); expect(started).toContain("不判定旧请求成功或未执行");
  });
  it("omits absent revision descriptor, sends explicit empty, and never accepts a client snapshot field", async () => {
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(record()), { status: 200 })); vi.stubGlobal("fetch", fetcher);
    await workOrderApi.revise(oid, 1, plan()); await workOrderApi.revise(oid, 1, plan(), { manifest_id: null, notes: [] });
    const first = JSON.parse(fetcher.mock.calls[0]![1]!.body as string) as Record<string, unknown>;
    const second = JSON.parse(fetcher.mock.calls[1]![1]!.body as string) as Record<string, unknown>;
    expect(first).not.toHaveProperty("context"); expect(second.context).toEqual({ manifest_id: null, notes: [] }); expect(second).not.toHaveProperty("input_snapshot");
  });
});
