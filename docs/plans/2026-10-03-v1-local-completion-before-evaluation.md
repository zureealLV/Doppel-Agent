# Doppel Agent Local Project Completion Before Evaluation — Implementation Plan

> **For Hermes:** Continue the existing plan sequentially in the user-requested child chat. Do not delegate again or create a worktree without another explicit request. Preserve the established whole-version validation/commit cadence.

**Goal:** Finish the v1.0 Local Mode implementation and desktop product closure before spending on real-model evaluation.

**Architecture:** Retain the shared RunService, scheduler, policy gateway, durable events/checkpoints and Legacy baseline. Extend native Graph/Deep conversations and the Vue workspace using actual runtime contracts; keep conversation projections distinct from authoritative runtime/checkpoint state. Move the default UI only after parity and real packaged-window acceptance, retaining an accessible compatibility entry point.

**Tech Stack:** Existing Python 3.11+, FastAPI, SQLite, LangGraph, Deep Agents, Vue 3/TypeScript, pywebview and PyInstaller. No PostgreSQL/Redis/Celery/Kubernetes addition for v1.0.

**Latest human authorization (2026-10-04):** “如果受限了 就验证吧” resumes the
consolidated verification in a fresh attempt04. Preserve old receipts, all N1–N6
requirements, the no-paid-call boundary and the major-version branch cadence.

**Previous human deferral (2026-10-04):** continue implementation/documentation,
but do not operate the computer or perform verification yet. No native automation,
application launch, test/probe/benchmark/verifier or live CI check until testing
is requested again. The current construction state, preserved runtime recovery
records and unchanged full N1–N6/final-version scope are consolidated in
[current stage handoff](2026-10-04-current-stage-handoff.md). Historical ordering
below does not authorize resuming tests from an automatic goal continuation.

**Latest human branch clarification (2026-10-04):** at a genuinely completed
major-version boundary, fork an actual new chat branch using the app's
`fork_thread`, then continue the next authorized major phase there. An in-place
summary is not a branch. Keep frequency low; no per-task forks or simultaneous
edits to this checkout. This supersedes the older blanket no-new-chat instruction
only at that boundary, not before current tests/acceptance/delivery finish.

**Latest human ordering override (2026-10-04):** complete the current major-version
implementation/build first, then run its unified tests. Candidate-source E root
switch and H documentation preparation may therefore precede G testing; their
release acceptance, batch commit/push and new-major chat remain after testing.
Earlier per-increment/native-first ordering below is historical where it conflicts
with this instruction. Preserve every DoD, parity, safety and release requirement;
do not promote the daily EXE or declare a release from an untested build.

## 1. Authority, baseline and ordering

- This is a continuation of `2026-09-20-langgraph-deepagents-mcp-concurrency.md` Tasks 28–29 and its Definition of Done, not a replacement architecture.
- Lv's 2026-10-03 instruction is now authoritative: **finish the project implementation first; real evaluation later**. Earlier checkpoints deferring native conversation/history/UI parity in favor of paid evaluation are historical, not the next execution order.
- Repository: `D:/Codex Program files/Agent/Doppel-Agent`; parent saved project: `D:/Codex Program files/Agent`.
- Starting HEAD: `952a03b7d9dda78dc0f2188e32a620279f1211c2`, clean main/origin/main before this planning-only handoff. App version remains **0.14.4**. The v0.15 candidate is unreleased.
- Prior frozen acceptance: 25/25 gates; Python 639 passed /2 skipped /1 warning; Vue 19; lifecycle 36×5=180; MCP 11×3=33; direct_factory 81 supported /99 excluded; run_service 126 supported /54 excluded, out of the original 180 paths. These are **prior-source results**, not acceptance for future product changes.
- Exact prior-source CI: `https://github.com/zureealLV/Doppel-Agent/actions/runs/37125791049`; the locally captured result/jobs records show success for HEAD952a03b (three jobs). The parent handoff's fresh API lookup hit a TLS handshake timeout, so this is captured prior-run evidence, not a new live CI refresh. Preserve the earlier failed CI and repair logs rather than overwriting history.
- Do not publish a mini-release per task. Write tests alongside implementation, consolidate acceptance/review, then one task-scoped implementation batch commit/push and exact-HEAD CI verification. Focused RED/GREEN diagnostics are allowed for an actual defect; do not weaken watchdog/assertion/skip gates to obtain green.

### Hard boundaries during implementation

