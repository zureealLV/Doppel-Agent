import { reactive } from "vue";

export type WindowAction = "minimize" | "maximize" | "restore" | "close";
export interface DesktopApi { window_action: (action: WindowAction) => Promise<boolean> }
export interface DesktopCloseHooks {
  beforeClose?: () => Promise<void>;
  afterClose?: (closed: boolean) => void;
}
export class DesktopController {
  readonly state = reactive({ ready: false, busy: false, preparingClose: false, error: "" });
  private api?: DesktopApi;
  constructor(private readonly closeHooks: DesktopCloseHooks = {}, private readonly closeWaitMs = 5000) {}
  attach(api?: DesktopApi): void {
    this.api = typeof api?.window_action === "function" ? api : undefined;
    this.state.ready = Boolean(this.api);
  }
  async act(action: WindowAction): Promise<boolean> {
    if (!this.api || this.state.busy || !["minimize", "maximize", "restore", "close"].includes(action)) return false;
    const api = this.api;
    let closed = false;
    this.state.busy = true; this.state.error = "";
    try {
      if (action === "close" && this.closeHooks.beforeClose) {
        this.state.preparingClose = true;
        let timer: ReturnType<typeof setTimeout> | undefined;
        try {
          await Promise.race([
            Promise.resolve().then(() => this.closeHooks.beforeClose!()),
            new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error("close preparation timed out")), this.closeWaitMs); }),
          ]);
        } catch {
          this.state.error = "关闭前准备尚未完成；请检查历史选择、子任务原请求与草稿退出确认。";
          return false;
        } finally {
          if (timer !== undefined) clearTimeout(timer);
          this.state.preparingClose = false;
        }
      }
      // Do not call an old host binding if it detached/replaced while flushing.
      if (this.api !== api) { this.state.error = "原生窗口连接已变化，请重试。"; return false; }
      const accepted = await api.window_action(action);
      if (!accepted) this.state.error = "原生窗口尚未就绪。";
      closed = action === "close" && accepted === true;
      return accepted === true;
    } catch (error) {
      this.state.error = error instanceof Error ? error.message : String(error);
      return false;
    } finally {
      this.state.busy = false;
      if (action === "close") this.closeHooks.afterClose?.(closed);
    }
  }
}
