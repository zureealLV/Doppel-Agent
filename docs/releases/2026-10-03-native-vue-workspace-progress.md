# Native Vue workspace — Task D progress and evidence (2026-10-03)

Authority: [the A–H continuation](../plans/2026-10-03-v1-local-completion-before-evaluation.md)
and [the parity contract](../plans/2026-10-03-local-product-parity.md).
Baseline HEAD: `952a03b7d9dda78dc0f2188e32a620279f1211c2`; version remains **0.14.4**.
This closes D's implementation increment, not E–H, final product parity, or a release.
The entire A–H working batch remains **uncommitted and unpushed**. No tag, paid
evaluation, account/ledger change, real-key access, or daily EXE replacement occurred.

## Implemented

- `/runtime/` now offers **Native conversations / Standalone Runtime / Legacy history**.
  Native actions use `/api/v1/conversations`; the original Legacy component and
  `conversations.sqlite3` APIs remain separate. Root `/` still serves Legacy.
- Native Graph/Deep/Legacy drafts bind an immutable runtime. Native history, title,
  groups, archive/search (including archived results), per-conversation profile
  defaults, message-to-run navigation and selected historical run inspection are wired.
  Existing settings remain shared; viewing/selection does not call the provider probe.
- Native selection/run restoration has isolated local-storage IDs, no credentials or
  prompt persistence. Archive/interrupted reopening, durable acceptance, stable retry
  idempotency, active leases and cancelled/failed projections retain backend authority.
- A shared `RunController` and `RunInspector` now serve both native and standalone
  runs: ownership-scoped retrieval/SSE/cancel/resume, event-sequence deduplication,
  reconnect cursor, stale-response/stream disposal and duplicate decision protection.
- One inspector shows approvals (approve/reject/edit/cancel), actual applied diff,
  model output, Graph nodes, typed lifecycle/Skill/MCP/patch/child events, bounded
  child creation/follow-up/cancellation, queue and wall-clock timing, reported usage
  and actual configured verification receipts. A Deep fallback is explicitly labeled
  as Graph, **not** native Deep success. Missing usage is not zero cost; wall time
  includes pauses and does not pretend to be pure model/tool latency.
- Actual Graph node/model/tool events are emitted at execution boundaries. Edited
  approval proposals and patch receipts carry the actual applied unified diff;
  Deep receipts preserve full tool content instead of a truncated representation.
- Three-pane responsive layout, accessible width controls, wrapping permission labels,
  scrollable dialogs, focus states and the terminal-without-answer state are fixed.
  Native DOM IDs no longer collide with the preserved Legacy component. Safe Markdown,
  Ctrl K, search arrows/Enter, Enter/Shift Enter and IME send guards remain.

## Fresh automated evidence

Ignored local evidence root: `.bench-results/local-native-20261003/`.

| Gate | Actual result | Evidence |
|---|---|---|
| Frozen full Python suite | **671 passed, 2 skipped, 1 warning**, 361.16 s | `full-d04.log` |
| Source stability | before/after full suite and final frontend rebuild identical | `final-d04-source-{before,after}.json`, `final-d-final-source.json` |
| Frontend behavioral/SSR tests | **48 passed / 5 files**, Vitest 3.2.7 | `frontend-d-final.log` |
| Typecheck | pass | `frontend-typecheck-d-final.log` |
| Production build | pass, Vite 7.3.6 | `frontend-build-d-final.log` |
| Ruff / whitespace | pass | `ruff-d-final.log`, `diff-check-d-final.log` |
| Focused native lifecycle/node tests | **32 passed, 1 warning** | `python-d03.log` |
| Browser API/store cross-check | exact run ownership, unique seq, one user projection per turn, completed-only assistant projection; at most one decision per seeded interrupt | `browser-api-summary.json`, `browser-d03/api-evidence.json`, `browser-d04/api-evidence.json` |

Final source inventory SHA-256:
`338c5e6c95b63f21d0e51e18e8159b88b998e799acb6210fd80ff3c18eca6e1c`.
This hashes dirty source, fixtures/tests and generated frontend assets with tracked
asset deletions explicitly bound. Documentation and ignored QA files are outside
that inventory; it is trusted-harness provenance, not a clean commit or OS attestation.
Final assets: `index-CRmkhIz8.css` and `index-BSrf02w_.js`.

The two skips are host-unavailable symlink creation (`test_deep_backend.py:38`,
`test_runtime_review_inputs.py:49`). The warning is Starlette's deprecated AnyIO
`BlockingPortal` alias; neither is silently counted as a pass.

New frontend coverage includes native restore, archive/interruption, empty/error/retry,
search/list/selection/send races, profile rollback/deleted-profile rejection, durable
accepted-run read failures, duplicate submission and stable idempotency; controller
ownership/stale actions, reconnect cursor, foreign-event filtering, duplicate resume,
child grants and disposal. SSR tests alone are not browser acceptance.