1. **Zero external paid model calls**, including GUI connection probes and benchmark canaries. Use explicit Mock/scripted providers and local stdio/loopback MCP controls. Do not read/decrypt keys, query balances, change accounts or ask about provider-side caps during implementation.
2. Preserve the existing ¥10 shared-ledger guard and sticky-stop semantics. The guard is benchmark-runner scoped, not an account-wide/normal-chat cap. Do not reset ledgers or automatically update price dates. Resolve real-call prerequisites only in the later evaluation phase.
3. Do not replace the daily `.dist/DoppelAgent` EXE or touch actual conversation data during development. Build new isolated artifacts; seed disposable workspaces for migration/deletion/approval/command tests. No operating other apps or closing Lv's games.
4. Do not equate Mock 180 plumbing, supported-path success or UI visibility with real model quality, full runtime equivalence or full project release completion.
5. Local Mode implementation-complete, offline product acceptance, and model-quality/release claims are separate statuses. The final experimental/model-quality gate remains pending until its later authorized phase.

## 2. Next phase: native conversations and unified desktop workspace

### Task A — Freeze the product parity/gap contract

**Files:**
- Create: `docs/plans/2026-10-03-local-product-parity.md`
- Read: original Tasks 1–29 and DoD; `bench/cases/runtime/capabilities.json`
- Read: `src/doppel_agent/conversations.py`, `src/doppel_agent/runtime/service.py`, `src/doppel_agent/runtime/base.py`, `src/doppel_agent/api/schemas.py`, `src/doppel_agent/api/routes/runs.py`, `src/doppel_agent/persistence/migrations.py`
- Read: `frontend/src/App.vue`, `workspace.ts`, `workspaceApi.ts`, `workspaceTypes.ts`, `components/ConversationWorkspace.vue`, `components/ModelSettings.vue`, and old `web/app.js`

**Steps:**
1. Inventory every existing desktop feature, original Local Mode requirement, runtime mode and API boundary. Record implemented/tested/missing, exact code and existing test evidence.
2. Identify identity/state ownership: conversation, checkpoint thread, run, message, approval and child-agent IDs; define allowed multi-turn/mode/profile transitions before writing migration code.
3. Write concrete acceptance rows for native Graph/Deep creation, list/detail/search, drafts/groups/rename/archive, profile binding, multi-turn context, run association, restart and approval resume; also enumerate old UI parity and its compatibility route.
4. Separate required product gaps from intentional baseline/factory limitations. Do not force Legacy to gain native Graph checkpoint semantics or expose unsafe tools merely to inflate the 180 denominator.

**Pass gate:** every required feature has a named implementation step/test; explicit exclusions have a reason and remain visible. Scope is v1.0 Local Mode, not every imagined future feature.

### Task B — Add durable native conversation contracts and storage

**Files:**
- Modify: `src/doppel_agent/conversations.py` or create `src/doppel_agent/persistence/conversations.py` after the ownership audit
- Modify: `src/doppel_agent/persistence/migrations.py`, `runs.py`, `api/schemas.py`
- Create: `tests/test_native_conversations.py`

**Steps:**
1. Add tests for old-database migration, preservation of Legacy messages/settings/groups, native identity/mode/profile binding and deterministic ordering.
2. Add message/run association and projections with idempotent reconciliation. A duplicate create/completion/resume must not duplicate user/assistant messages or side effects.
3. Define active/interrupted conversation behavior: serialize turns or explicitly reject conflicts; reject unsafe mode/ID reuse, archive/delete during active work, and cross-conversation run/approval access.
4. Test title/body search including archives; reopen the store after process-equivalent reconstruction and prove data is intact. Test permanent deletion only on disposable seeded data and define checkpoint retention/cleanup explicitly.

**Pass gate:** existing data survives migration; required native history persists without replacing authoritative runtime events/checkpoints. No secret becomes message metadata or browser storage.

### Task C — Integrate RunService and the native conversation API

**Files:**
- Modify: `src/doppel_agent/runtime/service.py`, `api/app.py`, `api/schemas.py`, `api/routes/runs.py`
- Create: `src/doppel_agent/api/routes/conversations.py`
- Create: `tests/api/test_native_conversations_api.py`
- Extend: `tests/api/test_runs_api.py`, `test_sse_resume.py`, `tests/test_service_approval_concurrency.py`, `test_service_cancellation.py`

