# Task F — original Local Mode DoD audit (2026-10-04)

Authority: [A–H continuation](../plans/2026-10-03-v1-local-completion-before-evaluation.md)
and the original plan's Task5, API inventory and twelve-row Definition of Done.
Baseline HEAD remains `952a03b7d9dda78dc0f2188e32a620279f1211c2`; version **0.14.4**.
This closes the source/contract audit and its named backend gaps, **not whole-v1
completion, E default replacement, G packaged acceptance, H delivery or evaluation**.
The full acceptance result below must be read with its source hash and retained failures.

## Required gaps repaired

### Exclusive Local Mode service ownership

`persistence/ownership.py` uses an OS lock held on an open sentinel handle:
Windows `msvcrt.LK_NBLCK` on byte0; POSIX nonblocking `flock`. The sentinel is not
unlinked and stale PID/mtime guesses are not used. The kernel releases ownership
on process death. `RunService.start()` acquires it before runtime/child recovery;
concurrent starts serialize and a second owner fails without recovering live runs.

Owned startup SQLite work drains despite cancellation. Startup failure cleans up
before release. Close rejects new mutations, waits for admitted request IO and
scheduler/process/owned-provider/MCP cleanup, and only then releases the owner. Repeated
close/cancellation drains the same owned future. A cleanup failure is fail-closed:
the lock remains until process exit, rather than permitting concurrent uncertain
owners. Closed service instances are not restartable; construct a fresh instance.
Public create/cancel/resume and child mutations require this lifetime. Read-only
inspection can still read durable records after close. Store constructors perform
additive schema setup, not recovery. This is RunService ownership, not an OS sandbox
against unrelated tools or a claim that the historical Legacy console is locked.

The old approval-race fixture which started two active services in one workspace
is now an **exclusive-owner reconstruction**, with two stale concurrent requests
on its current owner. The one decision/one patch assertions remain intact; the new
owner tests separately prove the second live owner is rejected. Windows hard-exit
proof targets the actual lock-owning Python PID, not merely its venv redirector.
Native packaged duplicate-launch UX/process acceptance is still G.

### Checkpoint retention, not audit/secure erasure

`persistence/retention.py` defaults to the newest **32 snapshots per checkpoint
namespace** for quiescent registered threads. Entire obsolete registered terminal
threads expire after **30 days** only when they are not a current visible native
conversation thread. Archived-but-visible current threads are also protected from
whole-thread expiry. Queued/running/interrupted/leased and unregistered factory
threads are not pruned. Terminal child snapshots are compacted, not expired.

Checkpoint write locks are acquired first, in fixed DB order, **before** the runtime
admission lock. Waiting on a busy saver therefore does not hold the DB needed by
its durable events. Checkpoint commits drain while the runtime admission lock is
still held. The selector holds a runtime SQLite `BEGIN IMMEDIATE` through checkpoint selection
and deletion, serializing new admission/turn leases/child changes; it does not prune
from a stale list after another turn starts. It touches only the three fixed owned
checkpoint DB names (`checkpoints.sqlite3`, `deep-checkpoints.sqlite3`,
`focused-fallback.sqlite3`); absent DBs are not created and escaped paths are rejected.
Removed checkpoint writes are removed with their snapshots. Runtime runs, durable
events, conversation tombstones, accepted-request idempotency and **all side-effect
ledgers remain**. Current completed snapshots contain complete conversation state;
arbitrary historical checkpoint time travel is intentionally not a Local Mode API.

Maintenance runs on exclusive-owner startup, terminal root-turn cleanup and each
terminal child generation. A real three-generation child follow-up regression
retains two snapshots instead of growing to nine; stable child id/history survives.
SQLite/IO maintenance errors after a committed outcome are logged by exception class
without rewriting that successful turn; startup maintenance errors fail startup.
`dry_run=True` reports counts without deletion. Pages may be reused via WAL/freelist;
there is no automatic VACUUM, secure erasure, hard total-disk quota or automatic
retirement of current conversation/audit/ledger data. Factory-only historical
threads require their own explicit policy; the service never guesses their owner.
Deletion tests use disposable stores only. Real Graph **and Deep** tests retain just
two snapshots, reconstruct the service, and answer from all four prior/current turns.

### Original Skills/MCP management API inventory

