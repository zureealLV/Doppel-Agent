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