**Steps:**
1. Add versioned native conversation endpoints and validate identifiers, loopback/origin protections and run ownership. Preserve existing run API/idempotency semantics and Legacy routes.
2. Bind accepted runs to durable conversations; snapshot actual profile/model choices per run and keep per-conversation defaults separate. Preserve explicit permission grants.
3. Implement multi-turn context through each runtime's real contract. Graph/Deep checkpoint history must not be duplicated by an extra conversation replay; mode changes need an explicit tested policy. Test a second turn whose scripted answer depends on the first turn.
4. Persist/reconcile outcomes across success, rejection, edit approval, cancellation, timeout, queue rejection, provider error and shutdown/restart. A partial/failed turn must not become a fabricated assistant success.
5. Cover crash windows between run acceptance/event append/message projection with retry/reconciliation controls; use stable sanitized errors, not raw provider exceptions.
6. Verify SSE reconnect/Last-Event-ID, interrupted-run restart/resume, duplicate resume protection and concurrent-turn rejection/serialization on actual production service paths.

**Focused diagnostic commands, if needed (repo root):**
```powershell
$env:PYTHONUTF8='1'
& .venv/Scripts/python.exe -m pytest tests/test_native_conversations.py tests/api/test_native_conversations_api.py tests/api/test_runs_api.py tests/api/test_sse_resume.py -q
```

**Pass gate:** both Graph and Deep demonstrate native, durable multi-turn conversation behavior under scripted providers; Legacy remains identifiable and compatible. A supplied conversation_id alone is not proof of a complete conversation system.

### Task D — Integrate Vue conversation/runtime flows

**Files:**
- Modify: `frontend/src/api.ts`, `types.ts`, `workspaceApi.ts`, `workspaceTypes.ts`, `workspace.ts`, `runtime.ts`, `App.vue`, `styles.css`
- Modify: `frontend/src/components/ConversationWorkspace.vue`, `RunComposer.vue`, `ApprovalPanel.vue`, `ModelSettings.vue` and relevant event/subagent panels
- Extend: `frontend/src/workspace.test.ts`, `workspaceComponents.test.ts`, `runtime.test.ts`

**Steps:**
1. Display runtime identity honestly and route native conversation actions to the new API rather than the Legacy proxy. Retain old-history access without silent reinterpretation.
2. Wire history/search/groups/archive/profile selection and native send/resume/cancel into one navigable workspace. Refresh/reopen restores selected conversation and active/interrupted run, not duplicate draft/runs.
3. Preserve all observable Task 28 objects: Graph nodes, child agents, Skill hits, MCP origin, queue/run timing, approvals, unified diff and verification/test outcomes.
4. Add behavioral tests for loading/error/empty states, stale response races, duplicate submit/resume, archived result navigation and SSE recovery. Keep safe Markdown rendering and secret-free settings storage.
5. Fix cramped header/permission labels, form scrolling, minimum-width behavior, keyboard focus and accessible button states; do not claim UI coverage from SSR component tests alone.

**Frontend commands (frontend directory; existing configured Node/npm):**
```powershell
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
```

**Pass gate:** parity rows are backed by real browser interactions and API/store evidence. No implicit paid probe is triggered by settings viewing or default profile selection.

### Task E — Switch default UI only after parity, with compatibility

**Files:**
- Modify: `src/doppel_agent/web/server.py`, `api/app.py`, `desktop.py` as required by actual route ownership
- Retain: legacy `web/index.html`, `web/app.js`, `web/app.css` through a documented compatibility entry point
- Extend: `tests/test_web.py`, `tests/api/test_runs_api.py`; regenerate `web/frontend_dist/` from the accepted source

**Steps:**
1. Test direct desktop root, native API origin, Legacy compatibility route, deep-link reloads and static bundle paths; reject traversal/foreign Host/Origin regressions.
2. Expose the unified Vue workspace by default only when Task A parity rows pass. If a required row fails, keep the parallel arrangement and fix it; do not declare replacement complete.
3. Verify old data/profile visibility without auto-migrating/deleting user files. Document the route/data compatibility and rollback path.

## 3. Subsequent closure: entire Local Mode requirement audit

### Task F — Close the original DoD gaps, not just frontend gaps

**Files:** original authority plan; `bench/cases/runtime/capabilities.json`; existing runtime, MCP, persistence, scheduler and process-supervision modules/tests identified by Task A.

1. Audit all original Local Mode DoD rows: same API for three modes; restartable interrupt/checkpoint; exactly-once ledger; diff/conflict approvals; progressive Skill disclosure; stdio/Streamable HTTP MCP pagination/multimodal/structured content/is_error; queue/semaphores/cancel/deadline/workspace locks; durable SSE; Windows process-tree termination; reproducible fixed-task protocol; bounded pressure/fault tests; evidence-linked documentation.
2. Implement any required production gap before calling implementation complete, retaining existing safety contracts. For unsupported benchmark paths, record required parity work vs intentional baseline/boundary differences; update capability flags only after native controls actually prove the new capability.
3. Preserve original20×3×3 protocol denominators and full-mode guard until its stated prerequisites are genuinely satisfied. If an intentional runtime difference requires a revised experiment, document the decision and comparison design; do not silently count exclusions as successes.
4. Do not add v1.1 distributed infrastructure. Do not obtain real-model scores in this phase.

