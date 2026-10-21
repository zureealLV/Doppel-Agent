import { describe, expect, it, vi } from "vitest";
import { NativeReplyStream } from "./replyStream";
import type { RuntimeEvent } from "./types";

const event = (seq: number, type: string, payload = {}, run_id = "r"): RuntimeEvent =>
  ({ seq, type, payload, run_id, thread_id: "t", timestamp: "" });
const state = () => ({ streamingText: "", streamingCallId: "", budgetNotice: "" });
function fixture() {
  let emit!: (item: RuntimeEvent) => void;
  let signal!: AbortSignal;
  const api = { events: vi.fn().mockResolvedValue([]), streamEvents: vi.fn((_r, _after, s, cb) => {
    signal = s; emit = cb; return new Promise<void>(resolve => s.addEventListener("abort", () => resolve()));
  }) };
  const view = state();
  const stream = new NativeReplyStream(view, () => api);
  return { stream, view, api, emit: (e: RuntimeEvent) => emit(e), signal: () => signal };
}
describe("native incremental reply", () => {
  it("shows real deltas before completion, deduplicates replay, and replaces each model turn", async () => {
    const f = fixture(); f.stream.follow("c", "r");
    await vi.waitFor(() => expect(f.api.streamEvents).toHaveBeenCalled());
    f.emit(event(1, "provider.call_started", { call_id: "one", actor: "runtime_model" }));
    const delta = event(2, "provider.content_delta", { call_id: "one", actor: "runtime_model", text: "你好" });
    f.emit(delta); f.emit(delta);
    expect(f.view.streamingText).toBe("你好");
    f.emit(event(3, "provider.content_delta", { call_id: "one", actor: "runtime_model", text: " Lv" }));
    expect(f.view.streamingText).toBe("你好 Lv");
    f.emit(event(4, "provider.call_started", { call_id: "two", actor: "runtime_model" }));
    expect(f.view.streamingText).toBe("");
    f.emit(event(5, "provider.content_delta", { call_id: "one", actor: "runtime_model", text: "stale" }));
    expect(f.view.streamingText).toBe("");
    f.stream.dispose();
  });
  it("aborts on navigation and ignores late wrong-run and child output", async () => {
    const f = fixture(); f.stream.follow("c", "r");
    await vi.waitFor(() => expect(f.api.streamEvents).toHaveBeenCalled());
    f.emit(event(1, "provider.call_started", { call_id: "one", actor: "runtime_model" }));
    f.emit(event(2, "provider.content_delta", { call_id: "one", actor: "deep_builtin_investigator", text: "child" }));
    f.emit(event(3, "provider.content_delta", { call_id: "one", actor: "runtime_model", text: "wrong" }, "other"));
    expect(f.view.streamingText).toBe("");
    f.stream.follow("elsewhere", "");
    expect(f.signal().aborted).toBe(true);
    f.emit(event(4, "provider.content_delta", { call_id: "one", actor: "runtime_model", text: "late" }));
    expect(f.view.streamingText).toBe("");
    f.stream.dispose();
  });
  it("rebuilds an active reply from durable events without submitting or replaying tools", async () => {
    const f = fixture();
    f.api.events.mockResolvedValue([
      event(1, "graph.budget_exhausted", { reason: "step_limit" }),
      event(2, "provider.call_started", { call_id: "one", actor: "runtime_model" }),
      event(3, "provider.content_delta", { call_id: "one", actor: "runtime_model", text: "已检查" }),
    ]);
    f.stream.follow("c", "r");
    await vi.waitFor(() => expect(f.view.streamingText).toBe("已检查"));
    expect(f.api.streamEvents.mock.calls[0]?.[1]).toBe(3);
    expect(f.view.budgetNotice).toContain("步数");
    f.stream.follow("c", "");
    expect(f.view.streamingText).toBe("");
    f.stream.dispose();
  });
});
