import { describe, expect, it } from "vitest";

import { eventGroup, extractDiff, formatDuration, mergeEvents } from "./runtime";
import { decodeSseBlocks } from "./api";
import type { RuntimeEvent } from "./types";

const event = (seq: number, type: string, payload: Record<string, unknown> = {}): RuntimeEvent => ({
  seq,
  run_id: "run-1",
  thread_id: "thread-1",
  type,
  timestamp: "2026-09-22T00:00:00+00:00",
  payload,
});

describe("runtime presentation helpers", () => {
  it("classifies graph, skill, MCP, subagent, patch and lifecycle events", () => {
    expect(eventGroup("checkpoint.saved")).toBe("graph");
    expect(eventGroup("deep.skill_loaded")).toBe("skill");
    expect(eventGroup("mcp.tool_executed")).toBe("mcp");
    expect(eventGroup("subagent.completed")).toBe("subagent");
    expect(eventGroup("patch.proposed")).toBe("patch");
    expect(eventGroup("run.status_changed")).toBe("lifecycle");
  });

  it("merges replayed events by sequence without reordering", () => {
    expect(mergeEvents([event(2, "runtime.started")], [event(1, "run.status_changed"), event(2, "runtime.started")]))
      .toEqual([event(1, "run.status_changed"), event(2, "runtime.started")]);
  });

  it("extracts unified diffs from event payloads and nested interrupt requests", () => {
    expect(extractDiff([event(1, "patch.proposed", { unified_diff: "--- a/x\n+++ b/x" })], null))
      .toContain("+++ b/x");
    expect(extractDiff([], {
      metadata: {
        interrupts: [{ value: { tool_calls: [{ args: { _doppel_patch: { unified_diff: "@@ -1 +1 @@" } } }] } }],
      },
    } as never)).toContain("@@ -1 +1 @@");
  });

  it("formats short durations with useful precision", () => {
    expect(formatDuration(348)).toBe("348 ms");
    expect(formatDuration(2240)).toBe("2.24 s");
    expect(formatDuration(null)).toBe("—");
  });

  it("decodes named SSE events instead of relying on EventSource onmessage", () => {
    const decoded = decodeSseBlocks('id: 1\nevent: runtime.started\ndata: {"seq":1,"type":"runtime.started"}\n\n');
    expect(decoded.items[0]).toMatchObject({ seq: 1, type: "runtime.started" });
    expect(decoded.rest).toBe("");
  });
});
