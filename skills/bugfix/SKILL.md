---
name: bugfix
description: Reproduce, localize, minimally fix, and regression-test a concrete software defect.
---

# Bug Fix

## Trigger

Use for a reproducible error, failing behavior, stack trace, incorrect result, or regression.

## Workflow

1. Convert the report into an observable expected-versus-actual statement.
2. Reproduce with the narrowest existing test or command; save the exact failure evidence.
3. Trace from the failing boundary to the first invalid state transition.
4. Add a regression test that fails for the confirmed cause, not merely the visible symptom.
5. Apply the smallest coherent fix and rerun focused then broader checks.

## Tool selection

- Read and search before editing.
- Request approval before `write_file`, `edit_file`, command, or mutating MCP calls.
- Prefer argv-based project commands; never invent a successful run.

## Stop conditions

Stop only when the regression test passes, relevant existing tests pass, and the root cause is explained from evidence.

## Failure fallback

If reproduction depends on unavailable credentials, services, or data, isolate the boundary with a deterministic fixture and label live verification pending.

## Output

Return symptom, root cause, changed files, regression coverage, commands run, results, and remaining risk.

## Acceptance

The original failure is demonstrated, the test fails before the fix when practical, and no unrelated refactor is hidden in the patch.
