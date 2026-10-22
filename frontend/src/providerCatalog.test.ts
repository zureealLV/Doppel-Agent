import { expect, it } from "vitest";
it('catalog entries retain auditable sources and distinguish plans, regions and local services', async () => {
  const { providerCatalog, searchProviders, providerForEndpoint } = await import('./providerCatalog');
  expect(providerCatalog.length).toBeGreaterThanOrEqual(24);
  expect(new Set(providerCatalog.map(p => p.id)).size).toBe(providerCatalog.length);
  for (const provider of providerCatalog) {
    expect(new URL(provider.sourceUrl).protocol).toBe('https:');
    expect(provider.verifiedOn).toBe('2026-10-22');
    expect(provider.capabilities).toMatchObject({ protocol: 'openai-chat-completions', nativeResponses: false, nativeAnthropic: false, oauth: false });
    expect(new URL(provider.baseUrl).search).toBe('');
    expect(provider).not.toHaveProperty('api_key');
  }
  expect(searchProviders('mimo').map(p => p.baseUrl)).toEqual(expect.arrayContaining(['https://api.xiaomimimo.com/v1', 'https://token-plan-cn.xiaomimimo.com/v1']));
  expect(searchProviders('Ollama', 'local')).toHaveLength(1);
  expect(searchProviders('Ollama', 'cloud')).toHaveLength(1);
  expect(searchProviders('不存在的供应商')).toEqual([]);
  expect(providerForEndpoint('https://api.deepseek.com/')?.id).toBe('deepseek');
  expect(providerForEndpoint('https://gateway.example/v1')).toBeUndefined();
});
