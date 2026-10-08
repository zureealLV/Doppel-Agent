# Local Coding Workbench + Help — 2026-10-08 prerelease

## Release scope and status

Version: **v0.15.0-rc.1** (Python metadata/import: `0.15.0rc1`; frontend/tag: `0.15.0-rc.1`). This prerelease carries the accumulated Local Coding Workbench construction plus Lv's accepted interface and the new help/documentation layer. It is **not** a stable v1.0, full G3/native N1–N6 acceptance, full runtime/model evaluation or whole S0–S10 completion.

Source ownership transferred only after `.bench-results/workbench-helper-cleanup-20261008-01/parent-handoff-ready.json` confirmed parent edits finished, prior EXE actual exit0 and no new app launch. No DoppelAgent EXE was running at this chat's preflight. This chat does **not** launch an EXE automatically, read real credentials, call paid models, upgrade dependencies or overwrite the daily `.dist/DoppelAgent` installation.

## User-facing delivery

- Source-grounded [Chinese guide](../USER_GUIDE_CN.md), [English guide](../USER_GUIDE.md), and [Chinese](../TROUBLESHOOTING_CN.md)/[English](../TROUBLESHOOTING.md) troubleshooting, linked from both READMEs.
- Shared titlebar Help: documentation, implemented shortcuts, troubleshooting, GitHub Issues, actual build version/about. No fictional update checker, cloud status, task manager or performance tracker.
- Menu supports native link/button activation, ↑↓ wrapping, Home/End, Escape with trigger focus restoration, Tab/outside-pointer/focus closure; closing/project-switch block dismisses and disables it. Listeners disposed on unmount. Native modal dialogs use their built-in focus/close semantics.
- Ordinary changes/extension/order explanations fold behind labelled summaries. Dangerous-operation, permission, approval, unknown/late-reply and close-wait prompts remain at their existing actions; controller/lifecycle semantics are unchanged.
- Earlier compact composer/Legacy small-window fixes and parent Runtime footer cleanup are included without rewriting their receipts.

## Evidence rules

TDD logs retain trigger/menu/folding/version RED and GREEN. Intermediate synthetic-host identity assertion/output and scoped-style SSR fixture corrections are preserved; they are not native focus evidence. Compiled Vue/SSR tests are separate from real-browser geometry/input checks. Packaging receipts freeze **after** generating `frontend_dist`; the original build process is retained to actual exit and the EXE is hashed without launching it.

Targeted checks, current hashes, exact Git commit/remote HEAD/CI and publication outcome are appended below once actually observed. No pending step is represented as passed merely by this document existing.

## Fresh scoped validation and review

- Help TDD: trigger/menu first RED -> GREEN; explanation/version first RED -> GREEN; **8 new checks**. Full frontend after virtual-dialog/disabled-count integration repair: **524/524, 53 files**, types0. The original full run's 19 failures/4 synthetic-host errors are retained; they are not hidden as initial green.
- Real headless Edge using the compiled Help component and shared shell: **5/5 sizes** (1264×812, 884×582, 760×520, 640×480, 360×300), actual pointer/key input, focus restoration, modal Escape, outside focus/Tab dismissal and blocked transitions. Existing native/Legacy conversation geometry revalidated **8/8** at the original four sizes. These are browser fixtures, **not packaged native EXE acceptance**. Initial harness root-scan timeout, duplicate-Vue import failure and fixture-button overlap were corrected with all failed receipts retained.
- Targeted Python desktop/project/web/build checks **54/54**; full-source Ruff0 and compileall0. Relative guide/README links **54/54** exist locally; public GitHub links are verified after push, not assumed from local existence.
- Independent Help/docs/version review passed. Selective accumulated-source security audit classified 19 static hits as fixture fake secrets, RegExp.exec or bounded SQL placeholders/fixed identifiers, but found a real blocker: origin-less compatibility `GET /api/v1/mcp/servers/{name}/tools` could enter MCP discovery.
- First regression **2 failed / 1 passed** proved connector entry. GET now uses passive cached tools only (missing/stale cache stays empty), retains old descriptor fields and adds `cache_state`; actual refresh remains the confirmed extension POST. Added `Cache-Control: no-store` with separate FIRST RED -> GREEN. Catalog/extension/cache regression batch **26/26** (one existing Starlette/anyio deprecation warning). The first successful build is superseded and preserved; only a fresh post-fix build is publishable. Narrow independent re-review resolved the blocker; broad correctness/native coverage remains unclaimed.
- Publication uses an explicit source/test/bench/docs/build-input path allowlist, not `git add .`. Temporary browser fixture HTML and root Vite cache were removed; ignored receipts, candidates, real data and credentials are excluded. Selective review is not a comprehensive audit of every accumulated backend definition.

