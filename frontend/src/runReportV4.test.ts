// FIRST full-v4 definitions, ALL UNRUN. Synthetic numeric HTTP/host only.
import { afterEach, expect, it, vi } from 'vitest';
import { parseRunReport, reportFilename, runReportApi } from './runReportApi';
import { RunReportController } from './runReport';
import { reportRun, reportFixture } from './testSupport/runReportFixture';
import { reportV2Fixture } from './testSupport/reportEvidenceFixture';
import { reportV3Fixture } from './testSupport/providerReportFixture';
import { costSubset, reportV4ChildrenFixture, reportV4Fixture, reportV4OverflowFixture } from './testSupport/providerCostFixture';
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('accepts full4 only with original numeric/evidence/price scopes and detached deeply frozen export', () => {
  const wire = reportV4Fixture(), parsed = parseRunReport(wire, reportRun);
  expect(parsed.version).toBe(4); expect(parsed.provider_usage?.selected.total_tokens).toBe(10);
  if (!('version' in parsed.cost)) throw new Error('fixture requires v4 cost');
  expect(parsed.cost.currencies[0]?.amount).toBe('0.00001625');
  wire.cost.price_snapshot!.rates.input = '999'; wire.cost.scopes.root.currencies[0]!.amount = '999';
  wire.provider_usage.selected.total_tokens = 999;
  expect(parsed.cost.price_snapshot?.rates.input).toBe('1.25');
  expect(parsed.cost.scopes.root.currencies[0]?.amount).toBe('0.00001625');
  expect(Object.isFrozen(parsed.cost.price_snapshot?.rates)).toBe(true);
  expect(Object.isFrozen(parsed.cost.scopes.root.currencies[0])).toBe(true);
  expect(Object.isFrozen(parsed.cost.scopes.children)).toBe(true);
  expect(parseRunReport(JSON.parse(JSON.stringify(parsed)), reportRun)).toEqual(parsed);
});

it('keeps historical1/2/3 cost unknown and rejects cross-version additions or missing4 evidence', () => {
  for (const wire of [reportFixture(), reportV2Fixture(), reportV3Fixture()]) {
    expect(parseRunReport(wire, reportRun).cost.state).toBe('unknown');
    expect(() => parseRunReport({ ...wire, cost: reportV4Fixture().cost }, reportRun)).toThrow('invalid_run_report');
  }
  const wire = reportV4Fixture(), { evidence: _e, ...noEvidence } = wire, { provider_usage: _p, ...noProvider } = wire;
  for (const bad of [noEvidence, noProvider, { ...wire, cost: reportFixture().cost }, { ...wire, version: 5 },
    { ...wire, redaction: reportV3Fixture().redaction }, { ...wire, usage_basis: 'callbacks_plus_price' }])
    expect(() => parseRunReport(bad, reportRun)).toThrow('invalid_run_report');
});

it('unknown or invalid price source cannot turn independently known usage into free or complete billing', () => {
  const wire = reportV4Fixture();
  Object.assign(wire.cost, { ...costSubset(false, true), state: 'unknown', source_state: 'invalid', price_snapshot: null });
  wire.cost.scopes.root = costSubset(false, true);
  const parsed = parseRunReport(wire, reportRun);
  expect(parsed.provider_usage?.selected.total_tokens).toBe(10);
  if (!('version' in parsed.cost)) throw new Error('fixture requires v4 cost');
  expect(parsed.cost.currencies).toEqual([]); expect(parsed.cost.unknown_units).toBe(1);
  expect(parsed.cost.billing_complete).toBe(false); expect(parsed.cost.account_cap_guaranteed).toBe(false);
});

it('full parser pairs partial18 children and large overflow with original complete counts, not standalone subsets', () => {
  const children = parseRunReport(reportV4ChildrenFixture(), reportRun);
  expect(children.provider_usage?.selected.known_units).toBe(18);
  if (!('version' in children.cost)) throw new Error('fixture requires v4 cost');
  expect(children.cost.known_units).toBe(18); expect(children.cost.scopes.children_omitted).toBe(2);
  expect(children.cost.scopes.other_children.unknown_units).toBe(1);
  expect(children.cost.currencies[0]?.amount).toBe('0.0002925');
  const overflow = parseRunReport(reportV4OverflowFixture(), reportRun);
  expect(overflow.provider_usage?.selected.subtotal_overflow).toBe(true);
  if (!('version' in overflow.cost)) throw new Error('fixture requires v4 cost');
  expect(overflow.cost.state).toBe('partial'); expect(overflow.cost.currencies[0]?.input_tokens).toBe('18014398509481982');
  expect(overflow.cost.currencies[0]?.amount).toBe('22517998136.8524775');
});

