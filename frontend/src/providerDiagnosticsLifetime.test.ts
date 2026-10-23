import { expect, it } from 'vitest';
import { HttpError } from './api';
import { ModelSettingsLifetime } from './modelSettingsLifetime';

it.each([
  ['provider_probe_authentication', '凭据'], ['provider_probe_permission', '权限'],
  ['provider_probe_rate_limited', '429'], ['provider_probe_model_or_endpoint', '404'],
  ['provider_probe_network', 'TLS'], ['provider_probe_request_or_protocol', '协议'],
  ['provider_probe_timeout', '超时'],
])('displays safe fixed %s guidance but keeps entered probe outcome unknown', async (code, label) => {
  const owner = new ModelSettingsLifetime(), token = owner.attach();
  await owner.perform(token, { kind: 'probe', profileId: 'fixture', invoke: async () => { throw new HttpError(503, code!); } });
  expect(owner.state.uncertain).toBe(true); expect(owner.canMutate(token)).toBe(false);
  expect(owner.state.error).toContain(label);
  const reopened = owner.attach(); expect(owner.canMutate(reopened)).toBe(false);
  expect(owner.state.error).not.toContain('进程已退出');
});
it('does not echo arbitrary transport body or clear uncertainty through a read', async () => {
  const owner = new ModelSettingsLifetime(), token = owner.attach();
  await owner.perform(token, { kind: 'probe', profileId: 'fixture', invoke: async () => { throw new HttpError(503, 'PRIVATE_PROVIDER_BODY'); } });
  expect(owner.state.error).not.toContain('PRIVATE_PROVIDER_BODY');
  await owner.perform(token, { kind: 'read_settings', profileId: 'fixture', invoke: async () => ({ active_profile_id: 'fixture', profiles: [], key_protection: 'offline' }) });
  expect(owner.state.uncertain).toBe(true);
});
