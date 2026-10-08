# Native conversation backend increment — 2026-10-03

This continues Tasks A–C of the Local Mode completion-before-evaluation plan.
It is **not** whole-product acceptance, a version release, model evaluation or a
claim that all A–H gates are closed. Version remains **0.14.4**, no tag, no daily
`.dist/DoppelAgent` replacement, no commit/push before the consolidated G/H batch.
The three planning-only handoff documents are preserved in the implementation tree.

## Implemented and inspected

- Product ownership/parity contract: `docs/plans/2026-10-03-local-product-parity.md`.
- Forward-only runtime schema migrations 2–3 add separate `native_*` conversations,
  groups and idempotent message projections, run ownership, safe profile snapshots
  and durable turn leases. DDL and migration markers now share a transaction.
- `persistence/conversations.py` supplies drafts, history, search (including archives),
  groups, rename/archive/profile defaults and visible-history deletion. Legacy console
  DB/settings/groups/messages are not imported or modified. A seeded old-v1 runtime
  DB upgrade and unchanged Legacy DB/settings bytes are tested.
- Acceptance inserts the run, its user message, ownership/lease and projection event
  atomically. Completion/status projection is transactional. Injected projection
  failures roll back acceptance/completion; reconciliation is repeat-safe.
- Conversation UUID, checkpoint segment ID and run UUID are distinct. Mode is immutable;
  active/interrupted turns, archive/delete/default-profile races and route/run ownership
  conflicts are rejected. A lease also protects the completion/cancellation drain window.
- Graph continues authoritative checkpoint messages once; Deep bootstraps only empty
  checkpoints. Failed/cancelled/expired segments rotate to a fresh thread and seed only
  completed pairs. Legacy v1 receives completed pairs without claiming native checkpoints.
- RunService reconstructs native history and pending approvals after service/store reopen.
  Accepted model/provider/endpoint snapshots omit credentials and survive changed defaults;
  async children inherit their parent's accepted snapshot. Explicit injected providers are
  labeled `explicit_override`, not falsely labeled an actual saved Mock account.
- Versioned native conversation/group APIs and owned run/event/cancel/resume paths are
  separate from the old proxy. Loopback Host/same-origin checks now also protect v1.
  SSE accepts bounded validated `Last-Event-ID` cursors, including owned streams.
- Cancelling an interrupted run releases its ownership without executing pending tools.
  Provider failures use stable sanitized errors; failed/partial turns do not acquire a
  fabricated completed assistant message.

## Evidence from this increment

All work and destructive cases used disposable seeded workspaces. Providers were
explicit Mock/scripted or loopback controls; external paid model calls were **zero**.
No real user keys were read/decrypted, no balance/account action occurred, no shared ¥10 ledger
was reset and no price/capability/protocol denominator was changed.

Local evidence directory: `.bench-results/local-native-20261003/` (ignored, not published).

| Attempt | Result / provenance |
|---|---|
| `focused-01.log` | 26 passed, initial store/API/two-turn controls |
| `focused-02.log` | 67 passed, 1 failed: Graph edit test omitted the original tool-call ID; retained failure |
| `focused-03.log` | 68 passed after using the actual Graph approval ID contract |
| `focused-04.log` | 71 passed, including lease/cancellation drain and migration atomicity |
| `checkpoint-cancellation-01.log` | 8 passed after correcting the remaining TestClient to a loopback base URL |
| `child-snapshot-01.log` / `02.log` | Initial observer assumed a nonexistent child ID prefix; corrected to actual record shape; then 1 passed |
| `python-full-01.log` | 664 passed, 2 failed, 2 skipped, 1 warning. This run overlapped backend edits and is not frozen acceptance. One failure was the remaining nonloopback TestClient; the fixture aggregate also failed, without per-case JSON from that pytest invocation. No guard/assertion was weakened. |
| `native-fixtures-01.json` | Stable independent rerun: 13 fixtures /21 native paths passed, source-unchanged=true, full_matrix_ready=false, task_quality_scored=false. Predates the final child snapshot labeling change; not final-package proof. |
| `python-full-02.log` | **670 passed /2 skipped /1 warning**, 400.34 s, on unchanged final A–C source; includes the native fixture aggregate and all new tests |
| `final-ac-source-before.json` / `after.json`, `ac-result.json` | Exact source inventory equal before/after the final full run; based on HEAD952a03b plus the uncommitted implementation diff |

Ruff `check src tests` and `git diff --check` pass. The existing warning is the
Starlette/AnyIO deprecated BlockingPortal alias, not suppressed. Baseline supported
counts remain factory81 /service126, exclusions99 /54, original180; those counts
are capability contracts, **not newly rerun whole-matrix success or quality scores**.

Production-path tests cover Graph/Deep two-turn context after service reconstruction
(exactly one prior user/assistant pair), duplicate submissions/completions, concurrent
turn rejection, wrong-conversation run/event/approval/cancel access, restart recovery,
approve/reject/edit/cancel/expiry on native patches, duplicate approval safety,
queue/provider/timeout/shutdown outcomes and cancellation at acceptance/completion writes.
Store/API tests and controlled reconstruction are not an OS hard-kill or native GUI test.

## Remaining gates / next settled step

1. **Task D next:** wire Vue to these native APIs, preserve separately identified old
   Legacy history, restore conversation/run selection and unify native approvals/diff/
   events/subagents/settings/search. No frontend source or bundle changed in this increment.
2. **E:** do not change the root/default UI before full product/browser/native-package
   parity. Retain an accessible Legacy compatibility route and rollback resources.
3. **F:** audit/close all Local Mode gaps, including retention/physical checkpoint cleanup,
   original Skill/MCP API inventory and startup ownership. Current restart recovery assumes
   an exclusively owned local service; launching multiple live services on the same workspace
   needs an explicit owner/recovery gate before whole-product closure. A tombstoned native
   conversation removes visible messages but retains runtime requests/events/checkpoints;
   it is not secure erasure or physical audit deletion.
4. **G/H:** fresh 25-gate consolidation, recalculated matrices/repeats, isolated wheel/sdist/
   EXE, real packaged native visual/keyboard/destructive-case acceptance and process/listener/
   DB closure, then scoped review and one batch commit/push/exact-HEAD CI. Current diagnostic
   full-suite success is not a substitute for these gates.
5. Real-model/human judging/final release stay deferred until implementation/product closure.
