---
name: code-review
description: Evidence-first review of code changes for correctness, security, regressions, and missing tests.
---

# Code Review

## Trigger

Use when the user asks to review a diff, branch, pull request, patch, or recent implementation.

## Workflow

1. Establish the review base and list changed files before reading unrelated code.
2. Read the smallest relevant diff and its surrounding call sites.
3. Trace input validation, authorization, persistence, concurrency, cleanup, and error paths.
4. Check tests for the risky branches found in step 3; do not equate test count with coverage.
5. Report only actionable findings, ordered by severity, with exact evidence paths and line ranges.

## Tool selection

- Prefer `glob`, `grep`, and bounded `read_file` windows.
- Use MCP tools only through their normalized `mcp__server__tool` names.
- Do not write files or execute commands during a review unless the user separately grants that capability.

## Stop conditions

Stop after every changed subsystem has been traced to tests or when missing evidence prevents a defensible claim.

## Failure fallback

If the diff or base is unavailable, state the missing artifact and perform a clearly labelled static inspection only.

## Output

List findings first. Each finding contains severity, defect, impact, reproduction condition, and evidence path. Put assumptions and test gaps after findings.

## Acceptance

Every finding is tied to reachable code, avoids style-only noise, and explains why existing tests do not catch it.
