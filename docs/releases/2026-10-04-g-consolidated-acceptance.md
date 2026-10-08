# G consolidated acceptance — source/package accepted, native parity partial

## Status and authority

Execution date: **2026-10-04**, Asia/Shanghai. Continue the existing
[A–H authority](../plans/2026-10-03-v1-local-completion-before-evaluation.md),
[G checklist](../plans/2026-10-04-g-consolidated-acceptance.md) and
[P01–P15 contract](../plans/2026-10-03-local-product-parity.md).

**G is not complete.** G1–G4 are accepted at this source freeze; G5 native parity
and G6 full lifecycle/ownership acceptance are partial. Carried E default switch,
G7 final-source cycle and H delivery remain pending. No required row is waived.

Version remains **0.14.4**; HEAD remains
`952a03b7d9dda78dc0f2188e32a620279f1211c2`. A–H remains one uncommitted batch.
There was no commit/push/tag, daily EXE replacement, paid model call, new chat,
agent or worktree. Root/default remains Legacy, with `/legacy/` and `/runtime/`.

Evidence roots, relative to repository:

- `.bench-results/g-local-20261004-01/`: new source/package/native receipts.
- `.artifacts/g-local-20261004-01/`: wheel/sdist/install environment/new EXE and
  disposable native fixture. Previous F attempts and old25 receipts are retained.

## Frozen provenance and isolation

Dirty source inventory SHA-256:
`5cc16169a24553b41a1b59c257169aa1f8e870afe1beee78d0c44792d0129a68`.
This is an inventory of actual accepted files, **not** the Git commit hash.
`validation-source.json`, `validation-source-after.json`, `validation-inputs.json`
and `postclose-audit.json` prove stable source/guarded inputs across acceptance,
packaging and native basic QA. Daily `.dist/DoppelAgent` hashes, sizes and
timestamps still match `daily-app-baseline.json`.

The native fixture owns its settings and six SQLite stores, with only the
`g-offline` / `G offline mock` profile, provider/model Mock, empty base URL and no
keys. No real settings, credentials, accounts or ledgers were read/decrypted.
Source/installed-smoke children remove credential/proxy/tracing environment names
and use a trusted CPython socket-connect audit permitting loopback only. Its
external positive control failed as expected; actual guarded runs recorded zero
unexpected external-connect attempts. Native EXE uses the fixture Mock profile
and removed credential environment; the Python guard is **not claimed** for its
packaged interpreter. This is neither OS traffic attestation nor a provider cap.

Python3.11.15; pytest8.4.2; Ruff0.16.8; LangGraph1.2.11; DeepAgents0.7.15;
MCP2.2.0; FastAPI0.141.1; aiosqlite0.22.1; httpx0.28.1; PyWebView6.2.1;
PyInstaller6.22.3/hooks2026.7. Recorded platform string is
`Windows-10-10.0.22631-SP0`; this does not assert the Windows marketing edition.
WebView2 observed owned executable version:154.0.4258.53.

## Fresh unified source acceptance: 25/25

`gate-results.json` records each command, exit/expected exit, PID and time.
`validation-final.json` is the **source-batch** receipt; its pending package/native
field is historical to that checkpoint, not the later package result.

| Gate family | Actual result |
| --- | --- |
| Python full suite | **735 passed,2 skipped,1 warning**,390.10s |
| Ruff / compile / locked dependencies | All passed |
| Lifecycle repeats | **106 tests ×5**, all passed; exact node IDs retained |
| MCP repeats | **23 tests ×3**, all passed; exact node IDs retained |
| Safety / task-fixture audits | Passed without relaxing inputs/oracles |
| Production factory support matrix | **81/81 supported passed;99 excluded** out180 |
| Production service support matrix | **126/126 supported passed;54 excluded** out180 |
| Fault matrix | **10/10** expected controls passed |
| Load / contention |100/100 complete, peak4/configured4; queue2 accepted/1 explicit rejection;20 readers/5 writers, peak writer1, zero read/write overlap |
| Mock plumbing |180/180; model-quality scores null |
| Preflight / full-mode guard | Passed; full mode correctly refused with **exit2** |
| Frontend tests / types / build | **56 passed/7 files**, types/build passed |
| Diff hygiene | Passed; line-ending warnings informational |

Lifecycle selectors now include ownership, retention, segment deadlines,
subagent/tool drain, provider retry/probe cleanup, native conversation and native
settlement controls; MCP includes discovery/catalog/executor/API plus actual
parent-child cancellation. No old `-k` silently excludes added tests.
See `repeat-collections.json` and all eight repeat logs.

Skips: `tests/runtime/test_deep_backend.py:38` and
`tests/test_runtime_review_inputs.py:49`, unavailable symlink creation.
Warning: Starlette's deprecated AnyIO BlockingPortal alias.
Support matrices use production dispatch and sealed inputs, with zero
infrastructure failures; excluded paths are **not successes**. No full180
production/model evaluation or real-model quality claim follows from these gates.