The original five routes now exist before the Legacy proxy catch-all:

- `GET /api/v1/skills`: progressive name/description catalog, not instruction bodies.
- `POST /api/v1/skills/reload`: fully validates a new candidate before atomically
  replacing the cached catalog; a failed reload retains the prior valid catalog.
- `GET /api/v1/mcp/servers`: configured name/transport/concurrency only; **no connection**,
  arguments, cwd, URL, env values or authentication material.
- `POST /api/v1/mcp/servers/{name}/probe`: explicit health discovery.
- `GET /api/v1/mcp/servers/{name}/tools`: explicit production paginated catalog.

All retain loopback Host/same-Origin middleware. Unknown servers404; sanitized
Skill validation422, MCP discovery502/timeout504. No raw transport exception reaches
HTTP error text. Skill runtime sources are validated per newly constructed Deep run;
reload does not rewrite an already running runtime's instructions.

Discovery has a10-second request deadline and connection/initialize has its own
10-second owner-task deadline. Cleanup must still drain; **not a hard10-second HTTP
latency SLA**. Healthy MCP session lifetimes are not limited to10 seconds. SDK
connector enter/exit remain in the same task. Pagination rejects repeated cursors,
more than100 pages or more than4096 collected tools rather than looping/growing
forever. No MCP tool execution route, auto-probe or automatic paid model call was added.

### Segment deadlines and accepted worker/SQLite lifetime

Create/resume use a monotonic deadline measured from request admission, including
queue delay, runtime initialization, provider/resource and workspace-lock waits,
execution and outcome IO. An expired queued job fails before provider/tools when
dispatched; there is no separate queue-expiry timer or hard HTTP latency SLA.
Human approval waiting is not charged to the fresh resume segment. Each scheduled
child generation has the parent's configured duration as a fresh budget, including
initialization. Explicit cancellation wins if already requested before dispatch.

Legacy Core is synchronous: cancellation drains its real worker before workspace
ownership/locks are released. It does not gain hard-abort/native-resume capability.
The async bridge for synchronous providers also drains its actual worker. Native
async HTTP remains cancellable. Likewise synchronous local tools and async-ledger
SQLite begin/finish work drain
despite repeated cancellation. If a synchronous Graph tool really completed after
cancellation, the actual ledger result is retained and actual `patch.applied` /
`graph.tool_finished` receipts explicitly carry `cancel_requested=True`. Cancellation
is not rollback; no receipt is invented for failed/indeterminate work. Cancellation
during an async external operation leaves its ledger indeterminate, never blind-retries.

Child admission, generation finalization/event IO and scheduler shutdown are owned
tasks. Concurrent follow-ups claim the next generation in one SQLite transaction;
one succeeds, the other fails without duplicating generation/history. Root failed,
rejected and cancel-decision IO also drain before owner handoff. Tests hold real
worker-thread gates, repeat cancellation, and prove a second service cannot take
ownership before the actual SQLite operation finishes. Child/run failure records,
tool-ledger error receipts and MCP transport failures retain stable class-only
messages instead of raw exception text; normal tool content is not universally
redacted and transport failure is never automatically retried.

The built-in async provider now has an aggregate execution-segment allowance of
**two extra retries and10 seconds cumulative reserved backoff**, in addition to its
existing per-call attempts/retry window and the service's total deadline. This
caps retries across model turns, not just each individual HTTP call. Initial
requests are not retries; model-step budgets remain separate. Direct Graph/Deep
run/resume set the same scope, and Deep's Graph fallback inherits it. Each service
worker/resume/child generation creates a fresh scope inside the actual scheduled
operation, not merely the HTTP task: scheduler workers were started before that
task and do not inherit its context. Concurrent runs sharing one provider have
independent allowances. Standalone provider calls retain their per-call policy;
opaque custom providers' internal retries are not governed by this adapter.

Half-open circuit probe ownership is released on retry-backoff cancellation and
invalid/oversized response exceptions; an aborted probe cannot permanently block
the profile. All new retry/probe proofs use `httpx.MockTransport`, not network or
model quality evidence. No price/paid-budget/capability contract was relaxed.

