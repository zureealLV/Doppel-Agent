// Private editable declarations ONLY. No prices fetched, FX, billing authority or IO.
export interface BillingTariff {
  version: 1; currency: 'CNY' | 'USD' | 'EUR' | 'GBP' | 'JPY'; effective_date: string;
  source_kind: 'user_declared' | 'offline_fixture' | 'official_reference'; source_reference: string;
  unit_tokens: 1000000; billing_basis: 'input_output_inclusive' | 'cache_partition_output_inclusive';
  reasoning_basis: 'included_in_output';
  rates: { input: string | null; cached_input: string | null; uncached_input: string | null; output: string };
}
export interface TariffEdit {
  action: 'preserve' | 'replace' | 'clear'; confirmed: boolean;
  currency: string; effective_date: string; source_kind: string; source_reference: string;
  billing_basis: string; reasoning_basis: string;
  input: string; cached_input: string; uncached_input: string; output: string;
}
export interface TariffPatch { billing_tariff?: BillingTariff | null; billing_tariff_confirmed?: true }
const configKeys = ['version', 'currency', 'effective_date', 'source_kind', 'source_reference', 'unit_tokens', 'billing_basis', 'reasoning_basis', 'rates'];
const editKeys = ['action', 'confirmed', 'currency', 'effective_date', 'source_kind', 'source_reference', 'billing_basis', 'reasoning_basis', 'input', 'cached_input', 'uncached_input', 'output'];
function invalid(): never { throw new Error('billing_tariff_invalid'); }
function object(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Object.getPrototypeOf(value) !== Object.prototype) invalid();
  const names = Reflect.ownKeys(value);
  if (names.length !== keys.length || names.some(key => typeof key !== 'string' || !keys.includes(key))) invalid();
  const result: Record<string, unknown> = {};
  for (const key of keys) {
    const field = Object.getOwnPropertyDescriptor(value, key);
    if (!field || !('value' in field)) invalid();
    result[key] = field.value;
  }
  return result;
}
function member(value: unknown, values: string[]): string {
  if (typeof value !== 'string' || !values.includes(value)) invalid();
  return value;
}
function rate(value: unknown): string {
  if (typeof value !== 'string' || !/^(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,8})?$/.test(value)) invalid();
  return value.includes('.') ? value.replace(/0+$/, '').replace(/\.$/, '') : value;
}
function date(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)) invalid();
  const year = Number(value.slice(0, 4)), month = Number(value.slice(5, 7)), day = Number(value.slice(8));
  const instant = new Date(0); instant.setUTCFullYear(year, month - 1, day);
  if (year < 1 || instant.getUTCFullYear() !== year || instant.getUTCMonth() !== month - 1 || instant.getUTCDate() !== day) invalid();
  return value;
}
export function canonicalTariff(value: unknown): BillingTariff {
  const raw = object(value, configKeys), rates = object(raw.rates, ['input', 'cached_input', 'uncached_input', 'output']);
  if (raw.version !== 1 || raw.unit_tokens !== 1000000 || raw.reasoning_basis !== 'included_in_output') invalid();
  const currency = member(raw.currency, ['CNY', 'USD', 'EUR', 'GBP', 'JPY']) as BillingTariff['currency'];
  const source_kind = member(raw.source_kind, ['user_declared', 'offline_fixture', 'official_reference']) as BillingTariff['source_kind'];
  const billing_basis = member(raw.billing_basis, ['input_output_inclusive', 'cache_partition_output_inclusive']) as BillingTariff['billing_basis'];
  const reference = raw.source_reference;
  if (typeof reference !== 'string' || reference.length > 2048 || !reference.trim() || /[\x00-\x1f]/.test(reference)) invalid();
  // TextEncoder replaces unpaired surrogates: refuse BEFORE encoding instead.
  for (const char of reference) { const point = char.codePointAt(0)!; if (point >= 0xd800 && point <= 0xdfff) invalid(); }
  if (new TextEncoder().encode(reference).byteLength > 2048) invalid();
  const partition = billing_basis === 'cache_partition_output_inclusive';
  if (partition ? rates.input !== null : rates.cached_input !== null || rates.uncached_input !== null) invalid();
  return { version: 1, currency, effective_date: date(raw.effective_date), source_kind, source_reference: reference.trim(),
    unit_tokens: 1000000, billing_basis, reasoning_basis: 'included_in_output', rates: {
      input: partition ? null : rate(rates.input), cached_input: partition ? rate(rates.cached_input) : null,
      uncached_input: partition ? rate(rates.uncached_input) : null, output: rate(rates.output) } };
}
export function emptyTariffEdit(): TariffEdit {
  return { action: 'preserve', confirmed: false, currency: '', effective_date: '', source_kind: '', source_reference: '',
    billing_basis: '', reasoning_basis: '', input: '', cached_input: '', uncached_input: '', output: '' };
}
export function tariffEditFromSaved(value: unknown): TariffEdit {
  try {
    const saved = canonicalTariff(value);
    return { ...emptyTariffEdit(), currency: saved.currency, effective_date: saved.effective_date,
      source_kind: saved.source_kind, source_reference: saved.source_reference, billing_basis: saved.billing_basis,
      reasoning_basis: saved.reasoning_basis, input: saved.rates.input ?? '', cached_input: saved.rates.cached_input ?? '',
      uncached_input: saved.rates.uncached_input ?? '', output: saved.rates.output };
  } catch { return emptyTariffEdit(); } // Corrupt/legacy data NEVER becomes free/default rates.
}
export function tariffPatch(value: unknown): TariffPatch {
  if (value === undefined) return {}; // Historical ProfileForm without editor.
  const raw = object(value, editKeys);
  if (raw.action === 'preserve') return {};
  if (raw.action === 'clear') return { billing_tariff: null };
  if (raw.action !== 'replace') invalid();
  if (raw.confirmed !== true) throw new Error('billing_tariff_confirmation_required');
  const partition = raw.billing_basis === 'cache_partition_output_inclusive';
  return { billing_tariff_confirmed: true, billing_tariff: canonicalTariff({
    version: 1, currency: raw.currency, effective_date: raw.effective_date, source_kind: raw.source_kind,
    source_reference: raw.source_reference, unit_tokens: 1000000, billing_basis: raw.billing_basis,
    reasoning_basis: raw.reasoning_basis, rates: { input: partition ? null : raw.input,
      cached_input: partition ? raw.cached_input : null, uncached_input: partition ? raw.uncached_input : null, output: raw.output } }) };
}
export function tariffErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message === 'billing_tariff_confirmation_required') return '请明确确认本次价格声明后再保存。';
  if (error instanceof Error && error.message === 'billing_tariff_invalid') return '价格声明不完整或无效：请检查币种、日期、来源、计费口径与十进制字符串单价。';
  return '请求失败，未认定设置已更新；请检查当前档案。'; // No arbitrary raw errors/key/reference echo.
}
