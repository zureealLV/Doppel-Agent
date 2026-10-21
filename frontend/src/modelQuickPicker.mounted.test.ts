import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Picker from "./components/ModelQuickPicker.vue";
import Activity from "./components/RunActivity.vue";
import { button, click, descendants, flushVue, mountVirtualProps } from "./testSupport/virtualHost";
import type { PublicSettings } from "./workspaceTypes";
import type { RunActivity } from "./replyStream";
const settings: PublicSettings = { active_profile_id: "a", key_protection: "fixture", profiles: [
  { id: "a", name: "DeepSeek", preset: "deepseek", provider: "openai", model: "flash", base_url: "", input_price: 0, output_price: 0, api_key_saved: true },
  { id: "b", name: "My local", preset: "local", provider: "openai", model: "local-test", base_url: "", input_price: 0, output_price: 0, api_key_saved: false }] };
const mounts: Array<{ unmount(): void }> = [];
beforeEach(() => { vi.stubGlobal("Document", class {}); vi.stubGlobal("ShadowRoot", class {}); });
afterEach(() => { mounts.splice(0).forEach(m => m.unmount()); vi.unstubAllGlobals(); });
describe("public progress and quick model menu", () => {
  it("searches saved models, chooses without probing, and exposes both settings entrances", async () => {
    const select = vi.fn(), configure = vi.fn(), add = vi.fn();
    const m = mountVirtualProps(Picker, { settings, selected: "a", disabled: false, onSelect: select, onConfigure: configure, onAdd: add }); mounts.push(m);
    await click(button(m.root, "DeepSeek")); await flushVue();
    const input = descendants(m.root).find(n => n.props['aria-label'] === '搜索模型或提供商')!;
    (input.props['onUpdate:modelValue'] as (v: string) => void)('local-test'); await flushVue();
    expect(m.root.textContent).not.toContain('flash');
    await click(button(m.root, 'My local')); expect(select).toHaveBeenCalledWith('b');
    await click(button(m.root, 'DeepSeek')); await flushVue();
    await click(button(m.root, '模型与提供商设置')); expect(configure).toHaveBeenCalledOnce();
    await click(button(m.root, 'DeepSeek')); await flushVue();
    await click(button(m.root, '添加提供商 / 自定义模型')); expect(add).toHaveBeenCalledOnce();
  });
  it("keeps progress and collapsed tool records after completion, with only one final answer", async () => {
    const items: RunActivity[] = [ { id:'one', kind:'text', text:'先核查入口', phase:'progress', status:'completed' },
      { id:'tool', kind:'tool', text:'read_file', status:'completed' },
      { id:'last', kind:'text', text:'最终总结', phase:'final', status:'completed' } ];
    const m = mountVirtualProps(Activity, { items, active:false, answer:'最终总结' }); mounts.push(m);
    expect(descendants(m.root).some(n => String(n.props.innerHTML).includes('先核查入口'))).toBe(true);
    expect(descendants(m.root).some(n => String(n.props.innerHTML).includes('最终总结'))).toBe(false);
    const tools = descendants(m.root).find(n => n.tag === 'details')!;
    expect(tools.props.open).toBeUndefined(); expect(tools.textContent).toContain('read_file');
  });
});
