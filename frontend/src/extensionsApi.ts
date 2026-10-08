import { request } from "./api";

export interface ExtensionServer { name: string; transport: "stdio" | "streamable_http"; max_concurrency: number }
export interface ExtensionTool {
  logical_name: string; remote_name: string; title: string; description: string;
  text_truncated: boolean; schema_hash: string;
}
export interface ExtensionSnapshot {
  server: string; cache_state: "missing" | "stale_generation" | "cached"; tools: ExtensionTool[];
  total: number | null; limit: 100; truncated: boolean; tool_execution_verified: false;
  output: { sensitive: true; globally_redacted: false; remote_text_trusted: false };
}
export interface ExtensionProbe {
  server: string; action: "probe"; probe_completed: true; protocol_version: string; tool_execution_verified: false;
}
export type ExtensionDiscovery = ExtensionProbe | ExtensionSnapshot & { action: "refresh" };
export interface ExtensionSkills {
  skills: Array<{ name: string; description: string; text_truncated: boolean }>;
  total: number; limit: 100; truncated: boolean; warnings: string[]; warning_total: number; warning_truncated: boolean;
  output: { sensitive: true; globally_redacted: false; text_trusted: false; instructions_returned: false };
}

function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function keys(value: Record<string, unknown>, expected: string[]): boolean {
  return Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key));
}
function text(value: unknown, bytes: number): value is string {
  return typeof value === "string" && value.length <= bytes && new TextEncoder().encode(value).length <= bytes;
}
function serverName(value: unknown): value is string {
  return typeof value === "string" && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(value) && !value.includes("__");
}
function scope(name: string): string {
  if (!serverName(name)) throw new Error("invalid_extension_scope");
  return `/api/v1/extensions/mcp/servers/${name}`;
}
function invalid(): never { throw new Error("invalid_extension_response"); }
export function parseExtensionSnapshot(value: unknown, name: string, refreshed = false): ExtensionSnapshot {
  if (!object(value) || !keys(value, ["server", "cache_state", "tools", "total", "limit", "truncated",
    "tool_execution_verified", "output", ...(refreshed ? ["action"] : [])])
    || value.server !== name || typeof value.cache_state !== "string" || !["missing", "stale_generation", "cached"].includes(value.cache_state)
    || value.tool_execution_verified !== false || value.limit !== 100 || typeof value.truncated !== "boolean"
    || !object(value.output) || !keys(value.output, ["sensitive", "globally_redacted", "remote_text_trusted"])
    || value.output.sensitive !== true || value.output.globally_redacted !== false || value.output.remote_text_trusted !== false
    || !Array.isArray(value.tools) || value.tools.length > 100 || refreshed && (value.action !== "refresh" || value.cache_state !== "cached")) invalid();
  if (value.cache_state === "cached") {
    if (typeof value.total !== "number" || !Number.isInteger(value.total) || value.total < 0 || value.total > 4096
      || value.tools.length !== Math.min(100, value.total) || value.truncated !== (value.total > 100)) invalid();
  } else if (value.total !== null || value.tools.length !== 0 || value.truncated !== false) invalid();
  for (const tool of value.tools) {
    if (!object(tool) || !keys(tool, ["logical_name", "remote_name", "title", "description", "text_truncated", "schema_hash"])
      || !text(tool.logical_name, 256) || !text(tool.remote_name, 256) || !text(tool.title, 256)
      || !text(tool.description, 512) || typeof tool.text_truncated !== "boolean"
      || typeof tool.schema_hash !== "string" || !/^[a-f0-9]{64}$/.test(tool.schema_hash)) invalid();
  }
  return value as unknown as ExtensionSnapshot;
}

export function parseExtensionDiscovery(value: unknown, name: string, action: "probe" | "refresh"): ExtensionDiscovery {
  if (action === "refresh") return { ...parseExtensionSnapshot(value, name, true), action: "refresh" };
  if (!object(value) || !keys(value, ["server", "action", "probe_completed", "protocol_version", "tool_execution_verified"])
    || value.server !== name || value.action !== action || value.probe_completed !== true
    || !text(value.protocol_version, 128) || value.tool_execution_verified !== false) invalid();
  return value as unknown as ExtensionProbe;
}