## Independent wheel/sdist installation

`package-content.json` checks **93 required source/data files** against the
accepted inventory, metadata0.14.4/three entry points and no private workspace or
environment settings in archives.

| Artifact | SHA-256 |
| --- | --- |
| `doppel_agent-0.14.4-py3-none-any.whl` | `0683d2dc7b97f5456bc3e49ee8e9bbdcf8f1b42e22a65da41ac041e5b52c04fb` |
| `doppel_agent-0.14.4.tar.gz` | `2886e996601ac916a27e2f9871be13c1f58fea50dd0e95ecc8faaeb89cccfae0` |

Rebuilt sdist wheel has every member name/byte identical to original wheel;
different ZIP-container hash is not silently called byte-identical artifact.
Fresh environment installs exact wheel **noneditable**, with89 applicable locked
dependency versions matching both install and build environments;9 platform-marker
exclusions are named in `package-dependency-parity02.json`.

`wheel-smoke.json` proves site-packages import provenance, actual production
Legacy/Graph/Deep reads without fallback, all three HTTP routes200, server/listener
shutdown and seven SQLite integrity/exclusive-reopen checks. Its original working
directory was the ignored artifact directory, not importable source/test roots.
The follow-up `wheel-smoke02.json` repeats these checks from a fresh temporary
directory **outside the entire checkout**, with no source/test PYTHONPATH and
explicit SQLite connection closure. Its fixture is retained at the absolute
temporary path in that receipt, not a user's data directory.

## New isolated packaged desktop

Accepted EXE:
`.artifacts/g-local-20261004-01/desktop02/DoppelAgent/DoppelAgent.exe`.
SHA-256 **`937520e8d621ab401b3e32a1905633c2f4b6ff809eacd7461f9b3fc38f776c62`**,
size21,284,555 bytes.

`desktop-manifest.json` records343 payload hashes,82 compiled application modules
matching normalized accepted source code, six web assets matching file hashes,
icon and entry linkage. Only build-local code filenames are normalized; module
extraction does not execute the archive. Vue bundles are
`index-BKvV4ZSj.css` and `index-Cl6RSzew.js`. Linkage alone is not execution proof.

Native automation targeted only this exact returned EXE window/PID85544. Root
Legacy rendered first, then Runtime Lab opened in the native WebView window.
Native screenshots/accessibility:
`native/01-legacy-root.jpg`, `02-graph-read.jpg`, `03-deep-read.jpg` and matching JSON.

| Native basic case | Durable result |
| --- | --- |
| Graph fresh `read evidence.txt` | `f89b878fb49148c5a1d6b02d31d190c0`,completed; actual Graph finish; fixture marker returned |
| Deep fresh `read evidence.txt` | `1d89b6fa6a694497a2233607ff7904aa`,completed; actual Deep finish/read_file; fixture marker returned |

Both show two messages, their honest immutable runtime and offline profile. API
run/event receipts verify actual runtimes and no fallback. These are independent
single-turn reads, **not dependent multi-turn/restart proof**. Native usage display
correctly says missing usage cannot be assumed zero; our zero-paid-call statement
comes from fixture/provider isolation, not missing usage.

Observed system DPI144/150%; stable native screenshot1896×1217. No global display
settings changed. Exact client dimensions, minimum window, other scaling and host
maximize/minimize/restore remain unaccepted.

## Idle normal close and store closure — bounded pass

Actual Vue native host close button used; no force-kill. Recorded EXE and six
owned WebView2 PIDs exited. Owned listeners5514/5515 disappeared and connection
probes refused. `native/preclose-processes.json`, `postclose-processes02.json`
and `postclose-audit.json` retain the evidence.

All six native-fixture SQLite stores pass integrity and exclusive transaction
reopen with explicitly closed connections. After close, their WAL/SHM are absent;
this is observed state, not the sole closure test. The OS workspace lock can be
reacquired/released. Full busy-work close, second-owner rejection/current-owner
health, native reopen and interrupted approval are still **pending**.

## Retained failed preparation and automation attempts

No production/oracle changes were needed for this G increment:

- `uv pip sync -r` was wrong CLI syntax; positional-file retry preserved.
- Offline dependency sync lacked cached pythonnet3.1.0; hash-locked online package
  sync succeeded. Package-index downloads are not paid model calls.
- First dependency parity parser ignored PEP508 markers;98 naive entries produced
  nine false missing packages. Correct89/9 marker-aware receipt retained separately.
- First PyInstaller spec resolved a relative icon under the spec directory and
  failed before emitting EXE. Absolute icon/entry paths fixed `desktop02`; old
  log/spec retained. Optional pycparser/tzdata build warnings are not automatically
  runtime failures or successes.
