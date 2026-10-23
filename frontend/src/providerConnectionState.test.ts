import { expect, it } from 'vitest';
import { connectionState, diagnostic, verificationIdentity } from './providerConnectionState';

it('gives unknown precedence over read/close/reopen and separates saved, checking, last success and drift', () => {
  const base = { saved: true, dirty: false, pending: false, unknown: false, failed: false, matched: true, verifiedAt: '2026-10-23T00:00:00Z' };
  expect(connectionState({ ...base, saved: false }).state).toBe('unsaved');
  expect(connectionState({ ...base, verifiedAt: undefined }).state).toBe('saved-unverified');
  expect(connectionState({ ...base, pending: true }).state).toBe('verifying');
  expect(connectionState(base).state).toBe('last-success');
  expect(connectionState({ ...base, matched: false }).state).toBe('configuration-changed');
  expect(connectionState({ ...base, failed: true }).state).toBe('failed');
  expect(connectionState({ ...base, unknown: true, pending: true, dirty: true }).state).toBe('unknown');
});
it('receipts bind protocol endpoint plan region model and opaque key revision, not key/hash', () => {
  const config = { provider: 'openai', preset: 'custom', base_url: 'https://EXAMPLE.com:443/v1/', region: 'cn', plan: 'payg', model: 'a' };
  const key = verificationIdentity(config, 'rev-1');
  expect(key).toBe(verificationIdentity({ ...config, base_url: 'https://example.com/v1' }, 'rev-1'));
  for (const change of [{ model: 'b' }, { plan: 'coding' }, { region: 'global' }, { base_url: 'https://example.com/v2' }]) expect(verificationIdentity({ ...config, ...change }, 'rev-1')).not.toBe(key);
  expect(verificationIdentity(config, 'rev-2')).not.toBe(key);
});
it('explains safe error classes without guessing balance or capabilities', () => {
  expect(diagnostic('rate_limited')).not.toContain('余额不足');
  expect(diagnostic('authentication')).toContain('凭据');
  expect(diagnostic('model_not_found')).toContain('模型');
  expect(diagnostic('protocol')).toContain('协议');
  expect(diagnostic('PRIVATE_RAW_EXCEPTION')).not.toContain('PRIVATE_RAW_EXCEPTION');
});

it('uses the same IPv6 endpoint identity as backend urlsplit without brackets and preserves explicit port zero', () => {
  const config = { preset: 'local', base_url: 'http://[::1]:11434/v1/', model: 'fixture' };
  expect(JSON.parse(verificationIdentity(config, 'rev'))[1]).toEqual(['http', '::1', 11434, '/v1']);
  expect(JSON.parse(verificationIdentity({ ...config, base_url: 'http://localhost:0/v1' }, 'rev'))[1][2]).toBe(0);
});
