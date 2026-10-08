// Compiled SSR definitions only, UNRUN; no DOM/native/layout/focus proof.
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { describe, expect, it, vi } from "vitest";
import SubagentPanel from "./components/SubagentPanel.vue";
import { ChildReviewController } from "./childReview";
import { childId, childParent, childSnapshot } from "./testSupport/childReviewFixture";

describe("root-retained child panel markup", () => {
  it("renders exact lineage, lifetime/global limits and escaped untrusted history without dispatch", async () => {
    const snapshot = childSnapshot(2); snapshot.items[0]!.answer = '<script>PRIVATE_OUTPUT</script>';
    const api = { snapshot: vi.fn(async () => snapshot), history: vi.fn(), spawn: vi.fn(), followUp: vi.fn(), cancel: vi.fn() };
    const c = new ChildReviewController(api); c.activate(true); c.selectSource(childParent); await c.readSnapshot(); c.selectChild(childId);
    const html = await renderToString(createSSRApp(SubagentPanel, { controller: c, active: true, blocked: false }));
    expect(html).toContain(childParent); expect(html).toContain(childId); expect(html).toContain('累计记录');
    expect(html).toContain('全局'); expect(html).toContain('不证明物理排空'); expect(html).toContain('&lt;script&gt;PRIVATE_OUTPUT&lt;/script&gt;');
    expect(html).not.toContain('<script>PRIVATE_OUTPUT</script>');
    expect(api.snapshot).toHaveBeenCalledOnce(); expect(api.spawn).not.toHaveBeenCalled(); expect(api.followUp).not.toHaveBeenCalled();
  });
});
