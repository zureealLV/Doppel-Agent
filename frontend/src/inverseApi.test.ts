// Unexecuted fixed transport definitions. Metadata reconciliation is POST, not GET or effect retry.
import { afterEach, describe, expect, it, vi } from "vitest";
import { inverseApi } from "./inverseApi";
const run = "a".repeat(32), review = "c".repeat(32), patch = "d".repeat(32);
afterEach(() => vi.unstubAllGlobals());
describe("exact inverse HTTP construction", () => {
  it("pins fixed routes, exact body projection and no-store; GET has no mutation/grants", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({ ok: true, json: async () => ({ fixture: true }) })); vi.stubGlobal("fetch", fetcher);
    await inverseApi.prepare(run, { confirmed: true, source_tool_call_id: "call", source_patch_id: patch, operation_id: review, workspace_write: true });
    await inverseApi.read(run, review); await inverseApi.decide(run, review, { confirmed: true, patch_id: patch, action: "approve", workspace_write: true });
    await inverseApi.reconcile(run, review, { confirmed: true });
    expect(fetcher.mock.calls.map(args => args[0])).toEqual([`/api/v1/changes/runs/${run}/inverse-reviews`, `/api/v1/changes/runs/${run}/inverse-reviews/${review}`,
      `/api/v1/changes/runs/${run}/inverse-reviews/${review}/decision`, `/api/v1/changes/runs/${run}/inverse-reviews/${review}/reconcile`]);
    for (const args of fetcher.mock.calls) expect(args[1]?.cache).toBe("no-store");
    expect(fetcher.mock.calls[1]![1]?.body).toBeUndefined(); expect(fetcher.mock.calls[2]![1]?.headers).toMatchObject({ "X-Doppel-UI": "1" });
    expect(Object.keys(JSON.parse(String(fetcher.mock.calls[2]![1]?.body))).sort()).toEqual(["action", "confirmed", "patch_id", "workspace_write"]);
    expect(JSON.parse(String(fetcher.mock.calls[3]![1]?.body))).toEqual({ confirmed: true });
  });
  it("refuses malformed IDs and non-true explicit confirmation before fetch", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    await expect(inverseApi.read("../outside", review)).rejects.toThrow();
    await expect(inverseApi.decide(run, review, { confirmed: false, patch_id: patch, action: "approve", workspace_write: true })).rejects.toThrow();
    await expect(inverseApi.prepare(run, { confirmed: true, source_tool_call_id: "\0call", source_patch_id: patch, operation_id: review, workspace_write: true })).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });
});
