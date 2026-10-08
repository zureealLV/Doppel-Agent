// B2b definitions only, UNRUN. Fake HTTP replies, not DOM/native/provider evidence.
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseChildAdmission, parseChildHistory, parseChildSnapshot, reviewPrompt, subagentReviewApi } from "./subagentReviewApi";

const parent = "a".repeat(32), child = "b".repeat(32), other = "c".repeat(32);
const output = { sensitive: true, globally_redacted: false, text_trusted: false };
const service = { read_only: true, owner_held: true, failed_close_diagnostic: false, execution_admission: "not_checked_by_read" };
const record = { subagent_id: child, parent_run_id: parent, status: "completed", prompt: "inspect", answer: "answer",
  generation: 1, created_at: "2026-10-05T00:00:00+00:00", updated_at: "2026-10-05T00:00:00+00:00",
  history: [], history_total: 0, history_offset: 0, history_limit: 16, history_truncated: false, error_present: false };
const snapshot = { parent_run_id: parent, items: [record], total: 1, limit: 100, truncated: false,
  counts: { lifetime_for_parent: 1, durable_active_for_parent: 0 },
  capabilities: { workspace_read: true, workspace_write: false, command_execute: false, mcp_execute: false, delegate: false },
  child_mode: "graph", profile_inheritance: "parent_snapshot", service,
  limits: { lifetime_per_parent: 4, global_active: 2, global_queue: 16 },
  scheduler_observation: { scope: "original_scheduler_in_memory", active: 0, queued: 0 },
  physical_drain_verified: false, output };
