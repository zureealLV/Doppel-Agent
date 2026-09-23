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