**Pass gate:** a checked Local Mode requirement table with code/tests/artifact evidence and explicit remaining evaluation/release gates. Known UI/native conversation gaps may not stay deferred while declaring implementation complete.

### Task G — Consolidated acceptance, isolated packaging and native visual QA

**Files:** existing25-gate recipe in `.bench-results/v015-ci-repair-final-20261003/`; repository CI; isolated `.artifacts/` and `.bench-results/` evidence; new dated `docs/releases/` report.

1. Freeze new source/fixtures/capability/protocol/oracle hashes before the batch, preserve all failed attempts and prove no source drift. Reuse the prior gate recipe/CLI help, not old success counts.
2. Run Python full suite/Ruff, frontend tests/typecheck/build, protocol safety/static gates, fixture/native supported matrices, repeated lifecycle/MCP, fault/load and source-stability checks. Recalculate test/support/exclusion denominators; paid calls remain zero.
3. Build wheel/sdist and a **new isolated EXE** from accepted source. The ordinary `scripts/build-desktop.ps1` writes `.dist`; use an isolated output override/direct equivalent, never overwrite the daily app. Verify actual package contents and noneditable installation.
4. Read the current computer-use skill before native QA. Test real packaged windows at standard/minimum dimensions and available DPI/scaling; record actual dimensions/scaling/screenshots, or mark unavailable configurations untested. Cover fresh/reopened native Graph and Deep conversations, history/search/archive/profile, approvals/diff/edit/reject/cancel, errors and keyboard navigation. Destructive cases use seeded disposable stores only.
5. Close normally; verify every owned child/server exits, bound listeners vanish and DB integrity/exclusive reopening/WAL/SHM state are sound. Do not reuse the prior EXE hash or its GUI acceptance for modified product code.
6. Repair observed defects, refreeze and rerun affected/whole gates as appropriate; never waive a failing parity/native lifecycle requirement.

### Task H — Evidence-bound documentation and one batch delivery

**Files:** `README.md` (English), `README_CN.md` (Chinese), `CHANGELOG.md`, `docs/TECHNICAL_REPORT_ZH.md`, testing/release reports and original plan checkpoints; verify any additional README filenames exist before editing.

1. Review task-scoped diff, secrets, generated assets and migration/ownership/cancellation risks; exclude private settings and ignored artifacts.
2. Document implemented and measured boundaries using fresh denominators/source hashes. List real evaluation and final release as pending separately; do not claim a model win, full180 support or final v1.0 release from offline acceptance.
3. Commit/push the consolidated implementation after acceptance. Verify remote HEAD and its exact CI, including platform-specific gates. Preserve original failure evidence and repair provenance.
4. Keep the current version/no tag/no daily EXE replacement until the remaining final release conditions are deliberately met. A clean accepted implementation candidate is a valid handoff to the later evaluation phase, not a fabricated release.

## 4. Deferred real-evaluation phase — not authorized to execute now

Only after implementation and offline/native product closure: reverify official model/pricing compatibility, provider-side budget isolation/cap, exact clean source and shared sticky ledger. Then follow the existing paid navigation → human review → blind-review protocol, with the **same total ¥10** accounting. Missing prerequisites stop paid execution, not the implementation tasks above. Any full180 expansion still requires its original capability/fixture/protocol gate and a feasible explicitly bounded budget.

## 5. Child-chat handoff

Start with Tasks A–C immediately, then continue D–H in this order; do not restart generic planning. The parent only adds this plan and small authority pointers, which should be preserved and included in the next implementation batch. All product work and subsequent validation belong to the child chat; the parent will not concurrently edit the shared checkout. Report actual progress, tests and remaining gates at each bounded stopping point. No extra chat/agent/worktree creation and no paid calls without another human request.

### 2026-10-03 A–C backend increment checkpoint

The parity contract and native store/API/RunService backbone are implemented in the
uncommitted whole-version batch. Fresh unchanged-source diagnostics: Python **670 passed,
2 skipped, 1 warning**, Ruff/diff-check pass. Graph/Deep durable two-turn context, native
approval reconstruction and cancellation/ownership/projection controls are tested; this
does not close UI, package, OS hard-kill, retention or startup-owner acceptance.
See [the evidence and remaining gates](../releases/2026-10-03-native-conversations-progress.md).
Next settled step is **Task D**, then E–H; do not redo A or run paid evaluation. Version
0.14.4/no tag/daily EXE and the original capability/protocol/budget guard are unchanged.

