import { describe, expect, it } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";

import ConversationWorkspace from "./components/ConversationWorkspace.vue";
import ModelSettings from "./components/ModelSettings.vue";

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

  it("renders a password input without recovered key material or automatic probes", async () => {
    const html = await renderToString(createSSRApp(ModelSettings, { settings: { active_profile_id: "profile", key_protection: "Windows DPAPI",
      profiles: [{ id: "profile", name: "local", provider: "openai", preset: "local", model: "fixture",
        base_url: "http://127.0.0.1/v1", input_price: 0, output_price: 0, api_key_saved: true }] } }));
    expect(html).toContain('type="password"');
    expect(html).toContain("不会自动运行");
    expect(html).not.toContain("api_key_dpapi");
    expect(html).not.toContain("localStorage");
  });
});
