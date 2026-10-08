// Source presentation definitions only; UNRUN, no authenticated/native proof.
import { describe, expect, it } from "vitest";
import { childEventLabel } from "./childLineage";
import type { RuntimeEvent } from "./types";
const parent = "a".repeat(32), child = "b".repeat(32);
function event(payload: Record<string, unknown>, type = "subagent.runtime"): RuntimeEvent {
  return { seq: 1, run_id: parent, thread_id: "thread", timestamp: "", type, payload };
}
describe("recorded child lineage labels", () => {
  it("uses outer original lineage, not nested runtime/tool fields or the selected generation", () => {
    const label = childEventLabel(event({ parent_run_id: parent, subagent_id: child, generation: 1,
      runtime_kind: "tool.finished", runtime_payload: { generation: 999, subagent_id: "spoof" } }));
    expect(label).toContain(parent); expect(label).toContain(child); expect(label).toContain("generation 1");
    expect(label).toContain("tool.finished"); expect(label).not.toContain("999"); expect(label).not.toContain("spoof");
  });
  it("never fabricates old missing/foreign/nonfinite lineage", () => {
    for (const payload of [{ subagent_id: child }, { parent_run_id: parent, subagent_id: child, generation: true },
      { parent_run_id: "c".repeat(32), subagent_id: child, generation: 1 },
      { parent_run_id: parent, subagent_id: child, generation: Infinity }]) {
      expect(childEventLabel(event(payload))).toContain("未固定");
    }
    expect(childEventLabel(event({}, "runtime.finished"))).toBeNull();
  });
});
