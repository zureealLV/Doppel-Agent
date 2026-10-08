# G Consolidated Acceptance Implementation Plan

> **Current human instruction (2026-10-04): verification resumed.** Lv said
> “如果受限了 就验证吧”. A fresh immutable attempt04 now covers the new source;
> previous attempt03 receipts are preserved, not reused as current acceptance.
> Use [current stage handoff](2026-10-04-current-stage-handoff.md) for the settled
> construction state and preserved full queue. Earlier “next native task” paragraphs
> and stale missing-confirmation blocker audits are not current execution authority.

> New source-only selection increment:
> [construction report](../releases/2026-10-04-workspace-selection-construction.md).
> It is not in attempt03's frozen source/bundle/packages. Attempt04 uses a new
> immutable source/package cycle and adds
> `tests/test_workspace_selection_lifecycle.py` to repeated lifecycle coverage.

> **For Hermes:** Execute sequentially in this checkout under the existing A–H
> authority. No subagents/new chats/worktrees; no per-task commits. Task H retains
> the one consolidated review/commit/push. Execution checkpoints are recorded below;
> unchecked items are not fully accepted.

**Goal:** Produce an evidence-linked, offline-accepted Local Mode implementation
candidate with a fresh isolated package and real native desktop/lifecycle proof.

**Architecture:** Extend the existing 25-gate recipe with F's regression seams,
freeze source and inputs, then package exactly that accepted source. Required
packaged parity gates release acceptance of E's default-route switch; a switch creates a new final
source/package acceptance cycle, not an exemption from testing.

**Tech Stack:** Python 3.11, pytest/Ruff/uv, Vue/TypeScript/npm, LangGraph,
DeepAgents, MCP, SQLite, PyWebView and PyInstaller on Windows 10.

**Current execution override (Lv, 2026-10-04): finish the major-version
implementation first, then test the complete major version.** Candidate-source
default-route implementation and documentation now precede unified acceptance.
Parity still gates release/promotion, not edits to isolated unaccepted candidate
sources. No daily replacement or release follows from a build-only result.

**Acceptance handoff:** the final section, “Repaired-package next-stage task queue”,
supersedes historical pending/refreeze instructions. Attempt02 source/package
gates, N0 and the appended N1 context/management/profile/keyboard/restart subsets
passed, including the later external-session recovery/focus/scroll subset. Full
N1 still awaits native deletion and post-delete adjacent-history reconciliation.
Human confirmation was received; physical Escape stopped that UI turn before
deletion. Do not reuse old process/window IDs or ask the answered question again.
N2 fixture/oracle preparation is not N2 acceptance. After construction closes,
perform the deferred unified N1–N6 acceptance/delivery sequence without inventing
passes. The build-first and construction-closure checkpoints supersede earlier
missing-confirmation blocker audits as the current next activity.

## Authority and entry state

- Original sequence: [A–H plan](2026-10-03-v1-local-completion-before-evaluation.md).
- Required product rows: [P01–P15 parity contract](2026-10-03-local-product-parity.md).
- Original DoD: [runtime plan](2026-09-20-langgraph-deepagents-mcp-concurrency.md).
- Latest evidence: [F audit and failed attempts](../releases/2026-10-04-local-mode-dod-audit.md).
- F receipt: `.bench-results/local-native-20261003/f-final-verification04.json`:
  12 source/static gates, zero paid calls, source unchanged, inventory SHA-256
  `5cc16169a24553b41a1b59c257169aa1f8e870afe1beee78d0c44792d0129a68`.
  Recorded F suite: 735 passed / 2 skipped / 1 warning; frontend 56 passed / 7 files.
  These are historical F results, not new G results.
- HEAD checked while organizing: `952a03b7d9dda78dc0f2188e32a620279f1211c2`.
  A–H changes remain uncommitted; version remains 0.14.4.
- Root/default desktop stays Legacy; `/legacy/` compatibility and `/runtime/`
  Vue are retained. The real default switch is pending, not already complete.

All paths below are relative to `D:/Codex Program files/Agent/Doppel-Agent`.
Use new attempt directories, such as `.bench-results/g-local-20261004-01/` and
`.artifacts/g-local-20261004-01/`. If executing later, use the actual date and an
unused attempt number. Never overwrite F01–F04 or the previous 25-gate evidence.

## G0 — Baseline, isolation and evidence preflight

**Files:** original plans/F report; `bench/runtime_audit_inputs.py`;
`scripts/desktop_entry.py`; `src/doppel_agent/desktop.py`; new attempt directories.

- [x] Read repo instructions and existing checkpoints; inventory dirty/untracked
  files without reset/clean/stash or broad staging. Preserve all A–F work.
- [x] Record HEAD, source/input hashes, dependency versions, runtime tooling,
  frontend bundle references, and daily `.dist/DoppelAgent` hashes/timestamps.
  A dirty inventory hash is distinct from Git HEAD.
- [ ] Create disposable fixture workspace/data and scripted provider/Skill/local
  MCP fixtures; seed permissions/profile/conversation data with no secrets.
  Verify actual settings/data resolution before launch. Never point QA at the
  user's real settings, workspace, accounts or databases.
- [x] Isolate provider environment and disable paid access; merely removing
  `DOPPEL_AGENT_API_KEY` is not sufficient if another credential/config path exists.
  Use fixture-owned config only, without reading/decrypting real credentials.
- [x] Read the current computer-use skill and check available native-control
  capabilities. If only browser control is available, record native QA as pending;
  browser screenshots cannot substitute for the packaged desktop.

**Pass:** reproducible inputs, isolated fixture paths and an auditable receipt;
no paid calls, no daily-app change. No cleanup of user data.

## G1 — Expand and recount the existing 25-gate runner

**Reference (read-only):**
`.bench-results/v015-ci-repair-final-20261003/run-validation.py`.
**Create:** `.bench-results/g-local-20261004-01/run-validation.py`, collection
receipts, gate/dependency/source manifests. Update attempt paths on later runs.

- [x] Copy the recipe into the new attempt, preserving commands, logs, expected
  exits and before/after `source_context` comparison. Add explicit input hashes,
  test collection counts and paid-call audit. Do not copy success results.
- [x] Expand each of the five lifecycle repeats beyond the old selectors with:
  `tests/test_service_ownership.py`, `tests/test_checkpoint_retention.py`,
  `tests/test_service_deadlines.py`, `tests/test_subagent_ownership.py`,
  `tests/test_tool_ownership.py`, `tests/test_provider_run_budget.py`,
  `tests/test_provider_probe_cleanup.py`,
  `tests/test_native_conversation_lifecycle.py`,
  `tests/api/test_native_settlement.py`. Retain the existing cancellation,
  checkpoint, approval and API terminal-event selectors.
- [x] Expand each of the three MCP repeats with catalog/executor/API discovery
  cases from `tests/mcp/test_catalog.py`, `tests/mcp/test_executor.py`,
  `tests/api/test_catalog_api.py`, alongside existing client-manager/runtime
  capability process controls. Do not accidentally filter new files out with
  the old `-k` expression; use explicit selectors or separated commands.
- [x] Run `--collect-only -q` for exactly the repeat selectors and save node IDs,
  counts and exclusions. Record observed counts per repeat; old 36×5 / 11×3
  are invalid after expansion.
- [x] Check current CLI help for benchmark commands before executing them;
  keep the original 20×3×3 protocol and full-mode refusal intact.

**Pass:** 25 named source gates retained with expanded repeat coverage, no empty
selection, counts derived from actual collection, and source/fixtures/capabilities/
protocol/oracles attributable to the new attempt. Packaging/native gates remain
separate; 25/25 alone is not G completion.

## G2 — Freeze and run unified offline acceptance

**Files:** new runner/receipts; `bench/cases/runtime/capabilities.json`; benchmark
fixtures/oracles; `frontend/`; `src/doppel_agent/web/frontend_dist/`.

- [x] Ensure generated frontend assets match source before the freeze. Capture
  source and all guarded inputs; no concurrent product edits during the batch.
- [x] Execute the expanded runner from repo root and retain every log:
  `.venv/Scripts/python.exe .bench-results/g-local-20261004-01/run-validation.py .bench-results/g-local-20261004-01`.
- [x] Review all gates, not just runner exit:

