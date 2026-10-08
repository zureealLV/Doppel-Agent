# Local product parity contract (2026-10-03)

Authority: the A–H continuation in `2026-10-03-v1-local-completion-before-evaluation.md`.
Baseline inspected: `952a03b7d9dda78dc0f2188e32a620279f1211c2`, version 0.14.4.
This is an implementation contract, not acceptance or a release. Paid calls: zero.

Current execution: Lv resumed verification on2026-10-04 with “如果受限了 就验证吧”.
See [current stage handoff](2026-10-04-current-stage-handoff.md). Historical evidence
below stays attributable to its original source/package; it does not accept the
final candidate's remaining native rows.

## Ownership decisions (A)

- Legacy console owns `conversations.sqlite3`, its groups/messages and old run/task/approval APIs.
  Existing settings remain in `provider-settings.json`; do not import, rewrite or decrypt them during development.
- Native product conversations live in `runtime.sqlite3` under `native_*` tables. This is
  deliberately separate from Legacy history: it permits atomic run acceptance, ownership,
  user-message insertion, status/completion projection and restart recovery in one DB.
- A conversation ID is a 32-character lowercase UUID hex, distinct from its current
  checkpoint `thread_id` (`native-<uuid>`). Thread IDs are never reused; an unsuccessful
  segment rotates to a fresh ID. Runtime mode is immutable. Changing mode means
  creating a new conversation; no Graph/Deep state translation or ID adoption.
- Run ID identifies one submitted turn; idempotency key identifies its exact original
  request (including explicit grants). One queued/running/interrupted turn per conversation.
  Interrupted turns retain ownership until resumed, cancelled or expired. No new-turn
  submission overwrites a pending checkpoint. Child IDs remain parent-run scoped.
- A durable turn lease remains held while a completion write is draining, even when
  status already reads completed. Cancellation cannot let a concurrent next turn inherit
  a checkpoint whose terminal result is still uncertain. Release is the final owned IO;
  cancellation after that point keeps the already committed outcome, rather than rewriting
  an old turn after a new one has acquired ownership.
- Profile defaults may change only while idle. Each accepted run snapshots the actual
  provider/model/endpoint without credentials; approval resume uses that snapshot. Settings
  viewing/selection never probes a provider. Explicit permissions are per-run, never inherited.
- Graph must append the new prompt to its authoritative checkpoint messages; Deep already
  uses its messages reducer. Neither replays UI projection history into a populated native
  checkpoint. Legacy v1 uses only completed conversation pairs (no checkpoint claim).
- Failed/cancelled/expired native turns may leave a partial checkpoint. The next accepted
  turn rotates to a new checkpoint thread and bootstraps completed user/assistant pairs only.
  Never blindly rerun tool effects or fabricate an assistant success from a failed turn.
- Projection is idempotent by `(run_id, role)`. Runtime records/events/checkpoints are
  authoritative. Reconciliation reconstructs missing user/completed assistant projections.
- Deletion is blocked while active. Visible history deletion tombstones the conversation;
  immutable runtime audit/side-effect ledgers are retained and IDs never reused.
  F now bounds quiescent checkpoint snapshots to32 per namespace and expires
  obsolete registered threads after30 days; pending/active/leased/unknown threads
  and current visible conversation threads are protected from whole-thread expiry.
  This is checkpoint retention, not physical/secure erasure or audit deletion.
- Old standalone runtime runs remain inspectable, not retroactively relabeled as conversations.
  The isolated hybrid candidate root now redirects to Vue; promotion/release still
  requires D/E/G parity and real packaged-window acceptance. Standalone Legacy
  console and the explicit compatibility entry remain separate and preserved.

## Product rows and named closure gates

| ID | Existing source / evidence | Gap and closure |
|---|---|---|
| P01 | `conversations.py`, `web/server.py`, `tests/test_web.py` | Preserve Legacy create/list/detail/messages/groups/settings; B old-store isolation test |
| P02 | `runtime/service.py`, `api/routes/runs.py`, `tests/api/test_runs_api.py` | Native create/draft/mode binding/list/detail; B/C `test_native_conversations*` |
| P03 | Legacy title/body search includes archives | Native search/rename/archive/group CRUD; B/C store and API tests; D navigation |
| P04 | `SettingsStore.public`, Vue `ModelSettings.vue` | Native per-conversation defaults + credential-free accepted snapshot; C tests, D selection |
| P05 | Graph replaces messages; Deep reducer appends | Durable two-turn Graph/Deep scripted answer dependent on first; C reconstruction and exact counts |
| P06 | v1 idempotency + atomic approval claim tests | Atomic run ownership/message association, conflict rejection, retry/reconcile/crash windows; B/C |
| P07 | `tests/test_service_approval_concurrency.py`, `test_service_cancellation.py` | Native success/reject/edit/cancel/timeout/queue failure/restart projections; C |
| P08 | `tests/api/test_sse_resume.py`, durable events | Native owned events/approval/cancel endpoints; Last-Event-ID recovery; C/D |
| P09 | Native `ConversationWorkspace`, separate `LegacyConversationWorkspace`, owned API/controller; new workspace-owned ID selection | Selection schema/API/service/Vue plus14 regressions written, not run or packaged; final-package restore/ownership acceptance remains open |
| P10 | Native history/manage/search/settings/groups | D implemented; E native rename dialog verified in browser/API/store; visible deletion GUI remains G; old D prompt limitation retained as provenance |
| P11 | Shared `RunController` / `RunInspector` and existing typed panels | D browser Graph/Deep approval/edit/diff/actual verification/child flows; measured Graph events; actual Skill/MCP GUI remains G |
| P12 | Enter/Shift Enter, Ctrl K, arrows, safe Markdown, review template | D keyboard/archive navigation and 1280/320 browser evidence; actual EXE/scaling/focus/scroll acceptance G |
| P13 | Accessible adjustable widths; actual queue/wall time/usage/tool/approval data; Legacy preserved | D inspector evidence; E host-only window bridge and drag-region implemented; actual native controls/default switch parity G remains open |
| P14 | Hybrid candidate root307 to `/runtime/`; explicit Legacy preserved; standalone console remains Legacy, API-only root404 | Attempt03 source routing regressions passed; final-package native default/compatibility/data acceptance deferred |
| P15 | Fresh attempt03 wheel/sdist/noneditable/windowed onedir EXE, package cycle11/11 | Build/archive/install proof exists; final-package destructive/native/process/DB closure remains G, deferred |