export function parseExtensionSkills(value: unknown, reloaded = false): ExtensionSkills {
  if (!object(value) || !keys(value, ["skills", "total", "limit", "truncated", "warnings", "warning_total", "warning_truncated",
    "output", ...(reloaded ? ["action"] : [])]) || reloaded && value.action !== "reload_skills"
    || !Array.isArray(value.skills) || value.skills.length > 100 || value.limit !== 100
    || typeof value.total !== "number" || !Number.isSafeInteger(value.total) || value.total < 0
    || value.skills.length !== Math.min(100, value.total) || value.truncated !== (value.total > 100)
    || !Array.isArray(value.warnings) || value.warnings.length > 50
    || typeof value.warning_total !== "number" || !Number.isSafeInteger(value.warning_total) || value.warning_total < 0
    || value.warnings.length !== Math.min(50, value.warning_total) || value.warning_truncated !== (value.warning_total > 50)
    || !object(value.output) || !keys(value.output, ["sensitive", "globally_redacted", "text_trusted", "instructions_returned"])
    || value.output.sensitive !== true || value.output.globally_redacted !== false || value.output.text_trusted !== false
    || value.output.instructions_returned !== false) invalid();
  const names = new Set<string>();
  for (const skill of value.skills) {
    if (!object(skill) || !keys(skill, ["name", "description", "text_truncated"]) || typeof skill.name !== "string"
      || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(skill.name) || names.has(skill.name)
      || !text(skill.description, 512) || typeof skill.text_truncated !== "boolean") invalid();
    names.add(skill.name);
  }
  if (!value.warnings.every(item => text(item, 256))) invalid();
  return value as unknown as ExtensionSkills;
}

export function parseExtensionServers(value: unknown): ExtensionServer[] {
  if (!Array.isArray(value) || value.length > 32) invalid();
  const names = new Set<string>();
  for (const server of value) {
    if (!object(server) || !keys(server, ["name", "transport", "max_concurrency"]) || !serverName(server.name)
      || typeof server.transport !== "string" || !["stdio", "streamable_http"].includes(server.transport)
      || typeof server.max_concurrency !== "number" || !Number.isInteger(server.max_concurrency)
      || server.max_concurrency < 1 || server.max_concurrency > 32 || names.has(server.name)) invalid();
    names.add(server.name);
  }
  return value as ExtensionServer[];
}

// No initialization, automatic discovery, legacy connecting GET, retry, timeout,
// AbortController, browser storage or tool execution. The UI must hold the original
// discovery promise and pin unknown server/action; cached reads cannot reconcile it.
export const extensionsApi = {
  async servers(): Promise<ExtensionServer[]> {
    const value = await request<unknown>("/api/v1/extensions/mcp/servers", { cache: "no-store" });
    if (!object(value) || !keys(value, ["servers"]) || !Array.isArray(value.servers) || value.servers.length > 32) invalid();
    return parseExtensionServers(value.servers);
  },
  async cached(name: string): Promise<ExtensionSnapshot> {
    return parseExtensionSnapshot(await request<unknown>(`${scope(name)}/tools/cached`, { cache: "no-store" }), name);
  },
  async discover(name: string, action: "probe" | "refresh"): Promise<ExtensionDiscovery> {
    const base = scope(name);
    if (action !== "probe" && action !== "refresh") throw new Error("invalid_extension_action");
    const value = await request<unknown>(`${base}/discovery`, { method: "POST", cache: "no-store",
      body: JSON.stringify({ action, confirmed: true }) });
    return parseExtensionDiscovery(value, name, action);
  },
  async skills(): Promise<ExtensionSkills> {
    return parseExtensionSkills(await request<unknown>("/api/v1/extensions/skills", { cache: "no-store" }));
  },
  async reloadSkills(): Promise<ExtensionSkills & { action: "reload_skills" }> {
    const value = await request<unknown>("/api/v1/extensions/skills/reload", { method: "POST", cache: "no-store",
      body: JSON.stringify({ action: "reload_skills", confirmed: true }) });
    return { ...parseExtensionSkills(value, true), action: "reload_skills" };
  },
};
