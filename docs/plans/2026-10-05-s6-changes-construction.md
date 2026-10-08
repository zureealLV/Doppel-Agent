# S6 — Changes / Git / verification construction

Status: 2026-10-05, A1/A2a/A2b/B1/B2a/B2b1/B2b2a/B2b2b/C1/C2a/C2b/C2c1/C2c2/C3/D1a/D1b/D2/D3a/D3b/D4a/D4b source and definitions written; next S7 construction.
Source construction only; Goal active, version 0.14.4.
Extends S6 of the next-major product vision, not a replacement roadmap.
S5 construction is written, not accepted. All execution/verification remains S9.

## Contract and sequential slices

S6 C construction detail (sequential; source/definitions only until S9):

- **C1 immutable reviewed command foundation:** bounded UTF-8/no-link config
  snapshot, strict command/argv/timeout/output schema, workspace/config identity
  and hash provenance, exact selected-name plan fingerprint. Preview executes
  nothing; reviewed execution rejects changed config/identity/plan before every
  command, uses the existing supervisor/environment and never invents test proof.
  Files: new workspace/verification_config.py, existing workspace/verification.py,
  new tests/workspace/test_verification_review.py.
- **C2 durable owned verification operations:** persist pending review/decision,
  run/patch/manual-operation provenance and actual outcome independently of patch
  effects; existing owner/lease/write-lock/resource/process barriers. Approval
  pins exact argv/config/selection, no model-supplied arbitrary argv, implicit
  auto-approval or command-grant inference. Unknown/replay never launches again.
  Product adapters must show actual verification preview in HITL or separate
  explicit verification review; grant alone is not config review. Keep direct
  console compatibility distinguishable, never label it operator-reviewed.
- **C3 service/query and lifecycle provenance:** scoped read-only evidence,
  cancellation/timeout/partial results versus patch success, explicit command
  limitations and sensitive-output handling, actual runtime fixture definitions.
  HTTP/ChangesWorkspace transport remains D; native acceptance remains S9.

C3 construction sequence (this turn): write query/lifecycle regression definitions,
then an existing-ledger SQL-read-only reader and owned service summary/detail
methods, then manual-terminal bounded maintenance and source review. No store
constructor, TTL update, event emission, config/project reads or execution/recovery
on a query. Queries require an already-held service owner, including a failed-close
diagnostic lifetime; they must never call start or reopen admission. Close must
join the original query worker before releasing owner. Keyset pages are source-run
scoped, <=16 rows and <=8 MiB aggregate decoded evidence, with no argv/output in
summaries. Detail exposes sensitive bounded output with explicit presentation and
completion limitations. Missing/unsafe/corrupt evidence fails closed.

