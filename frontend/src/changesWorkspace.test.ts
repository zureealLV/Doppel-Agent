// SSR definitions are not DOM focus/native dialog/restart acceptance. Deferred to S9.
import { describe, expect, it } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import ChangesWorkspace from "./components/ChangesWorkspace.vue";
import ChangesSnapshot from "./components/ChangesSnapshot.vue";
import type { GitAvailableStatus, GitDiffAvailable } from "./changesTypes";
const fp = "b".repeat(64);
describe("Changes source-backed read surface construction", () => {
  it("initial SSR invents no branch, clean status, grant or project verification; missing native controls disabled", async () => {
    const html = await renderToString(createSSRApp(ChangesWorkspace, { active: true, blocked: false, sourceRunId: "" }));
    expect(html).toContain("变更与验证"); expect(html).toContain("尚未读取 Git 快照");
    expect(html).toContain("历史补丁证据"); expect(html).toContain("仅桌面原生桥可授权");
    expect(html).not.toContain("已验证"); expect(html).not.toContain("refs/heads/main"); expect(html).not.toContain("v-html");
  });
  it("escapes admitted branch/path/diff and distinguishes excluded/unknown/raw comparison from cleanliness", async () => {
    const status: GitAvailableStatus = { available: true, reason: null, head_ref: "refs/heads/<branch>", head_id: null, object_format: null,
      ref_storage: "files", index_sha256: null, refs_sha256: fp, linked_worktree: false,
      rows: [{ path: "<img>.py", head: null, index: null, stages: [], staged: "none", worktree: "untracked", worktree_sha256: fp,
        worktree_blob_oid: null, worktree_mode: "100644", mode_comparison: "not_applied_on_windows", reason: null, agent_generated: null }],
      excluded_count: 2, unknown_count: 1, walked_entries: 3, coverage_reasons: ["workspace_unknown"], policy_sha256: fp,
      comparison: "raw_workspace_no_filters_or_eol_conversion", repository_clean: null,
      agent_attribution: "not_inferred_from_repository_changes", fingerprint: fp, captured_at: "fixture" };
    const diff: GitDiffAvailable = { available: true, reason: null, path: "<img>.py", plane: "combined", conflict_stage: null, fingerprint: fp,
      before_sha256: null, after_sha256: fp, before_exists: false, after_exists: true, before_mode: null, after_mode: "100644",
      comparison: "raw_workspace_no_filters_or_eol_conversion", text: "<script>literal</script>", agent_generated: null };
    const html = await renderToString(createSSRApp(ChangesSnapshot, { status, diff, stale: true, disabled: false, reading: false }));
    expect(html).toContain("refs/heads/&lt;branch&gt;"); expect(html).toContain("&lt;img&gt;.py");
    expect(html).toContain("&lt;script&gt;literal&lt;/script&gt;"); expect(html).not.toContain("<script>");
    expect(html).toContain("排除记录／遍历项"); expect(html).toContain("不等于敏感文件去重计数");
    expect(html).toContain("不是整个仓库 clean 证明"); expect(html).toContain("快照可能过期");
  });
});
