# Local Mode construction closure candidate — 2026-10-04

## Authority and status

Lv now requests whole-major-version implementation followed by whole-version
testing. This changes execution order, not the v1.0 DoD or release criteria.
Earlier native-first/default-after-parity instructions still describe release
acceptance; they no longer prohibit completing isolated candidate source edits
before the unified test phase. Version fields remain0.14.4, uncommitted in the
same consolidated batch. No daily EXE replacement, release/tag or paid evaluation.

Previous turn made concrete progress: a reusable build-only pipeline and all
three actual build outputs. The earlier Legacy-root build is preserved under
`.artifacts/local-build-20261004-01`; it is not the final-source candidate after
the root change described here.

## Final implementation increment

`src/doppel_agent/api/app.py` registers a hybrid-only GET `/` before the proxy
catch-all, redirecting307 to `/runtime/` with `Cache-Control: no-store`. This
opens the existing native Vue conversations page without moving stores,
submitting a run or probing a model. The existing same-origin middleware still
precedes routing. `/legacy/`, `/legacy` and old asset/API routes remain available.
The separate ConsoleServer keeps its lightweight original root; API-only mode
does not advertise a nonexistent frontend bridge.

New and updated `tests/api/test_runs_api.py` cases assert redirect/no-cache/default
Vue identity, actual old-console bytes at both compatibility aliases, old messages
and native-history separation, asset fetches, no accepted navigation run, bad
Host/Origin rejection on the new root, API-only404 and missing-bundle fail-closed
behavior. They subsequently passed in the fresh attempt03 whole-source suite
(742 passed, 2 host-symlink skips); native EXE acceptance remains separate.
No old Legacy preservation
assertion was removed: its original-root oracle now reads the independent legacy
server rather than incorrectly treating the new Vue root as old-console bytes.

README.md, README_CN.md, CHANGELOG.md and TECHNICAL_REPORT_ZH.md now distinguish
candidate implementation from historical release/test metrics, describe routing,
compatibility, build-only usage and pending whole-version verification. This is
H documentation preparation, not accepted H delivery.

The build runner no longer hard-codes a false unchanged-route claim; it explicitly
marks route acceptance deferred. Generated `.egg-info` is excluded from source
inputs because wheel/sdist generation owns that metadata. Source inputs still
include production modules, frontend sources/assets, build scripts, dependency
locks and README/package inputs.

## Implementation inventory versus verification

| Required seam | Candidate implementation | Unified verification still required |
| --- | --- | --- |
| Native Graph/Deep history, profiles, search/groups/archive/drafts | Native persistence/API/RunService and Vue workspace from A–D | Full N1 exact-target deletion/adjacent audit plus current-candidate native history proof |
| Approval/diff/verification/child inspector | Existing shared inspector and production patch/runtime contracts | N2 actual bytes, receipts, duplicate/crash/timeout and configured-command/child controls |
| Progressive Skill and MCP catalogs/transports | Skills registry, catalog routes, MCP lifecycle/content/gateway implementation from F | N3 native disclosure, transport/content/cache/probe and cleanup |
| Scheduler/owner/deadline/retention/cleanup | F owned lifetime, SQLite/worker/child/lock and retention repairs | Full source matrix plus N4 busy/second windowed owner/native drain/dimensions/DPI |
| Vue default with compatibility | Hybrid root redirect and existing runtime/Legacy routes | New route regression, final-source native acceptance; missing bundle must not silently fallback |
| Distribution and delivery | Build-only pipeline and candidate documentation | Wheel/sdist/noneditable/import/resource and EXE proof, scoped review, one commit/push/exact CI |

No required new backend feature is substituted with a Mock-only facade. Existing
capability exclusions, full180 refusal, paid budget/usage guards and original
model-quality protocol remain unchanged. Tests can still identify actual gaps;
fix those in this major-version candidate instead of inventing a smaller release.

## Next phase

Build the changed candidate into a new directory, recording source/artifact
identity and preserving the earlier build. After that implementation/build
boundary, run the original consolidated source/frontend/package and native
acceptance gates as **one major-version test phase**. Do not report historical
735/59 or prior EXE proof as current acceptance. Reconcile all P01–P15/DoD rows,
repair failures, rebuild changed inputs and finish N6 only after actual proof.
Real-model evaluation and a next-major new chat remain later authorized/boundary
steps; there is no v1.1 infrastructure or new chat merely because compilation
finished. Full objective remains unachieved until those required boundaries pass.

## Construction build outcome and major-version test boundary

The changed-source build completed under `.artifacts/local-build-20261004-02`:
Vue/compiler, offline wheel+sdist and PyInstaller all exited0; manifest marks
build_success true, tests_executed false and route acceptance deferred. EXE
21284903 bytes, SHA
`939dc0ba6fc9986e0ce745d1a46738c727cda24acbe0c7a39d934529e5d01b06`.
Build-input inventory SHA
`b67df0a9a563b0df4d397c4efe50978f1b93cc0f8131cd8052d0d1ace101c8a2`.
Daily app byte/size/mtime inventory unchanged. Earlier candidate01 is preserved.
This closes the implementation/build boundary, not product acceptance.

The deferred **major-version test phase now begins** under a new non-overwriting
`.bench-results/g-local-20261004-03` attempt. Original25 gate identities remain;
new root/API-only/missing-bundle regressions and five build-entry safety cases
are included in the full Python suite, scripts join lint/compile scope, and uv
lock verification is explicitly offline. These safety cases do not launch apps
or build dummy runtime evidence. Historical pass counts are not copied.
Package/noneditable/native/review/commit/CI remain subsequent parts of this same
whole-version acceptance phase. No intermediate mini-release is created.