| Gates | Required observation |
| --- | --- |
| Python/Ruff/compile/lock | Fresh full-suite denominator; explicit skips/warnings; all checks pass |
| Lifecycle ×5 / MCP ×3 | Fresh counts; no leaked workers, duplicate effects or undrained owners |
| Safety and task-fixture audits | Exact supported inputs; no relaxed oracle or paid guard |
| Factory and service supported matrices | Fresh success/support/exclusion counts out of each 180-path universe |
| Fault matrix / 100-job load | Expected faults, bounded pressure and clean owned cleanup |
| Offline Mock / preflight | Fixture controls only, not model-quality scores |
| Full-mode guard | Expected refusal exit **2**, not an authorized live/full run |
| Frontend tests/types/build / diff | Fresh counts and bundle correctness; no source drift |

- [x] Compare final source/inputs with pre-run inventory. Record two known F
  symlink skips and Starlette/AnyIO warning only if still observed; changes in
  skip/warning behavior require explanation, not copied text.
- [x] Recount capability boundaries. Previous planning baselines were factory
  81 supported / 99 excluded and service 126 / 54; they are not accepted G counts.
- [x] On failure: preserve the attempt; write a deterministic failing test,
  verify RED, fix minimally, verify focused GREEN. Refreeze into a new attempt
  and rerun the consolidated batch after production changes. Never revise an
  oracle merely to hide a product failure.

**Pass:** all 25 gates satisfy expected exits, source/input stability proven,
paid calls zero, fresh denominators recorded, and failure provenance retained.

## G3 — Isolated wheel/sdist and noneditable installation

**Files:** `pyproject.toml`; isolated `packages/`, installation environment and
package-content receipts under the attempt's `.artifacts/` directory.

- [x] Build both artifacts without writing the daily `.dist`:
  `uv build --out-dir .artifacts/g-local-20261004-01/packages`.
- [x] Hash/list wheel and sdist contents; check runtime modules, Legacy web
  assets, Vue index and every referenced bundle. Record build dependencies and
  actual version 0.14.4; don't rename this a v1.0 release.
- [x] Install the exact wheel **noneditable** in a fresh isolated environment
  with matching locked runtime dependencies; record resolved dependencies.
  Do not silently upgrade the accepted working environment.
- [x] From outside the checkout with repo `PYTHONPATH` removed, assert import
  provenance comes from that installation. Verify entry points, static routes,
  fixture-owned store/API startup and clean shutdown. Also verify sdist can
  reconstruct a wheel containing the required assets/modules.

**Pass:** usable independent installed artifact, content/hash/provenance receipts;
no fallback to editable source or private settings.

## G4 — New isolated desktop EXE

**Files:** `scripts/build-desktop.ps1` (reference only),
`scripts/desktop_entry.py`, `assets/doppel-agent.ico`; isolated EXE/spec/work paths.

- [x] Verify installed PyInstaller/toolchain and accepted dependency receipt.
  The ordinary build script installs editable dependencies and writes `.dist`;
  **do not run it unchanged** for G.
- [x] Use the equivalent onedir build with isolated destinations (repo root):

```powershell
$icon = (Resolve-Path assets/doppel-agent.ico).Path
$entry = (Resolve-Path scripts/desktop_entry.py).Path
& .venv/Scripts/python.exe -m PyInstaller --noconfirm --clean --windowed --onedir `
  --name DoppelAgent --icon $icon `
  --add-data "$icon;assets" --collect-data doppel_agent.web `
  --distpath .artifacts/g-local-20261004-01/desktop `
  --workpath .artifacts/g-local-20261004-01/pyinstaller-work `
  --specpath .artifacts/g-local-20261004-01/spec $entry