## Preserved isolated package attempt02 (post-security-fix)

- Candidate: `.artifacts/workbench-help-prerelease-20261008-02`. Original build supervisor **session92237 actual exit0**; frontend, offline wheel/sdist and PyInstaller subprocess waits each returned exit0. No dependency download or EXE launch.
- **283 build inputs**, generated frontend frozen before packaging; working-tree build-input SHA256 **d26dcac1456ec8b7f18143886f62f1ab907ce3e353bf6738d0085381e55444a1**, unchanged after packaging and isolated checks. This is a build-input byte manifest, not a complete S9/test/fixture freeze or canonical Git tree hash.
- EXE SHA256 **4cb9faa57ac04ccb9dcdb458cc46b07bafa804a152a51601a8c68a527da2e6ed**; wheel **3cf47bc398052d08bcedb3c6d778eea81fd705aa9415f9766f80adb2dd3c855b**; sdist **c5616dde3bed4f80febb8798da4abaf5ce36efb37c6044618fb53e791f591400**.
- All three frontend assets match source, wheel and `_internal` bytes. EXE embedded `doppel_agent` code version verified without execution. Wheel installed with `--no-index --no-deps` into a new isolated code directory; import resolves there and Mock health/conversation API smoke passed, **0 submitted runs**. Existing dependency runtime was used; this is not a fresh-machine/native GUI acceptance claim.
- Daily installation unchanged. Vite JS **510.69kB >500kB warning** retained. No keys/data read or paid calls. Full historical failed/freeze/native receipts remain intact.
- Local receipts: `.bench-results/workbench-help-20261008-01/` (including superseded/failed attempts). The exact publication commit, remote ref, CI outcome, public-document checks and downloadable-asset hashes are recorded in the corresponding GitHub prerelease notes and local publication receipt after upload. A prerelease label does not complete the broader goal.

Attempt02 is superseded only for publication byte provenance: staging all accumulated new files exposed five inherited blank EOF lines, invisible to the earlier unstaged tracked-only diff check. Removed those final blank lines in exactly two source and three test files; **ASTs identical before/after**, staged diff-check now0. A new attempt03 packages the final hygienic source; attempt02 and its successful isolated checks remain preserved above. No behavior or permission change was made by this whitespace cleanup.

## Historical evidence and remaining work

- [Small-window repair](2026-10-08-small-window-layout-fix.md): real-browser 8/8 layout; native interaction was limited by Lv's input interlocks, not full assistant-driven Legacy native acceptance.
- [Parent cleanup handoff](2026-10-08-helper-cleanup-handoff.md): frontend 516/516, build-only candidate, no launch/publication. Counts belong to that frozen source.
- [Whole goal](../plans/2026-10-06-new-session-whole-goal-handoff.md) and [S9 inventory](../plans/2026-10-07-whole-s9-entry.md) retain historical formal source/offline results, freeze/bundle-changing failed batches and incomplete native subscopes. Those results are **not promoted** to the new help/version source.

Remaining: fresh unified whole-source S9 and native N1–N6/effect/lifecycle coverage, full S10 evidence audit/delivery closure where still open, and separately authorized controlled paid-model evaluation. Engineering fixtures/Mock controls do not establish production reliability, model quality, review success rate or Token savings. The broader goal remains active.

## Final publication candidate — attempt03

Original build supervisor **session20143 actual exit0**, all three build subprocesses exit0. Final 283 build-input byte SHA256 **35ec8524161c21e2db77ec3a05a4ddd01bbef2b53160baf5bd182ce4a163ead3**; frozen through build and post-build checks.

- `packages/doppel_agent-0.15.0rc1-py3-none-any.whl`: SHA256 **d1c6a758214bd2cc6c7b824e0de66b61b41837f493a78a340007d5936d33c6d5** (555998 bytes).
- `packages/doppel_agent-0.15.0rc1.tar.gz`: SHA256 **758f5590bf101c45a4748ac38be7ed547704771b8315b2c223b864e64f77577b** (741574 bytes).
- `desktop/DoppelAgent/DoppelAgent.exe`: SHA256 **584d6bb8c468f748c017992532e21ce2ec7b56059ade6c08f717a838f4f4b9f1** (21833329 bytes).

Attempt03 repeats the isolated wheel import/Mock API smoke, embedded EXE version and all three frontend byte-parity checks successfully. Daily app unchanged; EXE **not launched**. Frontend bundle warning remains 510.69kB. Git staged diff-check0 / Ruff0; unchanged AST cleanup does not require relabelling prior browser/UI checks as native evidence.
