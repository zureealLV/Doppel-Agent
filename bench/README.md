# Code-review acceptance case

`review_001` is a **synthetic, intentionally flawed** 45-line Python service. The
runner copies only `service.py` into a fresh temporary workspace; the agent
cannot read `answer_key.json` or this README. The workspace is deleted after
the run. No repository or MedOps source code is sent to the model.

## Run a live evaluation

From the repository root:

```powershell
python bench/run_review.py --env-file '<path to a local .env with DEEPSEEK_API_KEY>'
```

Alternatively set `DEEPSEEK_API_KEY` in the current process and omit
`--env-file`. The key is read in memory and never written to the report.
The report is placed in ignored `.bench-results/`. It contains the prompt,
model answer, tool calls, model-reported token usage, and `pending_human_adjudication`.
The script itself does **not** assign a success percentage.

## Adjudication rubric

1. Did `tool_requests` show a successful `read_file` of `service.py`?
2. Compare findings to `answer_key.json`: owner bypass at line 16, SQL
   injection at line 26, and path traversal at lines 32–34.
3. For each, check a correct file/line, exploit condition, impact, and a
   plausible minimal fix. A keyword-only mention is not sufficient.
4. Count non-seeded findings separately. A claim dependent on an unstated
   caller contract is **unconfirmed**, not a proven vulnerability.
5. Check that the safe control `update_note_title` is not falsely accused of
   SQL injection or missing owner filtering.
6. Confirm no write or command tool was invoked.

This is one small acceptance case, **not** a general coding-agent benchmark.
A broader claim needs multiple repositories/tasks, a fixed model and prompt,
repeat runs, a baseline, denominators, false positives, and real token pricing.

## Deterministic fault matrix

Run the v0.14 offline reliability gate with:

```powershell
uv run --extra agent python -m bench.fault_matrix --output .bench-results/fault-matrix.json
```

The ten fixed scenarios cover Provider retry/circuit behavior, MCP ambiguous
disconnect and schema reconnect behavior, slow resumable SSE, restart lease
reconciliation, and process timeout cleanup. Every result stores the injected
fault, expected policy, raw observation, pass bit, and failure reason. These
fixed transports are systems evidence, not live-provider or model-quality data.

## v0.15 live matrix canary

Inspect the readiness gate without a credential or paid call:

```powershell
uv run --extra agent python -m bench.live_runtime_matrix --preflight
```

Two paid paths are enabled: **9 read-only navigation runs** (`--mode canary`)
and **12 blind read-only review runs** (`--mode review-canary`; four seeded
cases on legacy/graph/deep, one repeat). The review workspace contains only
`service.py`; `answer_key.json` remains outside it. Protocol v1.1 replaces
the review prompts that disclosed the defect type in v1.0. Historical v1.0
mock reports must not be compared as if the prompt were unchanged.

The runner requires a clean Git
checkout, archives one exact commit's tracked `src/doppel_agent`, and uses a
fresh workspace for each run. Review fixture bytes and SHA-256 hashes are frozen
at the start of a run. It records every attempt, including failures, in
ignored `.bench-results/`; a matching second invocation resumes without
replaying recorded runs. Set `DOPPEL_AGENT_API_KEY` in the process environment
and supply `--base-url`, `--model`, both per-million-token prices, and
`--max-cost-usd` to execute. Do not pass the secret as an argument.

`--max-cost-usd` is checked **between** serial runs and cannot prevent a single
model request from overshooting. Missing model-reported usage stops further
paid runs. `summary.json` is transport/accounting evidence, while
`review_queue.json` collects answers for separate human adjudication; the
keyword validator is not a quality score. Use a separate `--output-dir` for
each mode and model. Full 20×3×3 execution is blocked
until independent case fixtures and equivalent runtime capabilities exist.
See `docs/plans/2026-09-23-v0.15-live-runtime-matrix.md` for the remaining gates.

### 2026-09-30 offline adversarial audit

