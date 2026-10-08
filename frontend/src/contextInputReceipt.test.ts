// Read-only SSR definitions are not native layout or actual provider-input proof.
import { describe, expect, it } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import ContextInputReceipt from "./components/ContextInputReceipt.vue";
import RunComposer from "./components/RunComposer.vue";
import type { InputSnapshot } from "./types";

const snapshot = (): InputSnapshot => ({ version: 1, scope_work_order_id: "c".repeat(32), captured_at: "input-freeze-time",
  context_sha256: "d".repeat(64), context_bytes: 2048, estimated_tokens: 512, estimate_method: "utf8_bytes_div4", actual_tokens: null,
  rendered_context: "USER DATA <script>not executable</script>", manifest: { manifest_id: "a".repeat(32), created_at: "manifest-accept-time", budget_bytes: 65536, total_bytes: 8,
    entries: [{ index: 0, path: "src/fixture.txt", start_line: 2, requested_end_line: 99, end_line: 4, total_lines: 50, file_sha256: "e".repeat(64), content_sha256: "f".repeat(64), captured_at: "file-capture-time", text: "fixture", bytes: 8, truncation_reasons: ["entry_byte_limit"] }] },
  notes: [{ note_id: "b".repeat(32), revision: 3, scope_work_order_id: null, kind: "constraint", title: "Pinned assertion", body: "<img src=x onerror=bad()>", body_sha256: "1".repeat(64), confirmed_at: "note-confirm-time", sources: [{ kind: "user", label: "Explicit fixture declaration" }] }],
  predecessors: [{ work_order_id: "c".repeat(32), task_id: "parent-1", execution_revision: 2, attempt_id: "2".repeat(32), run_id: "3".repeat(32), status: "completed", lease_drained: true, result_finished_at: "result-finish-time", result_bytes: 12000, result_sha256: "4".repeat(64), text: "untrusted ALL PASSED", included_bytes: 1000, included_sha256: "5".repeat(64), truncation_reasons: ["per_predecessor_limit", "input_budget"] }] });

describe("saved input receipt / explicit opt-in definitions", () => {
  it("does not fabricate context for old/absent records", async () => {
    const html = await renderToString(createSSRApp(ContextInputReceipt));
    expect(html).toContain("没有上下文快照"); expect(html).toContain("不会用当前面板选择补造历史输入"); expect(html).not.toContain("d".repeat(64));
  });
  it("shows frozen file/note/predecessor identities and both hashes/byte bounds/times, never current-source green or actual-token claims", async () => {
    const html = await renderToString(createSSRApp(ContextInputReceipt, { snapshot: snapshot() }));
    for (const expected of ["src/fixture.txt", "L2–L4", "L99", "r3", "parent-1", "12000", "1000", "entry_byte_limit", "input_budget", "不是实际用量", "input-freeze-time", "manifest-accept-time", "file-capture-time", "note-confirm-time", "result-finish-time", "e".repeat(64), "f".repeat(64), "4".repeat(64), "5".repeat(64)]) expect(html).toContain(expected);
    expect(html).toContain("&lt;script&gt;"); expect(html).toContain("&lt;img"); expect(html).not.toContain("<script>"); expect(html).not.toContain("<img src=x");
    expect(html).not.toContain("hash 与本次检查一致"); expect(html).not.toContain("实际 Token 512");
  });
  it("distinguishes explicitly empty saved input from a missing receipt", async () => {
    const empty = { ...snapshot(), scope_work_order_id: null, manifest: null, notes: [], predecessors: [], rendered_context: "", context_bytes: 0, estimated_tokens: 0 };
    const html = await renderToString(createSSRApp(ContextInputReceipt, { snapshot: empty }));
    expect(html).toContain("已显式选择空上下文"); expect(html).not.toContain("没有上下文快照");
  });
  it("composer describes explicit consent, default no binding and inherited disabled controls", async () => {
    const html = await renderToString(createSSRApp(RunComposer, { busy: true, profiles: [] }));
    expect(html).toContain("默认不绑定"); expect(html).toContain("本次绑定项目上下文"); expect(html).toContain('class="composer-fields" disabled');
    expect(html).not.toContain('type="checkbox" checked');
  });
});