## Original Local Mode requirement audit (F closure checklist)

| Requirement | Existing implementation / tests | Fresh gate / remaining gap |
|---|---|---|
| Same API, three runtimes | `runtime/factory.py`, `tests/runtime`, `tests/api` | G rerun; Legacy intentionally lacks native resume/cancel |
| SQLite persistent interrupt + idempotent tool ledger | `persistence/checkpoints.py`, `tool_ledger.py`, `tests/integration`, approval concurrency | C native association; F OS owner, root/child retention, terminal-IO/tool-worker drain and actual cancelled-effect receipts implemented/tested; G repeated lifecycle/package |
| Diff-first/conflicts/verification | `workspace/patching.py`, `verification.py`, patch fixtures | C native edit/reject; D exact display; G seeded cases |
| Progressive Skills and safe paths | `skills/registry.py`, `resolver.py`, `tests/skills` | F v1 skill-list/reload API implemented, atomic validation; G real Skill UI/native tests |
| MCP transports/pagination/typed content/is_error | `mcp/*`, `tests/mcp`, runtime MCP fixtures | F explicit server/catalog/probe APIs + bounded discovery; G recalculate local-only lifecycle denominator |
| Queue/semaphores/cancel/deadline/read-write locks | `concurrency/*`, service cancellation, load/fault tests | C per-conversation lease; F total segment deadlines, aggregate cross-turn/fallback retry scope, probe-slot cleanup and atomic child generations implemented/tested; G fresh pressure/fault |
| Durable SSE reconnect | `api/sse.py`, `tests/api/test_sse_resume.py` | C Last-Event-ID; D stale-stream/reconnect |
| Windows process-tree termination | `workspace/process_supervisor.py`, process fixtures | G actual new package cleanup, Windows tests |
| Fixed20×3×3 protocol | `bench/cases/runtime`, protocol/capability tests | Preserve denominator180 and full guard; no paid quality claims |
| Evidence and CI | prior reports / exact-HEAD CI capture | H fresh docs/review/one batch commit+push+exact CI |

Intentional exclusions remain in `bench/cases/runtime/capabilities.json`: Legacy native
checkpoint/resume, reviewed multi-file patch, MCP gateway/cancel; Deep standalone arbitrary
commands; factory-specific injections. Do not change supported flags without production tests.
v1.1 PostgreSQL/Redis/Celery/Kubernetes and real-model/human judging are out of this implementation phase.

## D evidence checkpoint

See [native Vue progress](../releases/2026-10-03-native-vue-workspace-progress.md)
for fresh tests, actual browser/API/store evidence, retained failures and untested
native UI boundaries. D implements P09–P13's Vue seams without declaring the whole
parity table accepted. Default root remains Legacy; E/F/G/H, packaging and batch
delivery are still pending. No paid-model evidence or expanded capability claims.

## E compatibility preparation checkpoint (2026-10-04)

See [E report](../releases/2026-10-04-default-ui-compatibility-progress.md).
P10 native group rename browser gap is closed. P13 bridge implementation and P14
compatibility preparation are verified at their named unit/browser/source seams;
actual native window parity and default replacement are **not** passed. Continue F,
then G (including the deferred conditional E switch), then H. All A–H work remains
one uncommitted batch, version0.14.4/no daily EXE replacement/no paid evaluation.

## F source/contract audit checkpoint (2026-10-04)

See [F checked DoD, repairs and failure provenance](../releases/2026-10-04-local-mode-dod-audit.md).
Fresh unchanged-source **735 Python passed,2 skipped,1 warning**; frontend **56
passed/7 files**; all12 F source/static gates passed (full guard expected exit2).
Source inventory `5cc16169a24553b41a1b59c257169aa1f8e870afe1beee78d0c44792d0129a68`.
Owner/retention/catalog/deadline/retry/circuit/child/tool-IO gaps are implemented
and covered by the accepted source suite. This is not acceptance of P10–P15's
remaining native visual/package/default-route gates or a whole-v1 release.
Continue **G**, including the carried conditional E switch and any resulting
refreeze, then H. Original180/capability exclusions/paid guard are unchanged;
daily EXE/root Legacy/version0.14.4 remain untouched; no commit/push yet.
