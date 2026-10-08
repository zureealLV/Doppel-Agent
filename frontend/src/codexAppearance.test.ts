import { readFileSync } from "node:fs";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { describe, expect, it } from "vitest";
import NativeWorkspace from "./components/ConversationWorkspace.vue";
import WorkbenchShell from "./components/WorkbenchShell.vue";
import ProjectHome from "./components/ProjectHome.vue";
import ContextPanel from "./components/ContextPanel.vue";
import DesktopControls from "./components/DesktopControls.vue";

describe("Codex-reference appearance (not native acceptance)", () => {
  it("keeps the sidebar compact and the composer centered at the bottom without a busy canvas", () => {
    const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    expect(css).toContain(".codex-shell .codex-sidebar-heading");
    expect(css).toContain(".codex-shell .codex-group-options");
    expect(css).toContain("width: min(740px, calc(100% - 48px))");
    expect(css).toContain("margin: 0 auto 20px");
    expect(css).toContain(".codex-shell .conversation-messages { display: flex; flex-direction: column;");
  });
  it("uses icon tools with hover help while keeping context, settings and project entry points", async () => {
    const html = await renderToString(createSSRApp(NativeWorkspace, { active: true }));
    for (const label of ["搜索对话", "代码审查", "管理对话", "执行详情", "模型与 API", "已归档"]) {
      expect(html).toMatch(new RegExp(`<button[^>]*title="${label}"[^>]*>[\\s\\S]*?<svg`));
      expect(html).toContain(`aria-label="${label}"`);
    }
    expect(html).toContain("codex-group-options");
    expect(html).not.toContain("classic-runtime-note");
    expect(html).not.toContain("classic-history-link");
    const project = await renderToString(createSSRApp(ProjectHome, { blocked: false, beforeSwitch: async () => {}, afterSwitch: () => {} }));
    expect(project).toContain('title="项目"');
    expect(project).toContain('aria-label="项目"');
    const context = await renderToString(createSSRApp(ContextPanel, { workOrderId: null, blocked: false }));
    expect(context).toContain('title="上下文与项目笔记"');
    expect(context).toContain('aria-label="上下文与项目笔记"');
    const controls = await renderToString(createSSRApp(DesktopControls));
    for (const label of ["最小化窗口", "最大化或还原窗口", "关闭窗口"]) expect(controls).toContain(`title="${label}"`);
    expect(html).not.toMatch(/type="checkbox"[^>]* checked/);
    expect(html).toContain('id="native-chat-prompt"');
    expect(html).toContain("本次绑定已接受");
  });
  it("gives every real page an accessible icon rail and puts standard controls on the right", async () => {
    const html = await renderToString(createSSRApp(WorkbenchShell, {
      page: "conversations", closing: false, project: { name: "sample", path: "D:/sample" }, projectError: false,
    }));
    expect(html).toContain('class="codex-activity-rail"');
    expect(html).toContain('class="codex-content"');
    for (const label of ["工作台", "计划与任务", "变更与验证", "扩展中心", "独立 Runtime", "Legacy 历史"]) {
      expect(html).toContain(`title="${label}"`);
      expect(html).toContain(`aria-label="${label}"`);
    }
    const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    expect(css).toContain(".codex-window-actions");
    for (const trafficLight of ["#ff605c", "#ffbd44", "#00ca4e"]) expect(css).not.toContain(trafficLight);
    expect(css).toContain("grid-template-columns: 48px minmax(0, 1fr)");
  });
  it("removes public pane-width sliders rather than merely collapsing them", async () => {
    const html = await renderToString(createSSRApp(NativeWorkspace, { active: true }));
    expect(html).not.toContain("pane-controls");
    expect(html).not.toContain('type="range"');
    expect(html).toContain('aria-controls="native-inspector"');
    expect(html).not.toContain('class="native-inspector"');
  });
});