Native terminal status/projection can be visible while owned cleanup still holds
the turn lease. `lease_active` and `active_run_id` remain authoritative for next
admission. API tests now wait for terminal **and lease drain**, while interrupted
turns intentionally retain their lease. A deterministic held-release test proves
status-completed/lease-active is observable, next admission returns409, and only
after drain does the next turn return202. No early lease release was introduced
to conceal the test race.

## Checked original requirement table

Every row was checked against current implementation and named production-path
tests. The source suite/static gates below are the F evidence; G must rerun its
complete recipe and all changed denominators, not borrow historical success counts.

| ID | Original requirement | Code / current test evidence | Remaining gate / honest boundary |
|---|---|---|---|
| F01 | Same API starts Legacy/Graph/Deep | `api/routes/runs.py`, `runtime/factory.py`; `tests/api/test_runs_api.py`, native API two-turn parameterization | Same start API, not identical capability sets; Legacy has no native checkpoint resume/cancel parity |
| F02 | Persistent interrupt/restart | `persistence/checkpoints.py`, `runtime/{graph,deep}.py`, F ownership/retention; integration checkpoint/interrupt/restart tests, native lifecycle and `test_checkpoint_retention.py` | G repeated lifecycle + actual new package restart/OS exit; historical snapshots are bounded |
| F03 | Idempotent tool side effects | `persistence/tool_ledger.py`, Graph/Deep/MCP gateways; `test_tool_idempotency.py`, `test_service_approval_concurrency.py`, `test_tool_ownership.py`, MCP executor and cancellation tests | Crash/async external uncertainty fails closed, not blind replay or universal external exactly-once claims; cancelled synchronous completion has an actual effect receipt; ledger is not TTL-deleted |
| F04 | Diff approval and patch conflicts | `workspace/patching.py`, `workspace/verification.py`; patch approval/conflict/verification integration and workspace tests; native event receipts | G visible edit/reject/diff/cancel and seeded deletion/error cases in actual packaged window |
| F05 | Progressive Skills/format/path validation | `skills/{registry,resolver,spec}.py`, Deep skill sources; `tests/skills/*`, F `api/test_catalog_api.py` | Runtime is not taught unsafe tools by a catalog endpoint; real Skill GUI flow remains G |
| F06 | stdio + Streamable HTTP, pagination/typed/error payloads | `mcp/*`; `tests/mcp/*`, real local HTTP/Graph/Deep integration tests, F catalog API/control | G repeat real local transport lifecycle with recalculated count; no external configured server probes |
| F07 | Queue/layered limits/cancel/deadline/write locks | `concurrency/*`, `runtime/service.py`, provider retry/circuit policy; scheduler/resource/workspace lock/load tests, `test_service_deadlines.py`, `test_service_ownership.py`, `test_subagent_ownership.py`, `test_provider_run_budget.py`, `test_provider_probe_cleanup.py`, cancellation and tool worker tests | Total segment budget includes setup/queue/lock; aggregate retries span turns/fallback; sync workers drain rather than force-abort; not distributed worker leases; G pressure/fault and actual new EXE closure |
| F08 | Durable reconnectable SSE | `persistence/events.py`, `api/sse.py`; `tests/api/test_sse_resume.py`, native Last-Event-ID tests, Vue controller tests | G native refresh/reopen/reconnect acceptance; no in-memory-only success claim |
| F09 | Windows cancel kills owned process tree | `workspace/process_supervisor.py`; process cancellation integration + capability native controls | G new package child/server/listener/DB closure; do not reuse old EXE proof |
| F10 | Fixed20 ×3 repeats ×3 runtimes | `bench/cases/runtime`, matrix/capability/fixture/safety tests and F static CLI gates | Original180 retained; factory81 supported/99 excluded, service126/54; exclusions are not successes; full-mode guard stays closed |
| F11 | Pressure proves bounds/backpressure/no duplicate effects | `tests/load/test_scheduler_load.py`, concurrency and approval-race tests, benchmark load/fault harnesses | G actual fresh100-job load/fault/supported-matrix and repeated lifecycle reports required; no previous-source report borrowed |
| F12 | README metrics point to evidence, plans not accomplishments | Original plan, parity contract, A–F dated evidence and retained failure receipts checked | H must update English README/Chinese README/report/changelog with G fresh evidence, review/one batch commit/push/exact CI; not passed as a final release gate |

