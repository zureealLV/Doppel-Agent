// State/promise definitions only, not transport, native or physical cleanup proof.
import { describe, expect, it, vi } from "vitest";
import { ExtensionController, type ExtensionApi } from "./extensions";
import type { ExtensionServer, ExtensionSnapshot, ExtensionSkills } from "./extensionsApi";

const servers: ExtensionServer[] = [{ name: "demo", transport: "stdio", max_concurrency: 2 },
  { name: "other", transport: "streamable_http", max_concurrency: 1 }];
const snapshot = (server = "demo"): ExtensionSnapshot => ({ server, cache_state: "cached", tools: [], total: 0,
  limit: 100, truncated: false, tool_execution_verified: false,
  output: { sensitive: true, globally_redacted: false, remote_text_trusted: false } });
const skills = (): ExtensionSkills => ({ skills: [{ name: "evidence", description: "Headers only", text_truncated: false }],
  total: 1, limit: 100, truncated: false, warnings: [], warning_total: 0, warning_truncated: false,
  output: { sensitive: true, globally_redacted: false, text_trusted: false, instructions_returned: false } });
function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function ticks() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
function fixture() {
  const api = { servers: vi.fn(async () => servers), cached: vi.fn(async (name: string) => snapshot(name)),
    discover: vi.fn(async (name: string, action: "probe" | "refresh") => action === "probe"
      ? { server: name, action: "probe" as const, probe_completed: true as const, protocol_version: "fixture", tool_execution_verified: false as const }
      : { action: "refresh" as const, ...snapshot(name) }),
    skills: vi.fn(async () => skills()), reloadSkills: vi.fn(async () => ({ action: "reload_skills" as const, ...skills() })) };
  const controller = new ExtensionController(api); controller.activate(true); return { api, controller };
}
async function select(controller: ExtensionController) { await controller.readServers(); expect(controller.selectServer("demo")).toBe(true); }

