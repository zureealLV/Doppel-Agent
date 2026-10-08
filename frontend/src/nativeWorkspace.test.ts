import { beforeEach, describe, expect, it, vi } from "vitest";
import { NativeWorkspaceController } from "./workspace";
import { nativeWorkspaceApi as api, workspaceApi } from "./workspaceApi";
import type { NativeConversation, PublicSettings } from "./workspaceTypes";
vi.mock("./workspaceApi", () => ({ nativeWorkspaceApi: { selection: vi.fn(), saveSelection: vi.fn(), conversations: vi.fn(), groups: vi.fn(), conversation: vi.fn(), draft: vi.fn(), update: vi.fn(), search: vi.fn(), start: vi.fn() }, workspaceApi: { health: vi.fn(), settings: vi.fn() } }));
const settings: PublicSettings = { active_profile_id: "mock", key_protection: "fixture", profiles: [{ id: "mock", name: "Mock", provider: "mock", preset: "mock", model: "mock", base_url: "", input_price: 0, output_price: 0, api_key_saved: false }] };
const conv = (id = "c", changes = {}): NativeConversation => ({ id, mode: "graph", thread_id: "native-t", title: "新对话", archived: 0, group_id: null, group_name: null, profile_id: "mock", created_at: "", updated_at: "", messages: [], runs: [], active_run_id: null, ...changes });
const turn = { prompt: "hi", effort: "balanced" as const, permissions: { workspace_write: false, command_execute: false, mcp_execute: false, delegate: false } };
const deferred = <T>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; };
const make = () => { const store = new Map<string, string>(); const controller = new NativeWorkspaceController({ getItem: k => store.get(k) || null, setItem: (k,v) => { store.set(k,v); }, removeItem: k => { store.delete(k); } }); controller.updateSettings(settings); return { controller, store }; };
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.selection).mockResolvedValue({ saved: false, conversation_id: null, run_id: null }); vi.mocked(api.saveSelection).mockImplementation(async (conversation_id, run_id) => ({ saved: true, conversation_id, run_id })); vi.mocked(api.conversations).mockResolvedValue([]); vi.mocked(api.groups).mockResolvedValue([]); vi.mocked(workspaceApi.health).mockResolvedValue({ status: "ok", workspace: "disposable" }); vi.mocked(workspaceApi.settings).mockResolvedValue(settings); vi.mocked(api.conversation).mockResolvedValue(conv()); vi.mocked(api.draft).mockResolvedValue(conv()); });
describe("native conversation behavior", () => {
  it.each(["graph", "deep", "legacy"] as const)("keeps an archived %s profile read-only until explicitly reopened", async mode => {
    const { controller } = make();
    const second = { ...settings.profiles[0]!, id: "two" };
    controller.updateSettings({ ...settings, profiles: [...settings.profiles, second] });
    vi.mocked(api.conversation).mockResolvedValue(conv("c", { mode, archived: 1 }));
    await controller.open("c");
    const before = JSON.parse(JSON.stringify(controller.state.current));
    await controller.chooseProfile("two");
    expect(api.update).not.toHaveBeenCalled();
    expect(controller.state.profileId).toBe("mock");
    expect(controller.state.current).toEqual(before);
    // Archiving is not a global busy lock: a fresh conversation remains possible.
    expect(controller.busy).toBe(false);
    vi.mocked(api.conversation).mockResolvedValue(conv("c", { mode, archived: 0 }));
    vi.mocked(api.update).mockResolvedValue(conv("c", { mode, profile_id: "two" }));
    await controller.open("c");
    await controller.chooseProfile("two");
    expect(api.update).toHaveBeenCalledExactlyOnceWith("c", { profile_id: "two" });
    expect(controller.state.profileId).toBe("two");
  });
  it("drains pending audit preferences before toolbar close and gates new native navigation", async () => {
    const { controller } = make();
    vi.mocked(api.conversation).mockResolvedValue(conv("c", { runs: [
      { run_id: "r1", mode: "graph", status: "completed", lease_active: 0 },
      { run_id: "r2", mode: "graph", status: "completed", lease_active: 0 },
    ] }));
    await controller.open("c");
    const slow = deferred<{ saved: boolean; conversation_id: string | null; run_id: string | null }>();
    vi.mocked(api.saveSelection).mockReturnValueOnce(slow.promise);
    controller.selectRun("r1");
    const close = controller.prepareClose();
    expect(controller.state.closing).toBe(true);
    controller.selectRun("r2"); controller.clearSelection();
    await controller.open("another"); await controller.submit(turn);
    expect(controller.state.current?.id).toBe("c");
    expect(controller.state.selectedRunId).toBe("r1");
    expect(api.start).not.toHaveBeenCalled();
    slow.resolve({ saved: true, conversation_id: "c", run_id: "r1" });
    await close;
    controller.finishClose();
    expect(controller.state.closing).toBe(false);
  });
  it("refuses close after a failed preference and allows selection repair without replaying work", async () => {
    const { controller } = make();
    vi.mocked(api.saveSelection).mockRejectedValueOnce(new Error("private preference failure"));
    await controller.open("c");
    await expect(controller.prepareClose()).rejects.toThrow("preference was not saved");
    controller.finishClose();
    await controller.open("c");
    await controller.prepareClose(); controller.finishClose();
    expect(api.start).not.toHaveBeenCalled();
  });
  it("permits toolbar close during an active/interrupted run without cancel or resume calls", async () => {
    const { controller } = make();
    vi.mocked(api.conversation).mockResolvedValue(conv("c", { active_run_id: "r", runs: [
      { run_id: "r", mode: "graph", status: "interrupted", lease_active: 1 },
    ] }));
    await controller.open("c");
    expect(controller.busy).toBe(true);
    await controller.prepareClose(); controller.finishClose();
    expect(controller.state.current?.active_run_id).toBe("r");
    expect(api.start).not.toHaveBeenCalled();
  });
  it.each(["loading", "opening", "transition", "submitting"] as const)("refuses close while %s has not settled", async flag => {
    const { controller } = make();
    controller.state[flag] = true;
    await expect(controller.prepareClose()).rejects.toThrow("still pending");
    expect(controller.state.closing).toBe(false);
  });
  it("flushes writes appended after the first queue snapshot", async () => {
    const { controller } = make();
    vi.mocked(api.conversation).mockResolvedValue(conv("c", { runs: [
      { run_id: "r1", mode: "graph", status: "completed", lease_active: 0 },
      { run_id: "r2", mode: "graph", status: "completed", lease_active: 0 },
    ] }));
    await controller.open("c"); vi.mocked(api.saveSelection).mockClear();
    const first = deferred<{ saved: boolean; conversation_id: string | null; run_id: string | null }>();
    const second = deferred<{ saved: boolean; conversation_id: string | null; run_id: string | null }>();
    vi.mocked(api.saveSelection).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    controller.selectRun("r1");
    let finished = false;
    const flush = controller.flushSelection().then(() => { finished = true; });
    controller.selectRun("r2");
    first.resolve({ saved: true, conversation_id: "c", run_id: "r1" });
    await vi.waitFor(() => expect(api.saveSelection).toHaveBeenCalledTimes(2));
    expect(finished).toBe(false);
    second.resolve({ saved: true, conversation_id: "c", run_id: "r2" });
    await flush;
    expect(finished).toBe(true);
  });
  it("restores server-selected older archive/run with empty new-origin storage", async () => {
    const { controller, store } = make();
    vi.mocked(api.selection).mockResolvedValue({ saved: true, conversation_id: "old", run_id: "r1" });
    vi.mocked(api.conversations).mockResolvedValue([conv("recent")]);
    vi.mocked(api.conversation).mockResolvedValue(conv("old", { archived: 1, selected_run_id: "r1", runs: [
      { run_id: "r1", mode: "graph", status: "completed", lease_active: 0 },
      { run_id: "r2", mode: "graph", status: "completed", lease_active: 0 },
    ] }));
    await controller.initialize();
    expect(controller.state.current?.id).toBe("old");
    expect(controller.state.selectedRunId).toBe("r1");
    expect(controller.state.archived).toBe(true);
    expect(api.conversations).toHaveBeenLastCalledWith(true);
    expect(store.get("doppel.native.conversation")).toBe("old");
    expect(api.start).not.toHaveBeenCalled();
  });
  it("does not resurrect a cleared server choice from stale browser IDs", async () => {
    const { controller, store } = make();
    store.set("doppel.native.conversation", "stale");
    vi.mocked(api.selection).mockResolvedValue({ saved: true, conversation_id: null, run_id: null });
    vi.mocked(api.conversations).mockResolvedValue([conv("recent")]);
    await controller.initialize();
    expect(controller.state.current).toBeNull();
    expect(api.conversation).not.toHaveBeenCalled();
  });
  it("reports a failed persisted-selection load without falling back to an unrelated recent row", async () => {
    const { controller } = make();
    vi.mocked(api.selection).mockResolvedValue({ saved: true, conversation_id: "old", run_id: null });
    vi.mocked(api.conversation).mockRejectedValue(new Error("selected history unavailable"));
    vi.mocked(api.conversations).mockResolvedValue([conv("recent")]);
    await controller.initialize();
    expect(controller.state.current).toBeNull();
    expect(controller.state.error).toBe("selected history unavailable");
    expect(api.conversation).toHaveBeenCalledTimes(1);
  });
  it("orders selection writes and drains the newest after a slow older write", async () => {
    const { controller } = make();
    const slow = deferred<{ saved: boolean; conversation_id: string | null; run_id: string | null }>();
    vi.mocked(api.saveSelection).mockReturnValueOnce(slow.promise);
    vi.mocked(api.conversation).mockResolvedValueOnce(conv("a")).mockResolvedValueOnce(conv("b"));
    const first = controller.open("a");
    await vi.waitFor(() => expect(api.saveSelection).toHaveBeenCalledTimes(1));
    const second = controller.open("b");
    await vi.waitFor(() => expect(controller.state.current?.id).toBe("b"));
    expect(api.saveSelection).toHaveBeenCalledTimes(1);
    slow.resolve({ saved: true, conversation_id: "a", run_id: null });
    await Promise.all([first, second]); await controller.flushSelection();
    expect(vi.mocked(api.saveSelection).mock.calls).toEqual([["a", null], ["b", null]]);
    expect(controller.state.selectionSaving).toBe(false);
  });
  it("reports preference failures without resubmitting runtime work and permits a retry", async () => {
    const { controller } = make();
    vi.mocked(api.saveSelection).mockRejectedValueOnce(new Error("private transport detail"));
    await controller.open("c");
    expect(controller.state.current?.id).toBe("c");
    expect(controller.state.selectionError).toContain("保存失败");
    expect(controller.state.selectionError).not.toContain("private");
    await controller.open("c");
    expect(controller.state.selectionError).toBe("");
    expect(api.start).not.toHaveBeenCalled();
  });
  it("prioritizes the active lease over an older saved audit and remembers explicit empty audit", async () => {
    const { controller } = make();
    vi.mocked(api.conversation).mockResolvedValue(conv("c", { selected_run_id: "old", active_run_id: "active", runs: [
      { run_id: "old", mode: "graph", status: "completed", lease_active: 0 },
      { run_id: "active", mode: "graph", status: "interrupted", lease_active: 1 },
    ] }));
    await controller.open("c");
    expect(controller.state.selectedRunId).toBe("active");
    controller.selectRun(""); await controller.flushSelection();
    expect(api.saveSelection).toHaveBeenLastCalledWith("c", null);
    controller.clearSelection(); await controller.flushSelection();
    expect(api.saveSelection).toHaveBeenLastCalledWith(null, null);
  });
  it("initializes empty without a probe or run", async () => { const { controller } = make(); await controller.initialize(); expect(controller.state.loading).toBe(false); expect(controller.state.current).toBeNull(); expect(api.start).not.toHaveBeenCalled(); });
  it("reports initialization failure and permits a retry", async () => { const { controller } = make(); vi.mocked(workspaceApi.health).mockRejectedValueOnce(new Error("offline")); await controller.initialize(); expect(controller.state.error).toBe("offline"); await controller.initialize(); expect(controller.state.settings).toEqual(settings); });
  it("restores archived selection and interrupted run from the server, not another Legacy key", async () => { const { controller, store } = make(); store.set("doppel-conversation", "legacy"); store.set("doppel.native.conversation", "c"); const item = conv("c", { archived: 1, active_run_id: "r", runs: [{ run_id: "r", mode: "graph", status: "interrupted", lease_active: 1 }] }); vi.mocked(api.conversation).mockResolvedValue(item); await controller.initialize(); expect(controller.state.archived).toBe(true); expect(controller.state.selectedRunId).toBe("r"); expect(controller.busy).toBe(true); expect(api.conversation).toHaveBeenCalledWith("c"); });
  it("latest selection wins and sending is blocked while opening", async () => { const { controller } = make(); const slow = deferred<NativeConversation>(); vi.mocked(api.conversation).mockReturnValueOnce(slow.promise).mockResolvedValueOnce(conv("b")); const opening = controller.open("a"); await controller.submit(turn); expect(api.start).not.toHaveBeenCalled(); await controller.open("b"); slow.resolve(conv("a")); await opening; expect(controller.state.current?.id).toBe("b"); expect(controller.busy).toBe(false); });
  it("ignores an old search after clearing", async () => { const { controller } = make(); const slow = deferred<NativeConversation[]>(); vi.mocked(api.search).mockReturnValue(slow.promise); const search = controller.search("archive"); await controller.search(""); slow.resolve([conv()]); await search; expect(controller.state.searchResults).toEqual([]); expect(controller.state.searching).toBe(false); });
  it("ignores an older archive list response", async () => { const { controller } = make(); const slow = deferred<NativeConversation[]>(); vi.mocked(api.conversations).mockReturnValueOnce(slow.promise).mockResolvedValueOnce([conv("archive", { archived: 1 })]); const list = controller.refreshList(); controller.state.archived = true; await controller.refreshList(); slow.resolve([conv("recent")]); await list; expect(controller.state.conversations[0]?.id).toBe("archive"); });
  it("prevents double posting and stores only selection IDs", async () => { const { controller, store } = make(); await controller.open("c"); const slow = deferred<{ run_id: string; conversation_id: string; status: string }>(); vi.mocked(api.start).mockReturnValue(slow.promise); const send = controller.submit(turn); await controller.submit(turn); expect(api.start).toHaveBeenCalledTimes(1); slow.resolve({ run_id: "r", conversation_id: "c", status: "queued" }); await send; expect([...store.values()].join()).not.toContain("hi"); expect([...store.values()]).toContain("r"); });
  it("reuses the same idempotency key only through explicit original-request retry after a lost response", async () => { const { controller } = make(); await controller.open("c"); vi.mocked(api.start).mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce({ run_id: "r", conversation_id: "c", status: "queued" }); await expect(controller.submit(turn)).rejects.toThrow("network"); await controller.submit({ ...turn, prompt: "changed draft" }); expect(api.start).toHaveBeenCalledTimes(1); await controller.retrySubmission(); const calls = vi.mocked(api.start).mock.calls; expect(calls[0]![1]).toEqual(calls[1]![1]); expect(calls[0]![1]).toMatchObject({ mode: "graph", conversation_id: "c", profile_id: "mock" }); });
  it("binds project picks once on explicit consent, never re-prepares context on unknown retry or navigates away", async () => {
    const picked = { manifest_id: "a".repeat(32) as string | null, notes: [{ note_id: "b".repeat(32), revision: 1 }] };
    const prepare = vi.fn(async () => picked), controller = new NativeWorkspaceController(undefined, prepare);
    controller.updateSettings(settings); await controller.open("c");
    vi.mocked(api.start).mockRejectedValueOnce(new Error("lost admission")).mockResolvedValueOnce({ run_id: "r", conversation_id: "c", status: "queued" });
    await expect(controller.submit(turn, true)).rejects.toThrow("lost admission");
    const original = vi.mocked(api.start).mock.calls[0]![1];
    picked.notes[0]!.revision = 2; picked.manifest_id = null;
    await expect(controller.prepareClose()).rejects.toThrow("回复未知"); await controller.open("other"); controller.clearSelection();
    expect(controller.state.current?.id).toBe("c"); await controller.submit({ ...turn, prompt: "different" }, true);
    expect(api.start).toHaveBeenCalledTimes(1); await controller.retrySubmission();
    expect(prepare).toHaveBeenCalledTimes(1); expect(prepare).toHaveBeenCalledWith(null); expect(vi.mocked(api.start).mock.calls[1]![1]).toEqual(original);
    expect(original.context?.notes[0]!.revision).toBe(1); expect(controller.submissionUncertain).toBe(false);
  });
  it("does not prepare context implicitly, and rejected preparation does not create a draft or POST a run", async () => {
    const prepare = vi.fn(async () => { throw new Error("wrong scope"); }), controller = new NativeWorkspaceController(undefined, prepare);
    controller.updateSettings(settings); await expect(controller.submit(turn, true)).rejects.toThrow("wrong scope");
    expect(api.draft).not.toHaveBeenCalled(); expect(api.start).not.toHaveBeenCalled(); expect(controller.submissionUncertain).toBe(false);
    await controller.open("c"); vi.mocked(api.start).mockResolvedValue({ run_id: "r", conversation_id: "c", status: "queued" });
    await controller.submit(turn); expect(prepare).toHaveBeenCalledTimes(1); expect(vi.mocked(api.start).mock.calls[0]![1]).not.toHaveProperty("context");
  });
  it("rejects foreign-conversation acceptance and keeps the original intent for exact retry", async () => {
    const { controller } = make(); await controller.open("c");
    vi.mocked(api.start).mockResolvedValueOnce({ run_id: "r", conversation_id: "foreign", status: "queued" }).mockResolvedValueOnce({ run_id: "r", conversation_id: "c", status: "queued" });
    await expect(controller.submit(turn)).rejects.toThrow("身份不匹配"); expect(controller.state.selectedRunId).toBe("");
    await controller.retrySubmission(); expect(vi.mocked(api.start).mock.calls[1]![1]).toEqual(vi.mocked(api.start).mock.calls[0]![1]);
  });
  it("keeps accepted selection even if the projection fetch fails", async () => { const { controller } = make(); await controller.open("c"); vi.mocked(api.start).mockResolvedValue({ run_id: "r", conversation_id: "c", status: "queued" }); vi.mocked(api.conversation).mockRejectedValue(new Error("read failed")); await expect(controller.submit(turn)).rejects.toThrow(); expect(controller.state.selectedRunId).toBe("r"); expect(controller.busy).toBe(true); });
  it("does not replace a profile after an update failure or deletion", async () => { const { controller } = make(); await controller.open("c"); const second = { ...settings.profiles[0]!, id: "two" }; controller.updateSettings({ ...settings, profiles: [...settings.profiles, second] }); vi.mocked(api.update).mockRejectedValue(new Error("409")); await expect(controller.chooseProfile("two")).rejects.toThrow(); expect(controller.state.profileId).toBe("mock"); controller.updateSettings({ ...settings, active_profile_id: "two", profiles: [second] }); expect(controller.state.profileId).toBe(""); });
  it("retains completed run audit when projection finishes", async () => { const { controller } = make(); const item = conv("c", { active_run_id: "r", runs: [{ run_id: "r", mode: "graph", status: "running", lease_active: 1 }] }); vi.mocked(api.conversation).mockResolvedValueOnce(item).mockResolvedValueOnce({ ...item, active_run_id: null, runs: [{ run_id: "r", mode: "graph", status: "completed", lease_active: 0 }] }); await controller.open("c"); await controller.refreshRun(); expect(controller.busy).toBe(false); expect(controller.state.selectedRunId).toBe("r"); });
  it("keeps stale poll from replacing another conversation", async () => { const { controller } = make(); await controller.open("c"); const slow = deferred<NativeConversation>(); vi.mocked(api.conversation).mockReturnValueOnce(slow.promise).mockResolvedValueOnce(conv("b")); const polling = controller.refreshRun(); await controller.open("b"); slow.resolve(conv("c")); await polling; expect(controller.state.current?.id).toBe("b"); });
  it("disposal invalidates a draft and search", async () => { const { controller } = make(); const slow = deferred<NativeConversation>(); vi.mocked(api.draft).mockReturnValue(slow.promise); const draft = controller.prepareDraft(); controller.dispose(); slow.resolve(conv()); await draft; expect(controller.state.current).toBeNull(); });
  it("does not send in an archived conversation", async () => { const { controller } = make(); vi.mocked(api.conversation).mockResolvedValue(conv("c", { archived: 1 })); await controller.open("c"); await controller.submit(turn); expect(api.start).not.toHaveBeenCalled(); });
});
