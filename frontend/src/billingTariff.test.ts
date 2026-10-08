// B2b5c settings definitions FIRST, ALL UNRUN; fictional declaration, not official prices.
import { describe, expect, it } from 'vitest';
import { canonicalTariff, emptyTariffEdit, tariffPatch, tariffEditFromSaved } from './billingTariff';
import { profileConfiguration } from './workspace';
import { workspaceApi } from './workspaceApi';
import { vi, afterEach } from 'vitest';

const config = () => ({ version: 1, currency: 'CNY', effective_date: '2026-10-01', source_kind: 'offline_fixture',
  source_reference: 'PRIVATE_OFFLINE_DECLARATION_NOT_OFFICIAL', unit_tokens: 1_000_000,
  billing_basis: 'input_output_inclusive', reasoning_basis: 'included_in_output',
  rates: { input: '1.25000000', cached_input: null, uncached_input: null, output: '2.50000000' } });
const form = () => ({ name: 'fixture', preset: 'mock', model: 'fixture', base_url: '',
  input_price: 0, output_price: 0, api_key: '' });
const edit = () => ({ ...tariffEditFromSaved(config()), action: 'replace' as const, confirmed: true });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('original private tariff settings patch', () => {
  it('leaves legacy/default0 unknown and sends no tariff without explicit replacement', () => {
    expect(tariffPatch(emptyTariffEdit())).toEqual({});
    expect(profileConfiguration(form())).not.toHaveProperty('billing_tariff');
    expect(tariffPatch(tariffEditFromSaved(config()))).toEqual({});
    expect(emptyTariffEdit().input).toBe('');
    expect(emptyTariffEdit().currency).toBe('');
    expect(emptyTariffEdit().reasoning_basis).toBe('');
    expect(tariffPatch({ ...emptyTariffEdit(), action: 'clear' })).toEqual({ billing_tariff: null });
  });
  it('requires true confirmation and complete closed explicit provenance/basis/decimal rates', () => {
    for (const confirmed of [false, 1, 'yes', [], {}]) expect(() => tariffPatch({ ...edit(), confirmed } as never)).toThrow('billing_tariff_confirmation_required');
    const patch = tariffPatch(edit());
    expect(patch).toEqual({ billing_tariff: canonicalTariff(config()), billing_tariff_confirmed: true });
    expect(patch.billing_tariff?.rates.input).toBe('1.25');
    expect(patch.billing_tariff?.rates.output).toBe('2.5');
    for (const changes of [{ currency: '' }, { effective_date: '2026-02-30' }, { source_reference: '' },
      { reasoning_basis: '' }, { billing_basis: '' }, { input: '1e-8' }, { input: '0.000000001' }, { output: '-1' }]) {
      expect(() => tariffPatch({ ...edit(), ...changes } as never)).toThrow('billing_tariff_invalid');
    }
  });
  it('checks exact saved declaration without invoking getters and detaches nested rates', () => {
    const raw = config(), parsed = canonicalTariff(raw);
    raw.rates.input = '99'; expect(parsed.rates.input).toBe('1.25');
    const getter = vi.fn(() => 'PRIVATE');
    const hostile = Object.defineProperty(config(), 'source_reference', { get: getter, enumerable: true });
    expect(() => canonicalTariff(hostile)).toThrow('billing_tariff_invalid'); expect(getter).not.toHaveBeenCalled();
    for (const mutate of [(v: ReturnType<typeof config>) => Object.assign(v, { private: 'PRIVATE' }),
      (v: ReturnType<typeof config>) => Object.assign(v.rates, { input: 0.1 }),
      (v: ReturnType<typeof config>) => Object.assign(v, { unit_tokens: true }),
      (v: ReturnType<typeof config>) => Object.assign(v, { source_reference: '\ud800' })]) {
      const invalid = config(); mutate(invalid); expect(() => canonicalTariff(invalid)).toThrow('billing_tariff_invalid');
    }
    expect(tariffEditFromSaved({ currency: 'CNY' })).toMatchObject({ action: 'preserve', confirmed: false, input: '' });
  });
  it('constructs cache-only partition rates and never derives them from legacy floats', () => {
    const patch = tariffPatch({ ...edit(), billing_basis: 'cache_partition_output_inclusive', input: '', cached_input: '0.5', uncached_input: '2' });
    expect(patch.billing_tariff?.rates).toEqual({ input: null, cached_input: '0.5', uncached_input: '2', output: '2.5' });
    expect(() => tariffPatch({ ...edit(), billing_basis: 'cache_partition_output_inclusive', cached_input: '', uncached_input: '2' })).toThrow();
  });
  it('does not send private tariff draft/confirmation/reference on original probe or key-only request', async () => {
    const draft = { ...form(), billing_tariff_edit: { ...edit(), confirmed: false } };
    const probe = profileConfiguration(draft, 'probe'), forget = profileConfiguration(draft, 'forget_key');
    for (const value of [probe, forget]) {
      expect(value).not.toHaveProperty('billing_tariff_edit'); expect(value).not.toHaveProperty('billing_tariff');
      expect(JSON.stringify(value)).not.toContain('PRIVATE_OFFLINE');
    }
    const fetcher = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ ok: true, reply: 'offline' })));
    vi.stubGlobal('fetch', fetcher);
    await workspaceApi.probe(probe);
    expect(JSON.parse(fetcher.mock.calls[0]?.[1]?.body as string).config).not.toHaveProperty('billing_tariff');
    const exact = profileConfiguration({ ...form(), model: ' fixture ', base_url: '  ', api_key: 'PRIVATE_KEY' }, 'forget_key');
    expect(exact.model).toBe(' fixture '); expect(exact.base_url).toBe('  '); expect(exact.api_key).toBe('');
  });
});
