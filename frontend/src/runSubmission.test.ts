// Regression definitions only. Execute with the frozen whole version at S9.
import { describe, expect, it, vi } from "vitest";
import { HttpError } from "./api";
import { RunSubmissionController } from "./runSubmission";
import type { ContextDescriptor, RunRequest } from "./types";
const body = (): RunRequest => ({ prompt: "explicit task", mode: "graph", effort: "balanced", deadline_seconds: 600,
  permissions: { workspace_write: false, command_execute: false, mcp_execute: false, delegate: false } });
const context = (): ContextDescriptor => ({ manifest_id: "a".repeat(32), notes: [{ note_id: "b".repeat(32), revision: 1 }] });
const accepted = { run_id: "c".repeat(32), status: "queued" };
const deferred = <T>() => { let resolve!: (value: T) => void; return { promise: new Promise<T>(r => { resolve = r; }), resolve }; };

describe("frozen single admission / context prepare boundaries", () => {
  it("waits for explicit context, freezes the original prompt before await, and gates double submit/close", async () => {
    const gate = deferred<ContextDescriptor>(), prepare = vi.fn(() => gate.promise), send = vi.fn(async (_request: RunRequest) => accepted);
    const controller = new RunSubmissionController(send), original = body();
    const pending = controller.submit(original, prepare);
    original.prompt = "later draft"; original.permissions.workspace_write = true;
    expect(send).not.toHaveBeenCalled(); expect(() => controller.prepareClose()).toThrow("仍在");
    await expect(controller.submit(body())).resolves.toBeNull();
    gate.resolve(context()); await pending;
    expect(send).toHaveBeenCalledTimes(1); expect(send.mock.calls[0]![0]).toMatchObject({ prompt: "explicit task", context: context(), permissions: { workspace_write: false } });
    expect(() => controller.prepareClose()).not.toThrow();
  });
  it("never substitutes changed context/prompt/key after a lost reply, even if a retry later returns 4xx", async () => {
    const send = vi.fn<(request: RunRequest) => Promise<typeof accepted>>().mockRejectedValueOnce(new Error("lost ACK"))
      .mockRejectedValueOnce(new HttpError(409, "later refusal cannot disprove prior admission")).mockResolvedValueOnce(accepted);
    const picked = context(), prepare = vi.fn(async () => picked), controller = new RunSubmissionController(send);
    await expect(controller.submit(body(), prepare)).rejects.toThrow("lost ACK");
    picked.manifest_id = null; picked.notes[0]!.revision = 9;
    await expect(controller.submit({ ...body(), prompt: "different" }, prepare)).rejects.toThrow("上一提交");
    expect(send).toHaveBeenCalledTimes(1); expect(() => controller.prepareClose()).toThrow("回复未知");
    await expect(controller.retry()).rejects.toThrow("later refusal"); expect(controller.state.uncertain).toBe(true);
    await controller.retry(); expect(prepare).toHaveBeenCalledTimes(1);
    const calls = send.mock.calls.map(call => call[0]); expect(calls[1]).toEqual(calls[0]); expect(calls[2]).toEqual(calls[0]);
    expect(calls[2]!.context).toEqual(context()); expect(controller.state.uncertain).toBe(false);
  });
  it("a preparation failure sends nothing and permits a deliberate later submission", async () => {
    const send = vi.fn(async (_request: RunRequest) => accepted), controller = new RunSubmissionController(send);
    await expect(controller.submit(body(), async () => { throw new Error("unsaved picks"); })).rejects.toThrow("unsaved picks");
    expect(send).not.toHaveBeenCalled(); expect(controller.state.uncertain).toBe(false);
    await controller.submit(body()); expect(send.mock.calls[0]![0]).not.toHaveProperty("context");
  });
  it("a first definitive refusal releases the intent, while a malformed acceptance remains uncertain", async () => {
    const send = vi.fn<(request: RunRequest) => Promise<typeof accepted>>().mockRejectedValueOnce(new HttpError(400, "context_file_stale"))
      .mockResolvedValueOnce({ run_id: "", status: "queued" }).mockResolvedValueOnce(accepted);
    const controller = new RunSubmissionController(send);
    await expect(controller.submit(body())).rejects.toThrow("context_file_stale"); expect(controller.state.uncertain).toBe(false);
    await expect(controller.submit(body())).rejects.toThrow("缺少实际"); expect(controller.state.uncertain).toBe(true);
    await controller.retry(); expect(send.mock.calls[2]![0]).toEqual(send.mock.calls[1]![0]);
    expect(send.mock.calls[1]![0].idempotency_key).not.toBe(send.mock.calls[0]![0].idempotency_key);
  });
});
