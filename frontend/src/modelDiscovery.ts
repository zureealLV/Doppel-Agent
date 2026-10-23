export type DiscoveryStrategy = 'openai-list' | 'ollama-tags' | 'manual';
export interface ModelDiscoveryResult {
  status: 'ok' | 'error' | 'manual'; models: { id: string }[]; strategy: string; pages: number;
  metadata_only: true; credential_revision?: string; error_code?: string; message?: string;
}
export interface DirectoryEntry { models: string[]; fetchedAt: number; stale: boolean }
export function directoryIdentity(config: { base_url: string; preset: string; provider?: string; region?: string; plan?: string }, revision: string, strategy: DiscoveryStrategy): string {
  let endpoint = config.base_url;
  try { const url = new URL(endpoint); endpoint = `${url.protocol}//${url.host}${url.pathname.replace(/\/+$/, '')}`; } catch { /* Invalid drafts never dispatch. */ }
  return JSON.stringify([endpoint, config.provider || (config.preset === 'mock' ? 'mock' : 'openai'), config.preset, config.region || '', config.plan || '', revision, strategy]);
}
export class ModelDirectoryCache {
  private entries = new Map<string, DirectoryEntry>();
  get(identity: string): DirectoryEntry | undefined { return this.entries.get(identity); }
  markStale(identity: string): DirectoryEntry | undefined {
    const previous = this.entries.get(identity);
    if (previous) previous.stale = true;
    return previous;
  }
  accept(identity: string, reply: ModelDiscoveryResult, now = Date.now()): DirectoryEntry | undefined {
    if (reply.status !== 'ok') {
      return this.markStale(identity);
    }
    const entry = { models: reply.models.map(m => m.id), fetchedAt: now, stale: false };
    this.entries.set(identity, entry);
    if (this.entries.size > 32) this.entries.delete(this.entries.keys().next().value!);
    return entry;
  }
}
