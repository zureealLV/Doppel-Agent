// Synthetic Vue host: status/actions and follow policy, not browser geometry proof.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import Workspace from "./components/ConversationWorkspace.vue";
import { nativeWorkspaceApi, workspaceApi } from "./workspaceApi";
import type { NativeWorkspaceController } from "./workspace";
import type { NativeConversation, PublicSettings } from "./workspaceTypes";
import { button, click, descendants, flushVue, mountVirtualProps } from "./testSupport/virtualHost";
const host = vi.hoisted(() => ({ controller: null as NativeWorkspaceController | null }));
vi.mock("./workspace", async original => { const module = await original<typeof import("./workspace")>(); return { ...module,
  NativeWorkspaceController: class extends module.NativeWorkspaceController { constructor(...args: ConstructorParameters<typeof module.NativeWorkspaceController>) { super(...args); host.controller = this; } } }; });
vi.mock("./components/RunInspector.vue", () => ({ default: defineComponent({ setup: () => () => h("div") }) }));
vi.mock("./components/ModelSettings.vue", () => ({ default: defineComponent({ setup: () => () => h("div") }) }));
const settings: PublicSettings = { active_profile_id: "mock", key_protection: "fixture", profiles: [{ id: "mock", name: "Mock", provider: "mock", preset: "mock", model: "mock", base_url: "", input_price: 0, output_price: 0, api_key_saved: false }] };
const current = (status: string): NativeConversation => ({ id: "c", mode: "graph", thread_id: "t", title: "测试", archived: 0, group_id: null, group_name: null, profile_id: "mock", created_at: "", updated_at: "", messages: [], runs: [{ run_id: "r", status, mode: "graph", lease_active: 1 }], active_run_id: "r" });
const mounts: Array<{ unmount(): void }> = [];
async function mount(status = "running") { vi.mocked(nativeWorkspaceApi.conversation).mockResolvedValue(current(status)); const value = mountVirtualProps(Workspace, { active: true }); mounts.push(value); await flushVue(); return value; }
beforeEach(() => {
  vi.stubGlobal("Document", class {}); vi.stubGlobal("ShadowRoot", class {});
  vi.stubGlobal("window", Object.assign(new EventTarget(), { setInterval: vi.fn(() => 1), clearInterval: vi.fn(), setTimeout: vi.fn(() => 2), clearTimeout: vi.fn(), localStorage: { getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn() } }));
  vi.spyOn(workspaceApi, "settings").mockResolvedValue(settings); vi.spyOn(workspaceApi, "health").mockResolvedValue({ status: "ok", workspace: "fixture" });
  vi.spyOn(nativeWorkspaceApi, "selection").mockResolvedValue({ saved: true, conversation_id: "c", run_id: "r" });
  vi.spyOn(nativeWorkspaceApi, "saveSelection").mockImplementation(async (conversation_id, run_id) => ({ saved: true, conversation_id, run_id }));
  vi.spyOn(nativeWorkspaceApi, "conversations").mockResolvedValue([]); vi.spyOn(nativeWorkspaceApi, "groups").mockResolvedValue([]); vi.spyOn(nativeWorkspaceApi, "conversation").mockResolvedValue(current("running"));
});
afterEach(() => { mounts.splice(0).forEach(item => item.unmount()); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe("conversation run notice", () => {
  it("keeps the empty draft stable across polls without flashing selection-save status", async () => {
    const mounted = await mount();
    vi.mocked(nativeWorkspaceApi.conversation).mockResolvedValue({ ...current("completed"),
      runs: [], messages: [], active_run_id: null, selected_run_id: null });
    await host.controller!.open("c"); await host.controller!.flushSelection(); await flushVue();
    vi.mocked(nativeWorkspaceApi.saveSelection).mockClear().mockImplementation(() => new Promise(() => {}));
    for (let i = 0; i < 5; i++) {
      await host.controller!.refreshRun(); await flushVue();
      expect(mounted.root.textContent).not.toContain("正在保存历史选择");
    }
    expect(nativeWorkspaceApi.saveSelection).not.toHaveBeenCalled();
  });
  it.each([["queued", "任务排队中"], ["running", "正在执行"], ["interrupted", "等待审批"]])("shows %s in an actionable bar", async (status, text) => {
    const mounted = await mount(status); const notice = descendants(mounted.root).find(node => node.props.class === "conversation-run-notice");
    expect(notice?.textContent).toContain(text); expect(mounted.root.textContent).not.toContain("此对话有进行中或待审批运行");
    await click(button(notice!, "查看执行详情")); await flushVue();
    expect(descendants(mounted.root).some(node => node.props.id === "native-inspector")).toBe(true); expect(host.controller!.state.selectedRunId).toBe("r");
  });
  it("does not label a stale terminal active id as running", async () => { const mounted = await mount("completed"); expect(descendants(mounted.root).some(node => node.props.class === "conversation-run-notice")).toBe(false); });
  it("keeps budget notices separate from assistant output", async () => { const mounted = await mount(); Object.assign(host.controller!.state, { budgetNotice: "本轮检查步数已用完" }); await flushVue(); const notice = descendants(mounted.root).find(node => node.props.class === "conversation-run-notice"); expect(notice?.textContent).toContain("本轮检查步数已用完"); });
});
describe("streaming message and scroll following", () => {
  it("shows escaped streaming Markdown only for an active run", async () => {
    const mounted = await mount(); Object.assign(host.controller!.state, { streamingText: "partial <script>unsafe</script>", streamingCallId: "call" }); await flushVue();
    const draft = descendants(mounted.root).find(node => node.props['data-streaming'] === true); expect(draft?.textContent).toContain("输出中"); expect(descendants(draft!).some(node => String(node.props.innerHTML).includes("&lt;script&gt;"))).toBe(true);
    host.controller!.state.current!.active_run_id = null; await flushVue(); expect(descendants(mounted.root).some(node => node.props['data-streaming'] === true)).toBe(false);
  });
  it("follows the bottom but never pulls a reader away from history", async () => {
    const mounted = await mount(); const messages = descendants(mounted.root).find(node => node.props['aria-label'] === "对话消息")!;
    Object.assign(messages, { scrollHeight: 1000, clientHeight: 200, scrollTop: 800 }); (messages.props.onScroll as () => void)();
    Object.assign(host.controller!.state, { streamingText: "one" }); Object.assign(messages, { scrollHeight: 1100 }); await flushVue(); expect((messages as unknown as { scrollTop: number }).scrollTop).toBe(1100);
    Object.assign(messages, { scrollTop: 100 }); (messages.props.onScroll as () => void)(); Object.assign(host.controller!.state, { streamingText: "two" }); await flushVue(); expect((messages as unknown as { scrollTop: number }).scrollTop).toBe(100);
    await click(button(mounted.root, "回到底部")); await flushVue(); expect((messages as unknown as { scrollTop: number }).scrollTop).toBe(1100);
  });
});
