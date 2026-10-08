import type { RunRecord, RunRequest, RuntimeEvent, SubagentRecord } from "./types";

export class HttpError extends Error { constructor(readonly status: number, message: string) { super(message); } }

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json", "X-Doppel-UI": "1" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string; error?: string };
    throw new HttpError(response.status, typeof payload.detail === "string" ? payload.detail : typeof payload.error === "string" ? payload.error : `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function decodeSseBlocks(input: string): { items: RuntimeEvent[]; rest: string } {
  const normalized = input.replaceAll("\r\n", "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  const items: RuntimeEvent[] = [];
  for (const block of blocks) {
    const data = block.split("\n").filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart()).join("\n");
    if (data) items.push(JSON.parse(data) as RuntimeEvent);
  }
  return { items, rest };
}

export async function streamEvents(
  runId: string,
  after: number,
  signal: AbortSignal,
  onEvent: (event: RuntimeEvent) => void,
  conversationId?: string,
): Promise<void> {
  const response = await fetch(
    `${runPath(runId, conversationId)}/events?stream=true&after_seq=${after}`,
    { headers: { Accept: "text/event-stream", "Last-Event-ID": String(after) }, signal },
  );
  if (!response.ok || !response.body) throw new Error(`SSE HTTP ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try { while (true) {
    const chunk = await reader.read();
    buffer += decoder.decode(chunk.value, { stream: !chunk.done });
    const decoded = decodeSseBlocks(buffer);
    buffer = decoded.rest;
    decoded.items.forEach(onEvent);
    if (chunk.done) return;
  } } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export const runPath = (runId: string, conversationId?: string) => conversationId
  ? `/api/v1/conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(runId)}`
  : `/api/v1/runs/${encodeURIComponent(runId)}`;

export const ownedRuntimeApi = (conversationId?: string) => ({
  getRun: (runId: string) => request<RunRecord>(runPath(runId, conversationId)),
  events: (runId: string, after = 0) => request<RuntimeEvent[]>(`${runPath(runId, conversationId)}/events?after_seq=${after}`),
  streamEvents: (runId: string, after: number, signal: AbortSignal, onEvent: (event: RuntimeEvent) => void) => streamEvents(runId, after, signal, onEvent, conversationId),
  cancel: (runId: string) => post(`${runPath(runId, conversationId)}/cancel`),
  resume: (runId: string, interruptId: string, body: { action: "approve" | "reject" | "edit"; tool_calls?: Array<Record<string, unknown>> }) =>
    post(`${runPath(runId, conversationId)}/interrupts/${encodeURIComponent(interruptId)}/resume`, body),
});

const post = <T>(path: string, body?: unknown) => request<T>(path, {
  method: "POST",
  body: JSON.stringify(body ?? {}),
});

export const runtimeApi = {
  health: () => request<{ status: string }>("/api/v1/health"),
  createRun: (body: RunRequest) => post<{ run_id: string; thread_id: string; status: string }>("/api/v1/runs", body),
  getRun: (runId: string) => request<RunRecord>(`/api/v1/runs/${encodeURIComponent(runId)}`),
  events: (runId: string, after = 0) => request<RuntimeEvent[]>(`/api/v1/runs/${encodeURIComponent(runId)}/events?after_seq=${after}`),
  streamEvents,
  cancel: (runId: string) => post<{ cancel_requested: boolean }>(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`),
  resume: (runId: string, interruptId: string, body: { action: "approve" | "reject" | "edit"; tool_calls?: Array<Record<string, unknown>> }) =>
    post(`/api/v1/runs/${encodeURIComponent(runId)}/interrupts/${encodeURIComponent(interruptId)}/resume`, body),
  subagents: (runId: string) => request<SubagentRecord[]>(`/api/v1/runs/${encodeURIComponent(runId)}/subagents`),
  spawnSubagent: (runId: string, prompt: string) => post<SubagentRecord>(`/api/v1/runs/${encodeURIComponent(runId)}/subagents`, { prompt }),
  followUpSubagent: (runId: string, subagentId: string, prompt: string) => post<SubagentRecord>(
    `/api/v1/runs/${encodeURIComponent(runId)}/subagents/${encodeURIComponent(subagentId)}/follow-ups`, { prompt },
  ),
  cancelSubagent: (runId: string, subagentId: string) => post<{ cancel_requested: boolean }>(
    `/api/v1/runs/${encodeURIComponent(runId)}/subagents/${encodeURIComponent(subagentId)}/cancel`,
  ),
};
