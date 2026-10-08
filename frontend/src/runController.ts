import { reactive } from "vue";
import { ownedRuntimeApi } from "./api";
import { mergeEvents } from "./runtime";
import type { RunRecord, RuntimeEvent } from "./types";

export const terminalStatuses = new Set(["completed", "failed", "cancelled", "interrupted_expired"]);

// The same inspector owns standalone runs and conversation-owned runs. Every
// asynchronous completion (including stream callbacks) belongs to one epoch.
export class RunController {
  readonly state = reactive({ run: null as RunRecord | null, events: [] as RuntimeEvent[],
    loading: false, busy: false, error: "", connected: false });
  private epoch = 0;
  private runId = "";
  private conversationId: string | undefined;
  private stream: AbortController | null = null;
  private refreshing = new Set<number>();
  private disposed = false;
  private reconnectAt = 0;
  private decidedInterrupts = new Set<string>();
  private current(epoch: number): boolean { return !this.disposed && epoch === this.epoch; }
  private active(): boolean { return !!this.state.run && !terminalStatuses.has(this.state.run.status); }
  private stop(): void { this.stream?.abort(); this.stream = null; this.state.connected = false; }
  report(error: unknown): void { if (!this.disposed) this.state.error = error instanceof Error ? error.message : String(error); }
  async select(runId: string, conversationId?: string): Promise<void> {
    const epoch = ++this.epoch;
    this.stop(); this.runId = runId; this.conversationId = conversationId; this.reconnectAt = 0;
    this.decidedInterrupts.clear();
    Object.assign(this.state, { run: null, events: [], error: "", busy: false, loading: !!runId });
    if (!runId) return;
    await this.refresh();
    if (this.current(epoch)) this.state.loading = false;
  }
  async refresh(): Promise<void> {
    const epoch = this.epoch, runId = this.runId, api = ownedRuntimeApi(this.conversationId);
    if (!runId || this.disposed || this.refreshing.has(epoch)) return;
    this.refreshing.add(epoch);
    try {
      const after = this.state.events.at(-1)?.seq ?? 0;
      const [run, events] = await Promise.all([api.getRun(runId), api.events(runId, after)]);
      if (!this.current(epoch)) return;
      // Never mix rows from a wrong owner or a late stream into the selection.
      if (run.run_id !== runId || (this.conversationId && run.conversation_id !== this.conversationId)) throw new Error("运行所属对话不匹配。");
      this.state.run = run;
      this.state.events = mergeEvents(this.state.events, events.filter(e => e.run_id === runId));
      if (!this.active()) this.stop();
      else if (!this.stream && Date.now() >= this.reconnectAt) this.connect(epoch, api);
    } catch (error) { if (this.current(epoch)) this.report(error); }
    finally { this.refreshing.delete(epoch); }
  }
  private connect(epoch: number, api: ReturnType<typeof ownedRuntimeApi>): void {
    const stream = new AbortController(); this.stream = stream;
    this.state.connected = true;
    const runId = this.runId;
    void api.streamEvents(runId, this.state.events.at(-1)?.seq ?? 0, stream.signal, item => {
      if (this.current(epoch) && !stream.signal.aborted && item.run_id === runId) {
        this.state.events = mergeEvents(this.state.events, [item]);
        void this.refresh();
      }
    }).catch(() => {
      // Durable polling remains authoritative; retry SSE from the latest seq.
    }).finally(() => {
      if (!this.current(epoch) || this.stream !== stream) return;
      this.stream = null; this.state.connected = false; this.reconnectAt = Date.now() + 1500;
    });
  }
  async action(operation: (api: ReturnType<typeof ownedRuntimeApi>, run: RunRecord) => Promise<unknown>): Promise<void> {
    const run = this.state.run, epoch = this.epoch;
    if (!run || this.state.busy || this.disposed) return;
    this.state.busy = true; this.state.error = "";
    try {
      await operation(ownedRuntimeApi(this.conversationId), run);
      if (this.current(epoch)) await this.refresh();
    } catch (error) { if (this.current(epoch)) this.report(error); }
    finally { if (this.current(epoch)) this.state.busy = false; }
  }
  cancel(): Promise<void> { return this.action((api, run) => api.cancel(run.run_id)); }
  async decide(action: "approve" | "reject" | "edit", toolCalls?: Array<Record<string, unknown>>): Promise<void> {
    const id = this.state.run?.metadata.interrupts?.[0]?.id;
    if (!id || this.state.busy || this.decidedInterrupts.has(id)) return;
    this.decidedInterrupts.add(id);
    await this.action(async (api, run) => {
      const interrupt = run.metadata.interrupts?.[0];
      if (!interrupt || run.status !== "interrupted") throw new Error("没有待审批中断。");
      try { return await api.resume(run.run_id, interrupt.id, { action, ...(toolCalls ? { tool_calls: toolCalls } : {}) }); }
      catch (error) { this.decidedInterrupts.delete(id); throw error; }
    });
  }
  dispose(): void { this.disposed = true; this.epoch++; this.stop(); }
}