it('rejects corrupted money, private reference and legacy child array accessors before invoking them', () => {
  const bad = reportV4Fixture(); bad.cost.currencies[0]!.amount = '0.00001626';
  expect(() => parseRunReport(bad, reportRun)).toThrow('invalid_run_report');
  const privateField = reportV4Fixture(); Object.assign(privateField.cost.price_snapshot!, { source_reference: 'PRIVATE' });
  expect(() => parseRunReport(privateField, reportRun)).toThrow('invalid_run_report');
  const access = reportV4Fixture(); let invoked = false;
  Object.defineProperty(access.usage.children, Symbol.iterator, { get() { invoked = true; throw new Error('PRIVATE_GETTER'); } });
  expect(() => parseRunReport(access, reportRun)).toThrow('invalid_run_report'); expect(invoked).toBe(false);
});

it('fixed no-store read/download accept4 without another tariff/profile fetch or public billing flag', async () => {
  const fetcher = vi.fn(async (_url: string, _options?: RequestInit) => new Response(JSON.stringify(reportV4Fixture()), { headers: {
    'Content-Type': 'application/json', 'Cache-Control': 'no-store' } })); vi.stubGlobal('fetch', fetcher);
  expect((await runReportApi.read(reportRun) as { version: number }).version).toBe(4);
  fetcher.mockImplementation(async () => new Response(JSON.stringify(reportV4Fixture()), { headers: {
    'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'Content-Disposition': `attachment; filename="${reportFilename(reportRun)}"` } }));
  await runReportApi.download(reportRun);
  expect(fetcher.mock.calls.map(call => call[0])).toEqual([`/api/v1/reports/runs/${reportRun}`, `/api/v1/reports/runs/${reportRun}/download`]);
});

it('root controller exports the immutable prepared4 after hide/adoption, not newer display prices or a second read', async () => {
  const wire = reportV4Fixture(), display = reportV3Fixture();
  let resolve!: (value: unknown) => void;
  const api = { read: vi.fn(async (): Promise<unknown> => display), download: vi.fn(() => new Promise<unknown>(yes => { resolve = yes; })),
    save: vi.fn(async (_text: string, _filename: string) => {}) };
  const c = new RunReportController(api); c.activate(true); c.selectSource(reportRun);
  const work = c.prepareExport(); for (let i = 0; i < 8; i++) await Promise.resolve();
  c.activate(false); resolve(wire); await work; c.activate(true); c.adoptDeferred();
  wire.cost.price_snapshot!.rates.input = '999'; wire.cost.currencies[0]!.amount = '999';
  await c.readReport(); c.confirmExport(true); await c.exportPrepared();
  const saved = JSON.parse(api.save.mock.calls[0]![0]);
  expect(saved.version).toBe(4); expect(saved.cost.currencies[0].amount).toBe('0.00001625');
  expect(saved.cost.price_snapshot.rates.input).toBe('1.25'); expect(saved.cost.billing_complete).toBe(false);
  expect(api.download).toHaveBeenCalledOnce(); expect(api.read).toHaveBeenCalledOnce(); expect(api.save).toHaveBeenCalledOnce();
  expect(c.state.confirmed).toBe(false); expect(c.state.exportState).toBe('requested');
});

it('explicit local Blob download revalidates full4 and joins URL release, not file-writer completion', async () => {
  vi.useFakeTimers(); const click = vi.fn(), remove = vi.fn(), link = { href: '', download: '', hidden: false, click, remove };
  vi.stubGlobal('document', { createElement: vi.fn(() => link), body: { appendChild: vi.fn() } });
  vi.stubGlobal('window', { setTimeout: (callback: () => void, delay: number) => setTimeout(callback, delay) });
  const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:fixture-only');
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  const work = runReportApi.save(JSON.stringify(reportV4Fixture()), reportFilename(reportRun));
  expect(click).toHaveBeenCalledOnce(); expect(revoke).not.toHaveBeenCalled(); await vi.advanceTimersByTimeAsync(1); await work;
  const blob = create.mock.calls[0]![0]; if (!(blob instanceof Blob)) throw new Error('fixture requires Blob');
  const saved = JSON.parse(await blob.text()); expect(saved.cost.currencies[0].amount).toBe('0.00001625');
  expect(saved.redaction.policy).toBe('allowlisted_identifiers_enums_numeric_counters_and_tariff_contract_only');
  expect(remove).toHaveBeenCalledOnce(); expect(revoke).toHaveBeenCalledOnce();
});
