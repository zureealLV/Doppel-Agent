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

it("extracts only reported usage and actual configured verification", async () => {
  const { runtimeEvidence } = await import("./runtime");
  const evidence = runtimeEvidence([event(1, "graph.model_finished", { usage: null }), event(2, "deep.model_finished", { usage: { prompt_tokens: 10, completion_tokens: 5 } }), event(3, "patch.applied", { result: JSON.stringify({ verification: { success: false, results: [{ exit_code: 1 }] } }) }), event(4, "graph.tool_started", { tool: "read_file" })]);
  expect(evidence).toMatchObject({ input: 10, output: 5, usageReports: 1, tools: 1 });
  expect(evidence.verifications[0]?.report.success).toBe(false);
  expect(runtimeEvidence([event(1,"patch.proposed")]).verifications).toEqual([]);
});

it("does not display a stale proposed diff after an edited patch is applied", () => {
  expect(extractDiff([event(1,"patch.proposed",{unified_diff:"+original"}), event(2,"patch.applied",{result: JSON.stringify({ unified_diff: "+actually edited" })})], null)).toBe("+actually edited");
});

it.each(["graph", "deep"])("does not triple-count %s completion usage across provider observation layers", async (mode) => {
  const { runtimeEvidence } = await import("./runtime");
  // Native03: one original HTTP response produced all three durable events.
  const callId = "8e23f935b2ae4017a37799e324ab3038";
  const canonicalUsage = { input_tokens: 100, output_tokens: 10, total_tokens: 110 };
  const events = [
    event(9, "provider.request_finished", { call_id: callId, attempt_id: "8a23393cb1fc4eb381c5748ad1a5c7bb", usage: canonicalUsage }),
    event(10, "provider.call_finished", { call_id: callId, usage: canonicalUsage }),
    event(11, `${mode}.model_finished`, { provider_call_id: callId, usage: { prompt_tokens: 100, completion_tokens: 10 } }),
  ];
  expect(runtimeEvidence(events)).toMatchObject({ input: 100, output: 10, usageReports: 1, tools: 0 });
});

it.each(["provider.model_finished", "provider.call_finished", "provider.request_finished", "arbitrary.model_finished"])("uses only original compatibility callback kinds for %s", async (kind) => {
  const { runtimeEvidence } = await import("./runtime");
  const result = runtimeEvidence([event(1, kind, { usage: { input_tokens: 10, output_tokens: 2 } })]);
  expect(result).toMatchObject(kind === "provider.model_finished"
    ? { input: 10, output: 2, usageReports: 1 }
    : { input: 0, output: 0, usageReports: 0 });
});
