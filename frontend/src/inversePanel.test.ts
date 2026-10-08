// SSR/static definitions only, not native or mounted approval evidence. Deferred to S9.
import { readFileSync } from "node:fs";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { describe, expect, it } from "vitest";
import InverseReviewPanel from "./components/InverseReviewPanel.vue";
import { InverseReviewController } from "./inverseReview";

describe("inverse review UI construction", () => {
  it("does not fabricate source/review/success or inherit old write grants at initial render", async () => {
    const controller = new InverseReviewController();
    const html = await renderToString(createSSRApp(InverseReviewPanel, { controller, blocked: false, preimageRetained: false }));
    expect(html).toContain("精确逆向补丁"); expect(html).toContain("新的写入授权"); expect(html).toContain("先选择已确认 applied 的来源补丁证据");
    expect(html).not.toContain("checked"); expect(html).not.toContain("已撤销成功"); expect(html).not.toContain("sha256:");
  });
  it("keeps complete exact review separate from clipped historical diff and uses independent preparation/approval fields", () => {
    const panel = readFileSync(new URL("./components/InverseReviewPanel.vue", import.meta.url), "utf8");
    expect(panel).toContain("state.review.review.unified_diff"); expect(panel).not.toContain(".slice("); expect(panel).not.toContain("v-html");
    expect(panel).toContain("prepareWrite"); expect(panel).toContain("decisionWrite"); expect(panel).toContain("确认精确预览");
    expect(panel).toContain("只读恢复原 ID"); expect(panel).toContain("重发原准备请求"); expect(panel).not.toContain("重发原批准请求");
    const workspace = readFileSync(new URL("./components/ChangesWorkspace.vue", import.meta.url), "utf8");
    expect(workspace).toContain("inverse.prepareClose()"); expect(workspace).toContain("inverse.finishClose()");
    expect(workspace).toContain("controller.invalidateSnapshot()"); expect(workspace).toContain("canChangeSource");
    expect(workspace).toContain("epoch !== closeEpoch"); expect(workspace).toContain("closeEpoch++");
  });
});