describe("extension explicit action and lifecycle construction", () => {
  it("construct/activate/select/reentry never read or connect without explicit actions", async () => {
    const { api, controller: c } = fixture(); await ticks();
    for (const call of Object.values(api)) expect(call).not.toHaveBeenCalled();
    await select(c); expect(api.servers).toHaveBeenCalledOnce();
    expect(api.cached).not.toHaveBeenCalled(); expect(api.discover).not.toHaveBeenCalled();
    c.activate(false); c.activate(true); await ticks();
    expect(api.servers).toHaveBeenCalledOnce(); expect(api.discover).not.toHaveBeenCalled();
  });
  it("consumes consent on reads, action/server changes, visibility and blocked transitions", async () => {
    const { api, controller: c } = fixture(); await select(c);
    c.confirmDiscovery(true); c.chooseAction("refresh"); await c.discover(); expect(api.discover).not.toHaveBeenCalled();
    c.confirmDiscovery(true); await c.readCached(); expect(c.state.confirmed).toBe(false);
    c.confirmDiscovery(true); c.selectServer("other"); await c.discover(); expect(api.discover).not.toHaveBeenCalled();
    c.confirmDiscovery(true); c.activate(false); c.activate(true); await c.discover(); expect(api.discover).not.toHaveBeenCalled();
    c.confirmDiscovery(true); await c.prepareClose(); c.finishClose(); c.activate(true); await c.discover();
    expect(api.discover).not.toHaveBeenCalled();
    c.confirmDiscovery(true); await c.discover(); expect(api.discover).toHaveBeenCalledOnce();
    expect(api.discover).toHaveBeenCalledWith("other", "refresh");
    expect(c.state.confirmed).toBe(false); expect(c.state.probe).toBe(null);
    expect(c.state.catalog?.server).toBe("other"); expect(c.state.lastAction?.transport).toBe("streamable_http");
  });
  it("pins original server/action while live, preserves unknowns and narrows exit only", async () => {
    const { api, controller: c } = fixture(); await select(c); const flight = deferred<never>();
    api.discover.mockImplementation(() => flight.promise); c.confirmDiscovery(true);
    const operation = c.discover(); await ticks();
    expect(c.selectServer("other")).toBe(false); c.chooseAction("refresh"); c.confirmDiscovery(true);
    await c.discover(); expect(api.discover).toHaveBeenCalledOnce();
    flight.reject(new Error("PRIVATE_TRANSPORT_ERROR")); await operation;
    expect(c.state.uncertain).toBe(true); expect(c.state.intent).toEqual({ action: "probe", server: "demo", transport: "stdio",
      body: { action: "probe", confirmed: true } });
    expect(c.state.error).not.toContain("PRIVATE"); expect(c.selectServer("other")).toBe(false);
    await c.readCached(); await c.readSkills(); c.confirmSkillReload(true); await c.reloadSkills();
    expect(api.cached).not.toHaveBeenCalled(); expect(api.skills).not.toHaveBeenCalled(); expect(api.reloadSkills).not.toHaveBeenCalled();
    await expect(c.prepareClose()).rejects.toThrow("扩展操作回复未知"); c.finishClose(); c.activate(true);
    c.acknowledgeExit(true); expect(c.state.exitAcknowledged).toBe(true);
    expect(c.state.uncertain).toBe(true); expect(c.selectionLocked).toBe(true);
    c.confirmDiscovery(true); await c.discover(); expect(api.discover).toHaveBeenCalledOnce();
    await c.prepareClose(); expect(c.state.intent?.server).toBe("demo");
  });
  it("late successful discovery is retained but not displayed until explicit original receipt adoption", async () => {
    const { api, controller: c } = fixture(); await select(c);
    const flight = deferred<Awaited<ReturnType<ExtensionApi['discover']>>>(); api.discover.mockImplementation(() => flight.promise);
    c.chooseAction("refresh"); c.confirmDiscovery(true); const operation = c.discover(); await ticks(); c.activate(false);
    flight.resolve({ action: "refresh", ...snapshot() }); await operation;
    expect(c.state.catalog).toBe(null); expect(c.state.deferredAcknowledgement).toBe(true);
    expect(c.state.intent?.server).toBe("demo"); c.activate(true); await ticks();
    expect(c.state.catalog).toBe(null); expect(c.selectServer("other")).toBe(false);
    c.showOriginalReceipt(); expect(c.state.catalog?.server).toBe("demo"); expect(c.state.intent).toBe(null);
    expect(c.state.deferredAcknowledgement).toBe(false); expect(api.discover).toHaveBeenCalledOnce();
  });
  it("close joins actual original read/discovery promises and rejects superseded preparation", async () => {
    const { api, controller: c } = fixture(); await select(c);
    const flight = deferred<Awaited<ReturnType<ExtensionApi['discover']>>>(); api.discover.mockImplementation(() => flight.promise);
    c.confirmDiscovery(true); const operation = c.discover(); await ticks();
    let done = false; const close = c.prepareClose().then(() => { done = true; }); await ticks(); expect(done).toBe(false);
    flight.resolve({ server: "demo", action: "probe", probe_completed: true, protocol_version: "fixture", tool_execution_verified: false });
    await operation; await close; expect(done).toBe(true); expect(c.state.probe).toBe(null);
    expect(c.state.deferredAcknowledgement).toBe(true); // successful receipt, not a failed/unknown operation
    c.finishClose(); c.activate(true); c.showOriginalReceipt();
    const read = deferred<ExtensionSnapshot>(); api.cached.mockImplementation(() => read.promise);
    const reading = c.readCached(); await ticks();
    const oldClose = c.prepareClose().then(() => "incorrect", () => "superseded"); c.finishClose(); c.activate(true);
    expect(c.state.busy).toBe(true); await c.readServers(); expect(api.servers).toHaveBeenCalledOnce();
    read.resolve(snapshot()); await reading; expect(await oldClose).toBe("superseded"); expect(c.state.catalog).toBe(null);
  });
  it("an untrusted cross-server or false execution acknowledgement becomes unknown, never a receipt", async () => {
    const { api, controller: c } = fixture(); await select(c);
    api.discover.mockImplementation(async () => ({ server: "other", action: "probe", probe_completed: true,
      protocol_version: "PRIVATE", tool_execution_verified: false }));
    c.confirmDiscovery(true); await c.discover(); expect(c.state.uncertain).toBe(true); expect(c.state.probe).toBe(null);
    expect(c.state.error).not.toContain("PRIVATE"); expect(c.state.intent?.server).toBe("demo");
  });
  it("Skills headers require explicit read; reload requires separate fresh consent and keeps unknown intent", async () => {
    const { api, controller: c } = fixture(); await c.readSkills();
    expect(c.state.skills?.output.instructions_returned).toBe(false); await c.reloadSkills(); expect(api.reloadSkills).not.toHaveBeenCalled();
    c.confirmSkillReload(true); c.confirmDiscovery(true); expect(c.state.confirmed).toBe(false);
    api.reloadSkills.mockRejectedValue(new Error("PRIVATE_FILE_PATH")); await c.reloadSkills();
    expect(c.state.intent).toEqual({ action: "reload_skills", server: null, transport: null,
      body: { action: "reload_skills", confirmed: true } }); expect(c.state.uncertain).toBe(true);
    expect(c.state.skills).toBe(null); expect(c.state.error).not.toContain("PRIVATE");
    c.activate(false); c.activate(true); await c.reloadSkills(); expect(api.reloadSkills).toHaveBeenCalledOnce();
  });
  it("fresh project/controller has no old sensitive state, automatic request or global storage", async () => {
    const old = fixture(); await select(old.controller); await old.controller.readCached(); await old.controller.readSkills();
    old.controller.dispose(); const fresh = fixture(); await ticks();
    expect(fresh.controller.state.servers).toEqual([]); expect(fresh.controller.state.selectedServer).toBe(null);
    expect(fresh.controller.state.catalog).toBe(null); expect(fresh.controller.state.skills).toBe(null);
    for (const call of Object.values(fresh.api)) expect(call).not.toHaveBeenCalled();
  });
});
