// Construction definitions FIRST, UNRUN. Fake HTTP only; not backend/native proof.
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseRunReport, runReportApi, REPORT_RESPONSE_BYTES, reportFilename } from "./runReportApi";
import { reportFixture, reportRun, otherReportRun } from "./testSupport/runReportFixture";
import { reportV3Fixture } from './testSupport/providerReportFixture';
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const invalidMutations: Array<(v: ReturnType<typeof reportFixture>) => unknown> = [
  v => Object.assign(v, { private_error: "SECRET-no-token-pattern" }),
  v => Object.assign(v.usage.root, { answer: "<img onerror=unsafe>" }),
  v => Object.assign(v.cost, { amount: 0 }),
  v => Object.assign(v.usage, { billing_complete: true }),
  v => Object.assign(v.redaction, { anonymous: true }),
  v => Object.assign(v.service, { owner_held: false }),
  v => Object.assign(v.usage.observed, { input_tokens: true }),
  v => Object.assign(v.usage.observed, { input_tokens: Number.MAX_SAFE_INTEGER + 1 }),
  v => Object.assign(v.usage.root, { input_tokens: 11 }),
  v => Object.assign(v.snapshot, { scanned_events: 5001 }),
  v => Object.assign(v.snapshot, { truncated: true }),
  v => Object.assign(v.usage, { unknown_model_events: 1 }),
];
function response(value: unknown, disposition = false) {
  return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json", "Cache-Control": "no-store",
    ...(disposition ? { "Content-Disposition": `attachment; filename="${reportFilename(reportRun)}"` } : {}) } });
}
describe("closed allowlist report transport", () => {
  it('accepts held-owner startup quarantine without inventing a failed-close handle and keeps closed-failed paired', () => {
    const wire = reportV3Fixture(); wire.service.execution_admission = 'quarantined';
    expect(wire.service.failed_close_diagnostic).toBe(false);
    const parsed = parseRunReport(wire, reportRun);
    expect(parsed.service.execution_admission).toBe('quarantined'); expect(Object.isFrozen(parsed.service)).toBe(true);
    wire.service.execution_admission = 'closed_failed';
    expect(() => parseRunReport(wire, reportRun)).toThrow();
  });
  it('reads paired v3 through the original fixed route/body budget and explicit download disposition', async () => {
    const fetcher = vi.fn(async () => response(reportV3Fixture())); vi.stubGlobal('fetch', fetcher);
    const read = await runReportApi.read(reportRun) as ReturnType<typeof reportV3Fixture>;
    expect(read.provider_usage.selected.total_tokens).toBe(10);
    expect(fetcher).toHaveBeenCalledWith(`/api/v1/reports/runs/${reportRun}`, expect.objectContaining({ method: 'GET', cache: 'no-store' }));
    fetcher.mockImplementation(async () => response(reportV3Fixture(), true));
    await runReportApi.download(reportRun);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("accepts only the exact registered source and deep-frozen numeric projection", () => {
    const wire = reportFixture(), parsed = parseRunReport(wire, reportRun);
    wire.usage.root.input_tokens = 900;
    expect(parsed.usage.root.input_tokens).toBe(10); expect(Object.isFrozen(parsed)).toBe(true);
    expect(Object.isFrozen(parsed.usage.root)).toBe(true); expect(Object.isFrozen(parsed.usage.children)).toBe(true);
    expect(() => parseRunReport(reportFixture(), otherReportRun)).toThrow("invalid_run_report");
  });
  it.each(invalidMutations)("rejects private extras and contradictory evidence without rendering them", mutate => {
    const value = reportFixture(); mutate(value); expect(() => parseRunReport(value, reportRun)).toThrow("invalid_run_report");
  });
  it("accepts partial/unknown as such and never converts missing counters to zero", () => {
    const value = reportFixture(); value.usage.state = "partial"; value.usage.unknown_model_events = 1;
    expect(parseRunReport(value, reportRun).usage.state).toBe("partial");
    const empty = reportFixture(); empty.usage.state = "unknown";
    Object.assign(empty.usage.root, { known_model_events: 0, input_tokens: null, output_tokens: null });
    Object.assign(empty.usage.observed, { known_model_events: 0, input_tokens: null, output_tokens: null });
    expect(parseRunReport(empty, reportRun).usage.observed.input_tokens).toBeNull();
  });
  it("fetches only fixed same-origin routes on explicit requests and checks fixed download disposition", async () => {
    const fetcher = vi.fn(async () => response(reportFixture())); vi.stubGlobal("fetch", fetcher);
    await runReportApi.read(reportRun);
    expect(fetcher).toHaveBeenCalledWith(`/api/v1/reports/runs/${reportRun}`, expect.objectContaining({ cache: "no-store", redirect: "error" }));
    fetcher.mockImplementation(async () => response(reportFixture(), true)); await runReportApi.download(reportRun);
    expect(fetcher).toHaveBeenLastCalledWith(`/api/v1/reports/runs/${reportRun}/download`, expect.any(Object));
    fetcher.mockImplementation(async () => new Response(JSON.stringify(reportFixture()), { headers: { "Content-Type": "application/json", "Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="private.txt"' } }));
    await expect(runReportApi.download(reportRun)).rejects.toThrow("report_request_failed");
    await expect(runReportApi.read("../private")).rejects.toThrow("report_request_failed"); expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it("bounds streamed bytes, refuses redirect/HTML/failure bodies and emits only fixed errors", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("SECRET", { status: 503 })));
    await expect(runReportApi.read(reportRun)).rejects.toThrow("report_request_failed");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("x".repeat(REPORT_RESPONSE_BYTES + 1), { headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } })));
    await expect(runReportApi.read(reportRun)).rejects.toThrow("report_request_failed");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<private>", { headers: { "Content-Type": "text/html", "Cache-Control": "no-store" } })));
    await expect(runReportApi.read(reportRun)).rejects.toThrow("report_request_failed");
  });
  it("refuses inherited/accessor/nonenumerable fields, duplicate child attribution and fabricated overflow", () => {
    const getter = reportFixture(); Object.defineProperty(getter, "run_id", { enumerable: true, get: () => reportRun });
    expect(() => parseRunReport(getter, reportRun)).toThrow("invalid_run_report");
    const hidden = reportFixture(); Object.defineProperty(hidden, "run_id", { enumerable: false, value: reportRun });
    expect(() => parseRunReport(hidden, reportRun)).toThrow("invalid_run_report");
    const overflow = reportFixture(); Object.assign(overflow.usage.observed, { subtotal_overflow: true, input_tokens: null, output_tokens: null });
    expect(() => parseRunReport(overflow, reportRun)).toThrow("invalid_run_report");
    const child = { subagent_id: 'b'.repeat(32), generation: 1, known_model_events: 1, input_tokens: 10, output_tokens: 2, subtotal_overflow: false };
    const duplicate = Object.assign(reportFixture().usage, { children: [child, { ...child }] });
    expect(() => parseRunReport({ ...reportFixture(), usage: duplicate }, reportRun)).toThrow("invalid_run_report");
  });
  it("requests only one fixed local Blob attachment and joins its URL cleanup tick, not a file writer", async () => {
    vi.useFakeTimers(); const click = vi.fn(), remove = vi.fn(); const link = { href: '', download: '', hidden: false, click, remove };
    vi.stubGlobal("document", { createElement: vi.fn(() => link), body: { appendChild: vi.fn() } });
    vi.stubGlobal("window", { setTimeout: (callback: () => void, delay: number) => setTimeout(callback, delay) });
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:fixture-local-only'); const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const work = runReportApi.save(JSON.stringify(reportFixture()), reportFilename(reportRun));
    expect(click).toHaveBeenCalledOnce(); expect(link.download).toBe(reportFilename(reportRun)); expect(revoke).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1); await work;
    const blob = create.mock.calls[0]![0]; if (!(blob instanceof Blob)) throw new Error('fixture requires original Blob');
    expect(JSON.parse(await blob.text()).run_id).toBe(reportRun);
    expect(remove).toHaveBeenCalledOnce(); expect(revoke).toHaveBeenCalledWith('blob:fixture-local-only');
    await expect(runReportApi.save(JSON.stringify(reportFixture()), '../private.json')).rejects.toThrow("invalid_run_report");
    await expect(runReportApi.save(JSON.stringify({ ...reportFixture(), secret: 'private' }), reportFilename(reportRun))).rejects.toThrow("invalid_run_report");
    expect(click).toHaveBeenCalledOnce();
  });
});
