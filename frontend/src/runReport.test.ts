// Root ownership/lifecycle construction definitions, ALL UNRUN. No DOM/native IO.
import { describe, expect, it, vi } from "vitest";
import { RunReportController } from "./runReport";
import { reportFixture, reportRun, otherReportRun } from "./testSupport/runReportFixture";
import { reportV3Fixture } from './testSupport/providerReportFixture';
function deferred<T>() { let resolve!: (v: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }
async function ticks() { for (let i = 0; i < 16; i++) await Promise.resolve(); }
function fixture() {
  const api = { read: vi.fn(async () => reportFixture()), download: vi.fn(async () => reportFixture()), save: vi.fn(async (_text: string, _filename: string) => {}) };
  const c = new RunReportController(api); c.activate(true); c.selectSource(reportRun); return { c, api };
}
describe("original root-owned report flight", () => {
  it('retains v3 canonical prepared snapshot through hide and downloads exactly it without adding compatibility or rereading', async () => {
    const { c, api } = fixture(); const wire = reportV3Fixture(); api.download.mockResolvedValue(wire);
    await c.prepareExport(); c.activate(false); c.activate(true);
    wire.provider_usage.selected.total_tokens = 999;
    c.confirmExport(true); await c.exportPrepared();
    const saved = JSON.parse(api.save.mock.calls[0]![0]);
    expect(saved.version).toBe(3); expect(saved.provider_usage.selected.total_tokens).toBe(10);
    expect(saved.provider_usage.compatibility.total_tokens).toBeNull();
    expect(api.download).toHaveBeenCalledOnce(); expect(api.read).not.toHaveBeenCalled(); expect(api.save).toHaveBeenCalledOnce();
  });
  it("construction/source/open/hide/reentry never reads or exports", async () => {
    const { c, api } = fixture(); c.activate(false); c.activate(true); c.selectSource(otherReportRun); await ticks();
    for (const call of Object.values(api)) expect(call).not.toHaveBeenCalled();
  });
  it("pins and joins the original read through hide/close; late data needs explicit local adoption", async () => {
    const { c, api } = fixture(), original = deferred<ReturnType<typeof reportFixture>>(); api.read.mockImplementation(() => original.promise);
    const work = c.readReport(); await ticks(); c.activate(false); c.activate(true);
    expect(c.selectSource(otherReportRun)).toBe(false);
    let closed = false; const closing = c.prepareClose().then(() => { closed = true; }); await ticks(); expect(closed).toBe(false);
    original.resolve(reportFixture()); await work; await closing;
    expect(c.state.report).toBeNull(); expect(c.state.deferred).toBe(true); expect(c.unloadBlocked).toBe(false);
    c.finishClose(); c.activate(true); c.adoptDeferred(); expect(c.state.report?.run_id).toBe(reportRun);
    expect(api.read).toHaveBeenCalledOnce(); expect(api.save).not.toHaveBeenCalled();
  });
  it("retains a late prepared download without initiating a file download or refetching", async () => {
    const { c, api } = fixture(), original = deferred<ReturnType<typeof reportFixture>>(); api.download.mockImplementation(() => original.promise);
    const work = c.prepareExport(); await ticks(); c.activate(false); original.resolve(reportFixture()); await work;
    expect(c.state.exportSnapshot).toBeNull(); expect(api.save).not.toHaveBeenCalled();
    c.activate(true); c.adoptDeferred(); expect(c.state.exportSnapshot?.run_id).toBe(reportRun);
    await c.exportPrepared(); expect(api.save).not.toHaveBeenCalled();
    c.confirmExport(true); await c.exportPrepared(); expect(api.save).toHaveBeenCalledOnce(); expect(api.download).toHaveBeenCalledOnce();
  });
  it("exports an immutable exact prepared snapshot only with fresh consent; not a newer display read", async () => {
    const { c, api } = fixture(), wire = reportFixture(); api.download.mockResolvedValue(wire);
    await c.prepareExport(); wire.usage.root.input_tokens = 999;
    c.confirmExport(true); c.activate(false); c.activate(true); await c.exportPrepared(); expect(api.save).not.toHaveBeenCalled();
    c.confirmExport(true); await c.exportPrepared();
    expect(JSON.parse(api.save.mock.calls[0]![0]).usage.root.input_tokens).toBe(10);
    expect(api.save.mock.calls[0]![1]).toBe(`doppel-report-${reportRun}.json`);
    expect(c.state.exportState).toBe("requested"); expect(c.state.confirmed).toBe(false);
    await c.exportPrepared(); expect(api.save).toHaveBeenCalledOnce();
  });
  it("failed or malformed reads reveal no exception and cannot authorize export", async () => {
    const { c, api } = fixture(); api.read.mockRejectedValue(new Error("SECRET private path")); await c.readReport();
    expect(c.state.error).not.toContain("SECRET"); expect(c.state.report).toBeNull();
    api.download.mockResolvedValue(Object.assign(reportFixture(), { private_token: "SECRET" })); await c.prepareExport();
    expect(c.state.exportSnapshot).toBeNull(); c.confirmExport(true); await c.exportPrepared(); expect(api.save).not.toHaveBeenCalled();
  });
  it("failed-close recovery fences the old transition without cancelling or dropping its promise", async () => {
    const { c, api } = fixture(), original = deferred<ReturnType<typeof reportFixture>>(); api.read.mockImplementation(() => original.promise);
    const work = c.readReport(), closing = c.prepareClose(); const rejected = expect(closing).rejects.toThrow("报告准备已被替代");
    c.finishClose(); c.activate(true); expect(c.unloadBlocked).toBe(true); await c.readReport(); expect(api.read).not.toHaveBeenCalledTimes(2);
    original.resolve(reportFixture()); await work; await rejected; expect(c.state.report).toBeNull();
  });
  it("joins the actual injected file-request promise and reports ambiguity without automatic retry", async () => {
    const { c, api } = fixture(), saving = deferred<void>(); await c.prepareExport(); c.confirmExport(true); api.save.mockImplementation(() => saving.promise);
    const work = c.exportPrepared(); await ticks(); let closed = false; const close = c.prepareClose().then(() => { closed = true; });
    await ticks(); expect(closed).toBe(false); expect(c.unloadBlocked).toBe(true); saving.resolve(); await work; await close;
    expect(c.state.exportState).toBe("requested"); expect(api.save).toHaveBeenCalledOnce();
    c.finishClose(); c.activate(true); api.save.mockRejectedValue(new Error("SECRET")); c.confirmExport(true); await c.exportPrepared();
    expect(c.state.exportState).toBe("unknown"); expect(c.state.error).not.toContain("SECRET");
  });
  it("does not initiate a file request after same-turn hide or close fences its queued continuation", async () => {
    const { c, api } = fixture(); await c.prepareExport(); c.confirmExport(true);
    const exportWork = c.exportPrepared(); c.activate(false); await exportWork;
    expect(api.save).not.toHaveBeenCalled(); expect(c.state.exportState).toBe("none");
    c.activate(true); c.confirmExport(true); const next = c.exportPrepared(), close = c.prepareClose();
    await next; await close; expect(api.save).not.toHaveBeenCalled();
  });
});