- Native capture once needed fresh window bounds; offviewport Send click failed
  before input. Fresh screenshot verified textarea focus; Enter submitted Deep.
  This does not prove a product bug or complete keyboard/focus parity.
- Initial fixture DB query guessed nonexistent `id`; final audit inspects schema
  and uses `run_id`. Initial empty PowerShell PID property serialized `[null]`;
  corrected explicit enumeration in02 records `[]`, original receipt retained.

## Next execution order — do not repeat accepted G1–G4 without source changes

1. **G5 conversation/restart fixtures:** local loopback scripted provider endpoint
   for exact history-dependent Graph/Deep turns; verify existing conversations on
   native reopen, search/groups/archive/profile and Legacy separation.
2. **G5 approvals/effects:** offline reviewed patch fixture; actual native
   approve/edit/reject/cancel/conflict, verification/child inspector and exactly
   one durable filesystem-effect receipt. Preserve fail attempts.
3. **G5 Skill/MCP:** progressive Skill fixture, local stdio/Streamable HTTP servers,
   explicit probe/cache reload, pagination/structured/multimodal/is_error and honest
   sanitized error UI; no remote provider or real settings.
4. **G5/G6 lifecycle/host:** cancellation/queue/child/lock/setup paths, second-owner
   refusal while first healthy, busy normal close/handoff/reopen, native minimum
   dimensions/available DPI/host controls/compatibility reload. Seeded visible
   deletion needs action-time native UI confirmation; never silently claim it.
5. **G7 carried E:** only after every required packaged parity row passes, switch
   default to Vue; preserve Legacy and rollback. Any production/default change
   needs new freeze,25 gates, wheel/sdist/EXE and affected final native acceptance.
6. **G8/H:** full acceptance report then scoped code/security/migration/assets
   review, evidence-linked English README/Chinese README_CN and technical report,
   one batch commit/push, exact remote HEAD and CI. No early release/tag.

Later real evaluation is separately authorized and budget-gated under the same
total10CNY policy. G/H cannot alone complete final v1.0. Create next-major chat
only at a genuinely accepted major-version boundary; do not begin speculative
Server Mode/distributed infrastructure now.

## G5 continuation: packaged history-dependent requests, browser management

New evidence: `.bench-results/g-local-20261004-01/native-context01/`.
The preceding goal turn is **progress**, not a wait/no-progress turn: fresh
source/package/native-read/idle-close evidence and authoritative checkpoints
changed. This continuation also produces new measured evidence, not a status-only
restatement.

The same sealed `desktop02` EXE was relaunched against the same disposable
workspace. Only fixture settings were extended with `g-context`, an OpenAI-
compatible profile pointing to **127.0.0.1:7762/v1**, no keys. The external Python
fixture implements the actual Chat Completions boundary; there is no injected
provider in the packaged service. EXE PID108704 owns Legacy7806/API7807.

The fixture answers recall **only from the incoming messages**, not any process-
state nonce cache. Two positive and three negative controls reject missing or
duplicated previous user/assistant messages. Each fresh mode has a different
generated nonce. Second-turn prompt is only `G-CONTEXT recall`, with no nonce;
accepted answer requires exactly one original remember prompt and one original
assistant receipt. Four actual requests passed: Graph remember/recall and Deep
remember/recall. Each conversation retains four projected messages, one stable
checkpoint thread, actual runtime.finished identity and no fallback. Exact
messages/tool surfaces are retained in `requests.jsonl`; per-run events and
`context-before-restart.json` prove the packaged API result.

**Important boundary:** these requests were submitted through the packaged
production HTTP API, not native UI input. The fixture reports synthetic100/10
token usage for display plumbing; this is not measured LLM use, quality or paid
cost. Do not classify P05/G5 native-input/restart parity complete on this evidence.

Native automation selected the exact returned window987072/PID108704 but failed
to activate it. A fresh window inventory/get_window/state and one activation
retry still failed with `failed to activate captured window`. Native inputs
stopped; original root screenshot and recovery/error record are retained. No
locked-screen cause is asserted without confirmation; an asynchronous desktop-
state question was sent to Lv. This is not a proven product defect, a reason to
waive native acceptance or a blocker on all remaining implementation work.

An independent **browser** opened the actual packaged EXE's `/runtime/` page.
Visible actions created `G native-context fixture`, renamed/assigned Deep to
`G Deep durable context`, archived it (read-only task box), unarchived it and
reloaded. API receipt `browser-deep-durable.json` confirms title/group/archive0
and the same four messages. Search by first-turn message nonce found Graph;
Enter opened its immutable Graph history and correct recalled answer. Screenshots
and AX snapshots are retained as `browser-archive.*`, `browser-graph-context.*`
and `browser-graph-recall.png`. Browser host controls correctly remain disabled.
This is actual packaged web-content proof, **not native DPI/window-control proof**.

