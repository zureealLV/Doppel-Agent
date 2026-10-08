// B2b5c FIRST compiled-SFC/synthetic-host definitions, ALL UNRUN. NOT native dialog/focus/DOM proof.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ModelSettings from './components/ModelSettings.vue';
import { workspaceApi } from './workspaceApi';
import { canonicalTariff } from './billingTariff';
import type { PublicSettings } from './workspaceTypes';
import { ModelSettingsLifetime, modelSettingsLifetimeKey } from './modelSettingsLifetime';
import { button, checkLabel, click, descendants, flushVue, mountVirtualProps, VirtualNode } from './testSupport/virtualHost';

const declared = () => canonicalTariff({ version: 1, currency: 'CNY', effective_date: '2026-10-01',
  source_kind: 'offline_fixture', source_reference: 'PRIVATE_OFFLINE_REFERENCE', unit_tokens: 1000000,
  billing_basis: 'input_output_inclusive', reasoning_basis: 'included_in_output',
  rates: { input: '1.25', cached_input: null, uncached_input: null, output: '2.5' } });
function settings(): PublicSettings {
  return { active_profile_id: 'fixture', key_protection: 'fixture-only', profiles: [{ id: 'fixture', name: 'fixture',
    preset: 'mock', provider: 'mock', model: 'fixture-model', base_url: '', input_price: 0, output_price: 0,
    api_key_saved: true, billing_tariff: declared() }] };
}
const mounts: Array<{ unmount(): void }> = [];
function fixture(owner = new ModelSettingsLifetime(), changed = vi.fn()) {
  const m = mountVirtualProps(ModelSettings, { settings: settings(), onChanged: changed }, app => app.provide(modelSettingsLifetimeKey, owner)); mounts.push(m); return m;
}
async function value(root: VirtualNode, id: string, next: unknown) {
  const input = descendants(root).find(node => node.props.id === id);
  const update = input?.props['onUpdate:modelValue'];
  if (typeof update !== 'function') throw new Error('missing synthetic model handler');
  update(next); await flushVue();
}
async function submit(root: VirtualNode) {
  const form = descendants(root).find(node => node.tag === 'form'), handler = form?.props.onSubmit;
  if (typeof handler !== 'function') throw new Error('missing compiled form submit');
  await handler({ preventDefault() {}, target: form, currentTarget: form }); await flushVue();
}
beforeEach(() => {
  vi.stubGlobal('Document', class Document {}); vi.stubGlobal('ShadowRoot', class ShadowRoot {});
  Object.defineProperties(VirtualNode.prototype, { showModal: { configurable: true, value: vi.fn() }, close: { configurable: true, value: vi.fn() } });
  vi.spyOn(workspaceApi, 'saveProfile').mockResolvedValue(settings());
  vi.spyOn(workspaceApi, 'probe').mockResolvedValue({ ok: true, reply: 'offline-only fixture' });
});
afterEach(() => {
  for (const m of mounts.splice(0)) m.unmount();
  Reflect.deleteProperty(VirtualNode.prototype, 'showModal'); Reflect.deleteProperty(VirtualNode.prototype, 'close');
  vi.unstubAllGlobals(); vi.restoreAllMocks();
});
it('never auto saves/probes; saved source renders as private plain text and default0 is not free', async () => {
  const m = fixture(); await flushVue();
  expect(workspaceApi.saveProfile).not.toHaveBeenCalled(); expect(workspaceApi.probe).not.toHaveBeenCalled();
  expect(m.root.textContent).toContain('默认 0 不代表免费');
  expect(m.root.textContent).toContain('PRIVATE_OFFLINE_REFERENCE');
  expect(descendants(m.root).filter(node => node.tag === 'a')).toHaveLength(0);
});
it('requires fresh explicit confirmation for replacement and sends only canonical declared tariff on save', async () => {
  const m = fixture(); await flushVue(); await value(m.root, 'tariff-action', 'replace');
  await submit(m.root); expect(workspaceApi.saveProfile).not.toHaveBeenCalled();
  expect(m.root.textContent).toContain('明确确认');
  await checkLabel(m.root, '我确认本次声明'); await value(m.root, 'tariff-output', '3');
  await submit(m.root); expect(workspaceApi.saveProfile).not.toHaveBeenCalled(); // Any rate edit consumes consent.
  await checkLabel(m.root, '我确认本次声明'); await submit(m.root);
  const wire = vi.mocked(workspaceApi.saveProfile).mock.calls[0]![1];
  expect(wire.billing_tariff?.rates.output).toBe('3'); expect(wire.billing_tariff_confirmed).toBe(true);
  expect(wire).not.toHaveProperty('billing_tariff_edit');
  await value(m.root, 'tariff-action', 'replace'); await submit(m.root);
  expect(workspaceApi.saveProfile).toHaveBeenCalledTimes(1); // Successful response consent not reused.
});
it('probe omits unconfirmed private price and key removal ignores unsaved identity/tariff edits', async () => {
  const m = fixture(); await flushVue(); await value(m.root, 'tariff-action', 'replace');
  await value(m.root, 'profile-model', 'unsaved-model');
  await click(button(m.root, '测试连接'));
  expect(vi.mocked(workspaceApi.probe).mock.calls[0]![0]).not.toHaveProperty('billing_tariff');
  await click(button(m.root, '移除 Key'));
  const [id, config, forget] = vi.mocked(workspaceApi.saveProfile).mock.calls[0]!;
  expect(id).toBe('fixture'); expect(forget).toBe(true); expect(config.model).toBe('fixture-model');
  expect(config.api_key).toBe(''); expect(config).not.toHaveProperty('billing_tariff');
  expect(config).not.toHaveProperty('billing_tariff_confirmed');
});
it('key-only action preserves stored provider even when a legacy preset label disagrees', async () => {
  const saved = settings(); saved.profiles[0]!.provider = 'openai';
  const owner = new ModelSettingsLifetime();
  const m = mountVirtualProps(ModelSettings, { settings: saved }, app => app.provide(modelSettingsLifetimeKey, owner)); mounts.push(m); await flushVue();
  await click(button(m.root, '移除 Key'));
  expect(vi.mocked(workspaceApi.saveProfile).mock.calls[0]![1].provider).toBe('openai');
});
it('switching/new profile clears source draft and consent; clear tariff sends explicit null without a probe', async () => {
  const m = fixture(); await flushVue(); await value(m.root, 'tariff-action', 'replace');
  await checkLabel(m.root, '我确认本次声明'); await click(button(m.root, '新建')); await flushVue();
  expect(m.root.textContent).not.toContain('PRIVATE_OFFLINE_REFERENCE');
  await value(m.root, 'tariff-action', 'clear'); await value(m.root, 'profile-preset', 'mock'); await submit(m.root);
  expect(vi.mocked(workspaceApi.saveProfile).mock.calls[0]![1].billing_tariff).toBeNull();
  expect(workspaceApi.probe).not.toHaveBeenCalled();
});
it('consumes save consent on a failed reply, does not echo arbitrary errors or retry price automatically', async () => {
  vi.mocked(workspaceApi.saveProfile).mockRejectedValue(new Error('PRIVATE_KEY_OR_REFERENCE'));
  const m = fixture(); await flushVue(); await value(m.root, 'tariff-action', 'replace');
  await checkLabel(m.root, '我确认本次声明'); await submit(m.root);
  expect(m.root.textContent).toContain('未认定设置已更新');
  expect(m.root.textContent).not.toContain('PRIVATE_KEY_OR_REFERENCE');
  await submit(m.root); expect(workspaceApi.saveProfile).toHaveBeenCalledTimes(1);
});

