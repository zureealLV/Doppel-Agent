// Fixed HTTP definitions only; deferred to S9.
import { afterEach, describe, expect, it, vi } from "vitest";
import { verificationApi } from "./verificationApi";
const run = "a".repeat(32), review = "b".repeat(32), patch = "c".repeat(32), plan = "d".repeat(64);
afterEach(() => vi.unstubAllGlobals());
describe("fixed verification transport", () => {
  it("uses fixed no-store routes and explicit known body projections; GET has no grants/config/argv", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({ ok: true, json: async () => ({ fixture: true }) })); vi.stubGlobal("fetch", fetcher);
    await verificationApi.prepare(run, { confirmed: true, source_tool_call_id: "call", source_patch_id: patch, operation_id: review,
      names: ["unit"], command_execute: true, workspace_write: true });
    await verificationApi.list(run, ""); await verificationApi.read(run, review);
    await verificationApi.decide(run, review, { confirmed: true, plan_id: plan, action: "approve", command_execute: true, workspace_write: true });
    await verificationApi.cancel(run, review, { confirmed: true, plan_id: plan }); await verificationApi.reconcile(run, review, { confirmed: true });
    const base = `/api/v1/changes/runs/${run}/verification-reviews`;
    expect(fetcher.mock.calls.map(call => call[0])).toEqual([base, `${base}?limit=16&after_id=`, `${base}/${review}`,
      `${base}/${review}/decision`, `${base}/${review}/cancel`, `${base}/${review}/reconcile`]);
    for (const call of fetcher.mock.calls) expect(call[1]?.cache).toBe("no-store");
    expect(fetcher.mock.calls[1]![1]?.body).toBeUndefined(); expect(fetcher.mock.calls[2]![1]?.body).toBeUndefined();
    expect(JSON.parse(String(fetcher.mock.calls[4]![1]?.body))).toEqual({ confirmed: true, plan_id: plan });
    expect(JSON.parse(String(fetcher.mock.calls[5]![1]?.body))).toEqual({ confirmed: true });
    expect(String(fetcher.mock.calls[0]![1]?.body)).not.toContain("argv");
    expect(fetcher.mock.calls[3]![1]?.headers).toMatchObject({ "X-Doppel-UI": "1" });
  });
  it("rejects bad IDs/cursors/selection/confirmation before fetch and never silently drops duplicate names", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    await expect(verificationApi.list(run, "../cursor")).rejects.toThrow();
    await expect(verificationApi.cancel(run, review, { confirmed: false, plan_id: plan })).rejects.toThrow();
    await expect(verificationApi.prepare(run, { confirmed: true, source_tool_call_id: "call", source_patch_id: patch, operation_id: review,
      names: ["unit", "unit"], command_execute: true, workspace_write: true })).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });
});