Two automation-only attempts are retained as provenance: a management-modal
combobox locator initially matched both background and dialog fields; it was
scoped to the observed dialog before saving. Search result was a button, not a
guessed option; fresh AX inspection located it and Enter opened it. Neither is
silently recorded as a product failure or successful initial action.

Installed-wheel RunService in a separate process was refused by this live EXE's
OS owner lock; import provenance/expected RuntimeError are in `second-owner.json`.
Primary packaged API remained healthy afterward. This is a real cross-process
installed-versus-packaged lock check, **not a second windowed EXE startup test**.
`stage-audit.json` verifies exactly four fixture requests (no implicit UI probe),
unchanged source/guarded inputs/daily app and zero paid calls.

At the latest verified checkpoint, EXE108704 and fixture Python94436/session13867
remain live/responding with their own loopback listeners. They are intentionally
retained for next native normal-close/reopen/context proof; no forced termination
or restart is claimed. `live-stage-state.json` records the observation, but future
work must recheck actual process/session/listeners, not trust the file as proof.

**Next:** recover the interactive desktop/native-control path, observe existing
packaged histories, submit history-dependent turns through the native composer,
normally close and verify owned cleanup, relaunch the exact EXE, and run the
fixture's `reopen` case on retained conversations. Then approvals/effects and
Skill/MCP native flows, with busy shutdown/handoff/host parity. G/E/H and final
v1.0 remain incomplete; no default switch, source change, commit or release.

## G5 native continuation — dependent context and real restart passed

The later native-control recovery succeeded; the earlier activation failure is
retained as provenance, not silently erased or attributed to a guessed desktop
lock. This increment uses the same sealed desktop02 EXE/source, fixture workspace
and request-derived loopback endpoint. Previous goal turn was task organization
only (no acceptance progress); this turn produces new native/durable evidence.

| Mode | Native conversation / stable thread | Measured outcome |
| --- | --- | --- |
| Graph | `fc7a60d875da449da85bcba01d52bba3` / `native-7a634fdf95f34d449ee29071648c7d9d` | native remember/recall, real process restart, native reopen;6 messages/3 completed runs |
| Deep | `7d6ed9c5dd984bb59a207e4d2b575d5a` / `native-1714ca526b984d1b8e7e99edf056866b` | native remember/recall, real process restart, native reopen;6 messages/3 completed runs |

Graph nonce `graph-native-8d4c31` and Deep nonce `deep-native-7a6b42` are supplied
only in their first turns. Recall/reopen prompts contain no nonce. The fixture
requires exactly the prior user/assistant receipts in the incoming history and
does not cache answers. Every run has correct actual runtime.finished identity,
stable thread/mode/profile, completed status and no fallback/draining lease.
Verifier `native_context_readback.py` makes GET requests only; native inputs
were actual textarea focus/type/Enter actions, not API-submitted runs.

Evidence in `.bench-results/g-local-20261004-01/native-context01/`:

- `native-context-before-restart01.json`, `native-context-after-restart01.json`:
  complete conversations/runs/events and exact expected message sequence.
- `native-graph-two-turn.*`, `native-graph-after-restart.*`,
  `native-deep-after-restart.*`: native screenshot/AX proof. Earlier
  `native-deep-two-turn.*` and mode-immutability snapshots are retained.
- `native-preclose01.json`, `native-postclose-processes01.json`,
  `native-postclose01.json`, `launch02.json`: EXE108704 and six owned WebView2
  instances exited on host close;7806/7807 disappeared/refused; six stores pass
  integrity/exclusive reopen with closed connections; owner reacquired/released.
  Exact same EXE/workspace relaunched as80032 with13450/13451. This is genuine
  process restart, not page refresh. WAL/SHM absence is observed supplementary
  evidence, not the sole database-closure test.

### Bounded native management proof and second process restart

Actual EXE UI created `G native management fixture`, assigned Deep's native
conversation, renamed its title to `G Deep native durable context`, archived it,
and visibly disabled its textarea/Send. Native Ctrl K searched
`deep-native-7a6b42` (no longer in the title), found the archived conversation's
message body, and Enter opened it. Native unarchive restored editable state.
Group filtering showed one matching conversation; native rename dialog changed
the group to `G native management renamed`, preserving group identity
`a4024e86b70e47e98dd1ac84ae9a8d29`. Native page reload preserved data.

Screenshots/AX: `native-management-title-group.*`,
`native-management-archived-readonly.*`, `native-search-archived-body.*`,
`native-management-group-renamed.*`, `native-management-after-page-reload.*`.
API/store receipts `native-management-archived.json` and
`native-management-before-restart.json` agree with six messages/title/group and
archive transitions. Native fixture settings inspection shows local7762 URL,
scripted model, no saved Key and explicit nonautomatic connection-test warning;
no setting was saved, Key accessed or connection test triggered.

