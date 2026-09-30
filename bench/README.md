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

The initial contract was **1.0** / manifest **1.1**. The current
`cases/runtime/capabilities.json` version **1.1** freezes the exact v1.2
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
navigation, not MCP execution; Task 5B removes its historical extra grant in
manifest 1.2 and freezes a dedicated read-only fixture.

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

### Task 5B: nav-04 source fixture (protocol 1.2)

Only `nav-04` permissions changed; all four blind-review prompts and the
20-case / 180-key original denominator remain unchanged. The capability
denominators stay 81 direct / 126 service. Existing live contexts must use a
fresh output directory because manifest/contract hashes changed.

The fixture freezes four complete source files from commit `b90545f`.
Only those hash-checked public bytes reach a fresh workspace; fixture metadata,
expected read anchors and the human rubric remain outside it. No write,
command or MCP execution grant is used. Reproduce the offline native paths:

```powershell
uv run --extra agent python -m bench.audit_runtime_task_fixtures --output .bench-results/task-fixtures-audit.json
```

The scripted provider reads public paths with each factory's real tools.
Deep's native read defaults to 100 lines, so it follows returned pagination.
The validator aggregates successful pages by exact path, requires every frozen
anchor and unchanged public sources, and rejects unrelated/failed reads or
extra files. The report separates requested/actual/fallback runtime identity.
These checks prove source-read evidence, not the correctness of an explanation:
`human_review=pending`, `task_quality_scored=false`. The old keyword field is
still diagnostic only. Live selection stays 9 navigation / 12 review paths;
full mode stays blocked. Original Task 5B still has **12 remaining fixtures**,
then Task 5C must freeze scoring, argv/edits and adjudication inputs.

### Task 5B: tdd-01 (fixture 1.1 /audit 1.1)

`tdd-01` now has an independently reproduced synthetic idempotency defect.
Its source baseline is `dc0e6c5`, **not** a claim that production RunStore is
broken or that these synthetic bytes came from that Git commit. Fixture 1.1
explicitly records `source_kind=synthetic_seed` and freezes public and hidden
file hashes. Only `service.py` and `test_public.py` enter the workspace;
target/regression tests, reference source and scripted test stay external.

The supported control uses **Graph RunService**, with its normal reviewed
patch/command tools and four actual approvals: add candidate test → explicit
failing command → source repair → explicit passing command. Project-configured
post-patch verification also runs red/green. Source/test snapshots establish
ordering and no early effects; the candidate test cannot be weakened after red.
Both patch paths and exact command argv are checked before approval. Failed
imports/syntax are not valid failing assertions, replayed tool IDs are invalid,
and independent target/regression subprocesses must pass after repair.

The existing task-audit command now runs three native `nav-04` factory paths
plus this one supported service control. Audit schema is **1.1**, and every run
records its own boundary; do not pool them into a model-quality rate. Manifest
1.2 /capability 1.1 and their 180 original /81 direct /126 service denominators
are unchanged. TDD Legacy/Deep are unsupported, not failures or omitted parity.
Keep historical nav-only schema-1.0 reports unchanged. Live selection remains
9/12 and full mode is blocked; **11 Task 5B fixtures remain**, starting tdd-02.

These are trusted scripted controls, not a blind/paid model solving the defect.
Hidden materialization and argv bounds do **not** provide strong OS sandboxing
for arbitrary candidate Python code; Task 5C must retain that limitation and
settle evaluation isolation before broader paid execution/quality claims.