Before using a paid provider, reproduce the stronger local safety audit:

```powershell
uv run --extra agent python -m bench.audit_live_runtime_matrix `
  --output .bench-results/live-runner-audit.json
```

This uses only loopback scripted HTTP and no-network fake runners. It forces
read-tool round trips for all 21 enabled paths and checks request-level usage
stops, fallback after authentication errors, real synchronous error taxonomy,
invalid token usage and non-finite prices/budgets. It never scores model quality.
Exit 1 means a desired safety gate failed. At the 2026-09-30 source baseline,
all 21 positive paths passed but five safety gates failed; paid execution must
wait for Task 3A–3C in the existing plan. See
`bench/reports/2026-09-30-v0.15-retest.md` for evidence and boundaries.

### v0.14.1 safety patch status

Task 3A–3C is now implemented: all six audit gates must pass (exit 0).
Unknown/invalid per-turn usage and transport failures set a sticky terminal
evaluation state **before any subsequent provider request**, including Deep's
fallback. The sync adapter provides typed status/timeout/connection failures
without adding retries. Prices/budget must be positive finite numbers and token
counts non-bool non-negative integers. Unrepresentable costs are recorded and
stop subsequent execution; elapsed time and fixture hashes survive failed runs.

Live result/config/summary/review schema is **1.1**. Existing schema-1.0 stores
are not migrated or overwritten: start a new output directory. The audit
artifact has its own unchanged schema. Its scripted token counts are not paid
model usage. See `docs/releases/v0.14.1.md` and the separate
`bench/reports/2026-09-30-v0.14.1-live-runner-audit.json` for the patch evidence;
the earlier five-failure report is retained unchanged. Full mode remains
blocked; the next offline task is the per-boundary capability contract.

### v0.14.3 capability contract (Task 5A)

`cases/runtime/capabilities.json` version **1.0** freezes the exact v1.1
manifest bytes, eight capability definitions and the requirements of all 20
cases. Inspect each execution boundary separately:

```powershell
uv run --extra agent python -m bench.runtime_matrix --boundary direct_factory
uv run --extra agent python -m bench.runtime_matrix --boundary run_service
```

The original denominator remains **180**. Capability-eligible paths are
**81** for the direct production factory and **126** for RunService/API;
the reports include every excluded key and its reasons. Eligibility assumes
the per-case grants and required project verification/MCP configuration; it
does not certify fixtures, exact-once behavior, quality or full-matrix readiness.
The historical Mock smoke still runs all 180 paths with null task scores.

Direct Graph is read-only. RunService injects Graph patch/command/MCP tools.
Deep exposes reviewed patches and configured post-patch verification, but
never standalone commands; thus it cannot satisfy the TDD pre-patch command
requirement. Reconstructed direct Deep cannot automatically restore reviewed
patch metadata; RunService separately persists that metadata. Legacy writes,
synchronous approval and MCPBridge do not establish reviewed patch, durable
resume, cancellable worker or gateway parity. `nav-04` asks for source
navigation, not MCP execution; its historical extra grant is explicitly noted
and must be resolved with its fixture in Task 5B.

Live canary context now also freezes the capability contract hash/version,
boundary, original/supported/excluded denominators and exact selected keys
**before any paid request**. Old contexts or changed selections cannot resume;
use a fresh output directory. The enabled choices remain 9 navigation and 12
review paths. Full mode is still hard-blocked pending Task 5B/5C executable
oracles and a versioned parity/reduced-matrix decision, not unlocked merely by
this eligibility report.

Production-path probes also exposed and fixed two existing MCP defects:
RunService's Graph registry omitted `mcp_execute`, and SDK connector cancel
scopes were closed in a different task. Dedicated lifetime owners now enter and
exit each connector in the same task; startup and all-server shutdown drain
owned work even under repeated caller cancellation. Real local stdio gateway
tests cover Graph/Deep approval, execution audit and clean service close.
