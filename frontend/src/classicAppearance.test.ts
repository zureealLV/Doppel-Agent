import { readFileSync } from "node:fs";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { describe, expect, it } from "vitest";
import WorkbenchShell from "./components/WorkbenchShell.vue";
import NativeWorkspace from "./components/ConversationWorkspace.vue";

describe("daily desktop appearance on the native runtime (not OS acceptance)", () => {
  it("uses the released purple-gray palette instead of the experimental blue-green dashboard", () => {
    const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    const released = readFileSync(new URL("../../src/doppel_agent/web/app.css", import.meta.url), "utf8");
    for (const [name, value] of [["bg", "#27232e"], ["text", "#eeeaf3"], ["accent", "#b8acd1"]]) {
      expect(released).toContain(`--${name}:${value}`);
      expect(css).toContain(`--${name}: ${value}`);
    }
  });
  it("keeps project and every workbench page behind a compact desktop titlebar", async () => {
    const html = await renderToString(createSSRApp(WorkbenchShell, {
      page: "conversations", closing: false, project: { name: "sample", path: "D:/sample" }, projectError: false,
    }));
    expect(html).toContain("classic-shell");
    expect(html).toContain("classic-titlebar");
    expect(html).toContain('aria-label="工作区菜单"');
    for (const name of ["工作台", "计划与任务", "变更与验证", "扩展中心", "独立 Runtime", "Legacy 历史"]) expect(html).toContain(name);
  });
  it("starts with two panes and offers the original inspector only on demand", async () => {
    const html = await renderToString(createSSRApp(NativeWorkspace, { active: true }));
    expect(html).not.toContain('class="native-inspector"');
    expect(html).toContain('aria-controls="native-inspector"');
    expect(html).toContain('aria-pressed="false"');
    expect(html).toContain("LOCAL CODE AGENT");
  });
  it("retains explicit opt-in grants and context inside the compact composer", async () => {
    const html = await renderToString(createSSRApp(NativeWorkspace, { active: true }));
    expect(html).toContain("classic-composer-options");
    expect(html).toContain('id="native-chat-prompt"');
    for (const label of ["写入", "命令", "MCP", "委派", "本次绑定已接受"]) expect(html).toContain(label);
    expect(html).not.toMatch(/type="checkbox"[^>]* checked/);
    expect(html).toContain("受审原生运行，支持持久审批");
  });
});
