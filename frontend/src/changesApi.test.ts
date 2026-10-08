// Fixed transport definitions, unexecuted until S9; no native Git proof.
import { afterEach, describe, expect, it, vi } from "vitest";
import { changesApi } from "./changesApi";
const run = "a".repeat(32), fingerprint = "b".repeat(64);
afterEach(() => vi.unstubAllGlobals());
describe("Changes fixed read transport", () => {
  it("uses no-store fixed reads, encoded tool-call query and exact JSON diff without authority fields", async () => {
    const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ fixture: true }) })); vi.stubGlobal("fetch", fetcher);
    const signal = new AbortController().signal;
    await changesApi.status(signal);
    await changesApi.diff({ path: "src/日本語.py", plane: "combined", conflict_stage: null, expected_fingerprint: fingerprint }, signal);
    await changesApi.patches(run, 25, signal); await changesApi.evidence(run, "call/?&日本", signal);
    expect(fetcher.mock.calls.map(args => (args as unknown[])[0])).toEqual(["/api/v1/changes/status", "/api/v1/changes/diff",
      `/api/v1/changes/runs/${run}/patches?limit=25&offset=25`, `/api/v1/changes/runs/${run}/patch-evidence?tool_call_id=call%2F%3F%26%E6%97%A5%E6%9C%AC`]);
    for (const args of fetcher.mock.calls) expect((args as unknown[])[1]).toMatchObject({ cache: "no-store", signal });
    const init = (fetcher.mock.calls[1] as unknown[])[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ path: "src/日本語.py", plane: "combined", conflict_stage: null, expected_fingerprint: fingerprint });
    expect(init.headers).toMatchObject({ "X-Doppel-UI": "1" });
  });
  it("rejects malformed scope/selection/paging before fetch; exposes no metadata-root or arbitrary command method", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    await expect(changesApi.patches("../private", 0)).rejects.toThrow();
    await expect(changesApi.patches(run, -1)).rejects.toThrow();
    await expect(changesApi.evidence(run, "\u0000call")).rejects.toThrow();
    await expect(changesApi.diff({ path: "../outside", plane: "worktree", conflict_stage: null, expected_fingerprint: fingerprint })).rejects.toThrow();
    await expect(changesApi.diff({ path: "x", plane: "worktree", conflict_stage: null, expected_fingerprint: "bad" })).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled(); expect(Object.keys(changesApi).sort()).toEqual(["diff", "evidence", "patches", "status"]);
  });
});
