// Connection recipes, not a remote model registry or a claim of tested service availability.
export interface ProviderRecipe {
  id: string;
  name: string;
  aliases: readonly string[];
  group: 'cloud' | 'local';
  preset: 'deepseek' | 'openai' | 'local';
  baseUrl: string;
  models: readonly string[];
  authentication: 'api-key' | 'optional-key';
  sourceUrl: string;
  verifiedOn: string;
  note: string;
  discovery: 'openai-list' | 'ollama-tags' | 'manual';
  capabilities: { protocol: 'openai-chat-completions'; nativeResponses: false; nativeAnthropic: false; oauth: false; tools: 'model-dependent' };
}

// Capabilities describe this application's adapter, not every upstream API.
const capabilities: ProviderRecipe['capabilities'] = { protocol: 'openai-chat-completions', nativeResponses: false, nativeAnthropic: false, oauth: false, tools: 'model-dependent' };
function cloud(id: string, name: string, baseUrl: string, sourceUrl: string, models: string[] = [], note = '模型 ID 与权限以提供商控制台为准。', aliases: string[] = []): ProviderRecipe {
  return { id, name, aliases, group: 'cloud', preset: id === 'deepseek' ? 'deepseek' : 'openai', baseUrl, models,
    authentication: 'api-key', sourceUrl, verifiedOn: '2026-10-22', note, capabilities, discovery: ['openai', 'deepseek'].includes(id) ? 'openai-list' : 'manual' };
}
function local(id: string, name: string, port: number, sourceUrl: string, note: string): ProviderRecipe {
  return { id, name, aliases: [], group: 'local', preset: 'local', baseUrl: `http://localhost:${port}/v1`, models: [],
    authentication: 'optional-key', sourceUrl, verifiedOn: '2026-10-22', note, capabilities, discovery: id === 'ollama' ? 'ollama-tags' : 'manual' };
}

