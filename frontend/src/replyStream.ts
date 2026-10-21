import { ownedRuntimeApi } from "./api";
import type { RuntimeEvent } from "./types";

interface ReplyState { streamingText: string; streamingCallId: string; budgetNotice: string }
type ReplyApi = Pick<ReturnType<typeof ownedRuntimeApi>, "events" | "streamEvents">;

// A preview of an unfinished model turn, never a second stored assistant message.
// Durable SSE replay repairs disconnects; it never re-submits a run or tool.
export class NativeReplyStream {
  private epoch = 0;
  private owner = "";
  private cursor = 0;
  private stream: AbortController | null = null;
  private refreshing = false;
  private reconnectAt = 0;
  private disposed = false;
  constructor(private readonly state: ReplyState,
    private readonly api: (cid: string) => ReplyApi = ownedRuntimeApi) {}

  follow(cid: string, rid: string): void {
    if (this.disposed) return;
    const owner = rid ? `${cid}/${rid}` : "";
    if (this.owner !== owner) {
      this.stop(); this.owner = owner; this.cursor = 0; this.reconnectAt = 0;
      Object.assign(this.state, { streamingText: "", streamingCallId: "", budgetNotice: "" });
    }
    if (owner && !this.stream && !this.refreshing && Date.now() >= this.reconnectAt) {
      void this.connect(cid, rid, this.epoch);
    }
  }
  private receive(item: RuntimeEvent, rid: string): void {
    if (item.run_id !== rid || !Number.isSafeInteger(item.seq) || item.seq <= this.cursor) return;
    this.cursor = item.seq;
    if (["graph.budget_exhausted", "run.budget_exhausted"].includes(item.type)) {
      this.state.budgetNotice = "本轮检查步数已用完，正在整理已有证据；未检查部分不会冒充已审查。";
    }
    const p = item.payload;
    if (p.actor !== "runtime_model" || typeof p.call_id !== "string") return;
    if (item.type === "provider.call_started") {
      this.state.streamingText = ""; this.state.streamingCallId = p.call_id;
    } else if (item.type === "provider.content_delta" && p.call_id === this.state.streamingCallId && typeof p.text === "string") {
      // Same 4 MiB ceiling as the provider response; no unbounded preview buffer.
      this.state.streamingText = (this.state.streamingText + p.text).slice(0, 4 * 1024 * 1024);
    }
  }
  private async connect(cid: string, rid: string, epoch: number): Promise<void> {
    this.refreshing = true;
    const api = this.api(cid);
    const valid = () => !this.disposed && epoch === this.epoch;
    try {
      const events = await api.events(rid, this.cursor);
      if (!valid()) return;
      events.forEach(item => this.receive(item, rid));
      const stream = new AbortController(); this.stream = stream;
      await api.streamEvents(rid, this.cursor, stream.signal, item => {
        if (valid() && !stream.signal.aborted) this.receive(item, rid);
      });
    } catch { /* History polling remains authoritative. Reconnect from last seq, never repeat POST. */ }
    finally {
      if (valid()) { this.refreshing = false; this.stream = null; this.reconnectAt = Date.now() + 1500; }
    }
  }
  stop(): void { ++this.epoch; this.stream?.abort(); this.stream = null; this.refreshing = false; }
  dispose(): void { this.disposed = true; this.stop(); }
}
