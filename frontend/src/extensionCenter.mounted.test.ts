// Compiled component + synthetic Vue host only, NOT DOM/native/OS acceptance.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import ExtensionCenter from "./components/ExtensionCenter.vue";
import { extensionsApi, type ExtensionSnapshot } from "./extensionsApi";
import { mountVirtual, button, checkLabel, click, descendants, flushVue, type VirtualNode } from "./testSupport/virtualHost";

const snapshot = (): ExtensionSnapshot => ({ server: "demo", cache_state: "cached", total: 1, limit: 100, truncated: false,
  tool_execution_verified: false, tools: [{ logical_name: "mcp__demo__read", remote_name: "read", title: "<img onerror=PRIVATE>",
    description: "<script>PRIVATE_REMOTE</script>", schema_hash: "a".repeat(64), text_truncated: false }],
  output: { sensitive: true, globally_redacted: false, remote_text_trusted: false } });
interface Exposed { prepareClose(): Promise<void>; finishClose(): void }
const mounts: Array<{ unmount(): void }> = [];
function mount() {
  const mounted = mountVirtual<Exposed>(ExtensionCenter, { active: true, blocked: false, sourceRunId: "" });
  mounts.push(mounted); return mounted;
}
function disabled(node: VirtualNode) {
  for (let current: VirtualNode | null = node; current; current = current.parent) {
    if ((current === node || current.tag === "fieldset") && (current.props.disabled === true || current.props.disabled === "")) return true;
  }
  return false;
}
async function choose(root: VirtualNode, label: string, value: string) {
  const element = descendants(root).find(node => node.tag === "select" && node.props['aria-label'] === label);
  if (!element) throw new Error("synthetic select unavailable");
  const update = element.props["onUpdate:modelValue"];
  if (typeof update !== "function") throw new Error("compiled select model handler unavailable");
  update(value); await flushVue();
}
function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function select(root: VirtualNode) { await click(button(root, "读取配置列表")); await flushVue(); await choose(root, "MCP 服务器", "demo"); }