export const providerCatalog: readonly ProviderRecipe[] = [
  cloud('deepseek', 'DeepSeek', 'https://api.deepseek.com', 'https://api-docs.deepseek.com/', ['deepseek-flash', 'deepseek-v4-pro'], undefined, ['深度求索']),
  cloud('openai', 'OpenAI', 'https://api.openai.com/v1', 'https://developers.openai.com/api/reference/overview', [], '仅 Chat Completions；请填写支持该接口的模型 ID。ChatGPT 订阅不是 API 余额。'),
  cloud('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', 'https://openrouter.ai/docs/quickstart', [], '填写完整的组织/模型 ID；不同路由与模型的工具支持和计费不同。'),
  cloud('aliyun-cn', '阿里云百炼 · 中国', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope', [], '此为仍支持的旧通用域名；推荐替换为控制台中的 workspace 专属兼容域名。中国与国际区密钥不同。', ['DashScope', 'Qwen', '通义千问']),
  cloud('aliyun-intl', '阿里云百炼 · 新加坡', 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', 'https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope', [], '此为旧国际通用域名；按控制台替换 workspace 专属域名。请使用国际区对应密钥。', ['DashScope', 'Qwen']),
  cloud('minimax', 'MiniMax · 国际', 'https://api.minimax.io/v1', 'https://platform.minimax.io/docs/api-reference/text-openai-api', ['MiniMax-M3.1-Flash'], '国际按量端点，不是中国区或 Coding Plan；请匹配账号区域与套餐。'),
  cloud('zhipu', '智谱 BigModel', 'https://open.bigmodel.cn/api/paas/v4/', 'https://docs.bigmodel.cn/cn/guide/develop/openai/introduction', ['glm-5.3'], '此为通用按量端点，不自动转用 Coding Plan 端点。', ['GLM', 'Zhipu']),
  cloud('mimo-payg', 'MiMo · 按量付费', 'https://api.xiaomimimo.com/v1', 'https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call', [], '按量付费端点，使用对应 API Key（sk）；不是 Token Plan，切错可能走不同账单。', ['小米', 'Xiaomi']),
  cloud('mimo-plan', 'MiMo · Token Plan 中国', 'https://token-plan-cn.xiaomimimo.com/v1', 'https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call', [], 'Token Plan 中国端点，使用对应 tp/ttp Key；与按量端点分开，不自动替换或兜底。', ['小米', 'Xiaomi']),
  cloud('gemini', 'Google Gemini', 'https://generativelanguage.googleapis.com/v1beta/openai/', 'https://ai.google.dev/gemini-api/docs/openai', ['gemini-3.8-flash'], '使用 Gemini API 的 OpenAI 兼容层；不是 Gemini 原生协议或聊天订阅。'),
  cloud('nvidia', 'NVIDIA NIM', 'https://integrate.api.nvidia.com/v1', 'https://docs.api.nvidia.com/nim/reference/llm-apis', [], '填写目录中的完整模型 ID；不是本地 NVIDIA 模型安装器。'),
  cloud('fireworks', 'Fireworks AI', 'https://api.fireworks.ai/inference/v1', 'https://docs.fireworks.ai/tools-sdks/openai-compatibility', [], '填写完整模型/部署 ID，确认模型工具调用支持。'),
  cloud('groq', 'Groq', 'https://api.groq.com/openai/v1', 'https://console.groq.com/docs/openai'),
  cloud('together', 'Together AI', 'https://api.together.ai/v1', 'https://docs.together.ai/docs/inference/openai-compatibility', ['MiniMaxAI/MiniMax-M3']),
  cloud('kimi', 'Kimi · 中国按量', 'https://api.moonshot.cn/v1', 'https://platform.kimi.com/docs/get-api-key', ['kimi-k3'], '仅使用兼容 Chat Completions；不是 Kimi 聊天订阅或 Coding Plan。', ['Moonshot', '月之暗面']),
  cloud('ollama-cloud', 'Ollama Cloud', 'https://ollama.com/v1', 'https://docs.ollama.com/api/openai-compatibility', [], 'Ollama 云服务需要云端 Key，与 localhost 本地服务分开配置。'),
  cloud('mistral', 'Mistral AI', 'https://api.mistral.ai/v1', 'https://docs.mistral.ai/resources/migration-guides', ['mistral-large-latest']),
  cloud('xai', 'xAI', 'https://api.x.ai/v1', 'https://api.x.ai/docs/', [], '使用支持 Chat Completions 的模型 ID；本应用不使用 xAI Responses。', ['Grok']),
  cloud('siliconflow', '硅基流动 SiliconFlow', 'https://api.siliconflow.cn/v1', 'https://docs.siliconflow.cn/docs/api/chat-completions-post', ['deepseek-ai/DeepSeek-V4-Flash'], '中国区兼容端点；请使用完整模型 ID 与匹配区域的 Key。'),
  cloud('cerebras', 'Cerebras', 'https://api.cerebras.ai/v1', 'https://inference-docs.cerebras.ai/resources/openai', ['qwen-3.8-27b'], '共享服务填写模型 ID，专属服务填写 endpoint ID；非标准推理参数未在本应用中开放。'),
  local('ollama', 'Ollama · 本地', 11434, 'https://docs.ollama.com/api/openai-compatibility', '先自行启动 Ollama 并准备模型，填写本机实际模型 ID；无认证时 Key 可空。'),
  local('lmstudio', 'LM Studio', 1234, 'https://lmstudio.ai/docs/developer/openai-compat', '先在 LM Studio 启动兼容服务器，填写已加载模型 ID；端口按实际配置调整。'),
  local('vllm', 'vLLM', 8000, 'https://docs.vllm.ai/en/v0.21.0/getting_started/quickstart/', '连接已启动的 OpenAI 兼容服务；填写 served model ID，端口和认证按服务启动参数调整。'),
  local('llamacpp', 'llama.cpp', 9931, 'https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md', '当前官方示例端口 9931，旧版本可能为 8080，请以启动参数为准。填写已加载模型 ID；工具调用取决于模型和 chat template。'),
];

export function searchProviders(query: string, group?: ProviderRecipe['group']): readonly ProviderRecipe[] {
  const needle = query.trim().toLocaleLowerCase();
  return providerCatalog.filter(provider => (!group || provider.group === group) &&
    [provider.name, provider.id, ...provider.aliases, ...provider.models].join(' ').toLocaleLowerCase().includes(needle));
}

export function providerForEndpoint(baseUrl: string): ProviderRecipe | undefined {
  return providerCatalog.find(provider => provider.baseUrl.replace(/\/$/, '') === baseUrl.replace(/\/$/, ''));
}