### 2026-10-03 D native Vue increment checkpoint

Task D's implementation is complete in the same uncommitted A–H batch: native
conversation API/history and honest immutable runtime identity, preserved Legacy
history, shared run/approval/diff/verification/typed-event/child inspector, durable
restore/idempotency and stale-response/SSE controls. Actual isolated browser actions
and API/store receipts are recorded in
[the D progress report](../releases/2026-10-03-native-vue-workspace-progress.md).
Final unchanged-source Python **671 passed, 2 skipped, 1 warning**; frontend **48
passed**, typecheck/build/Ruff/diff-check pass. Source inventory SHA-256
`338c5e6c95b63f21d0e51e18e8159b88b998e799acb6210fd80ff3c18eca6e1c`.
Browser group-prompt rename, visible-history deletion, actual Skill/MCP GUI flows
and native EXE/scaling acceptance are not claimed; named E/G gates remain.
Next settled step is **Task E's parity/default-route review**, then F/G/H; retain
Legacy root/daily EXE until real packaged parity permits a switch. No paid calls,
new release/tag, commit or push occurred. Do not redo A–D or treat D as whole-v1 closure.

### 2026-10-04 E compatibility preparation checkpoint

E route/desktop preparation is implemented: explicit `/legacy/` compatibility,
three Vue page hash links with reload identity, host-only window controls and restore
state repair, native group rename dialog, static null-byte path repair. Fresh frozen
suite: **676 passed, 2 skipped, 1 warning**; frontend **56 passed**, typecheck/build/
Ruff/diff-check pass. Source inventory
`f728c93a82c36cb4af4f99fca89fb61eca5d47b9960a647e619fdd3b8f5bed6b`.
See [E evidence and route/data rollback](../releases/2026-10-04-default-ui-compatibility-progress.md).
**E's actual default switch is still pending:** real packaged desktop parity is not
passed, so root/desktop remain Legacy. This is a carried gate, not a waived failure
or completed replacement. Next bounded step is **F**, then G including the deferred
E switch only after parity, then H batch delivery. No paid calls, daily EXE replacement,
version/tag, commit/push, extra chat/agent/worktree or real-profile/key access.

### 2026-10-04 F original Local Mode DoD audit checkpoint

F's twelve-row source/contract audit and named backend gaps are closed in the same
uncommitted batch: exclusive OS workspace ownership with durable startup/close,
bounded root/child checkpoint retention, original Skills/MCP management APIs with
bounded discovery, total segment deadlines, atomic child follow-up/admission and
terminal IO, sync tool/provider worker drain with actual-effect receipts, stable
failure messages, cross-turn/fallback retry budgets and half-open probe cleanup.
Native terminal status is not a drained lease; deterministic API controls retain
the lease until owned cleanup finishes. No early-release workaround was used.

Fresh frozen F acceptance: **735 passed,2 skipped,1 warning**; frontend **56 passed
in7 files**; all **12 source/static gates** passed, including expected full-mode
refusal exit2. Source inventory
`5cc16169a24553b41a1b59c257169aa1f8e870afe1beee78d0c44792d0129a68`,
before/after unchanged. See [F audit and retained failed attempts](../releases/2026-10-04-local-mode-dod-audit.md).
Two symlink skips/one dependency warning remain explicit; offline controls are not
model-quality evidence, G25-gate/native/package acceptance, final v1 or H delivery.

Next settled step is **G**: expanded/recounted lifecycle/MCP/source/support gates,
fresh pressure/fault/matrices, isolated wheel/sdist/new EXE, actual native parity
and exit/DB closure. E's default switch remains conditional on that packaged parity;
if it changes source, refreeze/revalidate the final source and package before H.
Root/daily app stay Legacy; version0.14.4/no tag/daily EXE replacement/paid calls
are unchanged. H's one batch review/commit+push/exact CI is still pending. Do not
restart A–F, add distributed infrastructure or run paid evaluation.

### 2026-10-04 G task organization (planning only)

The next-stage tasks are decomposed in the
[G consolidated acceptance checklist](2026-10-04-g-consolidated-acceptance.md):
baseline/isolation → expanded and recounted25-gate batch → wheel/sdist/noneditable
install → new isolated EXE → real native parity → normal exit/DB closure →
conditional E switch with final-source reacceptance → H handoff. This does not
start or accept G, authorize paid evaluation, or change the one-batch H delivery.

### 2026-10-04 persistent-goal continuation through final Local Mode