A second host close/relaunch verifies the management data through actual process
restart, not only browser state. Six fixture stores again pass integrity/exclusive
reopen, owner lock reacquired/released and13450/13451 refuse connections.
`launch03.json` records new EXE28832; native Runtime entry at7295 shows the same
title/group/six messages/editable state. `native-management-after-restart.json`
equals the entire pre-close conversation detail; screenshot/AX is
`native-management-after-process-restart.*`.

### Retained failures and evidence boundaries

`native-continuation-failures01.json` preserves these attempts:

- A compound PowerShell relaunch command was policy-rejected before execution;
  no launch receipt/EXE appeared. The explicit fixture-only Python launcher
  subsequently succeeded; only its actual receipt counts as launch evidence.
- Native dropdown indexed clicking encountered an owned WebView2 popup outside
  target geometry. Reobserve/activate/native keyboard navigation verified the
  selected group before saving. Unknown click outcome was not counted as a pass.
- Second normal-close PID-only audit reported PID107644 still present, but its
  recorded survivor was new `pwsh.exe`/parent62980, not the original
  WebView2/parent108596. Preserve `native-postclose-processes02.json`; refreshed
  identity-aware `native-postclose-processes02-identity.json` verifies recorded
  instances exited (the reused shell had also exited by then). No unrelated
  process was killed. Future snapshots include creation time to avoid PID reuse.
- A third-launch click without screenshot-backed window geometry failed with
  `coordinate input geometry is unavailable`; fresh unique selection/screenshot
  and one retry succeeded. No production/oracle repair was needed.
- A guessed historical close-receipt subdirectory was absent; the observed
  root receipt/script was inspected instead. New close audits do not reuse its
  historical exactly-two-runs assertion.

`native-stage-audit01.json`: source/guarded inputs/daily-app hashes,size,timestamps
and EXE unchanged; fixture journal has **10 successful requests**, explicitly
**4 prior API turns +6 native-composer turns**. Synthetic100-input/10-output usage
tests display/accounting plumbing only, not measured model tokens or quality.
Fixture store contains12 completed runs (two earlier Mock reads, four prior API
turns, six native turns). Opening native settings/management and both relaunches
add no implicit provider request. Zero paid calls derives from fixture/provider
isolation, not absent usage. Source G1–G4 results were not unnecessarily rerun.

This closes native dependent Graph/Deep context/restart and the named bounded
management paths, **not all P01–P15 or G5/G6**. Title-search/arrow selection,
seeded visible deletion with action-time confirmation, full profile/Legacy/error/
keyboard cases, approvals/effects/verification/children, Skills/MCP, busy close,
second native EXE/handoff and full host sizing/DPI/compatibility remain pending.
G7/E default switch, H delivery, separately gated evaluation and final Task29
remain unaccepted; version0.14.4/root Legacy/daily EXE/no paid work unchanged.

`native-live-state01.json` records current EXE28832, fixture94436, ports7294/7295
and7762 and packaged healthok. Processes intentionally remain for subsequent
fixtures; file observations are not authority to reuse stale handles. Next:
remaining native management/profile boundaries, then offline approval/effect
fixtures, Skills/MCP and lifecycle/host rows. Goal remains active.

## Native G5b continuation: profile defect found and source repaired

This turn makes measured progress rather than restating the queue. The unchanged
sealed EXE passed native two-title search with visible Down/Up selection and Enter
open. Legacy history was genuinely empty and separate; Shift Enter inserted a
newline without submitting (GET-only receipts retained native6/Legacy0 and ten
fixture requests). Screenshots/trees are in `native-management01/`.

The next native Enter submission exposed a real defect: although the UI showed
`g-offline`, implicit Legacy draft creation called `select()` before capturing the
selected profile; the unbound draft restored global `g-context`. Conversation
`b5f311cdf92847ec93d3236f48540cc2`, run `cf4e134b73634226a887b8f5650ac9b7`
failed with sanitized `ProviderRequestError: provider HTTP 400`. The unchanged
strict fixture rejected unsupported `read evidence.txt`; **no fixture/oracle was
relaxed**. Journal denominator is now11: ten earlier200 plus this one400. This is
not successful Legacy runtime/profile acceptance, nor an external/paid request.

Three new regression cases first failed on exact default-versus-chosen mismatch:
implicit first submission and explicit agent/review drafts. The minimal source
repair captures the chosen profile before awaiting draft creation and persists
it before selection or model startup. Retained RED/GREEN logs and independent
frontend checks show **59 passed/7 files**, types/build pass; Vue JS is now
`index-DphE2Ezn.js`. This is a source fix, **not yet a repaired-EXE GUI pass**.