beforeEach(() => {
  vi.stubGlobal("Document", class Document {}); vi.stubGlobal("ShadowRoot", class ShadowRoot {});
  vi.stubGlobal("window", new EventTarget());
  vi.spyOn(extensionsApi, "servers").mockResolvedValue([{ name: "demo", transport: "stdio", max_concurrency: 2 }]);
  vi.spyOn(extensionsApi, "cached").mockImplementation(async () => snapshot());
  vi.spyOn(extensionsApi, "discover").mockImplementation(async (_name, action) => action === "probe"
    ? { server: "demo", action: "probe", probe_completed: true, protocol_version: "fixture", tool_execution_verified: false }
    : { action: "refresh", ...snapshot() });
  vi.spyOn(extensionsApi, "skills").mockResolvedValue({ skills: [{ name: "evidence", description: "Header <not HTML>", text_truncated: false }],
    total: 1, limit: 100, truncated: false, warnings: [], warning_total: 0, warning_truncated: false,
    output: { sensitive: true, globally_redacted: false, text_trusted: false, instructions_returned: false } });
  vi.spyOn(extensionsApi, "reloadSkills").mockRejectedValue(new Error("unexpected reload"));
});
afterEach(() => { for (const mounted of mounts.splice(0)) mounted.unmount(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("extension center source construction (not native acceptance)", () => {
  it("mount/reentry/selection performs no automatic directory read or connection", async () => {
    const mounted = mount(); await flushVue();
    for (const call of Object.values(extensionsApi)) expect(call).not.toHaveBeenCalled();
    await select(mounted.root); expect(extensionsApi.servers).toHaveBeenCalledOnce();
    expect(extensionsApi.cached).not.toHaveBeenCalled(); expect(extensionsApi.discover).not.toHaveBeenCalled();
    mounted.props.active = false; await flushVue(); mounted.props.active = true; await flushVue();
    expect(extensionsApi.servers).toHaveBeenCalledOnce();
    expect(mounted.root.textContent).toContain("不证明工具执行");
    expect(mounted.root.textContent).toContain("敏感且不可信");
  });
  it("separates passive cache/header reads from a fresh explicit active discovery consent", async () => {
    const mounted = mount(); await select(mounted.root);
    expect(disabled(button(mounted.root, "执行本次明确动作"))).toBe(true);
    await click(button(mounted.root, "只读当前缓存")); await flushVue();
    expect(extensionsApi.cached).toHaveBeenCalledOnce(); expect(extensionsApi.discover).not.toHaveBeenCalled();
    await checkLabel(mounted.root, "我确认该服务器与本次动作");
    await choose(mounted.root, "MCP 明确动作", "refresh");
    expect(disabled(button(mounted.root, "执行本次明确动作"))).toBe(true);
    await checkLabel(mounted.root, "我确认该服务器与本次动作");
    await click(button(mounted.root, "执行本次明确动作")); await flushVue();
    expect(extensionsApi.discover).toHaveBeenCalledWith("demo", "refresh");
    expect(disabled(button(mounted.root, "执行本次明确动作"))).toBe(true);
    await click(button(mounted.root, "读取 Skills 标题目录")); await flushVue();
    expect(mounted.root.textContent).toContain("Header <not HTML>");
    expect(extensionsApi.reloadSkills).not.toHaveBeenCalled();
  });
  it("blocked freeze consumes consent and fences late read presentation without forgetting its promise", async () => {
    const mounted = mount(); await select(mounted.root); const flight = deferred<ExtensionSnapshot>();
    vi.mocked(extensionsApi.cached).mockImplementation(() => flight.promise);
    await checkLabel(mounted.root, "我确认该服务器与本次动作");
    const reading = click(button(mounted.root, "只读当前缓存")); await flushVue();
    mounted.props.blocked = true; await flushVue();
    let joined = false; const close = mounted.exposed().prepareClose().then(() => { joined = true; }); await flushVue();
    expect(joined).toBe(false); flight.resolve(snapshot()); await reading; await close; await flushVue();
    expect(mounted.root.textContent).not.toContain("PRIVATE_REMOTE");
    mounted.exposed().finishClose(); mounted.props.blocked = false; await flushVue();
    expect(disabled(button(mounted.root, "执行本次明确动作"))).toBe(true);
    expect(extensionsApi.cached).toHaveBeenCalledOnce();
  });
  it("retains lost active reply and exposes only explicit exit acknowledgement, not retry/reset", async () => {
    const mounted = mount(); await select(mounted.root); const flight = deferred<never>();
    vi.mocked(extensionsApi.discover).mockImplementation(() => flight.promise);
    await checkLabel(mounted.root, "我确认该服务器与本次动作");
    const operation = click(button(mounted.root, "执行本次明确动作")); await flushVue();
    expect(disabled(button(mounted.root, "读取配置列表"))).toBe(true);
    flight.reject(new Error("PRIVATE_CONNECTOR_DETAIL")); await operation; await flushVue();
    expect(mounted.root.textContent).toContain("扩展操作回复未知"); expect(mounted.root.textContent).not.toContain("PRIVATE_CONNECTOR");
    expect(mounted.root.textContent).toContain("stdio");
    await expect(mounted.exposed().prepareClose()).rejects.toThrow("扩展操作回复未知");
    mounted.exposed().finishClose(); mounted.props.active = false; await flushVue(); mounted.props.active = true; await flushVue();
    await checkLabel(mounted.root, "我知悉原操作回复仍未知");
    await click(button(mounted.root, "仅允许退出")); await flushVue();
    expect(disabled(button(mounted.root, "执行本次明确动作"))).toBe(true);
    expect(disabled(button(mounted.root, "只读当前缓存"))).toBe(true);
    await mounted.exposed().prepareClose(); expect(extensionsApi.discover).toHaveBeenCalledOnce();
  });
  it("SSR entry is effect-free; compiled template contains no trusted remote HTML renderer", async () => {
    const html = await renderToString(createSSRApp(ExtensionCenter, { active: true, blocked: false }));
    expect(html).toContain("扩展中心"); expect(html).toContain("不自动连接");
    for (const call of Object.values(extensionsApi)) expect(call).not.toHaveBeenCalled();
    // Remote populated escaping remains a separate browser/SSR oracle at S9;
    // the virtual host above does not parse HTML or dispatch native events.
  });
  it("late active receipt requires explicit display and successful Skills reload consumes only fresh reload consent", async () => {
    const mounted = mount(); await select(mounted.root);
    const flight = deferred<Awaited<ReturnType<typeof extensionsApi.discover>>>();
    vi.mocked(extensionsApi.discover).mockImplementation(() => flight.promise);
    await checkLabel(mounted.root, "我确认该服务器与本次动作");
    const operation = click(button(mounted.root, "执行本次明确动作")); await flushVue();
    mounted.props.active = false; await flushVue();
    flight.resolve({ server: "demo", action: "probe", probe_completed: true, protocol_version: "fixture", tool_execution_verified: false });
    await operation; await flushVue(); mounted.props.active = true; await flushVue();
    expect(mounted.root.textContent).not.toContain("已记录 demo 的探测返回");
    await click(button(mounted.root, "显示原操作回执")); await flushVue();
    expect(mounted.root.textContent).toContain("已记录 demo 的探测返回");
    expect(extensionsApi.discover).toHaveBeenCalledOnce();
    const headers = await vi.mocked(extensionsApi.skills)(); vi.mocked(extensionsApi.skills).mockClear();
    vi.mocked(extensionsApi.reloadSkills).mockResolvedValue({ ...headers, action: "reload_skills" });
    await checkLabel(mounted.root, "我确认重新读取本项目 Skills 文件");
    await click(button(mounted.root, "重新加载 Skills（需要新的确认）")); await flushVue();
    expect(extensionsApi.reloadSkills).toHaveBeenCalledOnce(); expect(extensionsApi.skills).not.toHaveBeenCalled();
    expect(disabled(button(mounted.root, "重新加载 Skills（需要新的确认）"))).toBe(true);
    expect(mounted.root.textContent).toContain("Header <not HTML>");
  });
});
