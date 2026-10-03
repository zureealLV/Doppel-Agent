# Official DeepSeek canary: one shared 10 CNY budget

This is a **v0.15 unreleased candidate**, not a model-quality result or an
account-wide spending limit. It applies to `bench.live_runtime_matrix`, not to
ordinary desktop chats or probes. Never send a paid probe outside this ledger.

## Frozen 2026-10-03 profile

- Official endpoint `https://api.deepseek.com`; requested `deepseek-v4-flash`
  (also accepts `deepseek-flash`). Official documentation maps the former to
  **DeepSeek-V4.1-Flash**. This is an official mapping, not independent physical
  model identification; do not report it as original V4 or a measured result.
- `thinking={"type":"disabled"}`, `temperature=0`, `max_tokens=4096`, no SDK
  retries. Enabled thinking remains unsupported: the three history adapters
  do not preserve `reasoning_content`. Sync/async official-host adapters default
  to disabled thinking; unrelated OpenAI-compatible hosts are unchanged.
- Currency **CNY**, total **10**, input **2 CNY/M**, output **8 CNY/M**. Every
  input token is conservatively priced as a peak-hour cache miss. Discounts are
  not assumed; recorded amounts are **upper bounds**, not actual billed costs.
- A request reserves the whole documented 1M context (1,048,576 input tokens,
  larger than decimal 1M) plus the bounded output: **2.129920 CNY**, before HTTP.
  This intentionally avoids a guessed character-to-token ratio. Known valid
  usage settles the reservation; less than that worst-case amount remaining
  blocks a new request, even if a typical request would be cheaper.
- The price/token assumption is checked for the current Shanghai date. On a
  later date the CLI stops until the official bounds are reverified. Do not
  replace the pricing date or delete a used ledger just to bypass that stop;
  retaining prior accounting and explicitly reconciling a refreshed snapshot
  is required before cross-day continuation.

Sources checked 2026-10-03:
[models/pricing](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/),
[Chat Completions parameters](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/).
These guarantees are conditional on the documented input/output/price bounds
being honoured. A provider-side cap or an isolated, non-replenished total
balance <=10 CNY is still required; the CLI confirmation is an **operator
declaration**, not remote verification of that account setting.

## Shared accounting and failure handling

Both phases use the ONE canonical project ledger:

`D:/Codex Program files/Agent/Doppel-Agent/.bench-results/deepseek-cny-shared.sqlite3`

Different output directories do **not** create different budgets. The paid CLI
rejects another ledger path and cannot mix USD prices with CNY. Original USD
mode remains for other providers/old offline audits; it is a between-run stop
threshold, not a hard cap. Official DeepSeek cannot use that CLI route.

SQLite `BEGIN IMMEDIATE` serializes request reservations and run claims. Every
HTTP attempt has a durable pending reservation before it leaves the process.
Unknown usage, timeout, HTTP/parse errors or out-of-bound usage leave it held
and stop ALL subsequent requests, phases, and fallback. There is no automatic
refund, retry, pending timeout, ledger reset or recovery command.

Run claims prevent replay when a process exits after spending but before an
immutable result is published. A crash between result publication and claim
completion also stops continuation; inspect/reconcile manually rather than
discarding evidence. Known completed records resume without HTTP, and their
tokens/CNY amount must agree with the request ledger. Source, protocol,
capability/fixture/hidden-key hashes, installed dependencies, lock hash, model,
endpoint and billing parameters are frozen across both phases.

Usage bounds include any generated tokens counted by the provider; unknown or
invalid usage is not replaced by a runtime's character estimate. Fallback
shares the exact same sticky provider and ledger. No secret, request body or
hidden-answer content is stored in the ledger.

## Paid execution — only after original local/EXE/clean-source gates

Keep the original 9 navigation -> human evidence review -> 12 blind-review
order. Full 180 remains blocked. Configure only the dedicated
`DOPPEL_AGENT_API_KEY` environment variable; never copy another project's key,
place it in a command-line argument or paste it into a report. The following
commands are instructions, **not a claim they have been executed**.

After actually verifying the provider-side cap/isolated balance:

```powershell
Set-Location 'D:/Codex Program files/Agent/Doppel-Agent'
uv run --extra agent python -m bench.live_runtime_matrix `
  --mode canary --base-url 'https://api.deepseek.com' `
  --model 'deepseek-v4-flash' --max-cost-cny 10 --confirm-provider-cap `
  --output-dir '.bench-results/live-navigation-v015-cny'

# Only after navigation/accounting and human evidence review are sound:
uv run --extra agent python -m bench.live_runtime_matrix `
  --mode review-canary --base-url 'https://api.deepseek.com' `
  --model 'deepseek-v4-flash' --max-cost-cny 10 --confirm-provider-cap `
  --output-dir '.bench-results/live-review-v015-cny'
```

`context.json`, immutable `runs/`, `summary.json` and separate `review_queue.json`
remain independent per phase. Result schema 1.2 uses `currency=CNY`,
`cost_usd=null`, `cost_cny_upper_bound` as an exact decimal string. The summary
separates phase totals from the global shared ledger. Model-quality verdicts
remain pending; no local scripted fixture can supply them.