it('unmounted save stays owned by App lifetime; late settings need explicit adoption and cannot update the old parent', async () => {
  const owner = new ModelSettingsLifetime(), oldChanged = vi.fn(), newChanged = vi.fn();
  let resolve!: (value: PublicSettings) => void;
  vi.mocked(workspaceApi.saveProfile).mockImplementation(() => new Promise(yes => { resolve = yes; }));
  const first = fixture(owner, oldChanged); await flushVue();
  const form = descendants(first.root).find(node => node.tag === 'form'), handler = form?.props.onSubmit;
  if (typeof handler !== 'function') throw new Error('fixture requires compiled submit');
  const work = handler({ preventDefault() {}, target: form }); await flushVue(); first.unmount();
  const second = fixture(owner, newChanged); await flushVue();
  let closed = false; const closing = owner.prepareClose().then(() => { closed = true; }); await flushVue();
  expect(closed).toBe(false); expect(owner.unloadBlocked).toBe(true);
  resolve(settings()); await work; await closing; owner.finishClose(); await flushVue();
  expect(oldChanged).not.toHaveBeenCalled(); expect(newChanged).not.toHaveBeenCalled();
  expect(second.root.textContent).toContain('迟到回复');
  await click(button(second.root, '明确显示原请求的迟到回复')); await flushVue();
  expect(newChanged).toHaveBeenCalledOnce(); expect(workspaceApi.saveProfile).toHaveBeenCalledOnce();
});

it('failed probe stays unknown across reopen; explicit readonly settings read is not a retry or success acknowledgement', async () => {
  const owner = new ModelSettingsLifetime(); vi.mocked(workspaceApi.probe).mockRejectedValue(new Error('PRIVATE_KEY'));
  vi.spyOn(workspaceApi, 'settings').mockResolvedValue(settings());
  const first = fixture(owner); await flushVue(); await click(button(first.root, '测试连接')); await flushVue(); first.unmount();
  const second = fixture(owner); await flushVue();
  expect(second.root.textContent).toContain('本窗口不重试'); expect(second.root.textContent).not.toContain('PRIVATE_KEY');
  for (const phrase of ['移除 Key', '测试连接', '保存']) expect(button(second.root, phrase).props.disabled).toBe(true);
  await click(button(second.root, '明确只读查看当前设置')); await flushVue();
  expect(workspaceApi.settings).toHaveBeenCalledOnce(); expect(owner.state.uncertain).toBe(true);
  for (const phrase of ['移除 Key', '测试连接', '保存']) expect(button(second.root, phrase).props.disabled).toBe(true);
  expect(workspaceApi.probe).toHaveBeenCalledOnce(); expect(workspaceApi.saveProfile).not.toHaveBeenCalled();
});