Lv's persistent objective is staged updates/tests through the final version, with
a fresh chat at each completed major-version boundary. Keep that whole objective
active; finishing G or H alone does not prove it complete. Reuse the existing
plans and original DoD instead of inventing a competing roadmap.

| Remaining stage | Completion evidence / next transition |
| --- | --- |
| G + carried E | Expanded/recounted25 source gates, stable inputs, independent install, new isolated EXE, required actual native parity, normal exit/DB closure; conditional default switch requires final-source reacceptance |
| H implementation candidate | Reviewed scoped batch, evidence-bound bilingual docs, one commit/push, remote exact HEAD and exact CI; no final-release/model-quality claim |
| Existing real-evaluation phase | Separately authorized paid navigation/human review/blind review under the original total10 CNY sticky ledger, official compatibility/current pricing and provider-side cap; missing prerequisites stop paid work, not earlier implementation |
| Final v1.0 Local Mode / Task29 | Requirement-by-requirement audit of original DoD, final evaluation/release boundaries resolved honestly, deliberate version/tag/artifact decision, final-source/package provenance and exact release/CI verification; never count exclusions or Mock as real-model wins |
| Next major-version chat | Only after the current major version is actually accepted: create a new project chat with exact commit, reports, unresolved limitations and the next-version plan; transfer follow-on work there to bound context |

The original v1.1 Server Mode remains **conditional on measured single-machine
queue bottlenecks**. A new chat may start that evidence/scope review after the
major-version boundary, but this goal does not justify adding PostgreSQL/Redis/
distributed infrastructure to unfinished v1.0 or creating an endless series of
invented versions. New-chat creation at that actual boundary is authorized by
the persistent human objective; no extra chat is created during this G batch.


### 2026-10-04 G source/package checkpoint — native parity partial

Fresh G source acceptance is **25/25**:735 passed/2 skipped/1 warning,
106×5 lifecycle,23×3 MCP, frontend56/7 files; production factory81/81 supported
with99 exclusions and service126/126 with54 exclusions. Inputs/source/daily app
remain unchanged at the F inventory
`5cc16169a24553b41a1b59c257169aa1f8e870afe1beee78d0c44792d0129a68`.
Wheel/sdist reconstruction and locked noneditable outside-checkout smoke passed;
new isolated EXE/source/assets linked, actual native Graph/Deep fresh read passed.
Idle normal native close exited EXE/owned WebView2 processes and both listeners;
six native fixture stores integrity/exclusive-reopened, owner lock reacquired.
See [G evidence, failure provenance and next execution order](../releases/2026-10-04-g-consolidated-acceptance.md).

**G is not complete:** required native dependent conversation/restart,
approval/effects/Skill/MCP, busy shutdown/second-owner/handoff and host sizing/
compatibility flows remain. Next bounded step is G5 loopback scripted fixtures
and native dependent conversation/restart proof. Do not redo accepted G1–G4
unless production source changes. E default stays Legacy; G7 final switch cycle
and H review/batch commit+push/exact CI remain pending. No paid calls, version/tag,
daily replacement, extra chat/agent/worktree or real settings/key access.

### 2026-10-04 G5 packaged context continuation — partial

Actual sealed EXE production API passed request-derived Graph/Deep two-turn
context with a loopback-only scripted endpoint; browser against that packaged
server passed search/rename/group/archive/unarchive/reload controls. An installed
separate-process second owner was rejected while the EXE remained healthy.
Native activation failed after refreshed exact-window recovery; inputs stopped,
desktop-state clarification requested, and native composer/process-restart cases
remain pending. See the G report continuation; source/inputs/daily app unchanged,
no paid calls or source/default/release changes. Recover native interaction and
complete those cases before claiming G or carried E closure; other G rows remain
available work. The active goal is not completed, paused or globally blocked.

### 2026-10-04 G5 native context/restart and bounded management checkpoint

Native control recovered. The same sealed EXE now passes Graph/Deep dependent
remember/recall plus normal host close/true process restart/native reopen:
each six exact messages/three completed runs, stable thread/mode/profile,
actual runtime identity and no fallback. GET-only verification does not submit
these turns. Two normal closes verify owned process/listener release, six-store
integrity/exclusive reopening and owner-lock reacquisition/release.

Native group create/assign/filter/rename, conversation rename, archive/read-only/
unarchive, Ctrl K archived message-body search/Enter open, page reload and second
process-restart persistence also pass. Complete pre/post management details are
equal. Earlier API/browser and activation-failure evidence retains its original
provenance. A PID-only shutdown false positive was a different new process using
a recycled PID; the original and corrected identity-aware receipts remain, with
no forced termination or product/oracle change.