EXE28832 normally closed with creation-time/executable/PID identity and both
7294/7295 listeners gone. All7 actual fixture SQLite files passed integrity,
exclusive reopening, closed connections and owner-lock acquire/release; no
force-kill. The first auditor asserted the old six-store count and failed:
Legacy execution created `tasks.sqlite3`. Inspection verified exactly the prior
six-store set plus that named seventh DB; corrected audit checks all seven,
retaining the initial failure in `defect-and-repair01.json` and tool traceback.
This is idle close, not busy shutdown. The fixture server is retained separately.

**Current authority:** source changed, so historical G1–G4/package/native evidence
does not prove final-source acceptance. Fresh `g-local-20261004-02` refreeze,
25 gates, wheel/sdist/noneditable smoke, EXE linkage and the exact native regression
must complete before continuing parity. Root Legacy/version0.14.4/daily EXE stay
unchanged; G5/G6/default switch/H/final release remain open, paid calls0.

## Repaired attempt02 receipts and next-stage organization

This checkpoint supersedes the preceding “fresh cycle pending” handoff, while
retaining all old failures and pre-fix evidence. It rereads completed receipts;
no new test/native execution is claimed by the planning update.

- `g-local-20261004-02/validation-final.json`:25/25, source/inputs/daily unchanged,
  zero paid calls under trusted fixture/Python socket controls, not OS attestation.
  Logs:735 Python passed/2 symlink-environment skipped/1 deprecation warning;
  frontend59/7; lifecycle106×5; MCP23×3. Capability denominators remain factory
  81/81 supported with99 excluded, service126/126 with54 excluded out of180;
  Mock plumbing is not model quality.
- `package-cycle-final.json`:11 passed steps; sdist rebuilt wheel members identical,
  outside-checkout noneditable smoke and packaged source/assets linkage accepted.
  Applicable locked dependencies89/89 with9 marker exclusions. Offline pythonnet
  3.1.0 cache miss required exact locked registry acquisition; preparatory GBK
  read failure was corrected with explicit UTF-8. Neither is erased or a paid call.
- Current source inventory:
  `c7d93e62c5275ef158195d3350e6ace146c7a9ff1eb2a0f21690082578291c46`.
  Current EXE21,285,750 bytes, SHA
  `b913676c9af34949377f23cc9dff1d29241fd3197c03f6e040125ee991c37bcb`, under
  `.artifacts/g-local-20261004-02/desktop02/DoppelAgent/`.
- Attempt02 `native-profile01/` native screenshots/GET-only receipts prove chosen
  `g-offline` with global `g-context` survives implicit first submit and explicit
  agent/review draft creation. Conversation `5f51759318c341638fb2962710a2476d`,
  run `c2c62f753a8d411d9394da70f1e1a0da` contains actual Mock file-read answer;
  agent/review drafts contain no messages. Legacy counts0→0→1→2→3→3,
  native count0 and loopback requests11 unchanged. Reload/history reopening are
  evidenced; repaired-package true process restart/normal close is still pending.
  The earlier package native-pending field predates these bounded native receipts.

Next tasks are [N0–N6 in the existing G plan](../plans/2026-10-04-g-consolidated-acceptance.md):
seal repaired profile/events and idle exit/restart first; then current-source
Graph/Deep/conversation boundaries, actual approval effects, Skills/MCP, busy
lifecycle/second native owner/host parity; only then conditional default switch
with final-source25/package/native cycle and H review/delivery. Old native proof
is historical; full parity/effort restoration/G/H/final v1.0 remain unaccepted.

No source/default/version/tag/daily replacement, commit/push, paid calls, new
chat/agent/worktree or real-data operation occurs in this organization task.
Do not repeat unchanged attempt02 gates solely because these docs changed.

## N0 execution: repaired native profiles, normal exit and true restart

New measured evidence is sealed at
`g-local-20261004-02/native-profile01/n0-final01.json`. Native three-case Legacy
profile repair is now accepted with true process restart, not only page reload.
One completed Mock run `c2c62f753a8d411d9394da70f1e1a0da`, actual `read_file`
request/completion, eight ordered events and exact answer match unchanged fixture
text; two named drafts remain empty. Global `g-context` does not replace their
persisted chosen `g-offline`. All3 histories/profiles/run/events are identical
pre/post restart and visible in native reopening screenshots; native history0,
fixture journal11 and new provider/paid requests0 throughout N0.

Native normal close exits root111680 and6 owned WebView2 identities and both
13456/13457 listeners. First sample observed listeners gone but root still alive;
later identity-aware sample confirmed all exited before relaunch. Retain both
snapshots; this is observable drain, not forced termination or busy-close proof.
All4 actual stores (conversations/mcp-tool-executions/runtime/tasks) match the
pre-close set, integrity/exclusive-reopen with closed connections/no WAL-SHM,
and owner-lock reacquire/release using independently installed package code.