## Actual browser interactions — isolated, scripted, no paid model

The Codex in-app browser exercised fresh loopback apps with disposable workspaces
`browser-d*`. Mock profiles were seeded from scratch. External sync/async compatible
provider entry points were made to fail; the native service received an explicit
scripted provider. No real settings, saved keys or external model endpoint was used.
These tests prove product plumbing, **not** model quality or provider spending caps.

- Actual native Graph two-turn send, Shift Enter newline, Enter submission and refresh
  retained four messages/two turns without duplicates. Deep interrupted refresh/resume
  restored its pending approval. Settings viewing/closing did not start a turn.
- Profile selection, group creation/association, conversation rename/archive and
  Ctrl K + arrow/Enter archived-result navigation were observed (`browser-d/`).
- Graph approve/reject/edit, invalid edited JSON, restored applied edit diff, Deep
  approve, running-refresh/cancel and completed-only assistant projection were observed
  and cross-checked against durable events/store (`browser-d03/`). Applied effects were
  checked as exact bytes. A cancelled turn retained its user message but no invented
  assistant output.
- Graph and **native Deep** configured verification actually executed the seeded
  shell-free Python assertion: exit code **0**, stdout `offline verification passed`,
  Windows `job_object` supervision; measured command durations **140 / 156 ms**.
  UI displayed the real receipt and exact diff (`browser-d04/{graph,deep}-verification.txt`).
- A separate unchanged-patch Deep control fell back to Graph. Its explicit UI label
  and durable `fallback_runtime=graph` were checked (`honest-fallback.txt`); it is
  excluded from the native Deep success above.
- Actual child creation, completed follow-up, then cancellation of generation 3
  retained two completed history pairs (`subagent-followup-cancel.txt`, API evidence).
- Legacy historical messages remained distinct; duplicate DOM IDs were absent.
  Root compatibility navigation was actually checked (`default-legacy.txt`).
- At **1280×800**, the three panes fit client/body width **1265 px** with no root
  horizontal overflow. At **320 px** outer width, the settled client/body/root width
  was **305 px**, also without root overflow (`native-1280-final.png`,
  `native-320-final.png`). These are browser viewports, not native desktop/DPI tests.

Group rename uses the retained JavaScript prompt; the in-app browser did not expose
an active prompt, so **browser group rename is not passed**. Group CRUD has backend
coverage, but its actual native-window interaction belongs to E/G. Visible-history
deletion was not clicked in browser; its tombstone/audit-retention behavior is covered
by API/store tests. Actual Skill/MCP runtime browsing is not added to the browser
acceptance count; typed inspector rendering is implemented and local matrices remain G.

QA03 and QA04 were gracefully shut down via their fixture-only control endpoint;
both launchers exited **0**, listeners closed, owned QA helper processes were absent.
Every disposable SQLite DB passed `integrity_check`, exclusive reopening and WAL
checkpoint checks (`d-result.json`, `helper-process-closure.json`). This is source
server cleanup, **not** the new packaged EXE's native process-tree closure gate.

## Failures retained and repairs

- Early focused tests exposed incorrect event-store access in the added test and an
  async patch inserted into a sync method; repaired before acceptance, logs retained.
- Browser QA found stale original diff after a successful edited approval, duplicate
  native/Legacy IDs, a cancelled-run skeleton and 320 px scrollbar overflow. Fixed and
  revalidated; the final full suite used unchanged source.
- QA03's first seeded Graph approval hit a real stale-file conflict after a fixture
  reset accidentally wrote CRLF. It did **not** apply a patch despite the scripted
  final answer. The failure receipt is retained; a fresh exact-LF Graph turn produced
  one real applied effect. No conflict/verification assertion was weakened.
- `full-d01.log` passed 671 tests on an earlier frozen snapshot. `full-d02.log` had
  one offline aggregate-fixture failure while source changed and is **not acceptance**;
  no sole cause is asserted. `full-d03.log` was stopped to freeze the EOF correction.
  Only `full-d04.log` plus its unchanged hashes is final D acceptance.

## Next settled work — E, F, G, H

1. **E:** audit default/desktop parity, compatibility routes and controls; do not
   switch the daily/default UI before the required real packaged-window parity gate.
2. **F:** finish the Local Mode inventory and named gaps, including single-workspace
   startup ownership and explicit audit/checkpoint retention policy.
3. **G:** refreeze and rerun the consolidated gates/matrices, build isolated wheel,
   sdist and EXE, exercise real windows/scaling and process/DB closure. D's browser
   screenshots and old EXE evidence do not substitute for these gates.
4. **H:** task-scoped review, fresh documentation, one batch commit/push, remote HEAD
   and exact-commit CI verification. No small-version push was made at this checkpoint.

The original matrix remains **180**, with factory **81 supported / 99 excluded**
and service **126 / 54**; D did not rerun or expand that matrix. Real-model evaluation,
human judgment and the guarded total ¥10 phase remain separate and unexecuted.
