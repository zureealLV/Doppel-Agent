---
name: test-repair
description: Diagnose and repair deterministic test failures without weakening assertions or hiding product bugs.
---

# Test Repair

## Trigger

Use when a test suite, CI job, fixture, snapshot, or environment-specific check fails.

## Workflow

1. Run the smallest failing test and capture its full assertion or traceback.
2. Classify the cause as product defect, stale expectation, isolation leak, nondeterminism, or environment mismatch.
3. Inspect fixture lifetime, clock/randomness, filesystem, network, and concurrency boundaries.
4. Repair production code when behavior is wrong; update the test only when the expected contract changed.
5. Repeat the focused test, neighboring module tests, and the repository gate.

## Tool selection

- Use evidence-reading tools before command execution.
- Request command and write approvals through Doppel policy.
- Use MCP only for explicitly configured external test systems.

## Stop conditions

Stop when the failure is deterministic, the fix preserves assertion strength, and focused plus regression suites pass.

## Failure fallback

For unavailable external dependencies, provide an isolated test and name the exact live check still required.

## Output

Return failure classification, root cause, repair, commands and outputs, coverage added, and any unverified external boundary.

## Acceptance

No test is skipped, muted, retried blindly, or weakened solely to make CI green.