Same immutable EXE/existing fixture restarted as113260 with new native window
and ports1088/1087. Root remains Legacy and no fixture reset or implicit migration
occurs. `restart-live01.json` records identity/listeners/health, not permission
to reuse stale handles. Source/guarded inputs/entire daily app/EXE audit match.

Retained audit failure: output_bytes was incorrectly compared to disk51 CRLF
bytes; actual read_file text normalizes CRLF to LF and the loop correctly records
50 returned UTF-8 bytes. Exact raw/decoded contracts and source were inspected,
then verifier corrected; `validation-failures01.json` retains the failure, without
changing production, fixture bytes, answer or provider protocol. Initial
post-close “not drained yet” observation is also retained rather than relabelled
as an immediate-exit pass.

Next execution is N1 current-package Graph/Deep new-nonce context/restart and
remaining conversation/profile boundaries. Effort/review restoration, complete
G5/G6/P01–P15, conditional default switch, H and final v1.0 are not accepted.
Version0.14.4/dirty batch/root Legacy, no paid work/commit/push/new major chat
remain unchanged. Full persistent objective remains active.

## N1 context subset: repaired Graph/Deep history across true process restart

New seal: `g-local-20261004-02/native-context02/n1-context-final01.json`.
Current repaired EXE creates Graph conversation ed10d41be7644977a208e99731205911
and Deep conversation caa6229eaf3d4dbda242d02e63c706d9 through the native composer.
Both exact nonce-based remember/recall pairs pass; normal host close and genuine
process restart preserve prior four messages, run records and events exactly.
Native reopening then submits reopen once each: both completed, six total runs,
six messages per conversation, stable mode/thread/profile, no fallback or lease.
GET-only acceptance reconciles actual runtime.finished and completed
run.status_changed events with native completion/history screenshots.

Strict local journal17 retains baseline11 (historical400 included), adds exactly
six200 responses whose answers derive only from requested history. No cache or
relaxed duplicate protocol; synthetic100/10 usage is not LLM-quality/cost proof.
N0's three Legacy histories remain exactly equal, no migration/reset.

Idle native close releases113260 plus owned WebView2 identities/1088/1087 and all
seven actual DBs (checkpoints/conversations/deep-checkpoints/mcp-tool-executions/
runtime/tasks/tool-executions), independently checking integrity/exclusive reopen,
closed connection/WAL-SHM absence/installed owner release. Same sealed EXE and
workspace restart109228, creation2026-10-04T14:03:06+08:00, window2495142,
ports7598/7597. `restart-live01.json` captures fresh process/health and loopback
fixture identity; it is a checkpoint, not permission to reuse stale state.

Retained failure provenance: initial verifier used nonexistent run.completed;
actual source contract uses terminal run.status_changed. Geometry-unavailable
close attempt did not close the app; first process snapshot/failed45-second wait
is explicitly ineligible. Refreshed actual native close and subsequent sample02
prove drain. No production/fixture/oracle change, forced termination or paid call.

Final source/input/daily/EXE audit matches attempt02. This seals the N1 context
subset only; remaining conversation/profile/keyboard/restoration parity precedes
N2–N6. Full G5/G6/default switch/H/final v1.0 remain open. Version0.14.4/root
Legacy/dirty batch/no commit/push/tag/new chat unchanged. Latest persistent goal
tool reports usageLimited (objective incomplete), not complete; scheduler state
is unchanged by this human-requested continuation.

## N1 management/profile/keyboard/restart subset; N6 not reached

`g-local-20261004-02/native-management02/n1-management-final01.json` and
`n1-management-followup01.json` seal actual native create/rename/group/filter/
archive-read-only/unarchive/search, review template/quick/no-grants, missing chosen
profile without global fallback, immutable existing Graph versus next Deep draft,
sanitized HTTP failure, busy-control locking/recovery, Enter/Shift Enter/Ctrl K/
arrows/search closure and selected-history restoration. First Escape clears native
search input; second Escape closes the modal. First observation is retained, not
relabeled as a close pass. Ctrl R alone is not restart proof.

Current totals: **5 native conversations, 8 runs, 15 messages**, one group;
earlier two context conversations/six runs/events and all three Legacy histories
remain exactly unchanged. Strict context journal17 unchanged; separate local
controlled fixture4 actual requests = three500 adapter retries plus one200 busy
release. Failed Graph `490d8fb9d75c4f03a5ed518e7abed1e3` has one failed user
message, no fabricated answer/private-body marker/lease. Deep
`0b48ad6475df4b82bd912fe6368c1c80` completes and restores controls. Global
`g-context` remains separate from chosen `g-offline`/credential-free native fixture.
Synthetic100/10 usage is not paid/model-quality/cost proof; paid calls0.