No capability flag/protocol/fixture/oracle/budget contract was expanded. v1.1
PostgreSQL/Redis/Celery/Kubernetes and real-model scoring remain out of this phase.
The source audit has no new unnamed required backend gap. Known UI/native package
items are **explicit G/E gates**, not silently waived to call the project complete.

## Frozen F acceptance

Accepted source inventory SHA-256:
`5cc16169a24553b41a1b59c257169aa1f8e870afe1beee78d0c44792d0129a68`.
`f-source-before04.json` equals `f-source-after04.json`; `f-result04.json` records
**source_unchanged=true, all12 gates passed, paid_calls=0**. The hash includes
tracked/untracked/deleted implementation, tests, benchmark inputs and generated
frontend assets. Ignored evidence and plan/report prose are not source inventory.
The checkout is intentionally dirty in the whole A–H batch, not a clean release.

| F source/static gate | Fresh result / receipt |
|---|---|
| Full Python | **735 passed, 2 skipped, 1 warning**,431.55s; `f-full-final04.log` |
| Ruff / compileall | Pass; `f-ruff-final04.log`, `f-compile-final04.log` |
| Frontend tests | **56 passed in7 files**; `f-front-final04.log` |
| Typecheck / build | Pass; `f-typecheck-final04.log`, `f-build-final04.log` |
| Accounting/safety audit | Pass; `f-safety04.json` / `f-safety-final04.log` |
| Offline fixture controls | Pass; `f-fixtures04.json` / `f-fixtures-final04.log`; not model scoring/full180 readiness |
| Offline Mock smoke | Pass; `f-mock04.json` / `f-mock-final04.log` |
| Preflight | Pass; `f-preflight-final04.log`; no paid run |
| Full-mode guard | Expected refusal **exit2**; `f-full-guard-final04.log`; not a completed180-run matrix |
| Diff whitespace | Pass; `f-diff-final04.log` |

The2 skipped cases require symlink creation unavailable on this Windows host
(`runtime/test_deep_backend.py:38`, `test_runtime_review_inputs.py:49`). The1 warning
is Starlette's AnyIO `BlockingPortal` deprecation, retained rather than filtered.
Generated assets remain `index-BKvV4ZSj.css` and `index-Cl6RSzew.js`; default root
remains Legacy. This is **F's12 source gates**, not G's25-gate/package/native recipe;
fresh supported matrices/repeated lifecycle/fault/load/new EXE/CI remain G/H.

Environment receipt: Windows10, Python3.11.15; `f-dependencies04.json` records
Doppel-Agent0.14.4, pytest8.4.2, Ruff0.16.8, LangGraph1.2.11, DeepAgents0.7.15,
MCP2.2.0, FastAPI0.141.1, aiosqlite0.22.1 and httpx0.28.1. These are installed local
versions, not a claim about newest upstream releases. Frontend/native package
acceptance must use the same new source rather than borrowing the daily EXE.

## Failure provenance retained

Ignored evidence root: `.bench-results/local-native-20261003/`.
All original RED/repair attempts remain, including `f-owner-red.log`, owner green
and focused failures, `f-retention-red.log` and two fixture repair failures,
`f-api-red.log`, `f-pagination-red.log` (missing fixture import path) and the actual
pagination timeout RED `f-pagination-red02.log`. `f-focused-final01.log` records
an interrupted, initially unbounded startup-timeout test run; its owned pytest
process alone was stopped, then bounded connection startup was repaired.
`f-focused-final02.log`:62 passed/1 warning before the additional checkpoint-file
parameterization; `f-retention-final.log`:9 passed. These diagnostics do not replace
the final frozen full-suite result.

The first unchanged-source full batch (`f-result01.json`, source
`64fda05af3e41cf41f12a4cb7845248a331e198b734cbf1cb11474548f2c0dc5`)
failed:695 passed/2 skipped/1 warning, one native cancellation API failure
(`sqlite3.OperationalError: database is locked`). It is **not accepted**. The
checkpoint/runtime lock order was investigated and reproduced using a held real
checkpoint write lock (`f-lock-order-red.log`). Separate checkpoint transactions
now acquire their write locks before runtime admission, with checkpoint commits
before releasing admission. The event-store busy timeout remains unchanged.
`f-lock-order-green.log`:50 passed/1 warning, covering the deterministic contention
regression and native/cancellation/approval/owner/retention paths. A new source
freeze and full batch follow; no failing denominator was hidden or waived.

