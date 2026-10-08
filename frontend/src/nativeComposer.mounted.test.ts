// Compiled original native workspace/controller on a synthetic Vue host.
// No browser/OS layout, focus, provider or durable acceptance proof.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import NativeWorkspace from "./components/ConversationWorkspace.vue";
import { nativeWorkspaceApi, workspaceApi } from "./workspaceApi";
import type { NativeWorkspaceController } from "./workspace";
import type { NativeConversation, PublicSettings } from "./workspaceTypes";
import { button, descendants, flushVue, mountVirtualProps } from "./testSupport/virtualHost";

const host = vi.hoisted(() => ({ controller: null as NativeWorkspaceController | null }));
vi.mock("./workspace", async importOriginal => {
  const original = await importOriginal<typeof import("./workspace")>();
  return { ...original, NativeWorkspaceController: class extends original.NativeWorkspaceController {
    constructor(...args: ConstructorParameters<typeof original.NativeWorkspaceController>) { super(...args); host.controller = this; }
  } };
});
vi.mock("./components/RunInspector.vue", () => ({ default: defineComponent({ setup: () => () => h("div") }) }));
vi.mock("./components/ModelSettings.vue", () => ({ default: defineComponent({ setup: () => () => h("div") }) }));
const settings: PublicSettings = { active_profile_id: "mock", key_protection: "fixture-only", profiles: [{
  id: "mock", name: "Mock", provider: "mock", preset: "mock", model: "mock", base_url: "", input_price: 0,
  output_price: 0, api_key_saved: false,
}] };
const conversation = (archived = 0): NativeConversation => ({ id: "c", mode: "graph", thread_id: "native-t",
  title: "新对话", archived, group_id: null, group_name: null, profile_id: "mock", created_at: "", updated_at: "",
  messages: [], runs: [], active_run_id: null });
const mounts: Array<{ unmount(): void }> = [];
async function mount(archived = 0) {
  vi.mocked(nativeWorkspaceApi.conversation).mockResolvedValue(conversation(archived));
  const mounted = mountVirtualProps(NativeWorkspace, { active: true }); mounts.push(mounted);
  await flushVue(); return mounted;
}
beforeEach(() => {
  host.controller = null;
  vi.stubGlobal("Document", class Document {}); vi.stubGlobal("ShadowRoot", class ShadowRoot {});
  const window = Object.assign(new EventTarget(), { setInterval: vi.fn(() => 1), clearInterval: vi.fn(),
    setTimeout: vi.fn(() => 2), clearTimeout: vi.fn(), localStorage: {
      getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn(),
    } });
  vi.stubGlobal("window", window);
  vi.spyOn(workspaceApi, "settings").mockResolvedValue(settings);
  vi.spyOn(workspaceApi, "health").mockResolvedValue({ status: "ok", workspace: "disposable" });
  vi.spyOn(nativeWorkspaceApi, "selection").mockResolvedValue({ saved: true, conversation_id: "c", run_id: null });
  vi.spyOn(nativeWorkspaceApi, "saveSelection").mockImplementation(async (conversation_id, run_id) => ({ saved: true, conversation_id, run_id }));
  vi.spyOn(nativeWorkspaceApi, "conversations").mockResolvedValue([]);
  vi.spyOn(nativeWorkspaceApi, "groups").mockResolvedValue([]);
  vi.spyOn(nativeWorkspaceApi, "conversation").mockResolvedValue(conversation());
  vi.spyOn(nativeWorkspaceApi, "update").mockRejectedValue(new Error("unexpected composer write"));
  vi.spyOn(nativeWorkspaceApi, "start").mockRejectedValue(new Error("unexpected run"));
});
afterEach(() => { for (const mounted of mounts.splice(0)) mounted.unmount(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("native archived composer boundary", () => {
  it("disables every run-input surface while keeping history management and new drafts available", async () => {
    const mounted = await mount(1);
    expect(mounted.root.textContent).toContain("归档对话只读");
    for (const label of ["全面审查", "安全审查", "检查测试", "理解代码库", "发送任务"]) {
      expect(button(mounted.root, label).props.disabled, label).toBe(true);
    }
    const form = descendants(mounted.root).find(node => node.tag === "form" && node.props.class === "chat-composer");
    expect(form).toBeDefined();
    for (const node of descendants(form!).filter(node => ["textarea", "select", "fieldset"].includes(node.tag)
      || (node.tag === "input" && node.type === "checkbox" && node.parent?.tag !== "label"))) {
      expect(node.props.disabled, node.tag).toBe(true);
    }
    const context = descendants(form!).find(node => node.tag === "input" && node.parent?.props.class === "context-confirm");
    expect(context?.props.disabled).toBe(true);
    expect(button(mounted.root, "管理对话").props.disabled).toBe(false);
    expect(button(mounted.root, "新对话").props.disabled).toBe(false);
    expect(nativeWorkspaceApi.update).not.toHaveBeenCalled();
    expect(nativeWorkspaceApi.start).not.toHaveBeenCalled();
  });
  it("restores composer controls only after an explicit non-archived projection, without auto-send", async () => {
    const mounted = await mount(1);
    vi.mocked(nativeWorkspaceApi.conversation).mockResolvedValue(conversation());
    await host.controller!.open("c"); await flushVue();
    expect(button(mounted.root, "全面审查").props.disabled).toBe(false);
    const prompt = descendants(mounted.root).find(node => node.props.id === "native-chat-prompt")!;
    expect(prompt.props.disabled).toBe(false);
    const form = descendants(mounted.root).find(node => node.tag === "form" && node.props.class === "chat-composer")!;
    const selects = descendants(form).filter(node => node.tag === "select" && node.parent?.tag === "label");
    expect(selects).toHaveLength(2);
    for (const select of selects) expect(select.props.disabled).toBe(false);
    expect(nativeWorkspaceApi.update).not.toHaveBeenCalled(); expect(nativeWorkspaceApi.start).not.toHaveBeenCalled();
  });
  it("blocks archived form-handler entry even if a stale draft remains in component memory", async () => {
    const mounted = await mount();
    const prompt = descendants(mounted.root).find(node => node.props.id === "native-chat-prompt")!;
    (prompt.props['onUpdate:modelValue'] as (value: string) => void)("unsent local draft");
    host.controller!.state.current!.archived = 1; await flushVue();
    const form = descendants(mounted.root).find(node => node.tag === "form" && node.props.class === "chat-composer")!;
    await (form.props.onSubmit as (event: unknown) => Promise<void>)({ preventDefault() {} }); await flushVue();
    expect(prompt.value).toBe("unsent local draft");
    expect(button(mounted.root, "发送任务").props.disabled).toBe(true);
    expect(nativeWorkspaceApi.start).not.toHaveBeenCalled(); expect(nativeWorkspaceApi.update).not.toHaveBeenCalled();
  });
});