function receipt(prompt = "next", generation = 2) {
  return { parent_run_id: parent, reviewed_generation: generation - 1,
    record: { ...record, generation, status: "queued", prompt, answer: "",
      history: [{ prompt: "inspect", answer: "answer" }], history_total: 1 },
    admission_acknowledged: true, completion_verified: false, physical_drain_verified: false, output };
}
function reply(value: unknown) {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => value });
  vi.stubGlobal("fetch", fetcher); return fetcher;
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("reviewed child scoped transport", () => {
  it("does not request on import, and uses only reviewed no-store paths", async () => {
    const fetcher = reply(snapshot);
    vi.resetModules();
    await import("./subagentReviewApi");
    expect(fetcher).not.toHaveBeenCalled();
    expect(await subagentReviewApi.snapshot(parent)).toEqual(snapshot);
    expect(fetcher.mock.calls[0]).toEqual([`/api/v1/subagent-review/runs/${parent}`, expect.objectContaining({ cache: "no-store" })]);
  });

  it("rejects invalid scope and hidden/unconfirmed authority before fetch", async () => {
    const fetcher = reply(snapshot);
    for (const id of ["", "r", "../x", parent.toUpperCase(), parent + "?hidden=x"]) {
      await expect(subagentReviewApi.snapshot(id)).rejects.toThrow("invalid_subagent_review_request");
    }
    for (const body of [{ prompt: "next" }, { confirmed: 1, prompt: "next" },
      { confirmed: true, prompt: "next", permissions: { workspace_write: true } },
      { confirmed: true, prompt: " next " }, { confirmed: true, prompt: "\u0085next\u0085" },
      { confirmed: true, prompt: "\ud800" }, { confirmed: true, prompt: "x".repeat(4001) }]) {
      await expect(Reflect.apply(subagentReviewApi.spawn, null, [parent, body])).rejects.toThrow("invalid_subagent_review_request");
    }
    for (const generation of [0, -1, true, "1", 1.5, Infinity, Number.MAX_SAFE_INTEGER]) {
      await expect(Reflect.apply(subagentReviewApi.followUp, null, [parent, child,
        { confirmed: true, prompt: "next", expected_generation: generation }])).rejects.toThrow("invalid_subagent_review_request");
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("normalizes draft explicitly like the original manager, counting Unicode code points", () => {
    expect(reviewPrompt(" \u0085\u001c你好😀\u3000")).toBe("你好😀");
    expect(reviewPrompt("😀".repeat(4000))).toHaveLength(8000);
    expect(() => reviewPrompt("😀".repeat(4001))).toThrow("invalid_subagent_review_request");
    expect(() => reviewPrompt("\ud800")).toThrow("invalid_subagent_review_request");
    // U+FEFF is not Python str.strip whitespace; do not invisibly rewrite it.
    expect(reviewPrompt("\ufeffnext\ufeff")).toBe("\ufeffnext\ufeff");
  });

  it("pins the original exact POST body even if the caller changes its object", async () => {
    let finish!: (value: unknown) => void;
    const fetcher = vi.fn().mockReturnValue(new Promise(resolve => { finish = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const body = { confirmed: true as const, prompt: "next", expected_generation: 1 };
    const pending = subagentReviewApi.followUp(parent, child, body);
    body.prompt = "later"; body.expected_generation = 2;
    finish({ ok: true, json: async () => receipt() });
    expect((await pending).record.prompt).toBe("next");
    const [path, init] = fetcher.mock.calls[0];
    expect(path).toBe(`/api/v1/subagent-review/runs/${parent}/${child}/follow-ups`);
    expect(JSON.parse(init.body)).toEqual({ confirmed: true, prompt: "next", expected_generation: 1 });
    expect(init.signal).toBeUndefined();
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("validates exact source, page/generation and full-turn denominators", async () => {
    const value = { ...record, generation: 20, history_total: 19, history_offset: 0, history_limit: 16,
      history_truncated: true, history: Array.from({ length: 16 }, (_, i) => ({ prompt: `p${i}`, answer: `a${i}` })),
      service, physical_drain_verified: false, output };
    const fetcher = reply(value);
    expect((await subagentReviewApi.history(parent, child, 20, 0)).history).toHaveLength(16);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/subagent-review/runs/${parent}/${child}/history?expected_generation=20&offset=0&limit=16`);
    for (const changed of [{ ...value, parent_run_id: other }, { ...value, subagent_id: other },
      { ...value, generation: 21 }, { ...value, history_offset: 1 },
      { ...value, history: value.history.slice(1) }, { ...value, history_truncated: false },
      { ...value, physical_drain_verified: true }, { ...value, output: { ...output, globally_redacted: true } }]) {
      expect(() => parseChildHistory(changed, parent, child, 20, 0, 16)).toThrow("invalid_subagent_review_response");
    }
  });

  it("rejects cross-parent, duplicate records, impossible counts and hidden grants", () => {
    for (const value of [{ ...snapshot, parent_run_id: other },
      { ...snapshot, items: [{ ...record, parent_run_id: other }] },
      { ...snapshot, items: [record, record], total: 2, counts: { ...snapshot.counts, lifetime_for_parent: 2 } },
      { ...snapshot, counts: { ...snapshot.counts, durable_active_for_parent: 1 } },
      { ...snapshot, scheduler_observation: { ...snapshot.scheduler_observation, active: 3 } },
      { ...snapshot, capabilities: { ...snapshot.capabilities, delegate: true } },
      { ...snapshot, child_mode: "deep" }, { ...snapshot, service: { ...service, owner_held: false } },
      { ...snapshot, items: [{ ...record, generation: 2 }] },
      { ...snapshot, items: [{ ...record, error: "PRIVATE" }] }, { ...snapshot, api_key: "PRIVATE" }]) {
      expect(() => parseChildSnapshot(value, parent)).toThrow("invalid_subagent_review_response");
    }
  });

  it("spawn requires explicit confirmation and validates its original initial record", async () => {
    const value = { parent_run_id: parent, reviewed_generation: null,
      record: { ...record, status: "queued", prompt: "new child", answer: "" },
      admission_acknowledged: true, completion_verified: false, physical_drain_verified: false, output };
    const fetcher = reply(value);
    expect((await subagentReviewApi.spawn(parent, { confirmed: true, prompt: "new child" })).record.generation).toBe(1);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/subagent-review/runs/${parent}/spawn`);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ confirmed: true, prompt: "new child" });
    reply({ ...value, record: { ...value.record, generation: 2 } });
    await expect(subagentReviewApi.spawn(parent, { confirmed: true, prompt: "new child" })).rejects.toThrow("invalid_subagent_review_response");
  });

  it("acknowledges only the exact admitted turn, never completion or physical drain", () => {
    expect(parseChildAdmission(receipt(), parent, "next", child, 1).record.generation).toBe(2);
    for (const value of [{ ...receipt(), reviewed_generation: 2 },
      { ...receipt(), record: { ...receipt().record, subagent_id: other } },
      { ...receipt(), record: { ...receipt().record, prompt: "different" } },
      { ...receipt(), record: { ...receipt().record, generation: 3 } },
      { ...receipt(), completion_verified: true }, { ...receipt(), physical_drain_verified: true }]) {
      expect(() => parseChildAdmission(value, parent, "next", child, 1)).toThrow("invalid_subagent_review_response");
    }
  });

  it("cancel transports the reviewed generation and never manufactures a completion receipt", async () => {
    const value = { parent_run_id: parent, subagent_id: child, reviewed_generation: 2,
      cancel_requested: true, physical_drain_verified: false };
    const fetcher = reply(value);
    expect(await subagentReviewApi.cancel(parent, child, { confirmed: true, expected_generation: 2 })).toEqual(value);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ confirmed: true, expected_generation: 2 });
    reply({ ...value, reviewed_generation: 3 });
    await expect(subagentReviewApi.cancel(parent, child, { confirmed: true, expected_generation: 2 })).rejects.toThrow("invalid_subagent_review_response");
  });

  it("hides arbitrary HTTP/transport text and never retries or reconciles an unknown POST", async () => {
    for (const failure of [new Error("PRIVATE_TRANSPORT_EXCEPTION"), undefined]) {
      const fetcher = failure ? vi.fn().mockRejectedValue(failure)
        : vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: "PRIVATE_OUTPUT" }) });
      vi.stubGlobal("fetch", fetcher);
      await expect(subagentReviewApi.cancel(parent, child, { confirmed: true, expected_generation: 1 }))
        .rejects.toThrow("subagent_review_request_failed");
      expect(fetcher).toHaveBeenCalledOnce();
    }
  });

  it("rejects oversize JSON serialization before dispatch even with a valid character count", async () => {
    const fetcher = reply(snapshot);
    await expect(subagentReviewApi.spawn(parent, { confirmed: true, prompt: "\u0001".repeat(4000) }))
      .rejects.toThrow("invalid_subagent_review_request");
    expect(fetcher).not.toHaveBeenCalled();
  });
});
