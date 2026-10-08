// C1 definitions FIRST, ALL UNRUN. Promise ownership only, no SDK/native proof.
import { expect, it, vi } from 'vitest';
import { ModelSettingsLifetime } from './modelSettingsLifetime';
import type { PublicSettings } from './workspaceTypes';
const settings = (): PublicSettings => ({ active_profile_id: 'fixture', profiles: [], key_protection: 'fixture' });
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function ticks() { for (let i = 0; i < 12; i++) await Promise.resolve(); }

it('attach/hide/reentry never sends anything; a queued operation is fenced before dispatch on hide/close', async () => {
  const c = new ModelSettingsLifetime(), invoke = vi.fn(async () => settings());
  const first = c.attach(); c.detach(first); const second = c.attach(); expect(invoke).not.toHaveBeenCalled();
  const work = c.perform(second, { kind: 'save', profileId: 'fixture', invoke }); c.detach(second); await work;
  expect(invoke).not.toHaveBeenCalled(); expect(c.state.outcome).toBe('not_started'); expect(c.state.uncertain).toBe(false);
});

it.each(['save', 'forget', 'delete'] as const)('retains original %s promise after dialog unmount and joins it before close', async kind => {
  const c = new ModelSettingsLifetime(), token = c.attach(), flight = deferred<PublicSettings>();
  const invoke = vi.fn(() => flight.promise), work = c.perform(token, { kind, profileId: 'fixture', invoke }); await ticks();
  c.detach(token); const reopened = c.attach(); expect(c.canMutate(reopened)).toBe(false);
  let closed = false; const closing = c.prepareClose().then(() => { closed = true; }); await ticks();
  expect(closed).toBe(false); expect(c.unloadBlocked).toBe(true); expect(invoke).toHaveBeenCalledOnce();
  flight.resolve(settings()); await work; await closing; c.finishClose();
  expect(c.takeReply(reopened)).toBeNull(); expect(c.state.deferred).toBe(true);
  expect(c.takeReply(reopened, true)?.kind).toBe(kind); expect(c.canMutate(reopened)).toBe(true);
});

it('probe lifetime survives hide, and explicit old reply adoption neither repeats the probe nor becomes settings success', async () => {
  const c = new ModelSettingsLifetime(), token = c.attach(), flight = deferred<{ ok: boolean; reply: string }>();
  const invoke = vi.fn(() => flight.promise), work = c.perform(token, { kind: 'probe', profileId: 'fixture', invoke }); await ticks();
  c.detach(token); const next = c.attach(); flight.resolve({ ok: true, reply: 'offline-only' }); await work;
  expect(c.takeReply(next)).toBeNull(); const reply = c.takeReply(next, true);
  expect(reply?.kind).toBe('probe'); if (reply?.kind !== 'probe') throw new Error('fixture requires probe');
  expect(reply.value.reply).toBe('offline-only'); expect(invoke).toHaveBeenCalledOnce();
});

it.each(['save', 'forget', 'delete', 'probe'] as const)('unknown %s is sticky across dialogs and readonly metadata cannot grant retry or claim success', async kind => {
  const c = new ModelSettingsLifetime(), token = c.attach();
  const invoke = vi.fn(async () => { throw new Error('PRIVATE_KEY_OR_EXCEPTION'); });
  const request = kind === 'probe' ? { kind: 'probe' as const, profileId: 'fixture', invoke } : { kind, profileId: 'fixture', invoke };
  await c.perform(token, request);
  expect(c.state.uncertain).toBe(true); expect(c.state.uncertainAction).toBe(kind);
  expect(JSON.stringify(c.state)).not.toContain('PRIVATE_KEY'); expect(c.state.error).toContain('未认定设置已更新');
  c.detach(token); const next = c.attach(); c.finishClose();
  await c.perform(next, { kind: 'save', profileId: 'fixture', invoke: async () => { throw new Error('retry should not run'); } });
  expect(invoke).toHaveBeenCalledOnce(); expect(c.canMutate(next)).toBe(false); expect(c.unloadBlocked).toBe(true);
  const read = vi.fn(async () => settings()); await c.perform(next, { kind: 'read_settings', profileId: 'fixture', invoke: read });
  expect(c.takeReply(next)?.kind).toBe('read_settings'); expect(c.state.uncertain).toBe(true);
  expect(c.state.uncertainAction).toBe(kind); expect(c.canMutate(next)).toBe(false); expect(read).toHaveBeenCalledOnce();
});

it('failed-close recovery fences old late publication/host preparation without abandoning its live request', async () => {
  const c = new ModelSettingsLifetime(), token = c.attach(), flight = deferred<PublicSettings>();
  const work = c.perform(token, { kind: 'save', profileId: 'fixture', invoke: () => flight.promise }); await ticks();
  const close = c.prepareClose(), rejected = expect(close).rejects.toThrow('设置准备已被替代');
  c.finishClose(); expect(c.unloadBlocked).toBe(true); expect(c.canMutate(token)).toBe(false);
  flight.resolve(settings()); await work; await rejected;
  expect(c.takeReply(token)).toBeNull(); expect(c.takeReply(token, true)?.kind).toBe('save');
});

it('disposal never aborts an entered original request and forbids every late publication', async () => {
  const c = new ModelSettingsLifetime(), token = c.attach(), flight = deferred<PublicSettings>();
  const work = c.perform(token, { kind: 'save', profileId: 'fixture', invoke: () => flight.promise }); await ticks();
  c.dispose(); let done = false; work.then(() => { done = true; }); await ticks(); expect(done).toBe(false);
  flight.resolve(settings()); await work; expect(c.takeReply(token, true)).toBeNull(); expect(c.state.deferred).toBe(false);
});

it('presentation mutations cannot clear private unknown or close admission barriers', async () => {
  const c = new ModelSettingsLifetime(), token = c.attach();
  await c.perform(token, { kind: 'save', profileId: 'fixture', invoke: async () => { throw new Error('private'); } });
  c.state.uncertain = false;
  expect(c.canMutate(token)).toBe(false); expect(c.unloadBlocked).toBe(true);
  const retry = vi.fn(async () => settings());
  await c.perform(token, { kind: 'save', profileId: 'fixture', invoke: retry }); expect(retry).not.toHaveBeenCalled();
  await c.perform(token, { kind: 'read_settings', profileId: 'fixture', invoke: async () => settings() });
  const close = c.prepareClose(); c.state.closing = false;
  expect(c.isActive(token)).toBe(false); expect(c.takeReply(token, true)).toBeNull(); await close;
  c.finishClose(); expect(c.takeReply(token, true)?.kind).toBe('read_settings'); expect(c.canMutate(token)).toBe(false);
});
