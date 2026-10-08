// S7 transport definitions only. No provider/stdio/native/browser execution.
import { afterEach, describe, expect, it, vi } from "vitest";
import { extensionsApi, parseExtensionSkills } from "./extensionsApi";

const snapshot = { server: "demo", cache_state: "cached", tools: [], total: 0, limit: 100,
  truncated: false, tool_execution_verified: false,
  output: { sensitive: true, globally_redacted: false, remote_text_trusted: false } };
function reply(body: unknown) {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => body });
  vi.stubGlobal("fetch", fetcher); return fetcher;
}
afterEach(() => vi.unstubAllGlobals());

describe("extension passive/confirmed transport", () => {
  it("never connects at import; passive reads use only the new cached endpoint", async () => {
    const fetcher = reply(snapshot);
    expect(fetcher).not.toHaveBeenCalled();
    expect(await extensionsApi.cached("demo")).toEqual(snapshot);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0]).toEqual([
      "/api/v1/extensions/mcp/servers/demo/tools/cached", expect.objectContaining({ cache: "no-store" }),
    ]);
  });
  it("rejects hidden/invalid server scope before any request", async () => {
    const fetcher = reply(snapshot);
    for (const name of ["", "../demo", "demo?secret=PRIVATE", "demo__bad", "DEMO", "a".repeat(65)]) {
      await expect(extensionsApi.cached(name)).rejects.toThrow("invalid_extension_scope");
      await expect(extensionsApi.discover(name, "probe")).rejects.toThrow("invalid_extension_scope");
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("explicit discovery projects only fresh confirmation and selected action, with no abort/timeout/retry", async () => {
    const fetcher = reply({ action: "refresh", ...snapshot });
    await extensionsApi.discover("demo", "refresh");
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/extensions/mcp/servers/demo/discovery");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ action: "refresh", confirmed: true });
    expect(init.signal).toBeUndefined();
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it("keeps probe completion distinct from tool catalog/execution and rejects cross-server/authority replies", async () => {
    reply({ server: "demo", action: "probe", probe_completed: true,
      protocol_version: "fixture", tool_execution_verified: false });
    expect((await extensionsApi.discover("demo", "probe")).action).toBe("probe");
    for (const body of [{ ...snapshot, server: "other" }, { ...snapshot, tool_execution_verified: true },
      { ...snapshot, output: { ...snapshot.output, globally_redacted: true } },
      { ...snapshot, hidden_grant: "PRIVATE" }, { ...snapshot, cache_state: "missing", total: 0 }]) {
      reply(body);
      await expect(extensionsApi.cached("demo")).rejects.toThrow("invalid_extension_response");
    }
  });
  it("validates configured-only inventory without endpoint/argv/env disclosure", async () => {
    reply({ servers: [{ name: "demo", transport: "stdio", max_concurrency: 2 }] });
    expect(await extensionsApi.servers()).toEqual([{ name: "demo", transport: "stdio", max_concurrency: 2 }]);
    reply({ servers: [{ name: "demo", transport: "stdio", max_concurrency: 2, url: "PRIVATE" }] });
    await expect(extensionsApi.servers()).rejects.toThrow("invalid_extension_response");
  });
  it("unknown discovery response is not automatically posted again or reconciled by a cache read", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("PRIVATE_TRANSPORT_EXCEPTION"));
    vi.stubGlobal("fetch", fetcher);
    await expect(extensionsApi.discover("demo", "refresh")).rejects.toThrow();
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it("Skills headers and explicit reload use bounded new paths, never instructions or hidden authority", async () => {
    const headers = { skills: [{ name: "evidence", description: "Header only", text_truncated: false }],
      total: 1, limit: 100, truncated: false, warnings: [], warning_total: 0, warning_truncated: false,
      output: { sensitive: true, globally_redacted: false, text_trusted: false, instructions_returned: false } };
    const read = reply(headers); expect(await extensionsApi.skills()).toEqual(headers);
    expect(read.mock.calls[0][0]).toBe("/api/v1/extensions/skills");
    const reload = reply({ ...headers, action: "reload_skills" }); await extensionsApi.reloadSkills();
    expect(reload.mock.calls[0][0]).toBe("/api/v1/extensions/skills/reload");
    expect(JSON.parse(reload.mock.calls[0][1].body)).toEqual({ confirmed: true, action: "reload_skills" });
    expect(reload).toHaveBeenCalledOnce();
    for (const value of [{ ...headers, instructions: "PRIVATE" },
      { ...headers, output: { ...headers.output, instructions_returned: true } },
      { ...headers, skills: [{ ...headers.skills[0], description: "界".repeat(512) }] }]) {
      expect(() => parseExtensionSkills(value)).toThrow("invalid_extension_response");
    }
  });
});
