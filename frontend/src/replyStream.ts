import { ownedRuntimeApi } from "./api";
import type { RuntimeEvent } from "./types";

export interface RunActivity {
  id: string; kind: "text" | "tool"; text: string;
  phase?: "progress" | "final"; status: "running" | "completed" | "failed";
}
interface ReplyState {
  streamingText: string; streamingCallId: string; budgetNotice: string;
  activities: Record<string, RunActivity[]>;
}
type ReplyApi = Pick<ReturnType<typeof ownedRuntimeApi>, "events" | "streamEvents">;

// Only public assistant content and tool identity. Never reasoning or tool arguments.
// Durable replay is read-only: it never submits a model call or replays an effect.
export class NativeReplyStream {
  private epoch = 0;
  private conversationEpoch = 0;
  private conversation = "";
  private owner = "";
  private cursors = new Map<string, number>();
  private histories = new Set<string>();
  private loading = new Set<string>();
  private stream: AbortController | null = null;
  private refreshing = false;
  private reconnectAt = 0;
  private disposed = false;
  constructor(private readonly state: ReplyState,
    private readonly api: (cid: string) => ReplyApi = ownedRuntimeApi) {}

  follow(cid: string, rid: string, historyIds: string[] = []): void {
    if (this.disposed) return;
    if (cid !== this.conversation) {
      this.stop(); ++this.conversationEpoch; this.conversation = cid;
      this.cursors.clear(); this.histories.clear(); this.loading.clear();
      this.state.activities = {}; this.state.budgetNotice = "";
    }
    const owner = rid ? `${cid}/${rid}` : "";
    if (this.owner !== owner) {
      this.stop(); this.owner = owner; this.reconnectAt = 0;
      Object.assign(this.state, { streamingText: "", streamingCallId: "" });
    }
    // Completed histories load sequentially, avoiding a burst for long conversations.
    void this.restore(cid, historyIds.filter(id => id !== rid), this.conversationEpoch);
    if (owner && !this.stream && !this.refreshing && Date.now() >= this.reconnectAt) {
      void this.connect(cid, rid, this.epoch);
    }
  }
  private receive(item: RuntimeEvent, rid: string, live: boolean): void {
    if (item.run_id !== rid || !Number.isSafeInteger(item.seq) || item.seq <= (this.cursors.get(rid) || 0)) return;
    this.cursors.set(rid, item.seq);
    if (live && ["graph.budget_exhausted", "run.budget_exhausted"].includes(item.type)) {
      this.state.budgetNotice = "此历史运行使用了有限步数配置；未检查部分不代表已审查。新任务默认不限步数。";
    }
    const p = item.payload;
    const rows = this.state.activities[rid] ||= [];
    if (p.actor === "runtime_model" && typeof p.call_id === "string") {
      const id = `text:${p.call_id}`;
      if (item.type === "provider.call_started" && live) {
        this.state.streamingText = ""; this.state.streamingCallId = p.call_id;
      }
      if (["provider.content_delta", "assistant.turn"].includes(item.type) && typeof p.text === "string") {
        let row = rows.find(i => i.id === id);
        if (!row) { row = { id, kind: "text", text: "", status: "running" }; rows.push(row); }
        row.text = (item.type === "assistant.turn" ? p.text : row.text + p.text).slice(0, 4 * 1024 * 1024);
        if (item.type === "assistant.turn") {
          row.status = "completed"; row.phase = p.phase === "progress" ? "progress" : "final";
        }
        if (live && p.call_id === this.state.streamingCallId) this.state.streamingText = row.text;
      }
      return;
    }
    if (/^(graph|deep|legacy)\.(tool_|mcp_tool_|subagent_|skill_loaded)/.test(item.type) && typeof p.tool === "string") {
      const id = `tool:${typeof p.tool_call_id === "string" ? p.tool_call_id : item.seq}`;
      let row = rows.find(i => i.id === id);
      if (!row) { row = { id, kind: "tool", text: p.tool, status: "running" }; rows.push(row); }
      if (item.type.endsWith("finished")) row.status = "completed";
      if (item.type.endsWith("failed")) row.status = "failed";
    }
  }
  private async replay(cid: string, rid: string, valid: () => boolean, live: boolean): Promise<void> {
    while (valid()) {
      const before = this.cursors.get(rid) || 0;
      const events = await this.api(cid).events(rid, before);
      if (!valid()) return;
      events.forEach(item => this.receive(item, rid, live));
      if (events.length < 500 || (this.cursors.get(rid) || 0) <= before) return;
    }
  }
  private async restore(cid: string, ids: string[], epoch: number): Promise<void> {
    const valid = () => !this.disposed && epoch === this.conversationEpoch && cid === this.conversation;
    for (const id of ids) {
      if (!valid()) return;
      if (this.histories.has(id) || this.loading.has(id)) continue;
      this.loading.add(id);
      try {
        await this.replay(cid, id, valid, false);
        if (valid()) this.histories.add(id);
      } catch { /* A later history refresh retries GET only. */ }
      finally { if (valid()) this.loading.delete(id); }
    }
  }
  private async connect(cid: string, rid: string, epoch: number): Promise<void> {
    this.refreshing = true;
    const valid = () => !this.disposed && epoch === this.epoch;
    try {
      await this.replay(cid, rid, valid, true);
      if (!valid()) return;
      const stream = new AbortController(); this.stream = stream;
      await this.api(cid).streamEvents(rid, this.cursors.get(rid) || 0, stream.signal, item => {
        if (valid() && !stream.signal.aborted) this.receive(item, rid, true);
      });
    } catch { /* Reconnect from last seq, never repeat POST. */ }
    finally {
      if (valid()) { this.refreshing = false; this.stream = null; this.reconnectAt = Date.now() + 1500; }
    }
  }
  stop(): void { ++this.epoch; this.stream?.abort(); this.stream = null; this.refreshing = false; }
  dispose(): void { this.disposed = true; ++this.conversationEpoch; this.stop(); }
}
