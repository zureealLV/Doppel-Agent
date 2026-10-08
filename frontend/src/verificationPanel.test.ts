// Unexecuted SSR/static definitions, not mounted/native or real command proof.
import { readFileSync } from "node:fs";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { describe, expect, it } from "vitest";
import VerificationReviewPanel from "./components/VerificationReviewPanel.vue";
import { VerificationReviewController } from "./verificationReview";
describe("configured verification panel construction", () => {
  it("starts without fabricated plan/results, checked grants or execution on render", async () => {
    const controller = new VerificationReviewController();
    const html = await renderToString(createSSRApp(VerificationReviewPanel, { controller, blocked: false }));
    expect(html).toContain("精确命令验证"); expect(html).toContain("新的命令与写入授权");
    expect(html).not.toContain("checked"); expect(html).not.toContain("测试全部通过"); expect(html).not.toContain("manual-verification:");
  });
  it("shows full exact argv arrays/config identities and distinct attempts/output; no shell concatenation, truncation or automatic approval", () => {
    const panel = readFileSync(new URL("./components/VerificationReviewPanel.vue", import.meta.url), "utf8");
    expect(panel).toContain("JSON.stringify(command.argv"); expect(panel).toContain("config_hash"); expect(panel).toContain("snapshot_hash");
    expect(panel).not.toContain("v-html"); expect(panel).not.toContain(".slice(");
    for (const field of ["prepareCommand", "prepareWrite", "decisionCommand", "decisionWrite", "cancelConsent", "reconcileConsent"]) expect(panel).toContain(field);
    expect(panel).toContain("step.result.stdout"); expect(panel).toContain("step.result.stderr");
    expect(panel).toContain("只读恢复原 ID"); expect(panel).toContain("仅重发原准备"); expect(panel).not.toContain("重发批准");
    expect(panel).toContain("review ID keyset"); expect(panel).toContain("非全项目验收");
    expect(panel).toContain("不是 HTTP/准备 IO/工作区锁等待/物理排空的硬总时限");
    expect(panel).toContain("state.reading, state.armed");
    const inverse = readFileSync(new URL("./components/InverseReviewPanel.vue", import.meta.url), "utf8");
    expect(inverse).toContain("state.reading, state.armed"); // Even a failed GET/rearm consumes old UI checkbox grants.
  });
  it("wires cross-review source/mutation locks and joins each original flight before the original Changes close barrier", () => {
    const workspace = readFileSync(new URL("./components/ChangesWorkspace.vue", import.meta.url), "utf8");
    expect(workspace).toContain("verification.prepareClose()"); expect(workspace).toContain("verification.finishClose()");
    expect(workspace).toContain("verification.selectRun"); expect(workspace).toContain("verification.selectSource");
    expect(workspace).toContain("verification.state.effectEpoch"); expect(workspace).toContain("verification.selectionLocked");
    expect(workspace).toContain("epoch !== closeEpoch");
  });
});
