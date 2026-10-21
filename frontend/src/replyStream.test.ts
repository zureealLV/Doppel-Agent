import { describe, expect, it, vi } from "vitest";
import { NativeReplyStream, type RunActivity } from "./replyStream";
import type { RuntimeEvent } from "./types";

const event = (seq: number, type: string, payload = {}, run_id = "r"): RuntimeEvent =>
  ({ seq, type, payload, run_id, thread_id: "t", timestamp: "" });
const state = () => ({ streamingText: "", streamingCallId: "", budgetNotice: "", activities: {} as Record<string, RunActivity[]> });
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
  it("retains earlier progress and interleaved tools instead of replacing it with the next turn", async () => {
    const f = fixture(); f.stream.follow("c", "r");
    await vi.waitFor(() => expect(f.api.streamEvents).toHaveBeenCalled());
    f.emit(event(1, "provider.call_started", { call_id: "one", actor: "runtime_model" }));
    f.emit(event(2, "provider.content_delta", { call_id: "one", actor: "runtime_model", text: "先检查入口。" }));
    f.emit(event(3, "assistant.turn", { call_id: "one", actor: "runtime_model", text: "先检查入口。", phase: "progress" }));
    f.emit(event(4, "graph.tool_started", { tool: "read_file", tool_call_id: "t" }));
    f.emit(event(5, "graph.tool_finished", { tool: "read_file", tool_call_id: "t" }));
    f.emit(event(6, "provider.call_started", { call_id: "two", actor: "runtime_model" }));
    f.emit(event(7, "provider.content_delta", { call_id: "two", actor: "runtime_model", text: "发现入口校验缺口。" }));
    expect(f.view.activities.r?.map(i => i.text)).toEqual(["先检查入口。", "read_file", "发现入口校验缺口。"]);
    f.emit(event(8, "assistant.turn", { call_id: "two", actor: "runtime_model", text: "发现入口校验缺口。", phase: "final" }));
    f.stream.follow("c", "");
    expect(f.view.activities.r?.[0]?.text).toBe("先检查入口。");
    expect(f.view.activities.r?.[2]?.phase).toBe("final");
    f.stream.dispose();
  });
  it("replays every history page after reload, without opening an SSE on completed runs", async () => {
    const f = fixture();
    f.api.events.mockImplementation(async (_rid, after) => after === 0
      ? Array.from({ length: 500 }, (_, i) => event(i + 1, i === 0 ? 'assistant.turn' : 'audit', i === 0 ? { actor:'runtime_model', call_id:'early', text:'第一阶段', phase:'progress' } : {}))
      : [event(501, 'assistant.turn', { actor:'runtime_model', call_id:'late', text:'最后阶段', phase:'progress' })]);
    f.stream.follow('c', '', ['r']);
    await vi.waitFor(() => expect(f.view.activities.r?.map(i => i.text)).toEqual(['第一阶段', '最后阶段']));
    expect(f.api.events).toHaveBeenCalledWith('r', 500);
    expect(f.api.streamEvents).not.toHaveBeenCalled();
    f.stream.follow('c', '', ['r']); expect(f.api.events).toHaveBeenCalledTimes(2);
    f.stream.dispose();
  });
  it("fences late history reads across conversations and retains partial failed output", async () => {
    const f = fixture(); let resolve!: (events: RuntimeEvent[]) => void;
    f.api.events.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
    f.stream.follow('old', '', ['r']); f.stream.follow('new', '');
    resolve([event(1, 'assistant.turn', { actor:'runtime_model', call_id:'old', text:'不得串入', phase:'progress' })]);
    await Promise.resolve(); expect(f.view.activities).toEqual({});
    f.stream.follow('new', 'r'); await vi.waitFor(() => expect(f.api.streamEvents).toHaveBeenCalled());
    f.emit(event(1, 'provider.call_started', { actor:'runtime_model', call_id:'partial' }));
    f.emit(event(2, 'provider.content_delta', { actor:'runtime_model', call_id:'partial', text:'已经确认的证据' }));
    f.emit(event(3, 'provider.call_finished', { actor:'runtime_model', call_id:'partial', outcome:'failed' }));
    f.stream.follow('new', ''); expect(f.view.activities.r?.[0]?.text).toBe('已经确认的证据');
    f.stream.dispose();
  });
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