See [the G native continuation report](../releases/2026-10-04-g-consolidated-acceptance.md)
and `native-context01/native-stage-audit01.json`: ten loopback requests (four prior
API, six native),12 completed fixture runs, unchanged source/inputs/daily/EXE,
zero paid calls; synthetic usage is not model-quality evidence. G1–G4 need no
rerun unless production source changes. The prior organization-only goal turn
was not acceptance progress; this turn contributes measured native proof.

**Still open:** remaining Legacy/profile/title/keyboard/deletion boundaries,
approvals/effects/verification/children, Skills/MCP, busy lifecycle/second native
EXE/handoff/host/scaling/compatibility; then conditional E/G7 final-source cycle
and H one-batch delivery. Native deletion requires action-time confirmation.
G and final v1.0 are not complete, default remains Legacy/version0.14.4; no tag,
daily replacement, commit/push, new chat/agent/worktree or real-key access.

### G5b native-discovered repair checkpoint

Actual EXE title search/Down/Up/Enter and Legacy Shift Enter evidence was captured,
but empty-page first Legacy submission reset selected Mock to global loopback
profile. Failed run `cf4e134b73634226a887b8f5650ac9b7` and the eleventh local
request (HTTP400) are retained, not accepted. Three tests went RED before the
minimal Legacy draft profile-binding repair; frontend59/7 and types/build pass.
Old EXE28832 normally exited, both ports closed and all7 actual SQLite stores
integrity/exclusive reopen/owner release pass. Legacy added `tasks.sqlite3`;
the earlier six-count auditor failure remains documented.

Continue fresh `g-local-20261004-02` source/25-gate/package/noneditable/new-EXE
cycle and exact native profile regression **before** further G5b–G6 acceptance.
Pre-fix receipts remain historical; no default switch/H/final-release waiver.
Version0.14.4, batch-uncommitted status, zero paid calls and real-data isolation
are unchanged. Full original objective stays active.

### Repaired-package task organization — superseding handoff

Attempt `g-local-20261004-02` has completed25/25 source gates, Python735 passed/
2 skipped/1 warning, frontend59/7, lifecycle106×5/MCP23×3 and11 package steps.
Source SHA `c7d93e62c5275ef158195d3350e6ace146c7a9ff1eb2a0f21690082578291c46`;
new EXE SHA `b913676c9af34949377f23cc9dff1d29241fd3197c03f6e040125ee991c37bcb`.
Native repaired Legacy first-submit/agent-draft/review-draft selected profiles
are evidenced; page reload/history reopening are not true process restart.
Earlier attempt02-pending instructions above are now historical, not next actions.

