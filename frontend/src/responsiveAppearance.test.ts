import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { createSSRApp, h } from "vue";
import { renderToString } from "vue/server-renderer";
import { afterAll, describe, expect, it } from "vitest";
import LegacyWorkspace from "./components/LegacyConversationWorkspace.vue";
import NativeWorkspace from "./components/ConversationWorkspace.vue";
import WorkbenchShell from "./components/WorkbenchShell.vue";
import DesktopControls from "./components/DesktopControls.vue";

describe("small-window conversation structure (not OS acceptance)", () => {
  it("keeps Legacy optional controls folded instead of expanding a full-height form", async () => {
    const html = await renderToString(createSSRApp(LegacyWorkspace, { active: true }));
    expect(html).toContain("legacy-workspace");
    expect(html).toContain("classic-composer-toolbar");
    expect(html).toMatch(/<details class="classic-composer-options">[\s\S]*?显式权限[\s\S]*?<\/details>/);
    expect(html).toContain("classic-chat-header");
    expect(html).toContain("classic-welcome");
    expect(html).toContain('aria-controls="legacy-inspector"');
    expect(html).toContain("LEGACY CHAT / EXISTING HISTORY");
    expect(html).toContain("不会偷偷切换运行边界");
    expect(html).toContain("不绑定上方项目上下文");
    expect(html).not.toMatch(/type="checkbox"[^>]* checked/);
    expect(html).toContain('id="chat-prompt"');
  });
});

// Optional, explicitly requested fixture export for a separate real-browser
// geometry harness. The ordinary Vitest run has no filesystem side effect and
// does not pretend SSR/synthetic Vue proves CSS layout or native acceptance.
afterAll(async () => {
  const target = process.env.DOPPEL_LAYOUT_FIXTURE_DIR;
  if (!target) return;
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const folder = resolve(root, target);
  const name = relative(root, folder).replaceAll("\\", "/");
  if (!name.startsWith(".bench-results/") || name.includes("../")) throw new Error("Fixture output must stay in owned test receipts");
  mkdirSync(folder, { recursive: true });
  const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
  for (const [name, component, page] of [["legacy", LegacyWorkspace, "legacy"], ["native", NativeWorkspace, "conversations"]] as const) {
    const html = await renderToString(createSSRApp({ render: () => h(WorkbenchShell, {
      page, closing: false, project: { name: "sample", path: "D:/test-fixture/sample", switching_available: false }, projectError: false,
    }, { default: () => h(component, { active: true }), "window-controls": () => h(DesktopControls) }) }));
    writeFileSync(resolve(folder, `${name}.html`), `<!doctype html><html><meta charset="UTF-8"><style>${css}</style><body><div id="app">${html}</div></body></html>`);
  }
});
