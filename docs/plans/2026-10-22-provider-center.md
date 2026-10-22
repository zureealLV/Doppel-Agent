# Provider center: simple connections, explicit capability boundaries

Date: 2026-10-22 (Asia/Shanghai).

## Problem and design

The former model settings dialog mixed everyday connection fields with legacy
price values, tariff declarations, and recovery controls. Connecting a model
required knowing its protocol, endpoint and model ID before finding the key field.
The user's Hermes screenshots are UX references, not evidence that Doppel-Agent
already implements their authentication or local inference features.

Replace the long form with a provider center:

1. **Configured**: saved profiles, current/default status, edit and explicit use.
2. **API keys**: searchable, curated provider presets with official documentation.
3. **Custom endpoints**: name, Chat Completions URL, model ID, optional key.
4. **Local services**: Ollama, LM Studio, vLLM and llama.cpp connection presets.
5. **Account authorization**: clearly explain unavailable integrations instead of
   presenting login buttons that do not implement authentication.

Selecting a provider creates a fresh draft, supplies its endpoint and offers model
examples; editing an existing profile preserves its saved identity. Everyday fields
remain visible. Legacy prices and fixed tariff declarations live under an initially
collapsed advanced section. Preserve exceptional recovery controls when relevant.

The primary action saves and explicitly applies the profile to the current
conversation through its existing selection API. Saving a global default alone
must not be represented as changing an existing conversation. If the save succeeds
but application cannot be confirmed, report those two outcomes separately.

## Scope and semantics

- A preset means a documented compatible endpoint, **not** an account connection,
  an entitlement, or provider/model-specific runtime acceptance.
- A saved key means configured, not authenticated. A successful explicit probe
  means only that the tested request returned successfully.
- Saving/applying never automatically calls a paid provider. Connection testing is
  a separate, explicitly cost-labelled operation.
- Model examples are editable catalog hints, not a live model inventory. Models,
  account permissions, regions and pricing can change.
- Distinguish regional and billing endpoints. In particular MiMo pay-as-you-go and
  Token Plan are separate entries; never silently move a key between them.
- Existing DPAPI protection, blank-key retention, secret clearing, request ownership,
  unknown-outcome gates and delayed-reply adoption remain intact.
- No automatic credential import, OAuth-token reuse, remote catalog execution,
  background provider tests, balance queries or automatic runtime/model downloads.
- Existing Chat Completions adapters remain the transport boundary. Listing a
  service does not add native Responses, Anthropic Messages, AWS signing or ACP.

## Delivery phases

### Phase 1 — implemented UI and offline acceptance

Provider registry, searchable selection, simple forms, saved-profile application,
advanced-field disclosure, official-source links, offline regressions and real
browser interaction against an isolated local workspace. Keep the daily desktop
binary and actual provider settings untouched. Record acceptance separately from
any candidate build or eventual native EXE acceptance.

### Phase 2 — model discovery and protocol-specific adapters

Add an explicit backend model-list operation with timeouts, bounded response size,
redirect/credential-origin controls, sanitised errors, cancellation and retained
operation ownership. `/models` capability must be per provider, with manual model
entry as fallback. Test that catalog requests never send prompts or pricing sources.
Add Responses and Anthropic adapters only with streaming, tool-call history,
cancellation, usage receipts and offline contract tests; do not equate an HTTP 200
with full agent compatibility.

### Phase 3 — legitimate account authorization

Evaluate official third-party authorization paths independently for each provider.
Use registered client identity, PKCE/state, loopback callback or device authorization
where supported, protected refresh tokens, expiry/revocation and disconnect. Do not
scrape browser cookies, borrow another application's tokens or pretend an API key
is covered by a consumer subscription. Unsupported flows remain unavailable.

### Phase 4 — managed local inference

Treat installation, signed/checksummed downloads, GPU/VRAM sizing, disk estimates,
process lifetime, model licensing, cancellation and uninstall as a separate feature.
Local-service presets in phase 1 only connect to an already running server.

## Official research references

Checked 2026-10-22; documentation verification is not a live authenticated test.

- [DeepSeek first API call](https://api-docs.deepseek.com/): Chat endpoint and model examples.
- [OpenRouter quickstart](https://openrouter.ai/docs/quickstart): compatible gateway endpoint.
- [Alibaba compatibility](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope): regional endpoints; workspace-specific domains recommended, older domains still supported.
- [Kimi quickstart](https://platform.kimi.com/docs/get-api-key): Chat Completions remains documented alongside Responses and Messages.
- [MiniMax OpenAI compatibility](https://platform.minimax.io/docs/api-reference/text-openai-api): international Chat endpoint.
- [BigModel OpenAI compatibility](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction): China pay-as-you-go endpoint.
- [MiMo first API call](https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call): separate pay-as-you-go and Token Plan credentials/endpoints.
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai): OpenAI compatibility endpoint, not native Gemini transport.
- [Groq compatibility](https://console.groq.com/docs/openai), [Together compatibility](https://docs.together.ai/docs/inference/openai-compatibility), [Fireworks compatibility](https://docs.fireworks.ai/tools-sdks/openai-compatibility), [NVIDIA NIM](https://docs.api.nvidia.com/nim/reference/llm-apis).
- [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility): separate direct-cloud and local server examples.
- [LM Studio compatibility](https://lmstudio.ai/docs/developer/openai-compat): configurable local server endpoint.

The catalog is intentionally curated and extensible, not a claim to include every
provider on the Internet. Additional sources are maintained with each registry entry.
