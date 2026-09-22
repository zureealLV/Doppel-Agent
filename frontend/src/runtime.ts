import type { RunRecord, RuntimeEvent } from "./types";

export type EventGroup = "lifecycle" | "graph" | "skill" | "mcp" | "subagent" | "patch" | "other";

export function eventGroup(type: string): EventGroup {
  if (type.startsWith("run.") || type.startsWith("runtime.") || type.startsWith("approval.")) return "lifecycle";
  if (type.startsWith("checkpoint.") || type.startsWith("graph.")) return "graph";
  if (type.includes("skill")) return "skill";
  if (type.startsWith("mcp.") || type.includes("mcp_tool")) return "mcp";
  if (type.startsWith("subagent.") || type.includes("subagent")) return "subagent";
  if (type.startsWith("patch.") || type.includes("verification")) return "patch";
  return "other";
}

export function mergeEvents(current: RuntimeEvent[], incoming: RuntimeEvent[]): RuntimeEvent[] {
  const bySequence = new Map<number, RuntimeEvent>();
  for (const item of [...current, ...incoming]) bySequence.set(item.seq, item);
  return [...bySequence.values()].sort((left, right) => left.seq - right.seq);
}

function findUnifiedDiff(value: unknown, seen = new Set<unknown>()): string | null {
  if (!value || typeof value !== "object" || seen.has(value)) return null;
  seen.add(value);
  if ("unified_diff" in value && typeof value.unified_diff === "string") return value.unified_diff;
  for (const nested of Object.values(value)) {
    if (Array.isArray(nested)) {
      for (const item of nested) {
        const found = findUnifiedDiff(item, seen);
        if (found) return found;
      }
    } else {
      const found = findUnifiedDiff(nested, seen);
      if (found) return found;
    }
  }
  return null;
}

export function extractDiff(events: RuntimeEvent[], record: RunRecord | null): string {
  for (const item of [...events].reverse()) {
    const found = findUnifiedDiff(item.payload);
    if (found) return found;
  }
  return findUnifiedDiff(record?.metadata) ?? "";
}

export function formatDuration(milliseconds: number | null): string {
  if (milliseconds === null || !Number.isFinite(milliseconds) || milliseconds < 0) return "—";
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(2)} s`;
  const minutes = Math.floor(milliseconds / 60_000);
  return `${minutes}m ${Math.round((milliseconds % 60_000) / 1000)}s`;
}

export function elapsed(start: string | null | undefined, end: string | null | undefined): number | null {
  if (!start || !end) return null;
  const value = Date.parse(end) - Date.parse(start);
  return Number.isFinite(value) ? value : null;
}

export function compactPayload(payload: Record<string, unknown>): string {
  const text = JSON.stringify(payload, null, 2);
  return text === "{}" ? "无附加数据" : text;
}
