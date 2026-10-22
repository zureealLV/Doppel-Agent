// Compiled parent + real settings + real controller, synthetic host; offline API fixtures only.
// Not browser layout, native focus, live provider or DPAPI evidence.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import NativeWorkspace from './components/ConversationWorkspace.vue';
import LegacyWorkspace from './components/LegacyConversationWorkspace.vue';
import { ModelSettingsLifetime, modelSettingsLifetimeKey } from './modelSettingsLifetime';
import { nativeWorkspaceApi, workspaceApi } from './workspaceApi';
import type { NativeConversation, PublicSettings } from './workspaceTypes';
import type { NativeWorkspaceController, WorkspaceController } from './workspace';
import { button, click, descendants, flushVue, mountVirtualProps, VirtualNode } from './testSupport/virtualHost';

const host = vi.hoisted(() => ({ native: null as NativeWorkspaceController | null, legacy: null as WorkspaceController | null }));
vi.mock('./workspace', async importOriginal => {
  const original = await importOriginal<typeof import('./workspace')>();
  return { ...original,
    NativeWorkspaceController: class extends original.NativeWorkspaceController { constructor(...args: ConstructorParameters<typeof original.NativeWorkspaceController>) { super(...args); host.native = this; } },
    WorkspaceController: class extends original.WorkspaceController { constructor(...args: ConstructorParameters<typeof original.WorkspaceController>) { super(...args); host.legacy = this; } },
  };
});
vi.mock('./components/RunInspector.vue', () => ({ default: defineComponent({ setup: () => () => h('div') }) }));
const settings = (): PublicSettings => ({ active_profile_id: 'old', key_protection: 'offline-fixture', profiles: ['old', 'next'].map(id => ({ id, name: id, provider: 'mock', preset: 'mock', model: 'fixture', base_url: '', input_price: 0, output_price: 0, api_key_saved: false })) });
const conversation = (profileId = 'old', archived = 0): NativeConversation => ({ id: 'c', mode: 'graph', thread_id: 't', title: 'fixture', archived, group_id: null, group_name: null, profile_id: profileId, created_at: '', updated_at: '', messages: [], runs: [], active_run_id: null });
const mounts: Array<{ unmount(): void }> = [];
beforeEach(() => {
  vi.stubGlobal('Document', class Document {}); vi.stubGlobal('ShadowRoot', class ShadowRoot {});
  vi.stubGlobal('window', Object.assign(new EventTarget(), { setInterval: vi.fn(() => 1), clearInterval: vi.fn(), setTimeout: vi.fn(() => 2), clearTimeout: vi.fn() }));
  Object.defineProperties(VirtualNode.prototype, { showModal: { configurable: true, value: vi.fn() }, close: { configurable: true, value: vi.fn() } });
  vi.spyOn(workspaceApi, 'settings').mockResolvedValue(settings());
  vi.spyOn(workspaceApi, 'health').mockResolvedValue({ status: 'ok', workspace: 'offline' });
  vi.spyOn(workspaceApi, 'selection').mockResolvedValue({ saved: true, conversation_id: 'c' });
  vi.spyOn(workspaceApi, 'saveSelection').mockImplementation(async conversation_id => ({ saved: true, conversation_id }));
  vi.spyOn(workspaceApi, 'conversations').mockResolvedValue([]); vi.spyOn(workspaceApi, 'groups').mockResolvedValue([]);
  vi.spyOn(workspaceApi, 'conversation').mockResolvedValue(conversation());
  vi.spyOn(workspaceApi, 'setProfile').mockImplementation(async (_id, profileId) => conversation(profileId));
  vi.spyOn(nativeWorkspaceApi, 'selection').mockResolvedValue({ saved: true, conversation_id: 'c', run_id: null });
  vi.spyOn(nativeWorkspaceApi, 'saveSelection').mockImplementation(async (conversation_id, run_id) => ({ saved: true, conversation_id, run_id }));
  vi.spyOn(nativeWorkspaceApi, 'conversations').mockResolvedValue([]); vi.spyOn(nativeWorkspaceApi, 'groups').mockResolvedValue([]);
  vi.spyOn(nativeWorkspaceApi, 'conversation').mockResolvedValue(conversation());
  vi.spyOn(nativeWorkspaceApi, 'update').mockImplementation(async (_id, body) => conversation(body.profile_id));
  const saved = settings(); saved.active_profile_id = 'next';
  vi.spyOn(workspaceApi, 'saveProfile').mockResolvedValue(saved);
  vi.spyOn(workspaceApi, 'probe').mockRejectedValue(new Error('no paid tests allowed'));
});
afterEach(() => { for (const m of mounts.splice(0)) m.unmount(); Reflect.deleteProperty(VirtualNode.prototype, 'showModal'); Reflect.deleteProperty(VirtualNode.prototype, 'close'); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function mount(kind: 'native' | 'legacy') {
  const m = mountVirtualProps(kind === 'native' ? NativeWorkspace : LegacyWorkspace, { active: true }, app => app.provide(modelSettingsLifetimeKey, new ModelSettingsLifetime())); mounts.push(m); await flushVue();
  const opener = descendants(m.root).find(node => node.tag === 'button' && node.props['aria-label'] === (kind === 'native' ? '模型与 API' : '模型设置'))!;
  await click(opener); await flushVue(); return m;
}
async function save(root: VirtualNode) {
  await click(button(root, 'next')); await flushVue();
  const form = descendants(root).find(node => node.tag === 'form' && String(node.props.class).includes('provider-center-content'))!;
  await (form.props.onSubmit as (event: unknown) => Promise<void>)({ preventDefault() {} }); await flushVue();
}
it.each(['native', 'legacy'] as const)('%s save and use binds the current conversation through its actual profile API', async kind => {
  const m = await mount(kind); await save(m.root);
  expect(workspaceApi.saveProfile).toHaveBeenCalledOnce();
  if (kind === 'native') expect(nativeWorkspaceApi.update).toHaveBeenCalledExactlyOnceWith('c', { profile_id: 'next' });
  else expect(workspaceApi.setProfile).toHaveBeenCalledExactlyOnceWith('c', 'next');
  const controller = kind === 'native' ? host.native! : host.legacy!;
  expect(controller.state.current?.profile_id).toBe('next'); expect(controller.state.profileId).toBe('next');
  expect(m.root.textContent).toContain('已保存并应用'); expect(workspaceApi.probe).not.toHaveBeenCalled();
});
it.each(['native', 'legacy'] as const)('%s failed profile binding preserves old selection and does not retry save/application', async kind => {
  const m = await mount(kind);
  const api = kind === 'native' ? vi.mocked(nativeWorkspaceApi.update) : vi.mocked(workspaceApi.setProfile);
  api.mockRejectedValue(new Error('PRIVATE_BIND_FAILURE')); await save(m.root);
  const controller = kind === 'native' ? host.native! : host.legacy!;
  expect(controller.state.current?.profile_id).toBe('old'); expect(controller.state.profileId).toBe('old');
  expect(controller.state.settings?.active_profile_id).toBe('next');
  expect(m.root.textContent).toContain('已保存为默认'); expect(m.root.textContent).toContain('应用未确认');
  expect(m.root.textContent).not.toContain('PRIVATE_BIND_FAILURE'); expect(api).toHaveBeenCalledOnce(); expect(workspaceApi.saveProfile).toHaveBeenCalledOnce();
});
it.each(['native', 'legacy'] as const)('%s archived profile center saves defaults but cannot apply or probe automatically', async kind => {
  const m = await mount(kind); const controller = kind === 'native' ? host.native! : host.legacy!;
  controller.state.current!.archived = 1; await flushVue();
  expect(button(m.root, '使用已存档案').props.disabled).toBe(true); expect(button(m.root, '保存为默认').props.disabled).toBe(false);
  await save(m.root); expect(workspaceApi.saveProfile).toHaveBeenCalledOnce();
  expect(nativeWorkspaceApi.update).not.toHaveBeenCalled(); expect(workspaceApi.setProfile).not.toHaveBeenCalled(); expect(workspaceApi.probe).not.toHaveBeenCalled();
  expect(controller.state.current!.profile_id).toBe('old'); expect(m.root.textContent).toContain('尚未应用');
});

it('legacy does not report application when the server returns the old profile', async () => {
  const m = await mount('legacy');
  vi.mocked(workspaceApi.setProfile).mockResolvedValue(conversation('old'));
  await save(m.root);
  expect(host.legacy!.state.current?.profile_id).toBe('old');
  expect(host.legacy!.state.profileId).toBe('old');
  expect(m.root.textContent).toContain('应用未确认');
  expect(m.root.textContent).not.toContain('已保存并应用');
  expect(workspaceApi.setProfile).toHaveBeenCalledOnce();
});
