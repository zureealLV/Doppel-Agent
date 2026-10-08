// One ORIGINAL App-owned settings/probe request lifetime, not a new transport,
// executor, retry queue or store. No constructor/mount IO or extra key/config copy.
// The original admitted request closure retains its payload until settlement;
// clearing a dialog draft is not transport payload zeroization or cancellation.
// Joining the HTTP promise isn't proof of SDK/provider/SQLite/OS physical drain.
import { reactive, type InjectionKey } from 'vue';
import type { PublicSettings } from './workspaceTypes';
type SettingsAction = 'save' | 'forget' | 'delete' | 'read_settings';
type ProbeReply = { ok: boolean; reply: string };
type Request = { kind: SettingsAction; profileId: string; invoke(): Promise<PublicSettings> }
  | { kind: 'probe'; profileId: string; invoke(): Promise<ProbeReply> };
export type ModelSettingsReply = { kind: SettingsAction; profileId: string; value: PublicSettings }
  | { kind: 'probe'; profileId: string; value: ProbeReply };
export const modelSettingsLifetimeKey: InjectionKey<ModelSettingsLifetime> = Symbol('root-owned-model-settings-lifetime');

export class ModelSettingsLifetime {
  readonly state = reactive({ busy: false, closing: false, deferred: false, uncertain: false,
    uncertainAction: '' as Request['kind'] | '', uncertainProfileId: '', lastAction: '' as Request['kind'] | '',
    sourceProfileId: '', outcome: 'none' as 'none' | 'pending' | 'not_started' | 'reply_available' | 'acknowledged' | 'unknown', error: '' });
  private serial = 0;
  private active = 0;
  private publicationEpoch = 0;
  private closeEpoch = 0;
  private disposed = false;
  // Reactive presentation is not an admission authority. No public uncertainty
  // reset, and only the original close recovery changes private closing state.
  private uncertain = false;
  private closing = false;
  private flight: Promise<void> | undefined;
  private pending: { token: number; epoch: number; reply: ModelSettingsReply } | null = null;

  attach(): number {
    if (this.disposed) throw new Error('settings_owner_disposed');
    this.publicationEpoch++; this.active = ++this.serial; return this.active;
  }
  detach(token: number): void { if (token === this.active) { this.active = 0; this.publicationEpoch++; } }
  isActive(token: number): boolean { return !this.disposed && !this.closing && token !== 0 && token === this.active; }
  get unloadBlocked(): boolean { return !!this.flight || this.uncertain; }
  canRead(token: number): boolean { return this.isActive(token) && !this.flight && !this.pending; }
  canMutate(token: number): boolean { return this.canRead(token) && !this.uncertain; }

  perform(token: number, request: Request): Promise<void> {
    if (request.kind === 'read_settings' ? !this.canRead(token) : !this.canMutate(token)) {
      if (!this.disposed) this.state.error = '原设置请求仍在进行、回复待采用或结果未知；没有重发。';
      return Promise.resolve();
    }
    const epoch = this.publicationEpoch;
    this.state.busy = true; this.state.lastAction = request.kind; this.state.sourceProfileId = request.profileId;
    this.state.outcome = 'pending'; this.state.error = '';
    let entered = false;
    const work = Promise.resolve().then(async () => {
      // Fence BEFORE dispatch only. Once invoke entered, no abort/timeout/drop,
      // even dialog hidden/unmounted, failed-close recovery or root disposal.
      if (!this.isActive(token) || epoch !== this.publicationEpoch) {
        if (!this.disposed) this.state.outcome = 'not_started'; return;
      }
      entered = true;
      const value = await request.invoke();
      if (this.disposed) return;
      // Original typed transport correlation only, NOT source authentication or
      // native success. Retain one reply, never request config/key/exception.
      const reply = { kind: request.kind, profileId: request.profileId, value } as ModelSettingsReply;
      this.pending = { token, epoch, reply }; this.state.deferred = true; this.state.outcome = 'reply_available';
    }).catch(() => {
      if (this.disposed) return;
      if (entered && request.kind !== 'read_settings' && !this.uncertain) {
        this.uncertain = true; this.state.uncertain = true; this.state.uncertainAction = request.kind; this.state.uncertainProfileId = request.profileId;
      }
      this.state.outcome = 'unknown';
      this.state.error = '请求结果未知；未认定设置已更新或测试未执行。本窗口不重试，私有错误不显示。';
    }).finally(() => { if (this.flight === work) { this.flight = undefined; this.state.busy = false; } });
    this.flight = work; return work;
  }

  takeReply(token: number, explicit = false): ModelSettingsReply | null {
    const pending = this.pending;
    if (!this.isActive(token) || this.flight || !pending
        || !explicit && (pending.token !== token || pending.epoch !== this.publicationEpoch)) return null;
    this.pending = null; this.state.deferred = false; this.state.outcome = 'acknowledged';
    // A successful read or explicit late adoption NEVER clears uncertainty.
    // No reset method: fresh ownership requires ORIGINAL host drain/re-entry,
    // and backend persisted-unknown admission remains an independent C gate.
    return pending.reply;
  }

  async prepareClose(): Promise<void> {
    const epoch = ++this.closeEpoch; this.publicationEpoch++; this.closing = true; this.state.closing = true;
    const original = this.flight; if (original) await original;
    if (this.disposed || epoch !== this.closeEpoch) throw new Error('设置准备已被替代；不得继续旧主机切换。');
  }
  finishClose(): void { this.closeEpoch++; this.publicationEpoch++; this.closing = false; this.state.closing = false; }
  dispose(): void {
    this.disposed = true; this.closeEpoch++; this.publicationEpoch++; this.active = 0;
    this.pending = null; this.state.deferred = false; this.closing = true; this.state.closing = true;
    // Original entered promise is still retained by its own closure until joined.
  }
}
