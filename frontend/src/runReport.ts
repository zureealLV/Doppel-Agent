import { reactive, type InjectionKey } from "vue";
import { parseRunReport, reportFilename, reportIdentifier, runReportApi, type RunReport, type RunReportTransport } from "./runReportApi";
export const openRunReportKey: InjectionKey<(run: string) => void> = Symbol("root-owned-run-report");
type ReadKind = "view" | "export";
// Exactly one App-owned controller. No retained per-inspector transports, aborts,
// timer polling, persistence, automatic re-reads or hidden file download initiation.
export class RunReportController {
  readonly state = reactive({ runId: "", active: false, closing: false, busy: false, error: "", sourceNotice: "",
    report: null as RunReport | null, exportSnapshot: null as RunReport | null, deferred: false, confirmed: false,
    exportState: "none" as "none" | "request_started" | "requested" | "unknown", exportRunId: "" });
  private pending: { kind: ReadKind; report: RunReport } | null = null;
  private prepared: RunReport | null = null;
  private flight: Promise<void> | undefined;
  private epoch = 0;
  private closeEpoch = 0;
  private disposed = false;
  constructor(private readonly api: RunReportTransport = runReportApi) {}
  get selectionLocked(): boolean { return this.disposed || this.state.closing || this.state.busy || !!this.flight; }
  get unloadBlocked(): boolean { return !!this.flight; }
  private usable(): boolean { return this.state.active && !this.selectionLocked; }
  private disarm(): void { this.state.confirmed = false; }
  activate(active: boolean): void {
    if (active !== this.state.active) { this.epoch++; this.disarm(); }
    this.state.active = active && !this.disposed && !this.state.closing;
  }
  selectSource(run: string): boolean {
    if (run === this.state.runId && reportIdentifier(run) && !this.disposed) { this.state.sourceNotice = ""; return true; }
    if (!this.usable() || !reportIdentifier(run)) {
      this.state.sourceNotice = "报告来源已固定或不可用；没有切换、读取或重发原请求。"; return false;
    }
    this.epoch++; this.disarm(); this.state.runId = run; this.state.report = null; this.state.exportSnapshot = null;
    this.prepared = null; this.pending = null; this.state.deferred = false; this.state.error = ""; this.state.sourceNotice = "";
    this.state.exportState = "none"; this.state.exportRunId = ""; return true;
  }
  private publish(kind: ReadKind, report: RunReport): void {
    if (kind === "view") this.state.report = report;
    else { this.prepared = report; this.state.exportSnapshot = report; }
  }
  private read(kind: ReadKind): Promise<void> {
    if (!this.usable() || !reportIdentifier(this.state.runId)) return Promise.resolve();
    const run = this.state.runId, epoch = ++this.epoch; this.disarm(); this.state.error = ""; this.state.busy = true;
    this.pending = null; this.state.deferred = false;
    if (kind === "view") this.state.report = null;
    else { this.prepared = null; this.state.exportSnapshot = null; this.state.exportState = "none"; this.state.exportRunId = ""; }
    const work = Promise.resolve().then(() => kind === "view" ? this.api.read(run) : this.api.download(run)).then(value => {
      const report = parseRunReport(value, run);
      if (this.disposed) return;
      if (epoch === this.epoch && this.state.active && !this.state.closing) this.publish(kind, report);
      else { this.pending = { kind, report }; this.state.deferred = true; } // one bounded original-source snapshot
    }).catch(() => {
      if (!this.disposed) this.state.error = "报告读取未完成或边界不符；没有显示私有错误、启动任务或自动重试。";
    }).finally(() => { if (this.flight === work) { this.flight = undefined; this.state.busy = false; } });
    this.flight = work; return work;
  }
  readReport(): Promise<void> { return this.read("view"); }
  prepareExport(): Promise<void> { return this.read("export"); }
  adoptDeferred(): void {
    if (!this.usable() || !this.pending || this.pending.report.run_id !== this.state.runId) return;
    this.disarm(); this.publish(this.pending.kind, this.pending.report); this.pending = null; this.state.deferred = false;
  }
  confirmExport(confirmed: boolean): void { if (this.usable()) this.state.confirmed = confirmed === true && !!this.prepared; }
  exportPrepared(): Promise<void> {
    if (!this.usable() || !this.prepared || !this.state.confirmed || this.prepared.run_id !== this.state.runId) return Promise.resolve();
    const original = this.prepared, filename = reportFilename(original.run_id), text = JSON.stringify(original, null, 2), epoch = ++this.epoch;
    this.disarm(); this.state.busy = true; this.state.error = ""; this.state.exportState = "request_started"; this.state.exportRunId = original.run_id;
    const work = Promise.resolve().then(async () => {
      if (this.disposed || this.state.closing || !this.state.active || epoch !== this.epoch) return false;
      await this.api.save(text, filename); return true;
    }).then(started => {
      if (!this.disposed) this.state.exportState = started ? "requested" : "none"; // no file-completion/drain assertion
    }).catch(() => {
      if (!this.disposed) { this.state.exportState = "unknown"; this.state.error = "本地下载请求结果未知；不能证明未写文件，没有自动重试。"; }
    }).finally(() => { if (this.flight === work) { this.flight = undefined; this.state.busy = false; } });
    this.flight = work; return work;
  }
  async prepareClose(): Promise<void> {
    const epoch = ++this.closeEpoch; this.epoch++; this.state.closing = true; this.state.active = false; this.disarm();
    const original = this.flight; if (original) await original;
    if (this.disposed || epoch !== this.closeEpoch) throw new Error("报告准备已被替代；不得继续旧主机切换。");
    // Host owns actual SDK/provider/SQLite/OS cleanup. Export manager/file writer
    // is not observable here, even once the browser-download request has settled.
  }
  finishClose(): void { this.closeEpoch++; this.epoch++; this.disarm(); this.state.closing = false; }
  dispose(): void { this.disposed = true; this.closeEpoch++; this.epoch++; this.disarm(); this.state.active = false; this.state.closing = true; }
}