```

PyInstaller resolves relative data/icon paths against the generated spec directory.
The first G packaging attempt exposed this; use absolute icon/entry paths and
retain failed specs/logs rather than overwriting them on a repair attempt.

- [x] Record build command/log, EXE and payload hashes, asset inventory and
  source-to-package linkage. Compare source hashes and daily-app baseline again.
- [x] Launch the new EXE with `--workspace` pointing only to the prepared
  disposable fixture directory. Background helpers use hidden windows; the
  packaged app itself must be visible for actual native QA.

**Pass:** new isolated EXE starts from accepted source and contains correct
assets; daily `.dist/DoppelAgent` untouched. Prior EXE hashes/QA do not apply.

## G5 — Required real packaged desktop parity

**Files:** P01–P15 contract, new native screenshots/action receipts and fixture
DB/API receipts; new `docs/releases/2026-10-04-g-consolidated-acceptance.md` report
(use actual execution date if later).

- [ ] Execute every required parity row; record case ID, EXE/source hash,
  fixture, action, expected/actual outcome, screenshot and durable receipt.
- [ ] Cover the following minimum matrix using scripted/offline providers:

| Area | Required cases |
| --- | --- |
| Conversation identity | Legacy preserved; Graph/Deep fresh and multi-turn; mode immutable; profile snapshot honest |
| Restart/history | Close/reopen; durable context and interrupted approval; search, groups, archive/unarchive; seeded visible deletion |
| Approval/effects | Diff/conflict; approve/edit/reject/cancel; verification/child inspector; one actual-effect receipt; no fabricated success |
| Cancellation/ownership | Queue/running/setup/write-lock/child cases; terminal visibility versus lease drain; second-owner rejection; healthy current owner and later handoff |
| Skill/MCP | Progressive disclosure; cached discovery/reload; local stdio and Streamable HTTP; explicit probe, pagination, structured/multimodal/is_error and sanitized errors |
| UI/host | Empty/loading/error; keyboard/focus; restore/maximize/minimize/close; standard and minimum dimensions; available DPI/scaling |
| Compatibility | Root, `/legacy/`, `/runtime/`, Vue hash-link reload, assets and old data; no implicit migration or provider probe |

- [ ] Record actual window/client dimensions, display resolution/scaling and
  screenshots at each available configuration. Unsupported/unavailable DPI is
  explicitly untested, never counted as a pass. Do not change global display
  settings without need or pretend a CSS viewport proves native DPI behavior.
- [ ] Any required parity defect goes through RED → minimal fix → GREEN → new
  source freeze/acceptance/package. Recheck the affected native flow on the new
  EXE; source/component tests alone do not close it.

**Pass:** required product rows have real packaged/native proof plus durable
semantics. Native-control unavailable or required failures leave G pending.

## G6 — Normal exit, ownership handoff and database closure

**Files:** fixture stores/process/listener inventory and shutdown receipt;
`tests/test_service_ownership.py` as source-level control, not GUI substitution.

- [ ] Record EXE/owned child/server PIDs and bound listeners before close.
  Close through the app normally; wait for owned work to drain.
- [ ] Verify owned processes exited, recorded listeners vanished and no
  background MCP/provider/helper survives. Don't kill unrelated user processes.
- [ ] Check integrity/exclusive reopening of every fixture SQLite store;
  inspect actual WAL/SHM state, noting legitimate SQLite behavior rather than
  assuming file absence is sufficient proof. Verify no outstanding lease/owner
  prevents reopening; restart/reopen retained conversation and approvals.
- [ ] Check second-owner refusal while current owner remains intact, then clean
  handoff after the first closes. Verify daily app hashes remain unchanged.

**Pass:** visible normal close and durable owned cleanup with recorded evidence;
force-kill cleanup is a failure diagnosis, not a normal-exit pass.

## G7 — Conditional E switch and final-source cycle

**Files if parity passed:** `src/doppel_agent/web/server.py`,
`src/doppel_agent/api/app.py`, `src/doppel_agent/desktop.py` as needed;
`tests/test_web.py`, `tests/test_desktop_controls.py`, relevant API/frontend tests.

- [ ] Switch root/default to Vue **only after required packaged parity passes**;
  retain `/legacy/` and rollback/data boundaries. If parity failed, keep Legacy,
  repair the failure and leave E/G pending; do not waive the required gate.
- [ ] Add/verify failing route/default/host/compatibility tests first, then
  implement the minimum switch and verify focused tests.
- [ ] Freeze the final switched source in a new attempt; rerun consolidated
  source gates, build new wheel/sdist/EXE and verify installation/package linkage.
- [ ] Revalidate final default/compatibility/routes/native parity and exit/DB
  closure against that final EXE. Pre-switch screenshots cannot prove final
  default behavior.

**Pass:** final accepted source equals the final package source; default parity
and Legacy compatibility are measured, not inferred.

## G8 — Report and hand off to H (not a release)

- [ ] Report fresh 25-gate results separately from package/native/exit gates;
  exact support/exclusion denominators, skipped/untested cases, source/input/
  EXE hashes, dependencies, zero paid calls and retained failure attempts.
- [ ] Update original A–H checkpoints with actual E/G state. Mark accepted only
  when required gates really pass; otherwise list exact remaining blockers.
- [ ] H then performs scoped security/code/assets/migration/ownership review,
  English `README.md` / Chinese `README_CN.md`, `CHANGELOG.md`,
  `docs/TECHNICAL_REPORT_ZH.md` and evidence documentation; one batch commit/push,
  remote HEAD and exact CI verification, including platform-specific results.

**Excluded now:** no paid/model-quality evaluation, no expanded full180 run, no
distributed infrastructure, no version bump/tag, no daily EXE replacement.
Later paid evaluation remains separately gated under the original total ¥10
policy; G/H offline acceptance is not final v1.0 or a model-quality claim.

## 2026-10-04 execution checkpoint

G1–G4 accepted against source inventory
`5cc16169a24553b41a1b59c257169aa1f8e870afe1beee78d0c44792d0129a68`:
25/25 source gates;735 passed/2 skipped/1 warning;106×5 lifecycle and23×3 MCP;
factory81/81 with99 exclusions and service126/126 with54 exclusions; frontend56.
Wheel/sdist content/reconstruction, locked noneditable installation and an
outside-checkout smoke passed. New isolated `desktop02` EXE/source/assets match;
actual native Graph/Deep fresh read and idle normal close/store reopening passed.
Daily app/source/guarded inputs unchanged; zero paid calls.

G0's complete Skill/local MCP/scripted model fixture seed remains unchecked:
only disposable Mock/basic read fixture was needed so far. G5 and G6 remain
partial; all required native parity, dependent multi-turn/reopen, busy close,
second-owner/handoff, available native sizing and explicit Skill/MCP/effects
flows are not accepted by these two reads. G7/E switch and G8 full handoff/H are
pending. The partial evidence report is
[here](../releases/2026-10-04-g-consolidated-acceptance.md); it is not G completion.

**Next executable increment:** G5 local loopback scripted provider fixtures and
native dependent conversation/restart proof, then approvals/effects and Skill/MCP,
then remaining lifecycle/host parity. Do not repeat accepted G1–G4 unless product
source changes; do not restart A–F or switch the default prematurely.

### G5 context fixture checkpoint — native input/restart still pending

The same accepted `desktop02` EXE now passed Graph/Deep history-dependent
two-turn **production HTTP API** controls using a request-derived loopback
Chat Completions fixture, four exact requests/no provider injection. Actual
packaged-page browser management/search/group/archive/unarchive/reload passed;
installed-wheel second owner was refused while packaged owner stayed healthy.
See the G report's continuation section and `native-context01/stage-audit.json`.

Native activation failed after fresh exact-window selection/one recovery retry;
native input stopped and Lv was asked about interactive desktop state. These
API/browser checks do not close required native parity or process-restart proof.
Current EXE/fixture processes were verified live and retained for that next test;
recheck actual handles before reuse. No production source change or G1–G4 rerun
is needed yet. Next is native dependent composer/normal-close/reopen, then the
remaining approvals/effects/Skill/MCP/lifecycle/host rows. No gate is waived.

## 2026-10-04 next-stage task queue — organization only

This section refines the existing G5–G8/H order; it is not a new roadmap or
acceptance result. Lv requested task organization, not additional execution in
this turn. No production source, fixture settings, running app, default route or
release state is changed by this organization. Execute sequentially in this
checkout; no subagents, new chats/worktrees or per-task commits.

### Recorded entry point

- G1–G4 retain their accepted source/package evidence: 25/25 source gates,
  735 Python passes with 2 skips/1 warning, frontend 56 passes; no rerun merely
  for planning. These are previous measured results, not tests run this turn.
- The later native recovery succeeded. The on-disk receipt
  `.bench-results/g-local-20261004-01/native-context01/deep-native-before-restart.json`
  records Deep conversation `7d6ed9c5dd984bb59a207e4d2b575d5a`, stable thread
  `native-1714ca526b984d1b8e7e99edf056866b`, two completed runs and four messages.
  Native screenshot/AX receipts `native-deep-two-turn.*` and
  `native-immutable-deep-selected-graph-draft.*` accompany it. Deep recall is
  `recalled=deep-native-7a6b42;history_verified=true`.
- Graph native dependent turns and both native post-process-restart turns are
  still pending. Earlier packaged API/browser controls remain separately labeled;
  do not substitute them for native composer actions. Retain the activation
  failure and subsequent recovery evidence, rather than rewriting history.
- Process IDs, ports and UI element indices in old receipts are observations,
  not reusable live authority. Reobserve before any next action. This planning
  turn has not verified whether the EXE or fixture server is still running.

All file paths below are relative to `D:/Codex Program files/Agent/Doppel-Agent`.
Use the sealed `.artifacts/g-local-20261004-01/desktop02/DoppelAgent/DoppelAgent.exe`
and disposable `fixture-workspace` only; never the daily `.dist` or real profile.

### 1. G5a — Native dependent context and real restart (first executable task)

**Evidence:** existing `native-context01/`; append uniquely named new receipts.
**Contract:** P02/P04/P05/P09/P15; existing `context_fixture_server.py` controls.

- [x] Revalidate exact EXE hash, fixture workspace/settings resolution, current
  process/listeners, loopback fixture health and fresh native window state.
- [x] Through the native composer, submit Graph `G-CONTEXT remember
  graph-native-8d4c31`, then `G-CONTEXT recall`; capture each visible completed
  response and read-only API/store/run-event receipts. Reuse a draft only after
  observing that it has no prior submitted turn; otherwise create a fresh nonce.
- [x] Record owned processes/listeners, normally close through the host, then
  prove owned exit/listener removal and every fixture DB's integrity/exclusive
  reopening. Do not reuse old scripts that assert there are exactly two runs.
- [x] Relaunch the exact same EXE/workspace; locate both native conversations and
  submit only `G-CONTEXT reopen` through each native composer. Require correct
  nonce, six messages/three completed runs per conversation, stable thread/mode,
  actual runtime identity and no fallback. A page reload is not process restart.
- [x] Save a new request-count/source/input/daily-app audit. Preserve earlier
  four-request `stage-audit.json`; recount the current journal instead of copying
  its old denominator. Update the G report with explicit native/API provenance.

**Exit:** both modes pass native remember → recall → normal close/relaunch →
reopen, with exact package, durable context and owned-cleanup receipts.

### 2. G5b — Native conversation management and profile boundaries

**Contract:** P01/P03/P04/P09/P10/P12. **Evidence:** new `native-management01/`.

- [ ] Verify Legacy history remains isolated and readable; verify native mode
  immutability and credential-free accepted profile snapshot versus draft choices.
- [ ] Exercise native message/title search, keyboard selection, rename, group
  create/rename/assignment, archive/read-only state and unarchive.
- [x] Close/reopen and verify those management changes persisted in both UI/store.
- [ ] Seed disposable visible deletion targets; obtain action-time confirmation
  required by native-control guidance before deletion. Never touch real history.
- [ ] Record empty/loading/sanitized-error views, Enter/Shift Enter/Ctrl K,
  focus/scroll and state restoration; browser-only passes remain separate.

**Exit:** required management/profile paths have native screenshots plus durable
receipts; no implicit provider probe or migration.

### 3. G5c — Approvals, actual effects and inspector truthfulness

**Contract:** P06/P07/P08/P11. **Evidence:** new `native-approval01/` and a local
scripted patch fixture, restricted to disposable workspace files.

- [ ] Seed a deterministic proposed patch; show exact diff and expected file hash.
- [ ] Execute independent approve/edit/reject/cancel/conflict cases natively;
  compare approval state, actual bytes/hashes and durable effect ledger.
- [ ] Verify retry/reconcile cannot apply the approved effect twice; rejected,
  cancelled or conflicting requests must not fabricate successful writes.
- [ ] Restart a pending approval and verify reconstructed state and continued
  ownership; validate timeout/error projections where required by P07.
- [ ] Exercise verification and child inspector; separate actual test results,
  synthetic usage, completed answers and drained worker/lease state.

**Exit:** expected effects occur exactly once where authorized, never otherwise;
visible outcomes agree with files, events, approvals and ownership receipts.

### 4. G5d — Native Skills and local MCP acceptance

**Contract:** P11 plus original F Skill/MCP DoD. **Evidence:** new
`native-catalog01/`, progressive Skill fixture and local stdio/Streamable HTTP
servers. No remote MCP, real account or paid provider.

- [ ] Seed/discover a minimal Skill; prove progressive disclosure and safe paths.
- [ ] Verify native cached listing/reload and explicit probe behavior; opening
  settings must not silently perform a live probe.
- [ ] Exercise both local MCP transports and pagination; retain exact discovery
  and tool-call counts, structured/multimodal content and `is_error` behavior.
- [ ] Verify cache/error recovery and sanitized UI errors; normally stop fixture
  servers and prove owned cleanup without touching unrelated processes.

**Exit:** native UI and actual local transport/Skill events agree; no fabricated
tool result, relaxed fixture oracle or leaked owned server.

### 5. G5e/G6 — Busy lifecycle, ownership and native host parity

**Contract:** P06–P08/P12–P15. **Evidence:** new `native-lifecycle01/`.

- [ ] Cover queue/running/setup/write-lock/child cancellation; verify bounded
  admission, visible terminal state and eventual lease/worker drain separately.
- [ ] Launch a second windowed EXE against the same fixture workspace; verify
  startup refusal while the original owner remains healthy. The earlier
  installed-wheel second-owner control does not replace this native startup case.
- [ ] Normally close during owned work, prove process/listener/MCP/DB cleanup,
  then test fresh owner handoff and durable history/approval recovery.
- [ ] Test maximize/minimize/restore/close, standard/minimum native dimensions
  and available DPI. Record actual window/client dimensions; unavailable scaling
  remains untested, never a browser-viewport substitute.
- [ ] Verify root, `/legacy/`, `/runtime/`, hash-link reload and assets without
  implicit data migration; reconcile every required P01–P15 case with its receipt.

**Exit:** all required G5/G6 rows close without force-kill or waived failures.

### 6. G7/G8 → H — Conditional default switch and one-batch delivery

- [ ] Only after steps 1–5 pass, write failing default/compatibility tests and
  perform the minimum Vue-root switch; preserve `/legacy/` and rollback boundaries.
- [ ] Any production change, including that switch, starts a new source freeze:
  full 25-gate runner → wheel/sdist/noneditable smoke → fresh EXE linkage → final
  native default/parity/normal-exit acceptance. Old pre-switch screenshots are
  not final-source evidence. Retain failed attempts and use RED → fix → GREEN.
- [ ] Update G report and original A–H checkpoints with exact denominators,
  hashes, exclusions, untested configurations and zero-paid-call evidence.
- [ ] H reviews scoped security/code/migration/ownership/assets; updates English
  `README.md`, Chinese `README_CN.md`, `CHANGELOG.md`,
  `docs/TECHNICAL_REPORT_ZH.md`; then task-scoped stage/one commit/push, exact
  remote HEAD and CI verification. Do not broadly stage unrelated work.

**Exit:** reviewed offline implementation candidate, not automatically final v1.0.
Paid real evaluation remains separately authorized and budget/cap/usage gated;
Task29 decides final version/tag/artifacts against the original DoD. No daily EXE
replacement or speculative Server Mode now; next-major chat only at an actually
accepted major-version boundary.

### Planning verification / next handoff

Run `git diff --check`; inspect the plan's tail and changed-file inventory.
For an untracked plan, also inspect the complete file directly: Git diff alone
does not cover untracked contents. No product tests or native cases are counted
as newly executed by this task-organization update.

**Organization-time handoff (superseded by the execution checkpoint below):**
step 1 — observe the live native fixture setup, finish Graph's two turns and
complete both modes' real restart proof. Do not restart A–F, rebuild unchanged
G1–G4 or switch the default early.

### G5a execution checkpoint — native dependent context/restart accepted

The exact sealed EXE passed both native remember/recall/reopen sequences.
`native-context01/native-context-before-restart01.json` and
`native-context-after-restart01.json` are GET-only verification receipts for
native-composer submissions, not API-submitted turns. Graph/Deep each retain
six exact messages/three completed runs, stable thread/mode, one actual
runtime.finished per run and no fallback. Two normal native closes and relaunches
prove process restart, owned process/listener exit and six-store integrity/
exclusive reopen/owner release. The fixture journal now has ten requests:
four prior API controls plus six native turns; usage100/10 remains synthetic.

Native management additionally passed group create/assign/filter/rename,
conversation rename, archive/read-only/unarchive, Ctrl K archived message-body
search and Enter open, page reload and management persistence after a second
true process restart. `native-management-after-restart.json` equals the complete
pre-close conversation detail. Title-search/arrow selection, seeded visible
deletion, full Legacy/profile/empty/error/keyboard matrix remain open, so step2's
combined checklist is deliberately not wholly marked accepted. Opening/closing
fixture settings was read-only and did not add a provider request.

`native-stage-audit01.json` confirms source/guarded inputs/daily app/EXE unchanged,
12 completed fixture runs, ten exact fixture requests and zero paid calls.
`native-continuation-failures01.json` retains preparation/automation failures;
the second close's PID-only checker false positive was actual PID reuse by a
different executable, not a surviving WebView2 or a reason to force-kill.
Future process inventories must include creation time and executable/parent
identity; native control must refresh screenshot geometry on each new window.

At the recorded live checkpoint, EXE28832 and fixture94436 remain healthy with
ports7294/7295 and7762; `native-live-state01.json` is an observation only. Reverify
them before reuse. No production changes require repeating G1–G4 yet.

**Next executable work:** finish step2's remaining Legacy/profile/title/keyboard
boundaries and seeded visible deletion when action-time confirmation is obtained;
then deterministic native approval/effect fixtures (step3), Skills/MCP (step4),
busy lifecycle/host parity (step5). G5/G6 overall, G7/E switch, H and final v1.0
remain open. Do not count this checkpoint as full native parity or final release.

### G5b defect checkpoint — Legacy draft profile reset requires refreeze

Native title search returned two exact Deep titles; Down/Up visibly changed the
selected result and Enter opened it. Legacy empty history was isolated; native
Shift Enter added a newline without creating a conversation or provider request.
However, selecting `g-offline` on the empty Legacy page then submitting created
an unbound draft whose selection reset the chosen profile to global `g-context`.
Run `cf4e134b73634226a887b8f5650ac9b7` failed at the unchanged loopback fixture
with HTTP400; the eleventh journal request is a retained failure, not a pass.

Three regression cases went RED before repairing `WorkspaceController`: capture
the chosen profile and durably bind the Legacy draft before selection/submission,
for implicit first submission and explicit agent/review drafts. Frontend now has
59 passing tests in7 files; package-native repair acceptance remains mandatory.
`native-management01/defect-and-repair01.json` retains the failure and repair scope.
EXE28832 was normally closed; identity-aware process/listener proof and seven
SQLite integrity/exclusive-reopen/owner-release checks pass (Legacy added the
named `tasks.sqlite3`, not an unexplained waived eighth/missing store).

**Superseding next action:** fresh source attempt `g-local-20261004-02` must finish
all25 gates, new wheel/sdist/noneditable smoke and new EXE/source linkage, then
reproduce the exact empty-page native profile scenario on that repaired package.
Old package receipts are historical pre-fix proof, not final-source acceptance.
Do not resume old-source gate claims, switch root, delete fixture history or start
H early. Resume remaining G5b–G6 rows only with verified repaired-package identity.

## Repaired-package next-stage task queue — 2026-10-04 organization

This is a planning/documentation update, not another execution or test pass.
It extends the original G/A–H sequence. Read existing attempt02 receipts before
resuming; do not follow an earlier “attempt02 pending” checkpoint literally.

### Verified starting point and evidence boundaries

- Attempt `.bench-results/g-local-20261004-02/validation-final.json` records
  **25/25** gates, frozen source/inputs, unchanged daily app and zero paid calls.
  Logs record Python735 passed/2 symlink-environment skips/1 warning,
  frontend59 passed/7 files, lifecycle106×5 and MCP23×3. These were executed
  previously; this organization task only reread their receipts.
- `package-cycle-final.json` records11 passed package steps, exact sdist-wheel
  member reconstruction, independent noneditable smoke and frozen source.
  `package-dependency-parity02.json`/log records89/89 applicable locked packages
  and9 marker exclusions. Preserve the offline pythonnet3.1.0 cache-miss failure
  and preparatory UTF-8/GBK error; successful resume does not erase failures.
- Accepted source inventory SHA-256:
  `c7d93e62c5275ef158195d3350e6ace146c7a9ff1eb2a0f21690082578291c46`.
  Current native package:
  `.artifacts/g-local-20261004-02/desktop02/DoppelAgent/DoppelAgent.exe`, SHA-256
  `b913676c9af34949377f23cc9dff1d29241fd3197c03f6e040125ee991c37bcb`.
- `native-profile01/` contains actual native screenshots and GET-only receipts:
  empty-page first submission, explicit agent draft and explicit review draft
  retain selected `g-offline` while the global default remains `g-context`.
  One Legacy run reads actual fixture bytes using Mock; two drafts have no
  messages, native history remains empty, and fixture request count stays11.
  Profile survives page reload and completed-history reopening. This proves
  the three repaired profile paths, not full effort/review-mode restoration,
  a true repaired-package process restart, or model quality.
- `package-cycle-final.json`'s native-pending field predates those native receipts;
  preserve it as an immutable checkpoint, not as the latest aggregate status.
  Old attempt01 Graph/Deep context/management/restart proof remains historical
  pre-fix evidence; final-source proof must use the repaired package.
- HEAD remains `952a03b7d9dda78dc0f2188e32a620279f1211c2`; version0.14.4,
  uncommitted A–H batch and Legacy default are unchanged. No stage G/H/v1.0
  completion, default switch, push, paid evaluation or new chat is authorized
  merely by this organization update.

### Execution order and dependencies

| Task | Scope | Dependency | Exit evidence |
| --- | --- | --- | --- |
| N0 | Seal repaired Legacy profile regression; normal close/restart | Existing attempt02 package | Exact profiles/messages/events, identity-aware exit, all actual stores reopened, true restart retains history/profile |
| N1 | Current-package Graph/Deep context and remaining conversation/profile parity | N0 | Required G5a/G5b rows reconcile native UI with durable data; no implicit migration/probe |
| N2 | Native approval/effect/inspector matrix | N1 | Approve/edit/reject/cancel/conflict/timeout/restart; authorized effects exactly once |
| N3 | Native progressive Skills and local MCP transports | N2 | Native disclosure/cache/probe, actual pagination/content/error and owned cleanup |
| N4 | Busy lifecycle, second windowed owner, host and route parity | N3 | Terminal versus drain, normal busy exit/handoff, required P01–P15 evidence |
| N5 | Conditional E/G7 default switch and final-source reacceptance | All required N0–N4 pass | Failing contract tests then minimal switch, new25-gate/package/native cycle |
| N6 | H consolidated review and delivery | N5 | Scoped review, bilingual docs, one commit/push, remote exact HEAD and exact CI |

These rows are milestones, not single tool calls. Execute each checklist action
sequentially. If a required row fails, retain its failure, use RED → minimal fix
→ GREEN and refreeze/reaccept changed production source. Do not weaken fixture
oracles, count unsupported cells as passes, or repeat unaffected source gates
merely because planning documents changed.

### N0 — First bounded execution task

**Files:** existing `profile_readback.py`, `native-profile01/*.json` and screenshots;
create new `native-profile01/profile-acceptance01.json`, identity/close/restart
receipts and narrowly scoped audit helpers. Never overwrite original receipts.
**Fixture:** `.artifacts/g-local-20261004-02/profile-workspace` only.

1. Reverify the exact EXE hash, current window/process identity and actual ports.
   Launch receipt PID111680/ports13457–13456 is historical, not a reusable handle.
2. Read existing before/Shift Enter/first-submit/agent/review/reload receipts;
   assert counts0→0→1→2→3→3, all created profiles `g-offline`, global `g-context`,
   one exact completed answer and no additional loopback context request.
3. Read the completed run/events without submitting another prompt; reconcile
   actual `read_file` request/completion and bytes with fixture `evidence.txt`.
   Save a combined acceptance receipt linked to the native screenshots.
4. Inventory owned EXE/WebView2 processes by PID + executable + creation time,
   listeners and the exact fixture SQLite path set before normal native close.
   Currently observed set has4 files: conversations, mcp-tool-executions,
   runtime and tasks. Compare actual pre/post sets; never hardcode old6/7 counts.
5. Normally close the exact native window; prove recorded identities/listeners
   gone, all actual DBs pass integrity/exclusive reopen, connections close and
   owner lock is reacquired/released. No force-kill or user-data cleanup.
6. Relaunch the same repaired EXE and existing fixture (do not rerun the initial
   launcher that requires a new workspace); record a new returned window identity.
   Natively reopen completed history and both drafts; compare durable profiles,
   messages and run counts. Do not substitute page reload for process restart.

**Exit:** bounded three-path repair plus true restart/normal-exit proof sealed.
Review effort/nextMode restoration remains a separate contract inspection; a
profile pass must not silently accept it.

### N1 — Finish current-source conversation boundaries

**Files:** existing G5a/G5b cases and P01/P03/P04/P09/P10/P12;
new attempt02 `native-context02/`, `native-management02/` receipts/fixtures.
**Production seams if a defect is reproduced:**
`frontend/src/nativeWorkspace.test.ts`, `frontend/src/workspace.test.ts`,
`frontend/src/runtime.ts`, `frontend/src/workspace.ts`,
`src/doppel_agent/persistence/conversations.py` and corresponding API tests.

1. Allocate new Graph/Deep conversations/nonces; perform native dependent
   remember/recall, normal close, true restart/reopen on the repaired EXE.
   Preserve strict request-derived fixture behavior and exact mode/thread identity.
2. Verify immutable accepted mode/profile snapshot versus changing draft/global
   defaults, missing-profile handling and busy-state restrictions independently.
3. Reconcile current-package title/body search, arrow/Enter selection, rename,
   group create/rename/assign/filter, archive/read-only/unarchive and restoration.
4. Seed only visible disposable deletion targets; perform deletion only with the
   required action-time native confirmation. Verify the exact target disappeared
   and adjacent histories/stores survived; no broad deletion.
5. Capture empty/loading/sanitized error, Enter/Shift Enter/Ctrl K, focus/scroll
   and required state restoration. Inspect effort/review-mode persistence against
   the settled contract before claiming either a defect or acceptance.

**Exit:** each required case has current-package screenshots plus durable receipt;
Legacy/native history stays isolated, with no silent provider probe or migration.

### N2 — Approvals and real effects

**Files:** existing G5c; new `native-approval02/` and disposable patch fixture;
relevant `ApprovalPanel.vue`, run/controller tests, approvals/concurrency tests,
`src/doppel_agent/persistence/tool_ledger.py` and workspace patching/service.

1. Seed one deterministic proposed patch per independent case and record before
   bytes/hash, expected diff, approval identity and ledger baseline.
2. Natively approve, edit, reject and cancel separate cases; compare UI/events,
   approval state, actual file bytes/hash and durable ledger.
3. Create conflict and timeout cases; verify no fabricated successful write.
4. Retry/reconcile an approved effect; prove exact-once application.
5. Restart pending approval; verify recovery and ownership before continuing.
6. Exercise verification/child inspector; separate actual verification results,
   synthetic usage, terminal answer and drained worker/lease state.

**Exit:** every authorized effect occurs exactly once; all other cases have the
correct no-write/error/cancel outcome and observable evidence.

### N3 — Skills and MCP

**Files:** existing G5d; new `native-catalog02/`, fixture Skills and local stdio /
Streamable HTTP servers; catalog/API/MCP tests and existing loader/executor seams.

1. Discover a minimal fixture Skill; demonstrate progressive disclosure/safe paths.
2. Capture native cached list/reload and explicit probe; opening settings alone
   adds no live request.
3. Exercise both transports with pagination and exact discovery/call counters.
4. Validate structured/multimodal results, `is_error`, sanitized errors and recovery.
5. Normally stop owned fixtures and prove listener/process/client cleanup.

**Exit:** native catalog and actual local transport events match. No remote MCP,
real keys or synthetic results masquerading as actual tool output.

### N4 — Lifecycle and host parity

**Files:** existing G5e/G6/P06–P08/P12–P15; new `native-lifecycle02/`;
desktop, ownership, settlement/cancellation/deadline tests and source seams.

1. Cancel queue/running/setup/write-lock/child cases independently; count bounded
   admission, visible terminal state and eventual workers/leases drained separately.
2. Launch a second **windowed EXE** on the same fixture; verify startup refusal
   and continued health of the original owner. Wheel-only proof is insufficient.
3. Normally close while owned work exists; check processes/listeners/MCP/all
   actual DBs, then verify new-owner handoff and durable history/approval recovery.
4. Test native maximize/minimize/restore/close, standard/minimum window and actual
   client dimensions, available DPI. Unavailable scaling remains explicitly untested.
5. Verify root, `/legacy/`, `/runtime/`, hash reload/assets and no implicit migration;
   reconcile every required P01–P15 row with current-source receipts.

**Exit:** no required native row remains open, force-killed or waived.

### N5–N6 — Default switch and one-batch handoff

Use existing G7/G8/H tasks without inventing another release track. Only after
required native parity passes, write failing root/compatibility tests, minimally
switch Vue default, preserve Legacy/rollback, then freeze the new source and run
full25 gates → wheel/sdist/noneditable smoke → linked EXE → final native parity
and normal exit. Earlier pre-switch evidence cannot prove final default behavior.

H reviews task-scoped security/code/migrations/ownership/assets, updates English
`README.md`, Chinese `README_CN.md`, `CHANGELOG.md` and
`docs/TECHNICAL_REPORT_ZH.md`, then one consolidated scoped commit/push with
remote exact HEAD and exact CI verification. Do not stage the current dirty batch
or push a planning-only partial release. No daily EXE replacement/tag by default.

Paid real evaluation remains separately authorized and current compatibility /
price / total10 CNY sticky usage / provider-cap gated. Task29 then resolves the
original final v1.0 DoD and release decisions. Create the next-major chat only at
an actually accepted major-version boundary; Server Mode requires measured local
queue bottlenecks, not speculative infrastructure.

**Immediate handoff:** execute N0 next. Do not restart A–F or rerun unchanged
attempt02 source/package gates; this organization update changes documentation only.

### N0 execution checkpoint — repaired profile/restart/idle exit accepted

The organization-only turn above was not an acceptance pass. This continuation
executes N0 and seals `g-local-20261004-02/native-profile01/n0-final01.json`.
All three native-created Legacy profiles retain `g-offline` against global
`g-context`; one completed actual Mock `read_file` run and two empty drafts
reconcile with exact messages, eight ordered events and unchanged11-request
fixture journal. No model/provider request is added by this N0 continuation.

Native normal close of EXE111680 released both13456/13457 listeners and all6
owned WebView2 processes; the first identity-aware sample still saw the root
EXE, then a later sample proved the original root exited. Both observations are
retained (`postclose-processes01/02.json`); no force-kill or premature restart.
All4 actual pre/post SQLite paths match and pass integrity/exclusive reopen,
closed connections/WAL-SHM absence and independent installed owner-lock
reacquisition/release. This is **idle** exit, not busy lifecycle acceptance.

Same sealed EXE and same existing fixture restart as PID113260, new creation
time/window1250276, actual ports1088/1087. Native reopening of completed history,
agent draft and review draft visibly retains Mock. GET-only post-restart data
is exactly equal to pre-close conversations/run/events. Root still displays
Legacy; native history remains empty, with no reset/migration/provider probe.
The live identity/health snapshot is `restart-live01.json`; reverify before reuse.

One audit preparation assertion wrongly compared tool output bytes to on-disk
bytes: the unchanged Windows fixture is51 CRLF bytes, while `read_file_tool`
uses universal-newline text decoding and the loop counts50 returned UTF-8 bytes.
Exact raw bytes and exact decoded text are now asserted independently; failure
provenance is retained in `validation-failures01.json`. No production/fixture/
provider-oracle change or relaxed expected answer was used to obtain a pass.

Final audit confirms accepted source SHA
`c7d93e62c5275ef158195d3350e6ace146c7a9ff1eb2a0f21690082578291c46`, guarded
inputs, entire daily app and repaired EXE unchanged. Effort/review-mode
restoration, full native parity/G5/G6/default switch/H/final v1.0 remain open.

**Superseding next execution:** N1 new-nonce Graph/Deep context and real restart
on this repaired package, then remaining conversation/profile/keyboard cases.
Do not rerun historical profile-acceptance helpers after N1 adds native history
or requests: their sealed0-native/11-request denominators describe this N0 stage.
Continue with new receipts and exact updated denominators, not overwritten passes.

### N1 execution checkpoint — current-package context subset accepted

`g-local-20261004-02/native-context02/n1-context-final01.json` seals two native
Graph/Deep conversations on the repaired package. Each completed remember,
history-dependent recall, then reopen after true process restart: six runs total,
six messages/three runs per conversation. Exact prior messages/run records/events,
conversation IDs, thread IDs, modes and credential-free profile snapshots survive;
all runs have one actual runtime.finished and one completed run.status_changed,
no fallback and no active lease. GET-only verification never submits a turn.

The strict loopback fixture derives answers from requested history only, with
exact nonce/assistant-chain checks. Journal17 includes retained baseline11
(including the historical wrong-profile400) plus six new200 context successes.
Synthetic token usage is not paid/model-quality/cost measurement. All three N0
Legacy histories are exactly unchanged. Old fixtures/screenshots are not used
as proof for these new conversations on the repaired EXE.

Normal native idle close exits root113260 and owned WebView2 identities and
1088/1087 listeners; all seven actual SQLite paths pass integrity/exclusive
reopen/closed-connection/WAL-SHM absence and installed owner release. Same EXE
and existing workspace restart as109228, window2495142, ports7598/7597. Native
history reopening and third-turn completion screenshots reconcile with durable
readback. `restart-live01.json` records healthy identity/listeners and strict
fixture94436:7762; reverify before reuse, do not reuse stale UI indexes.

Retain `validation-failures01.json`: initial verifier expected a nonexistent
run.completed instead of the source's run.status_changed; corrected to the exact
terminal event contract without changing production or oracle. One close attempt
failed because screenshot geometry was unavailable, so first live process sample
and failed45-second drain log are ineligible. Refreshed native observation/actual
close and subsequent identity-drained sample02 support the accepted close.
No force-kill, fixture reset, relaxed protocol, migration or paid request.

Final source/input/daily/EXE audit still matches attempt02. This accepts only the
N1 context subset, not full N1/G5/G6/P01–P15/default switch/H/final v1.0.
**Superseding next execution:** remaining N1 immutable profile versus draft/global
defaults, missing/busy profiles, management/search/group/archive, keyboard/focus/
scroll and effort/review-state contract cases. App-interface deletion requires
action-time confirmation; do not waive that row or silently delete fixtures.
Then proceed N2–N6 sequentially. Do not rerun sealed helpers with historical
0/2-conversation or11/15-request denominators after further fixture changes.
Do not repeat unchanged attempt02 gates solely because these docs changed.

The full objective is incomplete. Latest goal-tool snapshot reports usageLimited,
not complete; this human-requested continuation records measured progress without
changing scheduler state. No commit/push/tag/daily replacement/new chat.

### N1 execution checkpoint — management/profile/keyboard/restart subset

New immutable receipts under `g-local-20261004-02/native-management02/`:
`n1-management-final01.json`, `n1-management-followup01.json`, paired native
screenshots/accessibility trees, exact GET-only data/run/event snapshots,
`preclose01.json`, `postclose-processes01.json`, `postclose01.json`,
`restart01.json`, `restart-live01.json` and `validation-failures01.json`.

Native management creates exactly one empty Graph draft, renames it, creates /
assigns / filters / renames a group, archives it with read-only composer, finds
archived title through search/Enter, then unarchives. Body search returns exactly
the two context conversations; Down/Enter selects Deep and restores its own
chosen profile/run. After genuine restart, Up wraps to Deep. Native search's
first Escape clears the nonempty input instead of closing the modal; retain that
ineligible close observation. Stable empty-query re-observation plus second
Escape closes the modal without changing the selected completed run. Shift Enter
inserts two lines without submitting; Enter submits each controlled task once.

Native review draft has quick effort/all four grants unchecked; its template
fills read-only review text without a run. Fixture-only removal of its chosen
temporary profile demonstrates a blank selection and disabled send, not fallback
to global `g-context`. Explicit native Mock reselection restores eligibility.
API profile removal here is setup only, **not native history deletion evidence**.
Changing the next-draft mode to Deep does not mutate this existing Graph review.
It submits Graph failure `490d8fb9d75c4f03a5ed518e7abed1e3` with the captured
quick/no-grants request. Three actual adapter retries receive controlled500;
only `ProviderRequestError: run execution failed` reaches UI/events/readback,
not the fake private-body marker. No fabricated assistant answer or active lease.

Next native draft is really Deep. Run `0b48ad6475df4b82bd912fe6368c1c80` pauses
at the independent local HTTP fixture: model profile/new-mode/new-draft/manage
controls are visibly disabled while running, then restored after fixture release
and actual completion. Usage100/10 is synthetic plumbing, not cost or quality.
Global remains `g-context`; all earlier Graph/Deep and three Legacy histories
are exactly unchanged. Current denominator is **5 native conversations / 8 runs /
15 messages**, three Legacy conversations, one renamed group, context journal17
unchanged and separate controlled journal4 (three500 plus one200). Nothing is
implicitly migrated or probed; paid calls0.

Native normal **idle** close of109228 releases its six owned WebView2 processes
and7598/7597 listeners. First5-second sample still sees the original root;
later identity-aware sample at10.688 seconds proves complete exit, not force-kill.
All seven actual SQLite paths match, pass integrity/exclusive reopen with closed
connections/WAL-SHM absence, and independent installed owner reacquire/release.
Same sealed EXE/existing workspace restarts as48564, new creation time
2026-10-04T16:53:00.287542+08:00, native window55250290, UI2952/API2951. Root
remains Legacy. Native Runtime restores selected busy conversation/run/profile;
all five conversation rows/eight runs/events exactly match before/after restart
and later keyboard checks, with no new provider requests. Draft prompt/effort/
grants are transient component state, not the settled selected-history contract;
accepted run request retains quick/no-grants. Ctrl R alone was not restart proof.

Retain UIA offscreen indices, non-target dropdown, asynchronous-scroll misclick
and first-Escape observations as ineligible for the intended actions. Fresh
stable observation/keyboard or screenshot coordinates recover without duplicate
creation or accidental submission. Source SHA, guarded inputs, daily app and EXE
still match attempt02; no production change or repeated unchanged25-gate batch.

**Pending action-time human confirmation:** delete only native empty draft
`8b98ff340f4242728f676b2e39eaccca`, title
`N1 disposable native management 20261004`, zero messages/runs, in the isolated
profile workspace. The existing confirmation question was asked once; no reply
has arrived. Do not repeat approval prompts, click delete, substitute an API
delete, waive adjacent-history/audit checks or mark full N1/N6 accepted.
After confirmation, reobserve the unique native target and verify adjacent eight
runs/events, Legacy history, group and retained audit before closing N1.

Safe N2 preparation only: `native-approval02/fixture-preflight01.json` creates
14 independent Graph/Deep approve/edit/reject/cancel/conflict/restart/verify
inputs with exact before/approved bytes/hashes/diffs and ledger baseline. The
request-derived fixture passes42 stage controls and rejects42 wrong-read/tool-ID
controls **without any provider request or native run**. Prior fixture files are
unchanged. Its terminal response is expressly not a patch-success oracle; native
decisions, actual bytes, ledger and durable events must independently prove effects.
Verification config/timeout/child cases still need setup. This is not N2 proof.

**Superseding next execution:** complete the confirmed native-delete/final N1
rows, then execute N2–N4, conditional N5 final-source package cycle, and N6 H
review/docs/one scoped commit/push/exact HEAD CI. Full objective remains incomplete,
goal scheduler usageLimited unchanged; no default/version/tag/release change.
Do not rerun sealed historical denominator helpers after adding/removing cases.

### Recovery and remaining N1 visual checks — 2026-10-04 continuation

Previous turn classification: **progress**, because it produced sealed native
management/profile/keyboard/restart receipts, independent N2 inputs and updated
the authoritative handoff. This continuation does not treat that progress or the
automatic goal message as native-delete confirmation.

Current-state inspection twice confirms historical EXE48564 and fixtures94436 /
114032 are absent;2951/2952/7762/11351 have no listeners. The first refused health
request is retained as a state change, not a timeout assumed terminal. The prior
exit cause and native-close provenance are **unproven**. New
`native-recovery03/recovery01.json` records actual absence, seven unchanged actual
SQLite paths/integrity/exclusive closed-connection reopen and installed owner-lock
reacquire/release before relaunch. No kill/reset or relabeling as a normal/busy
host-close pass. Prior accepted close/restart receipts remain immutable.

The same hashed EXE/existing fixture recovers as117092, creation
2026-10-04T19:17:49.124727+08:00, native window921516, UI2346/API2345. Two fresh
independent HTTP processes57924:7762 and103688:11351 import the exact unchanged
request-derived oracles but write new journals under `native-recovery03/`.
Historical17-context/4-controlled journals remain untouched; both recovery
journals add **zero requests**. Public credential-free profiles/endpoints, all
five native conversations/eight runs/15 messages and exact ordered events, three
Legacy histories, group and settings match before/after recovery and final UI
checks. Native root is still Legacy; Runtime Lab restores completed busy history
with the captured profile. Actual CIM/listeners/health are in `live01.json`.

Native task-box focus → Ctrl K → empty search focused → Escape returns the visible
task-box focus outline. A640-unit wheel scroll exposes model/grants, tools/
verification and child regions; opening/closing the empty search preserves their
visible relative anchors and focused composer. Paired screenshots/tree receipts
are sealed by `native-recovery03/n1-recovery-final01.json`. This is qualitative
native focus/scroll proof, **not pixel/DPI measurement or persisted post-restart
scroll-position proof**. The incidental native empty-field validation bubble is
retained; exact records/zero requests prove no run submission.

First screenshot capture did not match the target and was not saved/accepted;
activate-target plus fresh observation recovered correct fixture UI. A later
scroll-to-top action was interrupted by the computer-use user-input guard; refresh
was performed, final state saved, and no further native input or top-position pass
claimed. Window input grants no delete authority. Existing action-time deletion
question still has no human response; full N1/N2–N6 remain unaccepted.

N2 additional safe preparation: `native-approval02/verification-preflight01.json`
records **8 actual supervised child commands**, two exact-byte positive and six
initial/no-effect, CRLF-conflict and missing-file negative controls through the
independently installed VerificationPipeline. Fixed Graph/Deep configuration
templates and the byte/hash oracle are saved, not installed into the live fixture.
All14 native patch inputs and Graph ledger baseline remain unchanged, and no
model answer is consulted. These real control processes are **not packaged-native
approval, verification inspector or child-delegation acceptance**. Native verify
cases must explicitly grant configured-command permission and inspect the actual
run's command/report/event/effect identity separately.

Source inspection also distinguishes the Graph patch ledger from the direct
Deep patch adapter: do not invent a Graph ledger row for a Deep patch or infer
universal exactly-once from a final answer/file hash. N2 still has to reconcile
actual checkpoint/base-hash/tool receipt/approval/effect and duplicate/crash cases,
retaining fail-closed uncertainty rather than declaring a completed write.
No production/capability flags changed on inspection alone.

Final source/guarded-input/daily-app/EXE guard matches attempt02 SHA
`c7d93e62c5275ef158195d3350e6ace146c7a9ff1eb2a0f21690082578291c46`.
Paid requests0; version0.14.4/root Legacy/dirty batch/no push/tag/daily replacement
unchanged. The current goal tool now reports **active**, superseding the historical
usageLimited snapshots; this is external scheduler state, not an agent reset or
completion claim. Continue the original full objective, N1 then N2–N6 and original
v1.0/major-version boundaries, without shrinking scope or accepting open rows.

### N1 row reconciliation and three-turn blocker audit — 2026-10-04

The required N1 rows reconcile to the existing immutable native evidence:

| N1 row | Evidence and acceptance boundary |
| --- | --- |
| Graph/Deep context and true restart | `native-context02/n1-context-final01.json`; context-derived remember/recall/reopen and unchanged histories, not a browser refresh |
| Profile/mode/busy boundaries | `native-management02/n1-management-final01.json` and `n1-management-followup01.json`; explicit chosen profile, no fallback, immutable accepted mode and restored controls |
| Search and management | Same management receipts; title/body search, arrows/Enter, rename/group/filter/archive/read-only/unarchive, without deleting history |
| Empty/loading/error and keyboard/focus/restoration | Paired `new-empty-draft01`, `busy-accepting01` (native queued run, disabled submission, visible loading), busy/error/keyboard receipts; `native-recovery03/n1-recovery-final01.json` seals qualitative focus/scroll only, not unmeasured DPI/persisted scroll |
| Disposable native deletion | **Not accepted**: action-time human confirmation and exact-target disappearance/adjacent-history audit remain required |

`native-recovery03/blocker-revalidation01.json` records the current read-only
health/process/listener check: five native conversations, eight terminal runs,
zero active/leased runs; the unique draft `8b98ff340f4242728f676b2e39eaccca`,
`N1 disposable native management 20261004`, still has zero messages/runs and is
not archived. No delete, source/fixture mutation or paid call occurred.

The same mandatory confirmation remains absent across three consecutive goal
turns. Earlier turns made genuine subset/preflight progress; this turn's audit
is revalidation, not another implementation/acceptance increment. N2's entry
requires N1; N3–N6 are sequentially gated. Existing safe N2 preparation does not
close that prerequisite, and no acceptance job is running. Further speculative
preflight, repeated unchanged rebuilds or API deletion would not resolve the
native gate. The goal is therefore being marked **blocked, not complete**, pending
the human response to the already-issued exact-target confirmation question.
Do not repeat approval cards or infer authorization from automatic continuation.

On confirmation, freshly observe the target, perform only its native deletion,
verify adjacent eight runs/events, Legacy histories, group and retained audit,
then close N1 and resume N2–N6. Revalidate process identities before native use.
Version0.14.4, Legacy root, full A–H/v1.0 scope, dirty consolidated batch and the
single accepted-delivery commit/push boundary remain unchanged.

### Resumed blocker audit, first turn — 2026-10-04

The scheduler resumed the previously blocked goal as active; this starts a fresh
three-turn blocker audit, not native-delete authorization. The prior turn was
blocker/row/status reconciliation, not new implementation acceptance.
`native-recovery03/resumed-blocker01.json` finds EXE117092 absent twice and
2346/2345 without listeners. Fixtures57924/103688 and7762/11351 remain live with
unchanged creation identities. Empty app logs do not prove normal close or crash
cause. No restart/kill or deletion was performed.

Seven actual SQLite stores pass read-only integrity checks with their main-file
hashes unchanged. Current native totals remain5 conversations/8 runs/15 messages,
zero leases; the exact pending draft still has0 messages/0 runs and is neither
archived nor deleted. The original action-time human confirmation is still
missing. This is resumed blocker turn1, so the goal remains active, incomplete;
N1 and sequential N2–N6 remain open. Recover the same package only after fresh
process/store/owner preflight when the native gate can actually proceed. Do not
turn repeated idle relaunches or live fixture servers into acceptance progress.

### Resumed blocker audit, third turn — 2026-10-04

`native-recovery03/resumed-blocker02.json` and `resumed-blocker03.json` revalidate
unchanged exact draft0 messages/0 runs, totals5 conversations/8 runs/15 messages/
zero leases. EXE117092/2346/2345 remain absent; only the two idle fixture servers
are live. This is not a native acceptance job or verified wait for completion.
The previous turn was no progress. Human action-time delete confirmation remains
missing across three consecutive resumed goal turns, satisfying the fresh blocked
audit threshold. Sequential N1 then N2–N6 acceptance cannot proceed without that
input; safe preflight does not replace the native gate. Mark goal **blocked, not
complete** again. Preserve full scope and all evidence; no deletion, relaunch,
source/default/version change or push. After the human response, perform fresh
process/store/owner preflight, recover the unchanged package and resume exact
native deletion plus adjacent audit before N2. Automatic goal continuation alone
is not authorization and does not warrant duplicate approval questions.

### Human-authorized build-first override — 2026-10-04

Lv explicitly instructed: build first, perform testing together later. This
supersedes sequential native acceptance as the current execution order, not its
release criteria. Human deletion confirmation was received for the unique draft;
a subsequent physical Escape stopped Computer Use before deletion. Do not claim
it completed or repeat the already-answered question. Native inputs/tests are now
deferred by the latest instruction rather than treated as an implementation blocker.

The source audit finds Local Mode's A–D/F and E compatibility preparation already
implemented. N2–N4 describe primarily acceptance matrices, not missing backend
features to replace with speculative code. E's root default switch remains gated
on parity; it can be performed after the unified test phase, with a final-source
rebuild and acceptance if it changes sources. Do not flip it to call construction
finished. No real-model evaluations, version bump, release, commit/push/new-major
chat or daily EXE replacement are authorized by this build-only instruction.

Current deliverable: reusable `scripts/build-candidate.ps1` / `build_candidate.py`
run Vue compiler/Vite, offline wheel+sdist and windowed onedir PyInstaller into a
new `.artifacts/local-build-20261004-01` directory. Existing dependencies only,
no application launch, Vitest/pytest/native/probe/benchmark invocation. Save stage
logs, source/artifact hashes and explicitly untested manifest. The old daily app
must remain byte/size/mtime identical. Keep all native evidence/checkpoints intact.
Unified testing later covers the original source/package/native/default-route/
review/delivery gates; successful construction alone is not v1.0 or N6 acceptance.

### Build-first candidate completed — 2026-10-04

Reusable isolated build entry added; Vue/compiler, offline wheel+sdist and
windowed onedir EXE each exit0. Manifest:
`.artifacts/local-build-20261004-01/build-manifest.json`; report:
`docs/releases/2026-10-04-build-first-candidate.md`. EXE21284555 bytes, SHA
`697b7a8afbba22c2a92fe39b314e915105bf46d918c6f61640aec0416260f612`.
Inputs unchanged during packaging and daily app hashes/sizes/mtimes identical.
No app launch, behavioral tests, native mutation, paid work, default/version
switch, commit/push or completion claim. This finishes the requested current
build work; the full objective and all deferred unified-test gates remain open.
Next activity follows Lv's instruction: unified testing when requested, not
another idle native restart or repeated deletion-confirmation request.

### Construction closed; whole-major-version testing begins — 2026-10-04

Latest instruction permits candidate E source switch before the deferred test
phase, but not release before parity. Hybrid root now redirects307 to Vue;
legacy routes/data/standalone console and API-only behavior remain explicit.
Bilingual README, CHANGELOG and technical report distinguish new implementation
from historical release results. New route and build-entry regressions are written.
Candidate02 build Vue/wheel/sdist/windowed EXE exits0, EXE SHA
`939dc0ba6fc9986e0ce745d1a46738c727cda24acbe0c7a39d934529e5d01b06`;
daily app unchanged. See `2026-10-04-local-mode-construction-closure.md` report.

The construction boundary is now reached; follow objective item3 by running one
major-version unified test phase. Fresh `g-local-20261004-03` retains original25
gates with new root/build-safety coverage and scripts lint/compile, loopback-only
Python network guard, offline uv lock and separate failure logs. Tests and native
acceptance are not claimed before their actual outcomes. Keep all source inputs
frozen during the batch; any repair creates a new non-overwriting attempt.
N1–N4 native/package rows and N6 review/delivery remain after the source batch;
no paid calls, version/tag/daily replacement/commit/push/new-major chat yet.

### Actual chat-branch boundary clarified — 2026-10-04

The updated objective explicitly requires a real new chat branch at each finished
major version, not a summary in this window; keep branching infrequent. Use the
app's `fork_thread` at the accepted major-version boundary, not `create_thread`
for a miniature task and not an in-place recap. Record its actual returned child
identity, hand off the next authorized phase and stop concurrent parent checkout
edits. Current consolidated source/native/package/review/delivery remain in this
branch until that boundary. Do not fork during the running test batch or discard
its live session44492 merely because the objective wording changed.

Progress observed in attempt03: full Python suite742 passed/2 unavailable-symlink
skips/1 dependency warning; Ruff including scripts, compile including scripts and
offline lock passed. Lifecycle repeats are still running with actual OS identities.
This is intermediate fresh evidence, not complete25/25, native or release proof.

### Fresh whole-source batch and package cycle terminal — 2026-10-04

Session44492 completed25/25 expected outcomes, including the deliberate full-matrix
refusal exit2. Python742/2 host-symlink skips, frontend59/7 files, factory81/81 with99
exclusions, service126/126 with54 exclusions, and fault10/10 are fresh attempt03
results. Source/input/daily inventories stayed unchanged; the Python guard had
no unexpected external connection attempts.

Fresh `.artifacts/g-local-20261004-03` packaging completed11/11 steps: offline
wheel/sdist, byte-identical rebuilt wheel, external noneditable three-mode smoke,
89 applicable locked dependencies, and PyInstaller/frozen-code/assets verification.
EXE SHA `7256411e8823b3e353a9515a7dc2a50fa5653db867da4db7cff495c578959f30`.
Both sessions are terminal; do not restart them as running jobs. See
`docs/releases/2026-10-04-final-source-package-candidate.md` for receipts and
remaining N1–N6 boundaries. No native launch/delete, daily replacement, paid work,
commit/push or major-version fork occurred. Native acceptance must use this exact
new package; previous EXE proof is not transferable.
