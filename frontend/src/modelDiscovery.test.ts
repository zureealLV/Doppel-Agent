import { expect, it } from 'vitest';
import { directoryIdentity, ModelDirectoryCache } from './modelDiscovery';
import { providerCatalog } from './providerCatalog';
import { ModelSettingsLifetime } from './modelSettingsLifetime';

it('recipes explicitly choose metadata strategy rather than assuming all compatible services list models', () => {
  expect(providerCatalog.find(p => p.id === 'deepseek')?.discovery).toBe('openai-list');
  expect(providerCatalog.find(p => p.id === 'ollama')?.discovery).toBe('ollama-tags');
  expect(providerCatalog.find(p => p.id === 'mimo-plan')?.discovery).toBe('manual');
  expect(providerCatalog.every(p => ['openai-list', 'ollama-tags', 'manual'].includes(p.discovery))).toBe(true);
});

it('metadata lookup is the original App-owned request across unmount and close; late reply needs explicit adoption', async () => {
  const owner = new ModelSettingsLifetime(), token = owner.attach();
  let resolve!: (value: import('./modelDiscovery').ModelDiscoveryResult) => void;
  const work = owner.perform(token, { kind: 'discover', profileId: 'profile-a', directoryIdentity: 'opaque-source', invoke: () => new Promise(yes => { resolve = yes; }) });
  await Promise.resolve(); owner.detach(token); const reopened = owner.attach();
  let closed = false; const close = owner.prepareClose().then(() => { closed = true; });
  await Promise.resolve(); expect(closed).toBe(false); expect(owner.unloadBlocked).toBe(true);
  resolve({ status: 'ok', models: [{ id: 'fixture' }], strategy: 'openai-list', pages: 1, metadata_only: true });
  await work; await close; owner.finishClose();
  expect(owner.takeReply(reopened)).toBeNull();
  const reply = owner.takeReply(reopened, true);
  expect(reply?.kind).toBe('discover');
  if (reply?.kind === 'discover') expect(reply.directoryIdentity).toBe('opaque-source');
});
it('directories are scoped by normalized destination, opaque credential revision and time, never key bytes', () => {
  const cache = new ModelDirectoryCache();
  const key = directoryIdentity({ base_url: 'https://EXAMPLE.com:443/v1/', preset: 'custom', region: 'cn', plan: 'payg' }, 'opaque-rev', 'openai-list');
  const same = directoryIdentity({ base_url: 'https://example.com/v1', preset: 'custom', region: 'cn', plan: 'payg' }, 'opaque-rev', 'openai-list');
  expect(key).toBe(same);
  cache.accept(key, { status: 'ok', models: [{ id: 'org/model' }], metadata_only: true, strategy: 'openai-list', pages: 1 }, 100);
  expect(cache.get(same)?.fetchedAt).toBe(100);
  expect(cache.get(key.replace('opaque-rev', 'new-rev'))).toBeUndefined();
  cache.accept(key, { status: 'error', models: [], metadata_only: true, strategy: 'openai-list', pages: 0, error_code: 'authentication' }, 200);
  expect(cache.get(key)?.models).toEqual(['org/model']);
  expect(cache.get(key)?.stale).toBe(true);
});

it('a rejected metadata transport retains old models as stale without clearing the unknown gate', async () => {
  const owner = new ModelSettingsLifetime(), token = owner.attach();
  owner.directories.accept('fixture-identity', { status: 'ok', models: [{ id: 'old-model' }],
    metadata_only: true, strategy: 'openai-list', pages: 1 }, 100);
  await owner.perform(token, { kind: 'discover', profileId: 'fixture', directoryIdentity: 'fixture-identity',
    invoke: async () => { throw new Error('PRIVATE_NETWORK_ERROR'); } });
  expect(owner.directories.get('fixture-identity')).toEqual({ models: ['old-model'], fetchedAt: 100, stale: true });
  expect(owner.state.uncertain).toBe(true);
  expect(owner.canMutate(token)).toBe(false);
  expect(JSON.stringify(owner.state)).not.toContain('PRIVATE_NETWORK_ERROR');
});

it('discover exact preflight credential rejection remains correctable but arbitrary 400 stays unknown', async () => {
  for (const message of ['credential_reentry_required', 'OTHER_PRIVATE_ERROR']) {
    const owner = new ModelSettingsLifetime(), token = owner.attach();
    await owner.perform(token, { kind: 'discover', profileId: 'fixture', directoryIdentity: 'fixture', invoke: async () => { throw new (await import('./api')).HttpError(400, message); } });
    expect(owner.canMutate(token)).toBe(message === 'credential_reentry_required');
    expect(owner.state.uncertain).toBe(message !== 'credential_reentry_required');
  }
});
