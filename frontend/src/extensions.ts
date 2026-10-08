import { reactive } from "vue";
import { extensionsApi, parseExtensionDiscovery, parseExtensionServers, parseExtensionSkills, parseExtensionSnapshot,
  type ExtensionDiscovery, type ExtensionProbe, type ExtensionServer, type ExtensionSkills, type ExtensionSnapshot } from "./extensionsApi";

export interface ExtensionApi {
  servers(): Promise<ExtensionServer[]>; cached(name: string): Promise<ExtensionSnapshot>;
  discover(name: string, action: "probe" | "refresh"): Promise<ExtensionDiscovery>;
  skills(): Promise<ExtensionSkills>; reloadSkills(): Promise<ExtensionSkills & { action: "reload_skills" }>;
}
export interface ExtensionIntent {
  action: "probe" | "refresh" | "reload_skills"; server: string | null; transport: ExtensionServer['transport'] | null;
  body: Readonly<{ action: "probe" | "refresh" | "reload_skills"; confirmed: true }>;
}
type Receipt = ExtensionDiscovery | ExtensionSkills & { action: "reload_skills" };
function copy<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T; }

export class ExtensionController {
  readonly state = reactive({ active: false, closing: false, busy: false, error: "",
    servers: [] as ExtensionServer[], selectedServer: null as string | null, action: "probe" as "probe" | "refresh",
    catalog: null as ExtensionSnapshot | null, probe: null as ExtensionProbe | null, skills: null as ExtensionSkills | null,
    confirmed: false, reloadConfirmed: false, uncertain: false, exitAcknowledged: false, deferredAcknowledgement: false,
    intent: null as ExtensionIntent | null, lastAction: null as ExtensionIntent | null });
  private flight?: Promise<void>;
  private epoch = 0;
  private closeEpoch = 0;
  private disposed = false;
  private consent = "";
  private reloadConsent = false;
  private deferred: Receipt | null = null;
  constructor(private readonly api: ExtensionApi = extensionsApi) {}
  get selectionLocked(): boolean {
    return this.state.busy || this.state.closing || this.state.uncertain || this.state.deferredAcknowledgement;
  }
  get unloadBlocked(): boolean { return this.state.busy || this.state.uncertain && !this.state.exitAcknowledged; }
  private canRead(): boolean { return !this.disposed && this.state.active && !this.selectionLocked && !this.flight; }
  private disarm(): void { this.consent = ""; this.reloadConsent = false; this.state.confirmed = false; this.state.reloadConfirmed = false; }
  activate(active: boolean): void {
    if (active !== this.state.active) { this.epoch++; this.disarm(); }
    this.state.active = active && !this.disposed && !this.state.closing;
    // Visibility changes never read files/cache, connect, retry or adopt a late receipt.
  }
  private server(): ExtensionServer | undefined { return this.state.servers.find(server => server.name === this.state.selectedServer); }
  private signature(): string { const server = this.server(); return server ? JSON.stringify([server.name, server.transport, this.state.action]) : ""; }
  selectServer(name: string): boolean {
    if (!this.canRead() || !this.state.servers.some(server => server.name === name)) return false;
    if (name !== this.state.selectedServer) {
      this.epoch++; this.disarm(); this.state.selectedServer = name; this.state.catalog = null;
      this.state.probe = null; this.state.lastAction = null; this.state.error = "";
    }
    return true;
  }
  chooseAction(action: "probe" | "refresh"): void {
    if (!this.canRead() || !["probe", "refresh"].includes(action)) return;
    if (this.state.action !== action) { this.state.action = action; this.disarm(); }
  }
  confirmDiscovery(confirmed: boolean): void {
    if (!this.canRead() || !this.server()) return;
    this.consent = confirmed === true ? this.signature() : ""; this.state.confirmed = !!this.consent;
  }
  confirmSkillReload(confirmed: boolean): void {
    if (!this.canRead()) return;
    this.reloadConsent = confirmed === true; this.state.reloadConfirmed = this.reloadConsent;
  }
  private read<T>(send: () => Promise<T>, publish: (value: T) => void): Promise<void> {
    if (!this.canRead()) return Promise.resolve();
    this.disarm(); this.state.busy = true; this.state.error = ""; const epoch = ++this.epoch;
    // Assign the original promise before dispatch; synchronous throws cannot leave
    // a ghost flight. No abort/timeout/unmount is taken as remote or local drain.
    const work = Promise.resolve().then(send).then(value => {
      if (!this.disposed && this.state.active && !this.state.closing && epoch === this.epoch) publish(value);
    }).catch(() => {
      if (!this.disposed && this.state.active && !this.state.closing && epoch === this.epoch)
        this.state.error = "扩展目录读取未完成；没有探测、执行工具或授予运行权限。";
    }).finally(() => { if (this.flight === work) { this.flight = undefined; this.state.busy = false; } });
    this.flight = work; return work;
  }
  readServers(): Promise<void> {
    return this.read(() => this.api.servers(), value => {
      this.state.servers = copy(parseExtensionServers(value));
      if (this.state.selectedServer && !this.server()) {
        this.state.selectedServer = null; this.state.catalog = null; this.state.probe = null; this.state.lastAction = null;
      }
    });
  }
  readCached(): Promise<void> {
    const name = this.state.selectedServer;
    if (!name || !this.canRead()) return Promise.resolve();
    this.state.catalog = null;
    return this.read(() => this.api.cached(name), value => { this.state.catalog = copy(parseExtensionSnapshot(value, name)); });
  }
  readSkills(): Promise<void> {
    return this.read(() => this.api.skills(), value => { this.state.skills = copy(parseExtensionSkills(value)); });
  }
  discover(): Promise<void> {
    const server = this.server();
    if (!this.canRead() || !server || !this.state.confirmed || this.consent !== this.signature()) return Promise.resolve();
    const intent = Object.freeze({ action: this.state.action, server: server.name, transport: server.transport,
      body: Object.freeze({ action: this.state.action, confirmed: true as const }) });
    this.state.catalog = null; this.state.probe = null;
    return this.dispatch(intent, () => this.api.discover(server.name, intent.action));
  }
  reloadSkills(): Promise<void> {
    if (!this.canRead() || !this.state.reloadConfirmed || !this.reloadConsent) return Promise.resolve();
    this.state.skills = null;
    return this.dispatch(Object.freeze({ action: "reload_skills", server: null, transport: null,
      body: Object.freeze({ action: "reload_skills", confirmed: true as const }) }), () => this.api.reloadSkills());
  }
  private validateReceipt(reply: Receipt, intent: ExtensionIntent): Receipt {
    return intent.action === "reload_skills" ? { ...parseExtensionSkills(reply, true), action: "reload_skills" }
      : parseExtensionDiscovery(reply, intent.server!, intent.action);
  }
  private publishReceipt(reply: Receipt): void {
    if (!this.state.intent) throw new Error("extension_intent_unavailable");
    const value = this.validateReceipt(reply, this.state.intent);
    this.state.lastAction = copy(this.state.intent);
    if (value.action === "reload_skills") this.state.skills = copy(value);
    else if (value.action === "probe") this.state.probe = copy(value);
    else this.state.catalog = copy(value);
    this.state.intent = null; this.state.uncertain = false; this.state.exitAcknowledged = false;
    this.state.deferredAcknowledgement = false; this.deferred = null; this.disarm();
  }
  private dispatch(intent: ExtensionIntent, send: () => Promise<Receipt>): Promise<void> {
    this.disarm(); this.state.busy = true; this.state.error = ""; this.state.intent = intent;
    this.state.lastAction = null; this.state.exitAcknowledged = false; const epoch = ++this.epoch;
    const work = Promise.resolve().then(send).then(reply => {
      const value = copy(this.validateReceipt(reply, intent));
      if (this.disposed) return;
      if (epoch === this.epoch && this.state.active && !this.state.closing) this.publishReceipt(value);
      else { this.deferred = value; this.state.deferredAcknowledgement = true; }
    }).catch(() => {
      if (this.disposed) return;
      // Any failed/invalid POST acknowledgement stays unknown, including late or
      // apparent 4xx replies. No reentry, cache read or exit checkbox resets it.
      this.state.uncertain = true; this.state.error = "扩展操作回复未知；保留原服务器、动作与确认请求，不自动重试。";
    }).finally(() => { if (this.flight === work) { this.flight = undefined; this.state.busy = false; } });
    this.flight = work; return work;
  }
  showOriginalReceipt(): void {
    if (this.disposed || !this.state.active || this.state.closing || this.flight || this.state.uncertain || !this.deferred) return;
    this.publishReceipt(this.deferred); // explicit local adoption only, no new request or consent
  }
  acknowledgeExit(confirmed: boolean): void {
    if (this.disposed || !this.state.active || this.state.closing || this.flight || !this.state.uncertain || confirmed !== true) return;
    this.state.exitAcknowledged = true; this.disarm();
    // Narrows exit only. Keep original intent/unknown, no new operation or absence claim.
  }
  async prepareClose(): Promise<void> {
    const epoch = ++this.closeEpoch; this.state.closing = true; this.state.active = false; this.disarm(); this.epoch++;
    const original = this.flight; if (original) await original;
    if (this.disposed || epoch !== this.closeEpoch) throw new Error("扩展准备已被替代；不得继续旧的主机切换。");
    if (this.state.uncertain && !this.state.exitAcknowledged) throw new Error("扩展操作回复未知；请明确知悉保留原请求后退出，不能重跑。");
    // A known successful deferred receipt can close after original promise join.
    // Original host RunService/MCP manager still owns actual transport/OS cleanup.
  }
  finishClose(): void {
    this.closeEpoch++; this.epoch++; this.disarm(); this.state.closing = false;
    // A timed-out preparation may finish while its request is still live. The
    // original flight/busy/intent remain pinned; the old prepare epoch cannot proceed.
  }
  dispose(): void { this.disposed = true; this.closeEpoch++; this.epoch++; this.disarm(); this.state.active = false; this.state.closing = true; }
}
