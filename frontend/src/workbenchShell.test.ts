import { describe, expect, it } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import WorkbenchShell from "./components/WorkbenchShell.vue";

const project = { name: "项目 <not-markup>", path: "D:/project", switching_available: false };

describe("next-major workbench shell construction (not native acceptance)", () => {
  it("shows real project metadata with escaped text and retained advanced entries", async () => {
    const html = await renderToString(createSSRApp(WorkbenchShell, {
      page: "conversations", closing: false, project, projectError: false,
    }));
    expect(html).toContain("项目 &lt;not-markup&gt;");
    expect(html).toContain("D:/project");
    expect(html).toContain("高级入口");
    expect(html).toContain("独立 Runtime");
    expect(html).toContain("Legacy 历史");
    expect(html).toContain("变更与验证");
    expect(html).toContain("扩展中心");
    expect(html).not.toContain("切换项目");
    expect(html).not.toContain("已验证");
  });
  it("does not invent a project when the API is unavailable", async () => {
    const html = await renderToString(createSSRApp(WorkbenchShell, {
      page: "legacy", closing: false, project: null, projectError: true,
    }));
    expect(html).toContain("无法读取当前项目");
    expect(html).not.toContain("D:/project");
  });
  it("freezes all navigation while the existing close barrier runs", async () => {
    const html = await renderToString(createSSRApp(WorkbenchShell, {
      page: "runtime", closing: true, project, projectError: false,
    }));
    expect(html.match(/disabled/g)).toHaveLength(8); // Home + 6 pages + Help.
    expect(html).toContain("inert");
  });
});