Read-only here means SQLite mode=ro plus query_only, SELECT/read transactions and
no application business/schema writes. SQLite WAL readers still participate in
shared-memory coordination and may create/use sidecars; this is NOT a zero-write
filesystem guarantee. Do not use immutable=1 on live ledgers or copy a main DB
without its WAL to pretend otherwise. See [SQLite WAL read-only rules](https://sqlite.org/wal.html).
No physical sandbox or cross-database atomic snapshot is claimed. S9 definitions
must exercise a live WAL writer as well as missing/malformed existing metadata.

C2 implementation order: C2a adds strict stored-plan/result validation, durable
manual review/step intents/results and explicit owner-scoped service decisions;
C2b wires Graph/Deep/Legacy product policy to that review path (remove implicit
post-patch auto commands, preserve direct console compatibility); C2c covers
reconstruction, unknown metadata reconciliation and remaining process/admission
limits. C2a alone is not full product command-review parity or accepted safety.

C2c sequential implementation: **C2c1** adds metadata-only scoped reconstruction
and bounded existing-ledger maintenance (no schema creation/workspace file IO or
command recovery), explicit pending/live-operation cancellation and close-time
manual-task drain/admission fences. Only full actual sealed result prefixes may
reconcile to completed; missing/unsealed results remain indeterminate, never
absence/retry proof. **C2c2** then replaces reviewed text spool/weak Job fallback
with strict bounded original-supervisor transport, includes descendant/parent-exit
drain and native fixture definitions. C2c1 alone must not claim strict process
safety or whole C2 completion. All definitions remain unexecuted until S9.

C2c2 source contract: reviewed VerificationPipeline uses original run_binary with
require_tree_ownership=True (no text spool/fallback); direct run compatibility
continues text transport. Exact byte caps/deadline include inherited pipes;
overflow/timeout/admission failures seal stable failed-attempt evidence, never
truncated success. Windows strict cleanup must terminate remaining Job members
and query ActiveProcesses==0, even if parent exited and descendants closed pipes,
before unregister/owner release. Cleanup failure retains registration/Job and
quarantines further command/product admission; no sealed successful result.
Regressions first: fake transport/provenance/errors, gated drain/quarantine,
real reviewed Windows attach failure/overflow/timeout/closed-pipe descendant gates.
Source-only now; OS observations/N1–N6 and full acceptance remain S9.

### D4b source record — actual-engine HTTP/lifecycle fixtures (written, not executed)

New tests/api/test_changes_engine_integration.py defines actual Graph/Deep/reviewed
RunService Legacy -> HTTP create/resume -> two-file applied receipt -> independent
inverse/manual configured verification paths, in disposable fixture roots. Explicit
scripted model and binary supervisor, no provider socket or native process launch;
no direct runs.create/ledger.execute_patch_once or manufactured applied receipts.
Original command grant does not launch post-patch verification. Fresh separate
prepare/decision grants, exact argv/IDs, sealed server replay, inverse restore/delete
and later-user-edit preservation do not overwrite original patch/run outcomes.

Definitions extend existing C2b actual-engine receipt coverage, not replace the
runtime or create a second fixture executor. Source inspection found a definition
drift in tests/api/test_patch_effect_receipts.py: its intended-valid command config
omitted current mandatory timeout_seconds. Valid branch/fresh-review config now
include 10 seconds; malformed branch remains malformed. No test was executed.

Additional integrated definitions: close/reconstruct stored review/history from
an already-held new owner, original config/TTL/preview retained despite current
invalid config/user edits; GET refuses store construction/config/inverse reprepare,
adds no event/command/admission and never reopens a closed owner. Independent second
project with its own actual source/reviews rejects first-project scope. Summaries
omit argv/output; review-ID keyset/order/SQL ro versus physical WAL limitations stay
explicit. Interrupted pre-effect reads do not create a ledger merely to return
fake empty success. Future test IO touches only pytest disposable roots.

Effect-then-error reply injection occurs AFTER original durable prepare/decision,
not a forwarding mock: exact prepare ID/selection/config/TTL replay versus consumed
decision/read/replay without second command. This is NOT real socket-disconnect/UI
proof. Two-command first-failure prefix preserves planned/sealed/remaining counts,
source patch and original run. Original ASGI approval task with explicit cancel OR
close uses a gated fake binary supervisor; pending drain holds original owner/write
barrier, original tasks/control/worker join, unknown cancelled step remains unknown
through restart and refuses repeat. Fake active_count is NOT observed OS drain.

New docs/plans/2026-10-05-s6-native-acceptance-definitions.md adds concrete pending
N1 Changes entry/history/restart/scope, N2 patch/inverse/user-conflict/separate argv/
stale/prefix/ACK cases, N3 extension separation, N4 metadata authority/read freezes/
original Job/cancel/close/quarantine/DB release, and inherited N5–N6 final package/
default/one-batch delivery gates. Full source/input/package/process/window/origin/
oracle/IDs/ledger/events/UI/Job/owner evidence is required. Original unfinished
deletion/adjacent-history/other N rows and historical failure evidence remain;
mock/SSR/synthetic child/ASGI definitions do not pass native rows. No pass prewritten.

D4b source/definitions are written; S6 is NOT verified/accepted. NEXT S7 extension
center and parent/child task experience using original catalogs/MCP/subagent services,
then S8 sanitized reports/usage/lifecycle, S9 frozen-source whole-version tests,
isolated packaging and native N1–N6, S10 batch audit/commit/push/exact remote SHA/CI/
delivery. Goal active, version 0.14.4, full S0–S10 unchanged. No tests/lint/typecheck/
compile/build/package/Git/native/browser/process/provider/user DB/catalog/key/balance
operations, daily EXE replacement, new pass counts, promotion or chat fork.

### Previous D4a source record — mounted Changes lifecycle (written, not executed)

Regression definitions were written before source fixes. New
frontend/src/changesWorkspace.mounted.test.ts mounts the real compiled
ChangesWorkspace, panels and original controllers with a test-only Vue
createRenderer host (frontend/src/testSupport/virtualHost.ts). API/native replies
are scripted promises, not actual HTTP or OS observations. No new dependencies,
lockfile/runtime changes or test execution. Existing changesEntries.test.ts adds
static guards for original root freeze, unkeyed Changes lifetime and validated
host-URL full navigation. These guards do not mount or exercise the root/browser.

An initial-ready race was suspected but further controller source inspection
REFUTED it: native() already checks canRead()/state.active and state.reading,
so the original initial ready() cannot launch a native read before activation.
No failed test or established race is claimed. ready() now additionally expresses
those active/busy guards at the component boundary, avoiding redundant requests;
definitions guard original Git -> source patches -> native descriptor ordering
and late bridge-ready while reading. Descriptor reading is not a grant; no chooser/
authorize/revoke is automatic. The substantive source gap addressed is blocked
lifecycle/source freeze, not a fabricated initial-read bug.

Changes active state now includes !blocked, fencing original read presentation
and activation continuations during project-switch/close freeze. Review panels
disarm/consume old checkbox inputs on freeze; no new execution permissions are
inferred after resume. Source props also respect blocked and original review
locks; a deferred scope is adopted only after unblock without an unknown/live
operation. Unblock does not reset a same-source review or race another patch read
against the activation chain. Original mutation/native/read promises and nested
close epochs remain owned, retained and joined. Abort/unmount is not server drain.

Unexecuted mounted definitions cover initially ready and late-ready hosts,
blocked mount/resumption, late read and source-replaced evidence fences, actual
framed evidence feeding two independent review sources, refusal of exported
private/wrong-scope evidence, synthetic model-input fresh grants/consent reset,
lost approve keeping original IDs/body/source and cross-panel/Git/native locks,
inactive reentry with no automatic polling/retry/grant, live cancel reaching the
original approval with close joining both promises, superseded nested preparation
not reaching late barriers, and fresh project component memory without inherited
review/grants/descriptor or late old prepare publication. Fixture sealed results
are not executed commands/model quality/project acceptance. Synthetic host has no
HTML parser/native event dispatch/layout/focus/keyboard/a11y/escaping guarantee;
existing SSR escaping definitions and S9 native/browser gates remain separate.

D4a is NOT full D4/S6 acceptance. NEXT D4b: actual scripted Graph/Deep/RunService
Legacy engines producing durable patch receipts, scoped HTTP review/history and
original operation/close/switch fixture definitions, and explicit native N1–N6
acceptance extensions. Legacy console remains a distinct non-converted surface.
Then S7/S8, S9 full frozen-source unified verification/isolated packaging/native
acceptance, S10 whole batch review/commit/push/exact remote HEAD/CI/delivery.
Goal active, 0.14.4; no tests/lint/typecheck/compile/build/package/Git/native/browser/
process/provider/user DB/catalog/key/balance access, daily EXE replacement, new
pass counts, promotion or chat fork. Full original S0–S10 goal unchanged.

### Previous D3b source record — configured verification UI (written, not executed)

Regression definitions preceded implementation. New frontend/src files:
verificationTypes.ts, verificationApi.ts, verificationFraming.ts,
verificationReview.ts, components/VerificationReviewPanel.vue and unexecuted
verificationReview.test.ts, verificationApi.test.ts, verificationPanel.test.ts.
ChangesWorkspace joins the second controller; styles extended. Inverse controller
and panel definitions/source additionally pin immutable projection/creation/expiry,
refuse consumed-to-pending regressions, and consume old UI checkbox grants on
read/disarm/rearm (including a failed GET). No validation command was executed.

- Only a framed confirmed-applied source receipt can prepare; history independently
  queries the explicit current run. Fixed no-store routes project known fields:
  name selection only (null/all or <=16 exact unique names, UTF-8 <=128 bytes),
  never arbitrary argv/executable/root/config overrides. Prepare needs new command
  and write grants, but runs nothing; approve separately requires exact displayed
  source/review/plan/full argv/config path+hash/snapshot/workspace identity, timeout/
  byte/stop limits, explicit confirmation and NEW command+write grants. Frozen
  known plan is pinned in full; JS cannot reproduce Python's canonical 600.0 hash,
  so it treats plan_id as opaque and leaves cryptographic verification to backend.
  The 600-second execution-stage deadline includes command-resource waiting, NOT
  a hard end-to-end HTTP/preparation/workspace-lock/physical-cleanup time bound.
- GET disarms, cannot refresh config/TTL/admission or execute. Lost prepare retains
  original ID/body/names/grants and permits explicit same-ID prepare-only replay;
  selected-name order is checked on reply AND recovery. Lost approve/reject never
  resends; pending/404 cannot establish absence. Prior consumed views or consumed
  history summaries cannot regress through GET into fresh pending approval.
  Known terminal states do not regress; changed immutable plan/projection refuses.
- Original decision and cancel/control promises are independently retained. Exact
  explicit cancel can reach original live approval while its POST is pending,
  without aborting/retrying that POST; the backend owns actual task/process/write
  barrier drain. A lost control/drain ACK is not erased by a later main completed
  ACK. At most 16 original control intents retained; only explicit metadata-only
  reconcile, not missing-step/config/command recovery. Cancelled with an unsealed
  step remains unknown. An explicit terminal read narrows evidence, not physical
  OS cleanup or whole-project acceptance. No automatic cancel/retry/deadline race.
- Close joins original decision/control/read promises. Timeout resumption does
  not reopen effects early; only exact explicit narrowing cancel may reach a live
  task during resumed draining. Nested close epochs prevent late hooks advancing
  to read/host barriers. Read generations/abort fence stale pre-cancel replies.
  Exit acknowledgement only narrows exit blocking: unknown/IDs/payload remain,
  never new admission/forget/absence proof. Memory-only UI is not cross-restart
  recovery: record exact IDs for manual scoped reads; 404 not non-execution proof.
- Full escaped argv and bounded actual stdout/stderr are sensitive/unredacted,
  not S8 or lossless bytes; no v-html/clipped approval/browser persistence. Each
  result pins planned argv/name, exit/success/error/duration/supervision and strict
  ordered sealed prefix. Planned/attempted/sealed/unstarted/unsealed denominators
  remain distinct. A completed stop-on-failure prefix with success=false is not all
  planned commands running; verification does not overwrite/rollback patch proof.
- History is run-scoped SQL-read-only review-ID keyset, NOT chronological: <=16
  rows/page, <=8 MiB aggregate decoded backend evidence, no argv/output summaries;
  navigation cursor stack <=64 pages, explicit restart from first page possible,
  not a total-record cap. No automatic polling or silent stale-page selection.
  Known projections drop private extras. Query-only/WAL is not zero physical
  filesystem writes, cross-DB atomicity, hard syscall deadline or an OS sandbox.

Changes read/source/native controls and inverse/verification panels mutually lock
while original operations/read/unknown are present. Before possible command apply,
the old Git snapshot invalidates; no automatic Git refresh after verification.
Actual mounted event order, project-scoped remount/recovery, Graph/Deep/Legacy
review entry parity, real HTTP cancellation/owner drain and native behavior remain
D4 definitions plus S9 execution, NOT proven by controller fake/SSR/static tests.
NEXT D4: full source/engine/lifecycle and mounted integration/required native
fixture definitions, then S7/S8, S9 frozen-source unified Python/frontend tests,
isolated packaging and fresh native N1–N6, S10 whole-batch review/commit/push/exact
remote SHA/CI/delivery. D3 source is not accepted S6 or full-version completion.
Goal active, version 0.14.4, full S0–S10 unchanged. No tests/lint/typecheck/compile/
build/package/Git/native/browser/process probes/provider/user DB/catalog/key/
balance access, daily EXE replacement, release promotion/commit/push/fork or new
pass/CI counts. All new/old definitions remain unexecuted until S9.

### Previous D3a source record — exact inverse UI (written, not executed)

Controller regression definitions preceded implementation; no tests, RED/GREEN,
typecheck, build, package, Git commands, native/browser/process observations or
provider/user DB/catalog/key/balance access occurred. New frontend/src files:
inverseTypes.ts, inverseApi.ts, inverseFraming.ts, inverseReview.ts, components/
InverseReviewPanel.vue; inverseReview.test.ts, inverseApi.test.ts and
inversePanel.test.ts are unexecuted definitions. Changes controller/workspace,
App source-entry guard and styles extended. Version stays 0.14.4; Goal active.

Only an actually framed applied source receipt selects run/tool-call/patch IDs.
Prepare and decision require independent explicit confirmation and new write
grants; preview pins exact source/review/inverse-patch IDs, full diff and per-file
base/target hashes/modes. Immutable known projections exclude private preimage
blob fields, but full inverse diff is sensitive and UNREDACTED, not S8 output.
Approval view is not clipped like the historical diff. Frontend framing is not
cryptographic backend proof, streaming heap/network limits or an OS sandbox.

Original non-abortable mutation/payload/ID retained. Lost prepare permits only
explicit exact same-ID/payload prepare replay, no refreshed base/TTL. Lost
approve/reject NEVER resends; GET pending/404 is not non-execution proof. Read-only
recovery disarms approval; stored preview needs explicit reconfirmation plus a
new decision grant, with backend TTL/current-file/source rechecks. Applying or
indeterminate allows explicit metadata-only reconciliation, not patch replay;
failed can include partial effects. Applied inverse is not command verification,
current Git clean or whole-project success. Switching source is blocked while
busy/unknown. Explicit discard hides known UI selection only, not a server erase.

Before possible application, the prior Git snapshot is invalidated. Page exit
fences reads but retains original mutation acknowledgement; close joins original
promises. Nested preparation epochs stop timed-out hooks from advancing a late
read/host barrier. Explicit unknown-exit acknowledgement changes only exit
blocking, not unknown state or effect admission. UI intent is memory-only, not
cross-restart recovery: operator must record exact IDs, and missing records do
not establish absence. Actual original owner/supervisor drain remains backend
responsibility; frontend abort/dispose/exit is not physical cleanup evidence.

NEXT D3b: immutable configured argv/config/plan preview, separate fresh command
and write grants, scoped bounded verification history/detail, original sticky
IDs/payloads, read-only recovery, explicit cancel/join and metadata reconciliation,
partial/unknown results distinct from patch effects. Do not reproduce Python
canonical plan hashes with naive JSON.stringify (600.0 versus 600); pin the exact
server plan/ID and let the existing backend validate its canonical hash. Then D4
mounted/source/actual-engine/lifecycle definitions, S7/S8, S9 unified execution,
isolated package/native N1–N6, S10 whole-batch audit/commit/push/remote SHA/CI.
D3a is NOT full D3 or S6 acceptance and does not narrow the original S0–S10 goal.

### Previous D2 source record (written, not executed)

Regression definitions were written before the new read surface/controller/client;
no RED/GREEN execution, typecheck, build or native observations occurred. New files
under frontend/src: changesTypes.ts, changesApi.ts, changes.ts, components/
ChangesWorkspace.vue and ChangesSnapshot.vue; definitions changes.test.ts,
changesApi.test.ts, changesWorkspace.test.ts and changesEntries.test.ts. Existing
App/WorkbenchShell/navigation/conversation/work-order entries and styles extended;
navigation/shell definitions updated. All paths are within
`D:/Codex Program files/Agent/Doppel-Agent`.

- Fixed no-store status/diff/registered-run patch list/detail transport only.
  Selected diff projects exact admitted path, staged/worktree/combined plane,
  actual conflict stage and mandatory 64-hex fingerprint. No authority root,
  arbitrary argv, mutation/reconcile or provider methods in this read client.
  Status/selected diff framing refuses false cleanliness/agent attribution;
  mismatched/unknown/stale diff does not auto-refresh, change stage or reexecute.
- Actual ref/HEAD/format/capture time, visible rows INCLUDING unchanged records,
  excluded/unknown/coverage limits/raw comparison and Windows mode limitation
  are rendered separately. No fabricated branch/clean or full-project acceptance.
  Excluded count remains protocol records/traversal entries, not secret-file count.
  Empty visible rows do not assert ignored content unchanged. Git reading invokes
  existing fixed plumbing only when the future UI is entered/refreshed; no poll.
- Receipt queries require current-project registered source IDs; actual selected
  native conversation/work-order/standalone IDs are carried by explicit entries.
  Legacy keeps its own route, no state/history conversion. Git rows do NOT join
  path names into inferred Agent authorship. Separate historical receipt/status/
  source/quiescence hints are not fresh approval or current file identity proof.
  Pages are 25 rows, replacement-page presentation, offsets <=100000; full page
  only offers another read, never asserts total. Query failure is not empty-success.
- Sensitive status/diff/receipt/detail stays memory-only, rendered as escaped text,
  never v-html/link execution/browser storage/export. Accepted preimage fields
  refused by receipt framing; historical unified diff may still contain old content.
  Historical display clips at 20000 characters with explicit non-complete label;
  server evidence retained. This is NOT S8 global redaction. Response parsing uses
  existing request()/response.json(); frontend checks are NOT streaming network,
  JSON heap or OS hard budgets. Backend D1 byte/SQL/process boundaries remain.
- Complete pywebview bridge readiness is not authority. Zero-argument native
  authorize/read/revoke delegates actual chooser+confirmation to D1b host; no JS
  path/checkbox bypass or HTTP authority. Scope/lifetime/workspace/root/grant ID
  is a LAST observed native descriptor, not new execution permission. Native
  calls have no effect-timeout race or auto-retry. Lost reply, replacement host,
  stale callback, cancellation without current descriptor or failed read keeps
  authority unknown; only a current native read unlocks grant/revoke again.
  UI does not infer old authority absent from a canceled chooser.
- Generations/AbortController fence late read replies on source/page/close changes;
  original pending promises retained for preparation join. Abort is NOT backend
  command/OS absence or drain proof: original service owner still owns cleanup.
  Non-abortable native promise joined; timeout recovery cannot reopen this panel
  before original reads/native call settle. Close/switch hooks include Changes
  first. Root preparation epoch is checked after EACH await, so a timed-out old
  hook cannot proceed to late-freeze other workspaces or authorize host transition.
  Hash/navigation also frozen during transition. Failed preparation recovery
  clears sensitive presentation; actual owner/host close still requires S9 proof.

Controller/transport/SSR/static wiring definitions cover scoped/out-of-order reads,
conflict/fingerprint, corrupt/extra-preimage/structurally reordered receipt framing,
Git-unavailable with readable ledger, fixed errors, missing/replaced native bridge,
canceled/lost/native pending close and exact zero-argument calls. They are ALL
UNEXECUTED. SSR/static wiring does not prove DOM focus, dialog visibility, actual
host trust or root timeout orchestration. D4 mounted integration/engine fixtures
and S9 real native/HTTP/Git/Unicode/DPI/close/switch/source-preservation remain.

Next D3: separate explicit exact inverse/verification preview+decision UX, immutable
argv/config/source IDs, NEW grants, sticky lost-response payloads/IDs, read-only
recovery, cancellation/unknown/partial evidence; then D4/S7/S8/S9/N1–N6/S10.
Goal active; 0.14.4 unchanged. No tests/lint/typecheck/compile/build/package/Git/
native/browser/process probes/provider/real user DB/catalog/keys/balance operation,
daily EXE replacement, commit/push/promotion/chat fork or new pass/CI counts.
D2 is source construction, not usable-UI/native safety or full S6 acceptance.

### Previous D1b source record (written, not executed)

- WindowApi now exposes zero-path/zero-confirmation-argument native controls:
  git_metadata_authorize, git_metadata_authorization and git_metadata_revoke.
  Authorization requires actual native single-folder chooser THEN native confirmation
  of escaped exact current workspace/root/scope/lifetime. A JS boolean/path, HTTP
  body/query, gitfile pointer or merely choosing a directory cannot grant authority.
  Trusted origin, original window and CURRENT switcher kernel/ready state are checked
  again after both dialogs. Original action lock serializes native actions; no catalog/
  settings/keys/config access or project registration to inspect/change a grant.
- New workspace/git_authorization.py stores frozen directory identity selection
  BEFORE confirmation, including original workspace identity for native callers.
  Refuse relative/root/UNC/device/mapped-network/control/oversized/link/reparse alias
  selection before content IO. AFTER confirmation, capture only current `.git` <=4096,
  exact `commondir` <=128 and `gitdir` backpointer <=4096 bytes. Exact selected common
  root + worktrees entry/backpointer, original directory identity and pointer hashes/
  signatures are bound, including Unicode/space/non-.git bare-root names. No HEAD/
  index/objects/config/hooks/provider/Git or user business DB reads during granting.
- A SINGLE ephemeral exact binding is held by original RunService, with receipt
  grant_id/workspace/root/scope/persistent=false/command_or_write_grant=false.
  Not saved to workspace/catalog; reconstruction/restored/new project kernel starts
  empty. Actual inspection may read required refs/index/objects through the prior
  fixed sanitized private Git view; no arbitrary argv/Git writes or new runtime.
  Service prechecks binding on reads and actual GitMetadataReader `_discover` also
  compares original marker BEFORE external pointer opens, exact worktree/root/directory
  identity and both pointer files against bound signatures. A second valid worktree
  under SAME common root cannot inherit authority in the precheck-to-entry interval.
  Directory content churn is allowed; directory replacement/pointer changes invalidate
  and clear authority rather than silently recapture. Scope is not inferred from name.
- Grant replace/revoke uses original writer-preferring workspace barrier, no command
  slot. Successful revoke means original Git read/private cleanup drained first, not
  abrupt OS preemption. Revocation under quarantine is narrowing-only with already-
  held started owner; lifecycle BEFORE workspace write avoids close/operation deadlock.
  It never calls start/recovery/resources or resets quarantine. Original close clears
  authority before joining and on failed/startup cleanup; existing entered readers
  still drain, no new Git admission. Pointer invalidity never becomes empty-clean.
- DesktopKernel dispatches fixed private operations onto original captured API lifespan
  loop/thread via run_coroutine_threadsafe, never foreign asyncio.run/new executor/
  HTTP path grant. Same-loop blocking, owner loss/not-ready/closing/absent loop refuse.
  Native waits join original operation; 0.2s Future polls are NOT effect timeouts/retry
  triggers. Actual ended loop fences kernel/cancels lost dispatch without restarting,
  releasing owner or claiming operation absence. Completed TimeoutError distinguished
  from pending poll. Kernel close/start fences and original owner lifecycle retained.
- Application checks remain NOT a hostile filesystem actor sandbox or hard syscall/
  owner-loop/JSON deadline. Checked 10s pointer/directory budgets do not preempt IO;
  path admission/resolve calls can precede those checks. Native confirmation is a new
  REQUIRED S9 GUI gate, not proved by fake windows or ordinary API definitions.
- New synthetic pointer/bridge/owned service definitions cover no content before consent,
  wrong common root without following pointer, post-confirm root/workspace replacement,
  pointer/backpointer/directory changes, common churn, exact native True/cancel/origin/
  window/kernel/close/busy, no catalog/HTTP authority, original lifespan-loop dispatch,
  binding passed to inspector, unchanged admission under quarantine revoke, writer drain,
  reconstruction/close reset and repeated-cancel metadata-worker drain/no late publish.
  Actual SHA-1/SHA-256 x files/reftable Git integration definition now additionally
  exercises RunService unauthorized/grant/fingerprinted diff/revoke/source preservation.
  ALL UNEXECUTED. No test/lint/typecheck/build/probe/Git/native/provider/user-state IO.
- Official [pywebview native dialog API](https://pywebview.flowrl.com/api/#window-create-confirmation-dialog)
  and installed window.py confirmation return contract were read, not invoked. S9 MUST
  accept isolated real chooser cancel/confirmation, external worktree read/revoke,
  project switch/restoration no inherited grant, close with active read, origin navigation
  while dialogs pending, DPI/Unicode and original metadata/config preservation.

D1a/D1b construction is written, NOT full HTTP/native safety/GUI acceptance. Next
D2 real ChangesWorkspace read UI + native authority controls, then D3 exact sticky
inverse/verification review UX, D4 source/close/switch fixtures, S7/S8/S9/N1–N6/S10.
Goal active, 0.14.4. No actual user DB/catalog/keys/balance, daily EXE replacement,
commit/push/version promotion/chat fork or new pass counts. Original scope unchanged.

### Previous D1a source record (written, not executed)

- New api/routes/changes.py mounts fixed local `/api/v1/changes` routes for owned
  Git status/fingerprinted diff; registered run-scoped patch list/detail; inverse
  prepare/get/decision/reconcile and verification prepare/list/get/decision/cancel/
  reconcile. Strict extra-forbidden bodies, exact IDs/booleans/stage, no arbitrary
  argv/executable/metadata authority fields. Prepare/control POSTs require explicit
  confirmation; approval still uses original service fresh grants and exact review.
  GET never decides/expires/creates schema/reconciles. Reconcile is explicit POST
  metadata-only, not effect reexecution. Lost responses remain unknown and require
  original operation/review/plan IDs; frontend sticky controls are still D3.
- HTTP stream retention <=16 KiB and incoming-read timeout 5s, raw query <=8 KiB,
  duplicate/unknown queries and duplicate/nonfinite JSON refused before service.
  These are application request parsing limits, NOT upstream ASGI/network buffers
  or operation deadlines. Fixed safe error categories never echo raw validation
  inputs, private paths/output or exceptions. no-store includes success, validation,
  host/origin rejection and unknown-route/method responses under this prefix.
- RunService fixed Git reads borrow existing supervisor, owned operation/close
  join, workspace READ lock BEFORE original command resource; admission rechecked
  after each wait/constructor. Each private inspector owns its snapshot/drain;
  original cancellation reaches supervisor, no new executor or swallowed command
  cancellation. Quarantine/closed states refuse Git reads: these run actual fixed
  Git plumbing at S9, unlike SQL diagnostic queries. Native external metadata roots
  are still empty; no HTTP path grant. D1b must bind actual native confirmation to
  current owner/workspace lifetime and revoke on switch/close before D2.
- New PatchEvidenceQueries/InverseEvidenceQueries reuse C3 existing-only SQL-ro
  mode/query_only/runtime registration/read transaction and checked path/SQL budget.
  Pure strict JSON helper shared by patch receipt/result, inverse and verification
  framing rejects duplicate/nonfinite stored data without a second payload decode.
  No writable store/ledger constructor, runs.get, TTL update, project/config/event
  reads or recovery during GET. Missing tables/unregistered/corrupt sources fail
  unavailable/not-found, never fabricate empty-clean or retrospective receipts.
- Patch list <=100 rows/offset<=100000, <=4 MiB EACH encoded receipt/result/summary,
  <=16 MiB aggregate before payload fetch/decode. Detail <=12 MiB framing. Actual
  original result consistency is checked before projecting summary; stored accepted
  preimage blobs not exported, but historical unified diff can contain original
  sensitive project lines. Inverse detail preflights <=4 MiB EACH proposal/review/
  result and bounded scalar IDs/dates before row fetch; pending exact hash/projection
  validation, expiry PRESENTATION only, no stored mutation/fresh-file authorization.
  Outputs remain scoped sensitive/not globally S8-redacted. SQL readonly is NOT
  physical zero-write WAL/shared-memory behavior, cross-DB atomic snapshot, hostile
  race protection or hard syscall/JSON walltime; original C3 caveats remain.
- All patch/inverse/verification diagnostics require already-started held original
  owner and pin existing lifecycle lock through durable worker drain. Terminal failed
  close/quarantine permit diagnostic reads only, no start/resource/command/recovery
  gate reopening. Patch list keeps prior list shape; quiescence hint is false under
  quarantine/failed close and is never admission/approval or current Git attribution.
- New tests/api/test_changes_api.py, tests/api/test_changes_service.py and
  tests/test_changes_queries.py define fixed route forwarding/errors/host/origin/
  parser budgets/forbidden authority, fake Git owned borrowing, actual isolated
  source ledger/prepare/exact replay/reject, existing-only scope/missing tables/
  strict corruption/prefetch page+row quotas/live WAL and no preimage blob export.
  Existing inverse/verification service definitions retain explicit startup and
  extend failed-close held-owner diagnostics to patch/inverse without admission.
  ALL UNEXECUTED. Starlette/FastAPI body handler source was read, not exercised.

D1a is neither full D1 nor usable Changes UI/native authorization. Next D1b, then
D2/D3/D4/S7/S8/S9/N1–N6/S10. No tests/lint/typecheck/compile/build/probes/Git/native/
provider/actual user DB/catalog/key/balance operation, daily EXE replacement,
commit/push/version promotion/chat fork or acceptance result. Goal active, 0.14.4.

### Previous C3 source record (written, not executed)

- New persistence/verification_queries.py stores paths only and opens existing
  original ledger + registered runtime with mode=ro/query_only/read transactions.
  No store/schema construction, runs.get, TTL update, reconciliation, event/config/
  project reads or commands. Missing/unsafe/corrupt evidence is unavailable, not
  empty/clean/success. Fixed source-run registration metadata only; attached WAL
  sources are not an atomic cross-DB snapshot or source-quiescence proof.
- Source-run-scoped optional exact tool-call/patch filters, review-ID keyset (NOT
  chronological) <=16 rows + one lookahead; <=8 MiB aggregate selected encoded
  evidence before payload fetch. Detail <=128 KiB plan/8 KiB selection/4 MiB actual
  results/16 steps. Checked 3s path/SQL/iteration budget + 1s connection busy timeout
  is not hard syscall/JSON walltime. DB/sidecars/ancestors regular/no-link/single-link
  checks are not hostile-race atomic isolation. SQL/Unicode/framing errors expose
  only fixed codes. Shared store parsing now rejects duplicate/nonfinite JSON,
  bounds scalar fetches, and checks exact step-index/expired-decision framing.
- Summary validates actual framing before omitting all plan/argv/names/stdout/
  stderr. Detail marks scoped output sensitive/not globally redacted, UTF-8
  replacement presentation not lossless binary proof, exact stored review not
  fresh config/executable/dependency/snapshot validation. Effective pending expiry
  is presentation only; stored status stays unchanged. No query grants approval,
  retry, command absence, patch success or whole-project/native acceptance.
  Sealed attempts, actual exited commands, stop-prefix remaining work, unknown
  outcomes and original registered source remain distinct. Aggregate result
  persistence quota now retains a stable verification_evidence_budget unknown
  cause/prefix, including explicit metadata reconciliation; never retries.
- RunService get_verification/list_verifications require an already-held started
  owner, never call start. Original lifecycle lock pins owner through the durable
  worker drain; successful/in-progress close rejects new queries. Quarantine and
  terminal failed close allow only these metadata reads while owner remains held;
  no resources, execution/admission/recovery gates are reopened. Readiness is not
  inferred. Decision/cancel/reconcile terminal callbacks invoke one original
  metadata page AFTER write lock/task-map/cancel-fence release, with a repeated
  closed/quarantine guard after waiting. Ordinary pages still protect unknowns.
- New tests/test_verification_queries.py and expanded service/actual Graph/Deep/
  Legacy scripted fixtures define missing stores/tables, scoped filtering/pages,
  expiry without mutation/config reads, live WAL/read SQL, quota before fetch,
  corrupt framing/surrogate errors, hardlinked DB/all sidecars, registered leased
  source versus admission, failed-close diagnostics, repeated query cancellation/
  close drain and manual-terminal lock ordering/unknown protection. New fake-output
  quota definition keeps first result/source patch/run and refuses repeat. ALL
  UNEXECUTED; fake/scripted cases are not native/project/model proof.
- Only SQLite official documentation was checked, not actual SQLite/native calls:
  [WAL read-only rules](https://sqlite.org/wal.html) and
  [query_only limitations](https://sqlite.org/pragma.html#pragma_query_only).
  SQL-read-only is explicitly NOT physical zero-write sidecar behavior. No test/
  lint/typecheck/compile/build/Git/native/provider/user-state operation ran.

### D continuation (sequential construction, acceptance remains S9)

D1 implementation order: **D1a** writes regressions, owned Git service reads,
strict bounded HTTP transport and existing-only patch/inverse/verification reads
with the original held-owner diagnostic lifetime; no GET expiration/schema/recovery.
**D1b** supplies native owner-confirmed external metadata authorization (no HTTP
path grants), grant scope/lifetime/revocation and source/transport integration
definitions before D2. D1a alone is not full D1, linked-worktree UX or acceptance.
All command/native execution remains deferred to S9.

1. **D1 owned service/API transport:** connect existing GitInspector status/selected
   fingerprinted diff and existing source-run patch/inverse/verification services
   through fixed local API routes/strict bounded schemas/no-store/stable errors.
   No arbitrary command, Git write, unreviewed inverse/verification, automatic
   reconcile or grant inference. Held-owner read diagnostics must remain available
   without executing Git or restarting quarantined resources. External linked
   metadata authority must remain owner-confirmed, not arbitrary HTTP paths.
2. **D2 ChangesWorkspace read surface:** real branch/HEAD/status/staged/worktree
   planes and selected diff with explicit unavailable/excluded/partial/stale states;
   actual source-run receipt attribution only, not inferred Git authorship. Reuse
   existing WorkbenchShell/navigation/project identity/close barrier; no placeholder
   success/empty-clean and no conversion of engine state.
3. **D3 explicit inverse and verification reviews:** exact source/review/plan hashes,
   immutable argv previews/new command+write grants, separate patch/verification
   evidence, bounded history/detail sensitive output warnings, cancel/drain/unknown
   controls. Lost replies retain original IDs/payloads; read-only recovery never
   auto executes, grants, refreshes review/config or retries consumed unknowns.
4. **D4 source integration and fixture definitions:** project switch/close freeze,
   stale response/input protection, scoped old/new engine/history entry points,
   Graph/Deep/Legacy script fixtures and API/Vue SSR/controllers plus required
   native acceptance definitions. No execution until S9; then S7/S8 before S9/S10.

   Sequential D4 construction: **D4a** real Changes child/panel/controller mounted
   virtual-host definitions plus initial bridge/read and blocked input/lifecycle
   source fixes; **D4b** actual scripted Graph/Deep/RunService Legacy receipt and
   scoped API/lifecycle fixtures plus required native acceptance definitions.
   Static root strings or a synthetic child mount do not satisfy D4b/real GUI gates.

S6 B construction detail (sequential; no execution before S9):

- **B1 patch/receipt foundation:** extend existing PatchService with bounded,
  strict UTF-8, no-link/hardlink/alias snapshots, mode-preserving application,
  guarded partial-failure rollback and exact inverse preparation. Retain the
  public model tool's write-only schema: deletion exists only in an internally
  prepared inverse. Store bounded accepted preimages and expected/applied hashes
  in typed receipts; summaries exclude contents. Extend ToolExecutionLedger with
  additive receipt storage and a specialized reserved patch operation whose
  durable intent precedes filesystem mutation and whose completed result/receipt
  are sealed in one transaction. Interrupted intent is indeterminate, not retry.
- **B2 runtime/product ownership:** wire receipts through Graph/Deep/Legacy and
  existing owner/lease/grant/approval/workspace barriers, preserving patch success
  if later verification fails. New inverse preparation/approval is pinned to the
  selected durable receipt and expected current hashes/modes, with no automatic
  rebase or reapproval. Then C provenance and D APIs/UI; B1 alone is not usable
  runtime attribution or a shipped undo feature.

Files: `workspace/patching.py`, `persistence/tool_ledger.py`, new receipt domain
and regression definitions; B2 will modify existing runtime/tool adapters rather
than create a second executor. All regression definitions remain unexecuted.

### C2c2 source record (written, not executed)

- Reviewed VerificationPipeline uses original ProcessSupervisor.run_binary with
  require_tree_ownership=True, pinned argv/deadline/environment and per-stream
  byte caps plus total configured budget (including the one-byte edge). No text
  spool/fallback/truncated success. Returned bounded bytes are decoded explicitly
  with replacement for presentation, NOT a lossless binary/output identity proof.
  Direct pipeline.run retains distinct text compatibility/no reviewed provenance.
- Settled timeout/output overflow/strict startup admission errors yield failed
  attempt results with exit_code null, false success, empty outputs and stable
  verification_timeout/verification_output_limit/verification_supervision_unavailable
  codes, terminated/unavailable supervision. Store rejects exit-zero/output/label
  forgery for these errors. completed means sealed attempt results, not all commands
  executed or tests passed. Actual cleanup failure is distinct ProcessCleanupError:
  no fabricated result/seal, current step stays unknown, earlier results stay.
- Strict binary normal EOF, timeout, overflow and repeated cancel now terminate
  remaining owned tree and join original parent/read workers; Windows checks Job
  accounting ActiveProcesses==0 before checked handle close/unregister. This covers
  descendants which closed pipes after parent exit. Separate checked termination/
  close methods preserve weaker compatibility callers. Strict startup attach/resume
  cleanup also drains/checks its Job; failed cleanup retains process identity.
- Failed strict cleanup preserves registration/Job and sets process quarantine;
  later command/service admission/runtime construction and post-lock root/resume
  execution refuse. Close failure retains the workspace owner, not fake cleanup.
  Supervisor close fences new commands and waits <=10s original unregister gates,
  not only a second parent wait. Job accounting has a checked 10s cleanup polling
  budget; original worker/kernel drain may exceed command deadline, not hard total
  walltime. No hostile-actor sandbox/remote-service escape/executable pinning or
  POSIX Job-equivalence claim. Non-Windows remains process-group supervision.
- Existing C1/C2a/C2b fake fixtures now expose strict binary transport explicitly.
  New fake transport/store/owned-service/supervisor definitions cover exact argv/
  caps, display decoding, stable failed attempts versus raw exceptions, direct
  compatibility, failure-prefix stop rules, actual Job-empty gate/repeated cancel,
  cleanup quarantine/retained owner/startup identity/close unregister drain.
  New Windows isolated native definitions cover no text spool, actual accounting,
  attach denial before body, overflow, entered-body timeout, inherited versus
  closed-pipe parent-exit descendants, repeated cancellation through actual native
  Job drain gate. ALL UNEXECUTED; skips do not waive Windows native S9 requirements.

Win32 API source check (documentation only, not native observation):
[QueryInformationJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject),
[JOBOBJECT_BASIC_ACCOUNTING_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_accounting_information),
[TerminateJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject).

Next C3 scoped read-only verification summaries/details and source/output/lifecycle
provenance. Must allow metadata diagnostics under cleanup quarantine/failed close
with held owner WITHOUT reopening admission/recovery/commands; explicit attempt/
cancel/timeout/unknown versus patch success, bounded queries/output sensitivity and
manual-terminal maintenance. Then D HTTP/ChangesWorkspace and S7/S8/S9/N1–N6/S10.
No full C2/S6/native/HTTP/UI acceptance. Goal active, 0.14.4; source/definitions only,
no test/lint/typecheck/compile/build/bench/package/native/process/probe/Git/provider/
user-state execution, daily EXE replacement, commit/push/promotion/fork.

### C2c1 source record (written, not executed)

- VerificationReviewStore adds metadata-only reconciliation. Already-running or
  indeterminate reviews become completed ONLY for actual contiguous fully sealed
  results or an actual failure prefix stopped by the exact reviewed stop rule.
  Other consumed states stay indeterminate, including no step/no sealed result:
  no absence proof, workspace/config read, command retry or fabricated completion.
  Known failed/cancelled causes are not promoted even with sealed steps. Scoped
  RunService reconcile excludes live same-review tasks and holds original write
  lock/quiescent registered source/owner; metadata-only event, no original run or
  patch outcome rewrite.
- Pending cancellation has its own decision='cancel', no forged command approval,
  exact plan/source CAS and no grants required to stop/reject. Live decisions are
  tracked by scoped (run,review) task identity, duplicate requests refuse, <=256
  tracked decisions. Explicit cancel fences fresh decision admission and cancels/
  joins that actual task BEFORE acquiring the workspace lock, avoiding self-drain
  deadlock. Repeated control cancellation drains the requested work and refcounted
  fence. Absent live handle only permits stored metadata reconciliation, never
  claims a process stopped or an unknown command did not execute. Stored finals
  remain; patch receipts/source run status/answer/usage are unchanged.
- Service close fences new public admission, cancels tracked manual decision tasks
  (including lock waiters), joins admitted operations/evidence before supervisor
  cleanup and owner release. Existing original process drain is reused, NOT a new
  command executor. This lifetime wiring does not fix weak text Job/spool/parent-
  exit supervision or prove native power-loss cleanup; strict transport is C2c2.
- New verification_maintenance.py processes one keyset page of an existing ledger:
  <=64 rows, <=8 MiB selected encoded plan/selection/result payload per page,
  checked 2s SQL/iteration budget (not earlier attach/lock/blocked syscall walltime).
  No store construction/schema creation/config/project reads/commands or deletion.
  No-link/regular/single-link DB/sidecar/root checks; owning service attaches its
  existing runtime metadata read-only and protects unregistered/nonterminal/leased
  sources. Startup before manual admission may reconcile running/indeterminate;
  normal root-terminal maintenance protects both. Startup is one bounded page,
  NOT full-history recovery; remaining consumed rows cannot relaunch and have
  scoped explicit reconciliation. Cursor advances/wraps without output disclosure.
  Expired pending reviews no longer count against live capacity before their sweep;
  running/indeterminate still count. Global history/redaction/idle sweep remains S8.
- New/extended store/maintenance/service definitions cover full/stop-failure prefix
  versus unknown/missing, known terminal-cause preservation, exact pending cancel,
  expiry/live cap, no missing schema creation/keyset/source protections, duplicate
  decisions/reconcile refusal, gated fake live cancel/close/owner retention/new
  admission refusal/control repeated cancellation, startup reconstruction with
  invalid config and no launch, exact sealed replay and original source/run.
  ALL UNEXECUTED. Fake supervision is not native process/project proof.

Next C2c2 strict reviewed supervisor/descendant/parent-exit/output bounds and native
definitions, then C3/D/S7/S8/S9/N1–N6/S10. Full C2/S6 and HTTP/UI delivery remain.
Goal active, 0.14.4; source/definitions only, no test/lint/typecheck/compile/build/
bench/package/native/process/probe/Git/provider/user-state execution, daily EXE
replacement, commit/push/promotion/fork.

### C2b source record (written, not executed)

- RunService Graph patch registry and trusted factory Deep/Legacy paths require
  separate manual verification review. New exact boolean owner policy propagates
  through factory/Deep/langchain adapter and Legacy/reviewed Core/patch_tool;
  provider schema remains changes-only, no model-controlled review flag/IDs.
  Product patch creation no longer loads VerificationPipeline/config even when
  both write/command grants exist. Invalid verification config cannot implicitly
  block approved patch execution; explicit manual prepare handles config later.
- After actual patch sealing, returned output and patch.applied event metadata
  say not_run_separate_review_required, success null/results empty, actual source
  run/call/patch IDs/durability and current_workspace_not_original_patch_snapshot.
  No generated/pending review, command launch, fake completion or original run
  rewrite. Stored exact patch result shape stays unchanged; replay keeps marker
  without rerunning patch/config/commands. Marker is not durable command evidence.
- Separate C2a owner-scoped prepare/get/decide still requires fresh grants and
  exact plan review. Direct console/factory/tool default compatibility remains
  distinct, including old post-patch verification behavior. Existing explicit
  run_command/HITL is retained; this slice removes implicit configured verification,
  not all model-requested commands or other capabilities.
- Extended owned patch/adapter definitions cover policy despite supplied pipeline,
  no availability access, both grant states, real source linkage/schema/event,
  replay/later user work. Actual RunService scripted Graph/Deep/Legacy definitions
  add command-granted patches with invalid/valid config, no auto supervisor calls,
  then manual preview/get/reject/fresh approve/actual fake-step seal/replay and
  original source/run preservation. New trusted-policy definitions cover invalid
  types before side effects and factory direct default versus explicit owner flag.
  ALL UNEXECUTED; scripted model/fake supervision is not native/project proof.

Next C2c: unknown metadata-only reconciliation/reconstruction, manual cancel/close
admission, strict process/parent-exit/spool bounds and pending review lifecycle;
then C3/D/S7 and S8/S9/N1–N6/S10 unchanged. No HTTP/ChangesWorkspace delivery or
full C2/S6 acceptance. Goal active, 0.14.4; only source reads/edits and definitions,
no tests/lint/typecheck/compile/build/bench/package/native/Git/provider/user-state
execution or daily EXE replacement/commit/push/promotion/fork.

### C2a source record (written, not executed)

- New persistence/verification_reviews.py adds manual verification_reviews and
  ordered verification_command_steps to the owned patch ledger DB, NOT the patch
  execution reservation or original model ToolCall IDs. Strict stored plan/source/
  selection/hash/schema/result framing, canonical UTC/lifetime, exact reviewed
  argv and sequential actual result prefix are validated. Plan <=128 KiB;
  selection <=8 KiB; at most 16 steps and aggregate encoded actual results <=4 MiB
  (checked in SQL before fetching). Live pending/running/indeterminate cap 256,
  default TTL 900s (1..86400); not a full global history/expired-pending sweep cap.
- Fresh pending decision CAS binds exact review/plan ID. Durable running step
  intent precedes launch; actual named argv/exit/success/output/duration/supervision
  seals individually before another command. No duplicate step, changed argv,
  out-of-order prefix or continuation after stop-on-failure. Completed operation
  means sealed planned results (or an actual stop-on-failure prefix), NOT every
  test passed; aggregate success remains false on failed command/timeout. Missing
  result/unfinished step cannot complete. Failed/cancelled/indeterminate has no
  inferred success or command absence. Final replay returns evidence only, other
  consumed states refuse execution. Expiry commits before refusing late approval.
- RunService owner-scoped prepare_verification/get_verification/decide_verification
  require registered source and terminal/unleased/non-live source for preparation/
  decision, existing shared write lock, actual sealed applied receipt identity,
  fresh explicit command_execute AND workspace_write grants for approve/preparing
  because configured scripts may mutate files. Reject needs neither grant. Source
  private preimage expiry does not invalidate sealed historical attribution.
  Same operation-ID/source/selection retry preserves original plan/config/TTL;
  no silent refresh. Approval rechecks actual receipt and fresh exact config/plan;
  source is current_workspace_not_original_patch_snapshot, not a frozen Git tree.
- Selected plan now displays/fingerprints fixed operation_timeout_seconds=600.0.
  Owned execution includes resource waiting/commands in that deadline, not earlier
  workspace-lock/DB/admission waiting. Manual operation process identity is separate
  from source run/call; original run outcome/answer/usage and patch proof stay.
  Metadata-only events carry IDs/status/success/unknown marker, not stdout/argv.
  Actual scoped stored output remains sensitive; this is NOT global redaction.
- Existing VerificationPipeline adds draining on_start intent hook and rechecks
  config after that IO before launch, alongside actual per-result hook. Commands
  are not shielded from cancellation; DB results/abort metadata drain before owner/
  write-lock exit, including cancellation arriving during claim IO. Invalid/refused
  decisions do not rewrite older unknown operation state. Partial first result
  survives later config conflict, deadline/cancel, persistence/final-seal failure;
  no automatic retry or patch rollback. No model/provider call/second executor.
- Text ProcessSupervisor now keeps one original process-wait task shielded from
  wait_for cancellation, then joins it via await_durable after timeout/repeated
  cancellation before unregister/managed close. This fixes that wait-worker drain
  path, NOT strict Windows pre-execution Job ownership/descendant/power-loss proof.
  Text spool/parent-exit/native limitations remain C2c/S9; do not claim sandboxing.
- New tests/test_verification_review_store.py, tests/api/test_verification_service.py,
  tests/workspace/test_verification_supervisor_drain.py define real store/service
  CAS/scope/grants/TTL/sticky selection, actual step prefix/result framing/unknown
  refusal, fake supervisor success versus original patch/run, reject/replay/later
  user edits, first sealed result/config conflict, gated repeated-cancel owner/
  write-lock drainage, final-seal failure and original fake wait-worker drainage.
  ALL UNEXECUTED. Fake supervisor/process evidence is not native/project proof.

At the C2a handoff, next was C2b product Graph/Deep/Legacy policy wiring to explicit reviewed verification,
remove implicit post-patch auto commands but preserve distinct direct-console
compatibility. C2c still owns unknown read-only reconstruction/reconciliation,
manual cancellation/close admission/strict process limits and pending lifecycle;
then C3/D/S7. No public HTTP/ChangesWorkspace delivery or full C2/S6 acceptance.
Goal active, version 0.14.4, S0–S10 unchanged; construction only, no tests/build/
commands/Git/native/provider/actual user state access/push/promotion/fork.

### C1 source record (written, not executed)

- New workspace/verification_config.py captures <=64 KiB strict UTF-8 JSON,
  rejects duplicate keys/nonfinite or invalid commands, external configs,
  symlink/reparse/hardlink/nonregular files and observed file/parent changes.
  Reads compare lstat/opened fstat/post-read identities, bound bytes before JSON,
  never follow an external parent intentionally. Missing config yields no commands,
  not a guessed default. At most 16 commands, 64 argv entries each, 4096 UTF-8
  bytes per argument, 128 UTF-8 bytes per name; timeout <=600s per command,
  output <=1 MiB configured per command. No new dependency or schema execution.
- Frozen config/commands and an exact selected-name VerificationPlan pin original
  argv ordering/spacing, timeout/output/stop-on-failure, raw config SHA256, file
  snapshot fingerprint, relative config path and workspace identity. Fresh-copy
  projections cannot mutate the actual plan; pipeline commands use a read-only
  mapping. Preview does not execute, grant commands or consume operator approval.
- Existing VerificationPipeline now exposes prepare/run_reviewed and reuses its
  original supervisor execution path, not another executor. Exact current config,
  full frozen plan and workspace source are compared before execution and before
  each subsequent command. Same-content replacement, raw whitespace changes,
  changed argv/removal or a forged/foreign plan require a fresh review. Per-result
  awaitable hook drains before the next command, so C2 can seal actual partial
  evidence before later conflict/cancel/failure; the hook alone is not persistence.
  Reviewed-path report attaches exact plan provenance, not durable approval proof.
- Explicit true command grant is required by the foundation, but is NOT the
  missing owned service/operator approval barrier. Compatibility run still has
  no operator-reviewed report. Existing product patch after_effect still uses
  that compatibility method; C2 MUST replace implicit configured auto-verification
  with actual reviewed config/selection admission before claiming product safety.
  This C1 slice alone does not complete command-review parity or durable evidence.
- Named environment lookups avoid enumerating unrelated secret values. Stable
  verification_timeout replaces provider/process exception text in timeout
  result; existing timeout regression definition is updated. Stdout/stderr/argv
  are not globally redacted. Explicit interpreters/scripts may write files or
  access the network: shell=False/allowlisting is NOT an OS sandbox. Only config
  bytes/identity are pinned, not executable/dependency/workspace content; total
  run deadline, strict/native process limitations, lifecycle/output policies and
  cancellation/partial outcome persistence remain C2/C3/S8/S9.
- New tests/workspace/test_verification_review.py defines preview/no-effect/
  no-grant, immutable exact argv/copy, missing grant, changed config/identity,
  foreign/forged plan, second-command freshness with recorded first result,
  strict JSON/budget/hardlink/external scope and fake exact-plan report provenance.
  All definitions are UNEXECUTED. Fake supervisor is explicitly not native
  command supervision or actual project verification proof.

Next C2 durable pending/decision/actual verification operation and owned runtime
review wiring, then C3 scoped lifecycle/provenance, D API/ChangesWorkspace and S7.
Goal active, 0.14.4, S0–S10 unchanged; no command/test/Git/native/provider/real user
state execution, commit/push/promotion/fork. Unified acceptance remains S9.

### B2b2b source record (written, not executed)

- Patch ledger schema now additively retains a strict content-free receipt
  summary and explicit preimage_expired_at. Intent/actual receipt sealing writes
  summary beside the private receipt; old effects are never retrospectively
  attributed. Expiry clears only full receipt JSON after validation, preserving
  summary/IDs/status/arguments hash/result/audit. Historical confirmed-applied
  survives private expiry; new inverse preparation refuses patch_preimage_expired,
  rather than inventing empty before-content. Expiry flags with remaining private
  data or malformed summaries are refused as historical proof. Each list page
  still has a 4 MiB representation budget and makes no current Git ownership claim.
- Owner-scoped reconcile_inverse_patch serializes with effects under the existing
  write lock. For already-consumed applying/indeterminate reviews it reads only
  actual specialized ledger evidence and reconciles review metadata: sealed
  inverse receipt/result becomes applied, ledger failure becomes failed, missing/
  running/unsealed remains indeterminate. Full review/result/source validation
  precedes commit. No FS prepare/apply, command, model call or retry; pending
  reviews are never approved by reconciliation. Failed is not effect-absence proof.
- New PrivatePatchRetention invokes one phase/keyset page per maintenance call:
  pending review TTL, receipt preimages, then Legacy continuation snapshots.
  Default age 30 days, maximum 100 rows and aggregate 4 MiB decoded JSON per pass.
  Oversized/malformed rows stay protected; cursors progress past protected keys
  and phases cycle on later calls. SQL progress interrupts after a 2-second work
  budget; SQLite busy waits remain separately bounded by its 5-second timeout,
  not a hard total wall-clock guarantee. Additive store-owned indexes support
  selection. This is not a global history storage cap or immediate bulk expiry.
- Service startup after owner acquisition and root-turn cleanup after runtime
  lock exit invoke the helper; worker/cursor/write lock drain before owner exit
  on repeated cancellation. Child terminal callback intentionally does NOT acquire
  the write lock: the parent may still own it. Checkpoint DB locks fully leave
  before private maintenance. Ledger-before-runtime admission locking checks
  registered terminal/unleased roots and no running tool. Original and manual
  receipts/Legacy snapshots share conservative whole-run pending/applying/unknown
  review protection; manual expiry additionally validates final review provenance.
  Unregistered/active/interrupted/unknown data is never expired by this policy.
- Pending review expiry first validates IDs, canonical UTC dates/lifetime and
  public view; malformed timestamps cannot authorize clearing a proposal. Legacy
  expiry requires exact registered root/thread and valid bounded continuation,
  leaves run/thread/interrupt/status identity, clears snapshot/decision fields,
  records explicit expiry and refuses old load/consume/save revival. Audit/result/
  event/diff payloads are not deleted. Deliberately reviewed diffs can still hold
  legitimate before-context. WAL/freelist pages are reused, NOT secure erasure.
- Fixed existing DB paths and sidecars reject symlink/reparse/hardlink/nonregular
  targets. No store/schema initialization in retention; unsupported old schemas
  defer, not migrate during a dry run. Dry runs use read-only SQLite connections
  and perform no payload/schema mutation. Only ledger payload columns change;
  attached runtime admission locking is NOT a cross-WAL/filesystem atomicity claim.
- New tests/test_private_patch_retention.py and inverse service extensions define
  historical proof versus expired undo, exact replay/later user edits, live/
  unknown/unregistered/recent protection, pending/manual effect protection,
  canonical TTL corruption, pending/consumed Legacy expiry and sticky identities,
  bounded cursor/dry-run/oversize/hardlink cases, actual sealed inverse read-only
  reconciliation and missing evidence, manual result preservation after expiry,
  plus gated worker cancellation owner/write-lock drainage. All are UNEXECUTED.
  No actual user DB, provider, paid call, command or native operation was performed.

Next C: explicit reviewed verification configuration/argv/provenance/durable
evidence, then D owned HTTP/ChangesWorkspace, before S7. S8 still owns broader
redaction/reporting/lifecycle UX, idle maintenance triggers and global storage
policy; S9 must execute fixture/source/frontend/package/native acceptance.
B2/S6 and public undo UI are not accepted. Goal active, version 0.14.4, S0–S10
unchanged; no tests/build/Git/real state access, commit/push/promotion/fork.

### B2b2a source record (written, not executed)

- RunService now has owner-scoped list_patch_effects, get_inverse_patch,
  prepare_inverse_patch and decide_inverse_patch service methods. They are not
  public HTTP routes or UI yet. Registered source run must exist in this owned
  state root; inverse preparation/decision additionally requires terminal source,
  no lease and no live scheduler handle. The existing shared workspace write lock
  is held through source checks, private snapshot/approval CAS, actual patch
  worker, ledger seal and durable event. No second runtime/model executor.
- Preparation requires a new explicit boolean write grant, original applied
  receipt/run/tool-call/patch identity and client-generated operation ID. It
  copies an exact fresh inverse after current hash/mode/policy checks. Original
  run grants alone are not approval. Repeating the same operation ID returns
  the same review/base/deadline; different source conflicts, and a later user
  edit never triggers silent refresh. Preparation is not an effect. Only an
  operator decision with exact review ID and reviewed proposal ID may apply it.
  Reject requires no filesystem write grant. No edited inverse, rebase, reset,
  automatic verification command or new model deletion capability is introduced.
- New inverse_reviews table is in the existing patch ledger DB. Internal exact
  proposal versus validated public review/result are separate; public files
  contain paths/actions/hashes/modes, not restored content/preimage fields.
  The intentionally reviewed unified diff can contain legitimate original file
  context; this is not content redaction or secure-erasure proof. Each JSON field
  is bounded to 4 MiB; at most 256 live pending/applying reviews, TTL 1..86400s
  (service default 900s). Scope/ID/framing/mode/summary/receipt/public-result and
  canonical UTC dates are checked. This is not a global history storage cap.
- Pending decision CAS moves to applying before the actual existing
  execute_patch_once(..., tool_name='inverse_patch'), using an explicitly manual
  operation ID rather than pretending it is a model ToolCall ID. The exact
  proposal is re-bound to the sealed private source and FS bases are checked by
  PatchService at application. Successful manual receipt/result identifies its
  original source, emits patch.inverse.applied and does not rewrite the original
  run's model status/answer/usage. New inverse of an applied inverse can restore
  a deleted created-empty file, with another fresh review; never write empty as
  a substitute for deleting it.
- Completed approve/reject retries do not execute again. Rejected replay is a
  decision replay, not an effect replay. Failed/applying/indeterminate views
  require ledger inspection and do not claim no filesystem effect. Patch ledger
  seal and review finalization are separate transactions: final review-seal
  failure can leave applying with an actually sealed patch receipt; never repeat
  that effect automatically. Repeated cancellation drains the admitted operation
  including worker/seals/events before releasing lock/owner. Filesystem+DB are
  still not globally atomic, and native power-loss/ACL/race proof remains S9.
- Expired/rejected/finalized inverse reviews clear their private proposal field;
  exact expiry commits before refusing a late approve. Identity, reviewed diff
  and audit remain. Source receipt/Legacy continuation retention and periodic
  owner maintenance are still B2b2b, not completed by this per-review TTL handling.
- New tests/api/test_inverse_patch_service.py defines actual fixture RunStore/
  ledger/PatchService service operations without provider/command calls: prepare
  versus effect, exact replay/source binding, new grant/quiescent run/owner gate,
  later user edit preservation, created-empty deletion/fresh redo, real gated
  effect repeated-cancel lock/owner drainage, review-seal indeterminate handling,
  reconstruction/reject, redacted scoped receipt listing, committed TTL expiry/
  private proposal clearing and live-review budget. Root source effects are
  fixture-created, not actual runtime quality evidence; the separate real
  Graph/Deep/Legacy scripted-service definitions still await S9. All unexecuted.

**Next B2b2b:** explicit private preimage and Legacy continuation retention,
pending/applying/indeterminate protection and read-only outcome reconciliation
that cannot retry a write. Preserve effect/audit identity and distinguish expired
undo capability from confirmed historical application. Then C provenance and D
owned HTTP/API/ChangesWorkspace integration before S7. B2/S6 are incomplete;
service source is not a delivered or accepted undo UI. Goal active, 0.14.4,
original S0–S10 unchanged. No tests/lint/typecheck/compile/build/bench/package/
native/probes/process checks/Git/provider/paid calls or actual user DB/catalog/
keys/balances access; daily EXE untouched; no commit/push/version promotion/fork.
Unified fresh-source acceptance stays S9/N1–N6, full delivery S10.

### B2b1 source record (written, not executed)

B2b2 continues sequentially, not a reduced goal: first the actual owner-bound
manual inverse prepare/decision service using existing PatchService, shared
workspace lock and ToolExecutionLedger, client operation-ID replay and explicit
new write grant. Store the private exact proposal separately from the public
review; compare original receipt/current hashes/modes, fresh review ID/proposal ID,
CAS pending decision and TTL, drain the real worker on cancellation. No model
call, automatic command, rebase or reset. Then scoped receipt lookup and explicit
private preimage/Legacy continuation retention with pending-review protection,
followed by C/D. API/UI wiring stays D; all definitions execute only in S9.

- RunService selects reviewed Legacy for product runs. Direct factory/console
  defaults retain original synchronous Core/write_file behavior; no switch to
  Graph or replacement model loop. Core's opt-in reviewed registry exposes
  propose_patch instead of direct writes, reusing the same patch service/ledger,
  granted capabilities, command resource gate and borrowed service supervisor.
- AgentLoop now has an optional pending-call continuation/hook. It freezes at a
  specific call within the actual model turn. A resumed Core finishes the pending
  suffix, preserves previous tool results and advances the original step budget;
  no repeated model request or earlier successful effect just to recover review.
  The reserved final synthesis turn cannot authorize new product tool effects.
  Console execution without hooks keeps its old behavior.
- New persistence/legacy_review.py stores one bounded private continuation per
  run in the existing tool ledger DB, not a new executor/catalog. Schema 1,
  <=4 MiB UTF-8 JSON, <=1024 messages/64 pending calls; validate exact assistant
  suffix/prior completed-result framing. Original frozen context is carried,
  not re-read from the workspace. Public review includes the current tool call
  only, not prior private model context. Pending save refuses replacement;
  run/thread/interrupt/exact-snapshot CAS consumes each decision once, before
  executing it. A later pending call gets a fresh interrupt ID. Consumed/crashed
  state is not automatic recovery or authorization to replay an external effect.
- Reviewed Legacy bridges the synchronous Core worker to existing asynchronous
  ToolRegistry/ledger execution on the owning loop. Specialized patch owns its
  one reservation; other accepted calls use the existing generic ledger. Each
  sensitive call needs a fresh approve/reject/edit. Edits retain exact call ID/
  name and use existing hash/mode-pinned patch editor; invalid/foreign/stale
  decision failures stop, not fall through to approval of another pending call.
  Nullable HTTP tool_calls default is normalized without authorizing hidden edits.
- The outer adapter retains the actual Core worker through repeated cancellation.
  Its stop flag prevents future model/tool stages; already accepted effects drain,
  emit their actual receipt, and do not start verification after cancellation is
  observed at that effect boundary. Synchronous provider/current verification
  cannot be forcibly aborted by this flag; supports_cancel stays false, so do not
  claim instant deadlines or native process proof. Existing RunService owner,
  lease, scheduler and workspace locks surround run/resume; no new public bypass.
- Regression definitions add actual Legacy to the scripted RunService HITL/
  reconstruction fixture; assert no product write_file schema. Dedicated Core
  continuation definitions cover two pending calls/reconstruction/no repeated
  model/prior effect, reject, edit and stale edit, scope/consumed IDs, actual gated
  patch repeated-cancel drainage, and generic command argument preservation with
  fake transport. Store definitions cover structural framing, shared assistant
  call objects, exact CAS, private snapshot size and corruption. All unexecuted;
  the fake command transport is not actual command/native acceptance.

**Next B2b2:** owner/grant/lease/workspace-lock admitted fresh receipt-bound inverse
preparation/review/approval, scoped private receipt access and explicit receipt/
Legacy continuation retention policy. Then C verification provenance and D APIs/
ChangesWorkspace before S7. B2b1 does not deliver inverse/undo UI or complete B2/
S6. New Legacy continuation storage has no retention acceptance yet. Legacy MCP/
delegation lifecycle/product parity still needs S7's existing full scope, not a
claim from this patch bridge. Goal active, 0.14.4; source/definition construction
only, no tests/lint/typecheck/compile/build/bench/package/native/probes/process
checks/Git/provider calls or real user keys/balances/DB/catalog access. Daily EXE
untouched, no commit/push/version promotion/fork; S9/N1–N6/S10 remain due.

### B2a source record (written, not executed)

- ToolRegistry's specialized effect transport owns exactly one patch ledger
  reservation. Graph passes its actual ledger directly instead of nesting the
  effect inside generic execute_once/aexecute_once. The actual worker is drained
  under repeated cancellation before ownership may leave; a completed effect
  emits its receipt before optional verification. Failed persistence is still
  indeterminate, not fabricated completion. S9 must exercise the full service
  lease/lock boundary, not just a unit fixture's worker.
- Graph and Deep emit patch.applied from the actual effect callback, once per
  invocation; replay is explicitly tagged. Redacted results identify run ID,
  actual tool-call ID and sealed-ledger versus ephemeral durability. Private
  accepted preimages remain in the ledger, not tool output or event summaries.
  The old Deep post-tool repr-based patch event is removed.
- Deep now validates internal HITL input with a Pydantic schema and framework
  InjectedToolCallId rather than deriving an ID from argument hashes. Its public
  provider schema still exposes only changes. Bound run/sink context and prepared
  review are required; direct synchronous execution refuses this path. Installed
  langchain-core 1.6.3 BaseTool source was read for alias/ID injection contracts;
  no import, provider request or adapter invocation was executed. Actual contract
  compatibility is a mandatory S9 definition, not a passed assertion.
- Verification is outside the sealed effect operation. Missing explicit command
  grant skips commands; configured commands use the existing ResourceLimits slot
  and Deep borrows RunService's ProcessSupervisor. Failure returns a separate
  stable verification outcome while retaining the applied receipt; cancellation
  propagates without reverting or failing that completed patch. Replay does not
  execute verification again or pretend an earlier report was durably stored.
  Frozen reviewed config/argv provenance and durable verification receipts are
  still C, not completed by this transport change.
- Move EventSink/NullEventSink into the dependency-light root events module,
  retaining identical runtime.base re-exports, to break Graph's cold import
  cycle through runtime factory/Deep. Source import edges were inspected only;
  fresh-process graph/adapter imports have new unexecuted S9 definitions.
- New tests/workspace/test_owned_patch_tool.py defines generic double-reservation
  refusal, effect-before-verification ordering, failed verification, replay after
  user edits, missing command grant, cancellation while waiting for a command
  slot/after fake verification starts/repeated cancellation of a gated patch
  worker, real framework ToolCall ID injection and missing scope/review refusal.
  Fake verification does not prove native process drainage. New
  tests/api/test_patch_effect_receipts.py defines actual Graph/Deep RunService
  HITL, drained leases, single receipt event and ledger reconstruction with a
  scripted provider only. tests/test_runtime_cold_imports.py defines isolated
  import order and no state creation. All are definitions only, never executed.

**Next B2b:** Legacy's existing product execution path, fresh receipt-bound inverse
review/approval, owner/write grant/lease/workspace-lock admission, scoped private
receipt access and retention. Legacy is deliberately unchanged in B2a; no public
undo/receipt endpoint or UI is claimed. B2 and S6 are incomplete; then C/D before
S7. Goal active, version 0.14.4, original S0–S10 unchanged. No tests/lint/typecheck/
compile/build/bench/package/native/probes/process checks/Git commands/provider
calls or real user keys/balances/DB/catalog access; daily EXE untouched. S9 unified
fresh-source acceptance and old N1–N6/source-package gaps remain; S10 delivery
and major-boundary fork are not yet due.

### B1 source record (written, not executed)

- `workspace/patch_receipts.py`: schema-checked bounded private preimages with
  full SHA-256/missing hashes, before/after modes and per-file pending/applied/
  unchanged/rolled_back/preserved/unknown outcomes. Prepared/failed receipts are
  never automatically eligible for inverse. Summaries omit before_content;
  reviewed unified diffs may still contain legitimate before-context as before.
- `workspace/patching.py`: strict regular/no-link/hardlink/path/alias/ignore
  snapshot policy; max 32 files and 512 KiB each aggregate before/after contents,
  directory scans <=20,000 entries, bounded LF-only/no-final-newline diff.
  Base mode is added only when observed; absent optional fields preserve legacy
  proposal identity serialization. Old malformed/ambiguous diff receipts may be
  refused rather than silently refreshed; S9 must audit pending legacy approvals.
  Temporary files use exclusive creation and fsync before replace. Verify every
  base again just before effect; failed multi-file rollback verifies the actual
  committed content/mode/identity before restoring, otherwise preserves the
  user change and records uncertainty. No test failure invokes this rollback.
- Inverse preparation consumes an actually applied typed receipt and requires
  all current hashes/modes equal its after-state. Creates are reversed by exact
  deletion; original bytes/mode are restored, never an empty file substitute.
  Proposal preparation does not approve or execute it. Runtime/public tool
  prepare remains write-only, no new model deletion capability. Existing approval
  edit also checks mode so equal content cannot silently refresh a changed base.
- `persistence/tool_ledger.py`: additive receipt table in the existing DB with
  reserved operation foreign key/cascade. No new executor, DB catalog or migration
  of old result text into trusted receipts. execute_patch_once validates proposal
  binding, persists intent before any mutation, then seals actual receipt and
  tool result atomically in SQLite. Filesystem plus DB are not globally atomic:
  a crash/final-seal failure retains prepared/running as indeterminate and rejects
  repeating the effect. A failed effect records actual partial/guarded rollback
  evidence. Private applied lookup requires completed operation and applied
  receipt; list is run-scoped, <=100 rows, <=4 MiB decoded source receipt bytes
  per page, otherwise stable unavailable/budget error, never truncated success.
- Windows mode handling follows CPython 3.11's readonly 0444/0666 plus filename
  execute-suffix projection; these are not POSIX permissions or ACL/security-
  descriptor preservation. Only generated readonly temporary files may have
  readonly cleared for cleanup, never a readonly user target. Actual OS ACL/
  security attributes, power-loss persistence, junction/races and cleanup remain
  S9 gates; application identity checks are not a hostile-actor OS sandbox.
  Primary source consulted without executing Python/native probes:
  [CPython attribute mode projection](https://github.com/python/cpython/blob/3.11/Python/fileutils.c),
  [CPython Windows filename execute suffixes](https://github.com/python/cpython/blob/3.11/Modules/posixmodule.c).
- New `tests/workspace/test_patch_receipts.py` definitions cover pre-effect intent,
  private/redacted evidence, scoped lookup/exact replay with later user change,
  created empty file/deletion and redo, hash/mode stale inverse, no approval-edit
  mode refresh, partial failure with concurrent user edit, final-seal ambiguity,
  pre-effect persistence failure, hardlink/ignore/secret/device/alias/binary/byte
  refusal and old rows not backfilled. Existing patch failure definitions remain.
  These are fixture definitions only; no database, Python/test or Git was run.

Next B2 must use specialized application instead of wrapping/reserving the SAME
key twice in generic execute_once/aexecute_once. Drain the existing owned worker,
seal patch effect before optional verification and preserve completed patch
evidence on verification timeout/failure/cancel. Wire the actual runtime scope,
owner/lease/write grant and fresh inverse approval; no public arbitrary preimage/
receipt/root/argv endpoint. Retention deletion must cover private receipts without
inventing history or leaking contents. B1 alone is not runtime attribution or UI.

B2 sequential source slices: **B2a** shared owned tool effect transport, actual
Graph/Deep receipt reservation and pre-verification effect events, genuine Deep
tool-call IDs (never argument-hash identity), command resource gating and borrowed
supervisor, cancellation/replay/regression definitions. **B2b** Legacy product
reviewed patch path, durable fresh inverse approval/service-owner/write-lock and
receipt retention/access; then C/D. B2a cannot declare B2 or S6 complete and must
not quietly replace Legacy with a different execution runtime.

1. **A1 transport/parsers (this slice):** extend the existing ProcessSupervisor
   with bounded, lossless binary output and owned cancellation/timeout cleanup;
   add pure NUL-delimited index/tree parsers, strict object identities, excluded
   path counts, conflict stages, and case-alias rejection. Definitions only.
2. **A2 repository inspection:** an isolated, bounded metadata view must not load
   the project's `.git/config`, included/global configs, hooks, external diff,
   textconv, fsmonitor, credentials or alternate object directories. Resolve
   normal and linked-worktree ownership explicitly; unsupported/unsafe metadata
   is unavailable, not a clean worktree. Support unborn HEAD, SHA-1/SHA-256,
   index/HEAD staged identities and explicit conflicts. Read working files through
   existing bounded path/ignore/identity policy. No secret file hashing. Bound
   metadata, traversal, subprocess output, file bytes and aggregate deadline.
   Git missing / not a repository must retain patch and verification capability.
3. **B attribution/inverse patch:** reuse PatchService and ToolExecutionLedger.
   Show repository changes separately from ledger-backed agent patches, accepted
   base hashes and applied hashes. An inverse is a new exact approval-bound patch;
   preserve later user changes, including on newly created files. Never reset,
   checkout, stage, commit or auto-roll back an effective patch on test failure.
4. **C verification provenance:** reuse VerificationPipeline/ProcessSupervisor,
   freeze bounded reviewed config identity, argv/cwd/grant and actual exit/
   cancellation/timeout; exit zero does not assert model task success. Preserve
   owned task drain and admission/workspace lock semantics.
5. **D product integration:** owned RunService APIs, changes router, read-only
   ChangesWorkspace and VerificationPanel, stale/unknown/empty/unavailable UI,
   explicit patch/verification approvals and durable receipts. Add isolated
   source regression definitions, then advance to S7 construction. No S6 acceptance
   claim before S9.

## Transport / parser decisions

Existing text `run()` retains its current verification/tool contract. New
`run_binary()` shares process start, registration, Job Object/process group and
tree termination; no second executor or shell. Concurrent pipe readers retain at
most limit+1 bytes per stream, terminate on overflow, and join before unregister.
Timeout covers process *and pipe drain*, including descendants holding a pipe
when Job Object/process-group ownership is available. Tree termination now checks
the owned Job/group even after parent exit. Windows taskkill fallback still
cannot reconstruct an exited parent's descendants; its existing best-effort
boundary is **not** promoted to a hard descendant deadline. A2 must reject unsafe
inspection supervision or strengthen admission before exposing it as safe.
Repeated cancellation cannot abandon the owned drain. Binary overflow is an
error, not a truncated successful Git record set. Old text spool behavior is not
claimed to be bounded by this new method.
The existing repeated-cancellation drain helper is moved without semantic change
to dependency-free `owned_async.py`, with its persistence import retained as a
compatibility re-export. ProcessSupervisor must not newly require LangGraph/
aiosqlite just by importing workspace tools.

Parsers accept only complete `-z` payloads with bounded records/output, exact
known modes, strict lowercase SHA-1/SHA-256 OIDs, UTF-8 paths, and no Windows
case aliases. Unmerged stages stay visible; index/tree symlink and gitlink modes
remain metadata only and never authorize following or reading them. Protected
and static excluded paths return a count, never their names/content. Dynamic
workspace ignore rules and filesystem identity checks belong to A2, not these
pure syntax parsers. Git diff is never agent attribution.

Official format references (consulted, no Git command executed):
[ls-files --stage -z](https://git-scm.com/docs/git-ls-files),
[ls-tree -z](https://git-scm.com/docs/git-ls-tree),
[cat-file](https://git-scm.com/docs/git-cat-file),
[Git environment/config](https://git-scm.com/docs/git).
Local installed Git version is not measured in construction.

## Deferred S9 evidence

Run the new binary child fixtures and malformed/bounded parser definitions;
exercise ordinary/unborn/linked/split/sparse/SHA-256 repositories and denied
links/config/alternates, binary and unreadable/oversize files. Then prove HTTP
lifecycle/approval/attribution/user-edit conflicts and Vue/native unavailable,
diff, inverse and verification outcomes on the frozen whole version. Existing
N1–N6 and source/package divergence remain open. No build/test/probe/real user
database/provider/key/balance/native operation or Git command in this slice.

## A2 sequential implementation detail

### A2b implementation contract and source record

Extend the metadata view with bounded copied reftable stacks (common and linked
worktree), header-inferred hash format and private refStorage config; resolve
HEAD with fixed Git plumbing in that private view. Never read original config or
mistake dummy `.invalid` HEAD for unborn. Unsupported installed Git returns a
stable unavailable reason, not clean or an automatic upgrade.

Add an application-owned inspector: one serialized query per service, known
context handle before off-loop enter, cancellation-drained enter/read/exit and
strict supervised fixed argv only. Discover/pin an absolute Git executable
outside the workspace without implicit cwd/PATHEXT script search. Expose no
arbitrary argv/OID/root/blob endpoint. Only full validated OIDs derived from an
eligible path's HEAD/index may be read, with type/size/content hash checks.

Status separates exact HEAD/index metadata from bounded raw workspace comparison.
Reuse ManifestReader's dynamic deny/identity checks, capture ignore/source hashes,
bounded no-link untracked traversal and explicit unknown/excluded/coverage limits.
No custom attributes, CRLF conversion, filters or config are applied; raw-byte
differences are labelled raw comparisons, not porcelain parity. Never assert
whole-repository clean from this policy-filtered view. Symlinks/gitlinks remain
metadata only, including no submodule traversal.

Selected diff is a fresh path-bound snapshot with an optional expected status
fingerprint, not arbitrary historic blob extraction. Return raw-content hashes,
mode metadata and bounded UTF-8 unified text (binary/non-UTF8/too-large unavailable,
no lossy decode). Repository change evidence is never agent attribution. A2b
does not apply/reverse/stage/commit or run verification. S6 B/C/D and S9 remain.

Format/plumbing sources consulted without running Git:
[official reftable specification](https://github.com/git/git/blob/master/Documentation/technical/reftable.adoc),
[cat-file](https://git-scm.com/docs/git-cat-file),
[rev-parse](https://git-scm.com/docs/git-rev-parse).

A2b source is now written in `workspace/git_reader.py` and the extended metadata
view, not executed or accepted. The view copies only listed reftable files, checks
version/hash-format headers and defers actual CRC/ref semantics to private Git.
Private temp storage is refused inside the source/authorized metadata roots.
The ManifestReader ignore-stat hook preserves default S5 behavior while the Git
reader pins only policies actually visited in ancestor order, including absent
policies, without pre-reading nested denied files.

Bounds: 1,000 visible rows, 2,000 untracked walk entries, 8 MiB unique working-file/
ignore-policy reads, 1 MiB per working file/blob, 4 MiB per metadata protocol,
24 fixed commands, 256 KiB unified diff and at most 1,000,000 input line pairs.
The existing checked 10-second view budget includes preparation/commands/reads.
These are application checks, not hard preemption of blocked IO/CPU or OS isolation.
Excluded counts are excluded protocol records plus traversal entries, **not** a
deduplicated count of secret files. Coverage reasons and `repository_clean=null`
remain explicit even on successful status. `agent_generated=null` never infers
ownership from Git. Raw mode comparison is explicitly not applied on Windows.

New `tests/test_git_reader.py` scripted transport definitions cover fixed argv/
private lifetime, repeated cancellation and unreturned off-loop enter, forbidden
config/data access, stale fingerprints, stage selection, binary/non-UTF8/complexity
limits and synthetic reftable resolution. Metadata definitions add malformed
table-name rejection before open and linked common/local copying/mutation/cleanup.
`tests/integration/test_git_inspection.py` defines S9 actual Git owned temporary
repositories: mandatory SHA-1/SHA-256 x files/reftable, poisoned original config,
staged/worktree/combined diff, explicit external worktree authorization and source
preservation. No unsupported mandatory format is silently skipped. All definitions
are unexecuted; local Git version/ref-format and real Windows ownership remain
unmeasured. No status endpoint, ledger attribution or undo/verification UI yet.

Next **S6 B**: ledger-backed attribution and safe new approval-bound inverse
patches, preserving later user edits; then C/D before S7. No source rollback,
stage/commit/reset, Git/test/build/probe/native/provider/real data operation ran.

### Previous A2a implementation record (superseded by A2b where noted)

A2a now constructs the **metadata isolation boundary**, before any status/diff
endpoint: bounded regular/no-link metadata snapshots, HEAD/ref resolution,
checksum-derived index object format and referenced split-index copies, explicit
owner-authorized linked-worktree metadata roots, object-store link/alternate
rejection and a clean private Git directory/empty worktree/environment. Source
metadata config is never opened. Temporary view cleanup is context-owned.
An empty unborn repository without an index has unobserved object format, not
an asserted SHA-1 repository. No borrowed original worktree/config/hooks path is
passed to Git. Application-level identity checks are not an OS sandbox.

Windows strict binary admission uses CREATE_SUSPENDED, attaches a kill-on-close
Job Object, then resumes the single initial thread through the documented
Toolhelp/ResumeThread APIs. Attach/resume failure kills and joins the suspended
process before error, without falling back to taskkill. Existing text/default
binary callers keep their compatibility behavior. Git plumbing in A2b must
request this strict mode. No native call is executed during A2a construction.

A2b requirements at the A2a handoff were: owned asynchronous view/command lifetime, fixed read-only plumbing
methods (no arbitrary argv), index/HEAD status + dynamic-deny worktree reads,
bounded untracked traversal and selected diff, stable unavailable/unknown reasons
and fingerprints. At A2a, reftable reported an explicit requires-isolated-tables
reason instead of falsely reporting unborn; isolate/copy its bounded stack and
resolve it inside the private view before claiming full ref-storage coverage.
A2a alone is neither repository cleanliness nor a usable
changes endpoint. Outside-workspace metadata authorization must be supplied by
the owning application, never an arbitrary API path or project gitfile alone.
The later UI must expose a reviewed owner-bound grant rather than silently
following an external worktree pointer or declaring all worktrees unsupported.

Win32 admission references:
[CREATE_SUSPENDED](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags),
[ResumeThread](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread),
[Thread32First](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-thread32first).
Metadata references:
[repository layout](https://git-scm.com/docs/gitrepository-layout),
[index format](https://git-scm.com/docs/gitformat-index).

### A2a source record and bounds

- `workspace/git_metadata.py`: monotonic 10-second checked metadata budget,
  16 MiB aggregate source reads, 8 MiB per index/shared index, 1 MiB packed refs,
  40,000 observed paths and 20,000 object-store entries/depth <=4. These checks
  do not preempt a blocked filesystem syscall or create a hostile-actor sandbox.
- Ordinary/detached/unborn/loose/symbolic/packed HEAD, SHA-1/SHA-256 checksum
  framing for index versions 2/3/4, split base checksum binding and sparse flags.
  Full index/bitmap semantics still require the actual fixed Git plumbing at A2b
  and isolated S9 execution; synthetic metadata definitions are not Git parity.
- Source config/config.worktree/hooks are never opened/copied. External linked
  metadata is owner-authorized before any external file open, then exact common
  and project backpointer checked. Unicode/space worktree IDs are supported.
  Owner-bound common metadata may also be a bare repository with a non-`.git`
  basename; scope is not guessed from a directory suffix.
  Original object content is not opened by preparation; alternates, links,
  hardlinks, special files and identity changes fail closed. Large/unsafe stores
  are unavailable, never truncated-clean. No source lock/ref/index update.
- Private config/HEAD/index/shared base, empty worktree/home/hooks and a rebuilt
  environment with only named OS variables; never enumerate secret environment
  values. Source config, HOME/global/system/env config injection, credentials,
  external diff and replacements are not inherited. Network protocols/lazy fetch
are disabled in fixed options/env; actual commands were unimplemented at A2a.
- `ProcessSupervisor` strict binary mode now suspends/attaches/resumes with no
  fallback, drains failed startup and includes startup in the checked deadline.
  Compatibility mode/taskkill fallback remains explicitly weaker. New simulated
  attach-order/failure definitions plus real Windows suspended-body and actual
  parent-exited/child-ready pipe gates are written, not observed or run.
- `tests/test_git_metadata.py` defines only fake metadata fixtures (no Git init/
  provider/user DB): untouched config/key fixture, sanitized env/private cleanup,
  unborn/hash/version/split/sparse framing, packed refs/cycles/mismatch/reftable
  pending reason, authorized/unauthorized linked/unicode/backpointer, no alternate
  content read, link/hardlink/mutation and budget failures. Native symlink fixture
  skips do not waive the later required Windows junction/Job acceptance.

Nothing in A2a exposes a changes API or proves cleanliness/agent attribution.
No test/lint/typecheck/compile/build/package/native/Git command/provider/key/
balance/actual DB/catalog read, commit/push/version promotion or chat fork ran.
