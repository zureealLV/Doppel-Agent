import { describe, expect, it } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";

import ConversationWorkspace from "./components/LegacyConversationWorkspace.vue";
import NativeWorkspace from "./components/ConversationWorkspace.vue";
import ModelSettings from "./components/ModelSettings.vue";
import DesktopControls from "./components/DesktopControls.vue";
import { ModelSettingsLifetime, modelSettingsLifetimeKey } from './modelSettingsLifetime';

describe("workspace component structure (not live desktop acceptance)", () => {
  it("retains honest persistent-service and native runtime boundaries", async () => {
    const html = await renderToString(createSSRApp(ConversationWorkspace, { active: true }));
    expect(html).toContain("LEGACY CHAT / EXISTING HISTORY");
    expect(html).toContain("Graph / Deep");
    expect(html).toContain("旧版界面（验收前保留）");
    expect(html).toContain("包括归档");
    expect(html).toContain('id="chat-prompt"');
    expect(html).toContain("模板只填入提示词");
  });

  it("exposes separate native runtime identity and audit-retained deletion", async () => {
    const html = await renderToString(createSSRApp(NativeWorkspace, { active: true }));
    expect(html).toContain("NATIVE CONVERSATION / V1 RUNTIME");
    expect(html).toContain("不迁移、不冒充");
    expect(html).toContain('aria-controls="native-inspector"'); // Released two-pane default; audit opens explicitly.
    expect(html).toContain('id="native-group-rename-dialog"');
    expect(html).toContain('for="native-group-rename-input"');
  });
  it("labels native v1 Legacy as reviewed with durable approval, not the old chat path", async () => {
    const html = await renderToString(createSSRApp(NativeWorkspace, { active: true }));
    const legacyOption = html.match(/<option value="legacy"[^>]*>(.*?)<\/option>/)?.[1];
    expect(legacyOption).toContain("受审原生运行，支持持久审批");
    expect(legacyOption).not.toContain("无持久审批");
    expect(html).toContain("旧 conversations.sqlite3 历史在独立 Legacy 入口");
  });
  it("exposes disabled host-only window controls without a browser bridge", async () => {
    const html = await renderToString(createSSRApp(DesktopControls));
    for (const label of ["最小化窗口", "最大化或还原窗口", "关闭窗口"]) expect(html).toContain(label);
    expect(html.match(/disabled/g)).toHaveLength(3);
  });
  it("renders a password input without recovered key material or automatic probes", async () => {
    const app = createSSRApp(ModelSettings, { settings: { active_profile_id: "profile", key_protection: "Windows DPAPI",
      profiles: [{ id: "profile", name: "local", provider: "openai", preset: "local", model: "fixture",
        base_url: "http://127.0.0.1/v1", input_price: 0, output_price: 0, api_key_saved: true }] } });
    app.provide(modelSettingsLifetimeKey, new ModelSettingsLifetime()); // Same required owner; no production fallback. UNRUN.
    const html = await renderToString(app);
    expect(html).toContain('type="password"');
    expect(html).toContain("不会自动运行");
    expect(html).toContain("单价配置不是历史账单回执");
    expect(html).toContain("未固定币种、日期与来源");
    expect(html).toContain("默认 0 不代表免费");
    expect(html).not.toContain("api_key_dpapi");
    expect(html).not.toContain("localStorage");
  });
});
