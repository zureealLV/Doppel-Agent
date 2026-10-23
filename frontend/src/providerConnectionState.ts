import type { ModelProfile, ProfileForm } from './workspaceTypes';
export interface ValidationReceipt {
  protocol: string; identity: { provider: string; preset: string; region: string; plan: string; endpoint: unknown[] };
  model: string; credential_revision: string; verified_at: string; kind: 'text-probe' | 'mock-probe'; tools: 'unknown';
}
export interface ProviderProbeResult { ok: boolean; reply: string; validation_receipt?: ValidationReceipt; error_code?: string }
export function verificationIdentity(config: Pick<ProfileForm, 'preset' | 'base_url' | 'model' | 'region' | 'plan'> & { provider?: string }, revision: string): string {
  let endpoint: unknown[] = [];
  try { const u = new URL(config.base_url); endpoint = [u.protocol.slice(0, -1), u.hostname.toLowerCase().replace(/^\[|\]$/g, ''), u.port ? Number(u.port) : (u.protocol === 'https:' ? 443 : 80), u.pathname.replace(/\/+$/, '')]; }
  catch { endpoint = config.preset === 'mock' && !config.base_url ? [] : [config.base_url]; }
  return JSON.stringify(['openai-chat-completions', endpoint, config.provider || (config.preset === 'mock' ? 'mock' : 'openai'), config.preset, config.region || '', config.plan || '', config.model, revision]);
}
export function receiptMatches(receipt: ValidationReceipt, config: ProfileForm, profile: ModelProfile | null): boolean {
  const i = receipt.identity;
  const expected = JSON.stringify([receipt.protocol, i.endpoint, i.provider, i.preset, i.region, i.plan, receipt.model, receipt.credential_revision]);
  return !config.api_key && expected === verificationIdentity(config, profile?.credential_revision || '');
}
export function connectionState(input: { saved: boolean; dirty: boolean; pending: boolean; unknown: boolean; failed: boolean; matched: boolean; verifiedAt?: string }): { state: string; label: string } {
  if (input.unknown) return { state: 'unknown', label: '结果未知 · 不自动重试，关闭重开或只读查看不解除门禁' };
  if (input.pending) return { state: 'verifying', label: '验证中 · 等待原请求结束' };
  if (!input.saved) return { state: 'unsaved', label: '尚未保存' };
  if (input.failed) return { state: 'failed', label: '本次测试未通过 · 请按诊断检查' };
  if (input.dirty || input.verifiedAt && !input.matched) return { state: 'configuration-changed', label: '配置已变 · 上次验证不适用于当前配置' };
  if (input.verifiedAt && input.matched) return { state: 'last-success', label: `上次验证 ${input.verifiedAt} · 不代表永久在线` };
  return { state: 'saved-unverified', label: '已保存未验证 · 当前配置未测试' };
}
export function diagnostic(code?: string): string {
  code = code?.replace(/^provider_probe_/, '');
  if (code === 'permission') code = 'authentication';
  if (code === 'model_or_endpoint') return '模型或接口不存在（404）。核对模型 ID、端点路径和权限，不能仅凭状态确认原因。';
  if (code === 'request_or_protocol') return '请求或协议不兼容（400/422）。核对端点、模型和 Chat Completions 协议。';
  return ({ authentication: '凭据或权限被拒绝（401/403）。核对端点、区域、套餐和 Key 权限。', rate_limited: '请求受限（429）。检查速率、配额或服务限制；不能仅凭状态判断余额。', model_not_found: '模型不存在或不可用。核对模型 ID 与账号权限。', network: '网络或 TLS 连接失败。检查地址、证书和网络。', timeout: '请求超时；不自动重试，原请求是否完成需按操作记录判断。', protocol: '协议响应不匹配。核对 Chat Completions 兼容能力。', invalid_response: '元数据格式不兼容，保留手动输入。', unsupported: '服务不支持此模型目录接口，请手动填写。', redirect_blocked: '重定向被阻止；请使用文档中的正确端点。', limit_exceeded: '目录超过安全上限，使用手动输入或缩小服务目录。' } as Record<string, string>)[code || ''] || '服务请求未完成。核对文档和配置；不展示私有异常，不自动重试。';
}
