import { reactive } from "vue";
import { HttpError } from "./api";
import type { ContextDescriptor, RunRequest } from "./types";

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
// A failed transport/ACK is not a refusal. Never re-read mutable context or mint
// a new request key until the original admission outcome is known.
export class RunSubmissionController<T extends { run_id: string; status: string }> {
  readonly state = reactive({ busy: false, uncertain: false, error: "" });
  private pending: RunRequest | null = null;
  constructor(private readonly send: (request: RunRequest) => Promise<T>,
    private readonly validate: (reply: T, request: RunRequest) => void = () => {}) {}
  async submit(request: RunRequest, prepare?: () => Promise<ContextDescriptor>): Promise<T | null> {
    if (this.state.busy) return null;
    if (this.pending) throw new Error("上一提交回复未知；只能重试原请求，不能另发新任务。");
    this.state.busy = true; this.state.error = "";
    try {
      const body = clone(request);
      delete body.idempotency_key;
      if (prepare) body.context = clone(await prepare());
      this.pending = { ...body, idempotency_key: crypto.randomUUID() };
      return await this.dispatch(false);
    } catch (error) { this.report(error); throw error; }
    finally { this.state.busy = false; }
  }
  async retry(): Promise<T | null> {
    if (this.state.busy || !this.pending || !this.state.uncertain) return null;
    this.state.busy = true; this.state.error = "";
    try { return await this.dispatch(true); }
    finally { this.state.busy = false; }
  }
  private async dispatch(recovering: boolean): Promise<T> {
    const intent = this.pending!;
    try {
      const reply = await this.send(clone(intent));
      if (!reply || typeof reply.run_id !== "string" || !reply.run_id || typeof reply.status !== "string" || !reply.status) throw new Error("运行接受回复缺少实际 ID/状态；保留原请求。");
      this.validate(reply, intent);
      this.pending = null; this.state.uncertain = false;
      return reply;
    } catch (error) {
      // Even a later 4xx cannot disprove an earlier lost successful admission.
      const refused = !recovering && error instanceof HttpError && [400, 404, 409, 422].includes(error.status);
      this.state.uncertain = !refused;
      if (refused) this.pending = null;
      this.report(error); throw error;
    }
  }
  private report(error: unknown): void { this.state.error = error instanceof Error ? error.message : "运行提交未完成。"; }
  prepareClose(): void {
    if (this.state.busy || this.state.uncertain) throw new Error("运行提交仍在准备/接收或回复未知；请等待并重试原请求后关闭/切换项目。");
  }
}
