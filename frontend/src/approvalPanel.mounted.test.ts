// Compiled Vue component + synthetic host; not browser/native acceptance evidence.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ApprovalPanel from "./components/ApprovalPanel.vue";
import type { InterruptRecord } from "./types";
import { button, click, descendants, flushVue, mountVirtualProps } from "./testSupport/virtualHost";

const mounts: Array<{ unmount(): void }> = [];
beforeEach(() => {
  vi.stubGlobal("Document", class Document {});
  vi.stubGlobal("ShadowRoot", class ShadowRoot {});
});
afterEach(() => {
  for (const mount of mounts.splice(0)) mount.unmount();
  vi.unstubAllGlobals();
});

describe("reviewed patch edit draft", () => {
  it.each(["args", "arguments"])("submits only editable changes for %s without mutating the read-only receipt", async dialect => {
    const changes = [{ path: "existing.txt", content: "proposed\n" }, { path: "created.txt", content: "new\n" }];
    const original = { name: "propose_patch", description: "two-file patch", [dialect]: {
      changes, _doppel_patch: { patch_id: "original", unified_diff: "full original diff", changes: [
        { path: "existing.txt", base_hash: "sha256:original" }, { path: "created.txt", base_hash: "missing" },
      ] },
    } };
    const readonly = { name: "read_file", [dialect]: { path: "dirty.txt" } };
    const interrupt: InterruptRecord = { id: "original-review", value: {
      [dialect === "args" ? "action_requests" : "tool_calls"]: [original, readonly],
    } };
    const snapshot = JSON.stringify(interrupt);
    const decide = vi.fn();
    const mounted = mountVirtualProps(ApprovalPanel, { interrupt, busy: false, onDecide: decide });
    mounts.push(mounted);
    click(button(mounted.root, "编辑")); await flushVue();
    const textarea = descendants(mounted.root).find(node => node.props.id === "tool-calls-json")!;
    const draft = JSON.parse(String(textarea.value));
    expect(draft[0]).toEqual({ ...original, [dialect]: { changes } });
    expect(draft[1]).toEqual(readonly);
    const preview = descendants(mounted.root).find(node => node.tag === "pre")!;
    expect(preview.textContent).toContain("_doppel_patch");
    expect(preview.textContent).toContain("full original diff");
    expect(preview.textContent).toContain("sha256:original");
    draft[0][dialect].changes[0].content = "user-edited existing\n";
    draft[0][dialect].changes[1].content = "user-edited created\n";
    (textarea.props["onUpdate:modelValue"] as (value: string) => void)(JSON.stringify(draft));
    await flushVue(); click(button(mounted.root, "提交修改")); await flushVue();
    expect(decide).toHaveBeenCalledExactlyOnceWith("edit", draft);
    expect(JSON.stringify(interrupt)).toBe(snapshot);
    expect(preview.textContent).toContain("full original diff");
  });
});