Normal idle native close109228 drains its six WebView2 identities and7598/7597;
retain first still-draining sample and later10.688-second complete exit. All seven
actual DB paths pass integrity/exclusive closed-connection/WAL-SHM/installed owner
release checks. Same unchanged EXE and existing fixture restart48564 with new
creation/window55250290, UI2952/API2951; root Legacy and selected native completed
history restore. All conversation/run/event records exactly match before/after
and keyboard checks. `restart-live01.json` records actual identity/health/listeners.

`validation-failures01.json` retains offscreen/non-target UIA, asynchronous-scroll
misclick and first-Escape observations; recovery uses fresh stable observation,
without duplicate creation/submission or relaxed oracle. Source/inputs/daily/EXE
unchanged; no new production code, default/version switch, commit or push.

**Full N1 remains unaccepted:** action-time confirmation was requested for only
zero-message/zero-run native draft `8b98ff340f4242728f676b2e39eaccca` titled
`N1 disposable native management 20261004`; no human reply, deletion or API
substitute occurred. Final required-row reconciliation and adjacent audit checks
still precede N2 acceptance. N2 preparation adds14 isolated patch files and
42 positive/42 negative fixture controls, with exact hashes/diffs/ledger baseline
and no requests/runs. `native-approval02/fixture-preflight01.json` explicitly marks
native N2 false; configured verification/timeout/child still need setup.
N2–N6/default switch/H/full v1.0 remain open, scheduler usageLimited unchanged.

## Recovery/focus/scroll and independent verification preflight

Current revalidation finds historical EXE **48564** and fixture94436 /
114032 handles absent twice, with all four old listeners absent. Their latest
exit reason/normal-native-close provenance is unproven; do not convert it into a
lifecycle pass. `native-recovery03/recovery01.json` audits all seven actual stores
and installed owner release before restarting the identical EXE/existing fixture.
No force-kill/reset or overwritten historical evidence. Current EXE117092,
creation2026-10-04T19:17:49.124727+08:00, window921516, UI2346/API2345; independent
fixture processes57924:7762 and103688:11351 use unchanged oracles/fresh journals.
`live01.json` proves actual current process/listener/health identities.

`native-recovery03/n1-recovery-final01.json` seals exact five conversations/eight
runs/15 messages/events, all three Legacy histories/group/settings unchanged after
recovery and native focus/scroll checks. Ctrl K from focused composer, focused
empty search and Escape restore visible composer focus; scroll/modal closure
preserves visible anchor layout. No pixel/DPI or restart-scroll claim. Historical
17/4 request journals untouched; recovered journals0/0, paid calls0.
First non-target capture is not saved/accepted. Later user-input guard interrupts
scroll-to-top; only fresh observation follows, no false top-position pass or
inferred delete authorization. Native empty-field validation bubble is retained,
with exact data/zero requests proving no submission.

N2 `verification-preflight01.json` adds eight actual independently supervised
child commands: two positives and six byte-conflict/no-effect/missing negatives.
Fixed per-mode allowlisted argv/config templates remain outside the live fixture;
14 patch inputs/ledger baseline are unchanged and no model answer is consulted.
This is oracle/process preflight, **not native N2 approval/verification/delegation**.
Deep direct patch adapter is not its own Graph ledger: native checkpoint/effect /
duplicate/crash reconciliation is still required; no fabricated durable receipt.

Source/input/daily/EXE guard unchanged; no new source/default/capability/version/
commit/push. Full N1 still awaits existing action-time empty-draft delete response
and final row reconciliation. N2–N6/H/full v1.0 remain open. Current goal-tool state
is active, superseding older usageLimited observations; objective is not complete.

## N1 outstanding gate and blocker audit — 2026-10-04

Required-row reconciliation links existing context, management, profile, busy,
keyboard and recovery receipts; the native loading state is already captured in
`native-management02/busy-accepting01.json` with a queued run, disabled submission
and visible loading. This is not a new native run or expanded acceptance claim.

Read-only `native-recovery03/blocker-revalidation01.json` reconfirms five native
conversations/eight terminal runs/zero leases. The sole disposable empty draft
`8b98ff340f4242728f676b2e39eaccca` remains intact. Required action-time human
confirmation has not arrived across three consecutive goal turns. N1 deletion
and its adjacent-history audit remain open, hence sequential N2–N6 cannot be
accepted. The goal is being marked **blocked, not complete**, awaiting the
already-issued exact-target question; no duplicate approval card/API bypass.
Earlier safe preflight remains preflight, not native acceptance. Paid calls0,
no new source/default/version/commit/push. Resume at fresh exact-target native
observation after confirmation; retain the full objective and delivery gates.