The second freeze (`f-result02.json`, source
`a6750b1fa98e9a24d9d2f5a5b832887ccd23657ca0e27ff10e9baf19f22a1131`)
passed697 Python tests/2 skipped/1 warning and all12 source/static gates. The
wrapper was stopped by a goal-turn continuation after typecheck; no owned Python
process remained. Only unfinished tail gates resumed after rechecking the exact
source; the truncated build log is retained as `f-build-interrupted02.log`.
That source is superseded by the additional original-policy repairs, **not current
F acceptance**.

The third unchanged-source batch (`f-result03.json`, source
`34458a774c9ded62f62d9379b41a61b9b0ea7e2baa8dae6a89ce3f58aabdef3d`)
failed2 tests, with722 passed/2 skipped/1 warning. One API fixture polled only
terminal status, then expected an already-drained lease; the other fault-matrix
oracle required unsafe raw MCP transport text. The former was deterministically
reproduced without changing production lease semantics (`f-native-settlement-red.log`);
the latter still demands exactly one remote call and now the exact sanitized
class-only message. Both failures remain; a standalone pass never waived them.

Additional original-policy RED/repair evidence is retained: deadlines/raw child
errors (`f-deadline-red*.log`,47-test green), child admission/finalization ownership
(`f-child-red.log`,21-test green; an accidentally removed restart-recovery method
was restored before that green), child retention (`f-child-retention-red.log`,
nine-versus-two snapshots), tool/SQLite ownership and secret errors (`f-tool-red03.log`,
nine actual failures; first two attempts had fixture import errors), root mutation
finalization (`f-root-finalization-red.log`), cross-turn retries/sync provider bridge
(`f-run-retry-red.log`), direct Graph/Deep/fallback retry scope (`f-direct-retry-red02.log`)
and half-open cleanup (`f-probe-red02.log`). An unclosed SQLite connection in a new
test caused `f-tool-green.log` to fail Windows temporary cleanup; it was explicitly
closed, not ignored. `f-tool-green02.log`:75 passed/1 warning.

The initial aggregate-retry repair put context on HTTP admission instead of the
already-started scheduler worker and left4 regressions failing
(`f-retry-settlement-green.log`). The scope moved into each real scheduled segment;
`f-retry-settlement-green02.log`:66 passed/1 warning. Final focused regression
`f-final-focused04.log`:**85 passed/1 warning**, including the two real probe-slot
failures. It is diagnostic evidence, not a substitute for the final frozen batch.

## Next settled phase G, with E/H gates intact

1. Freeze source/fixtures/capabilities/protocol/oracles and rerun the consolidated
   **25-gate recipe** with recalculated lifecycle/MCP/support counts. Add owner and
   retention/deadline/child/ledger regression seams to lifecycle repeats; preserve
   failed attempts. Do not copy the old36×5 /11×3 denominators after adding tests.
2. Build isolated wheel/sdist/noneditable installation and **new isolated EXE**.
   Never overwrite `.dist/DoppelAgent`; do not reuse prior package hashes/windows.
3. Actual native Graph/Deep conversation reopen/history/search/archive/profile,
   approval/edit/reject/cancel/diff/verification/child, Skill/MCP, visible seeded
   deletion, error/keyboard/minimum size/DPI and host window controls acceptance.
   Include second-owner rejection/current-owner integrity and clean handoff.
4. Verify owned child/server exits, listeners vanish, DB integrity/exclusive reopen
   and WAL/SHM state. Switch E's default to Vue **only after** required packaged
   parity passes; otherwise retain Legacy root and its explicit `/legacy/` fallback.
   If that conditional switch changes source, refreeze and rerun consolidated
   acceptance/source-to-package checks before H; pre-switch screenshots are not
   proof of the final default route.
5. H consolidated review/evidence docs/one scoped commit+push/remote HEAD/exact CI.
   Version0.14.4, no tag/daily EXE replacement and zero paid calls remain unchanged.

No keys/account/paid provider settings were read/decrypted/probed; no model-quality
claim or release, commit/push, extra chat/agent/worktree was created in F.