The [G plan's repaired-package queue](2026-10-04-g-consolidated-acceptance.md)
extends the existing stages: N0 seal profile/run/event receipts plus normal close/
true restart → N1 current-package Graph/Deep and conversation/profile parity →
N2 approvals/effects → N3 Skills/local MCP → N4 busy lifecycle/second native
owner/host/fullP01–P15 → conditional N5 default switch and final-source full
acceptance → N6 H consolidated review/docs/one commit/push/exact CI.

This turn organizes tasks and rereads previous evidence, without rerunning gates
or accepting another native case. No required G5/G6 row is waived; effort/review
mode restoration is not accepted by the profile repair alone. Original final
evaluation/Task29/major-version boundary gates remain. Version0.14.4/root Legacy,
dirty batch/no paid work/no new chat remain unchanged. **Next execution: N0**,
not another unchanged source/package rebuild.

### N0 executed — repaired native profile plus true restart

`g-local-20261004-02/native-profile01/n0-final01.json` accepts N0, not whole G.
Three repaired Legacy profile paths reconcile native UI with durable data;
one actual Mock file-read run/eight ordered events and two empty drafts survive
normal host close and genuine process restart exactly. All4 actual SQLite paths
integrity/exclusive reopen/closed-connection/WAL-SHM/owner release pass; original
EXE plus6 owned WebView2 identities/listeners exit without force-kill. First
shutdown snapshot saw the root still draining; later sample proved exit before
relaunch. Raw51-byte CRLF fixture versus50-byte returned LF text auditor mismatch
is retained and corrected to the actual read/output contract, not a product fix.

Source/input/daily/EXE unchanged; no paid/provider requests added, migration,
default/version/release change or commit/push. Repaired EXE113260 is observed
healthy on1088/1087; revalidate identity rather than reuse stored handles.
Next is **N1** current-package Graph/Deep and conversation/profile parity.
Effort/review restoration/full G5–G6/E/H/evaluation/Task29 remain unaccepted;
the full objective stays active and no major-version chat is started yet.

### N1 context subset executed — repaired native Graph/Deep restart continuity

`g-local-20261004-02/native-context02/n1-context-final01.json` seals six native
remember/recall/reopen runs across Graph and Deep, three turns/six messages each.
The third turn follows normal host close and genuine same-EXE/workspace restart;
prior messages, run records and events compare exactly. Mode/thread/profile
identity remains stable, runtime terminal events reconcile, no fallback/lease
remains. Strict request-derived loopback journal17 = historical11 plus six new200
requests; synthetic usage is not real LLM/cost evidence and paid calls remain0.
N0 Legacy histories remain exactly unchanged. Native idle close independently
releases seven actual SQLite stores/owner/process identities/listeners, no kill.

Retain wrong terminal-event verifier assertion and unsuccessful geometry/close
attempt with ineligible first process sample; accepted second close observation
and subsequent identity-drain sample prove exit. No production/fixture/oracle
change. Final source/guarded inputs/daily/EXE hashes match attempt02.

Next: remaining **N1** conversation/profile/keyboard/restoration parity, then
N2–N6. Context subset is not complete N1/G/default-switch/H/v1.0 acceptance.
Version0.14.4/root Legacy/dirty batch remain; no paid work/commit/push/new chat.
Full objective remains incomplete; latest goal-tool scheduler status is
usageLimited, not complete, and is not changed by this human-requested turn.

### N1 management/profile/keyboard/restart subset executed; deletion pending

Current-package native management/search/group/archive/read-only/unarchive,
review quick/template/no-grants, missing-profile no-fallback, immutable existing
mode versus next draft, controlled sanitized error and busy-control recovery now
have paired native/durable evidence. `native-management02/n1-management-final01.json`
and `n1-management-followup01.json` retain actual Enter/Shift Enter/Ctrl K/arrows
and native search Escape semantics (first clears query, second closes modal).
Five native conversations/eight runs/15 messages survive normal idle close and
genuine restart exactly; prior Graph/Deep/three Legacy histories remain unchanged.
All seven actual DBs/owner/listeners/process identities release without force-kill.
Current same EXE PID48564/window55250290/UI2952/API2951; reverify before reuse.
Context requests17 unchanged; separate controlled local fixture4, paid calls0.

Full N1 awaits the existing action-time human confirmation to delete only empty
native management draft `8b98ff340f4242728f676b2e39eaccca`, then final row and
adjacent audit checks. No reply/delete/API substitute/waiver. N2 safe preparation
only creates14 independent patch fixtures and42 positive/42 negative request-
derived oracle controls with no provider request/run. It is not N2 acceptance.
Detailed current handoff is appended to the G plan; do not rerun unchanged source
gates or historical denominator helpers. Source/input/daily/EXE remain unchanged.
Then sequential N2–N4 → conditional N5 final-source package cycle → N6 H scoped
review/docs/one commit/push/exact CI. N6/default/full v1.0 remain incomplete;
version0.14.4/root Legacy/dirty batch/scheduler usageLimited unchanged.

### Current-session recovery, N1 focus/scroll and N2 verification controls

Authoritative process/listener inspection confirms previous48564/94436/114032
handles absent. Their exit reason is unproven, not native-close acceptance.
New `native-recovery03/` audits all seven actual SQLite paths/installed owner
release and restarts the identical EXE/existing fixture without reset. Current
117092/window921516/UI2346/API2345; fresh independent fixture processes restore
unchanged loopback oracles on7762/11351 with separate journals. Exact five native
conversations/eight runs/15 messages/events, Legacy histories/group/settings
remain unchanged; historical journals17/4 untouched, fresh journals0/0.

`n1-recovery-final01.json` records native composer/search focus return and visual
scroll-anchor preservation, not DPI/pixel/restart-scroll proof. Non-target initial
capture and user-input-interrupted scroll-to-top are ineligible; fresh state is
retained, no further native input or inferred deletion confirmation. Full N1's
existing empty-draft delete question is still unanswered, not waived.

N2 `verification-preflight01.json`: eight actual independent supervised oracle
children, two positives/six negatives, fixed config templates not installed in
live fixture,14 inputs/ledger unchanged, requests0. Not native approval/verification/
child acceptance. Distinguish Graph ledger from Deep direct-patch/checkpoint/base
guards and prove actual effect/recovery identity later; no capability/source change
based only on inspection. Full N1→N2–N4→conditional N5→N6 and original final-v1.0
boundaries remain open. Guarded source/daily/EXE unchanged, paid0/no release/push.
Current goal-tool status is **active**, superseding older usageLimited snapshots;
the full objective is preserved and incomplete.
