# Task E — default UI compatibility preparation (2026-10-04)

Authority: [A–H continuation](../plans/2026-10-03-v1-local-completion-before-evaluation.md).
Baseline HEAD remains `952a03b7d9dda78dc0f2188e32a620279f1211c2`, version **0.14.4**.
E route/desktop preparation is implemented and tested. **The actual default UI
replacement is not complete:** root and desktop launch still open Legacy until
the real packaged-window parity gate passes. F/G/H remain, with E's conditional
switch explicitly carried into G rather than silently waived.

No paid evaluation, real profile/key access, auto-migration, deletion of user data,
daily EXE replacement, tag, commit or push occurred. The A–H batch remains dirty.

## Changes

1. Added stable `/legacy` and `/legacy/` compatibility entries, with exact old
   `/legacy/app.js` and `/legacy/app.css` aliases. Original `/`, `/app.js`, `/app.css`
   are retained. Vue's compatibility links now name `/legacy/` explicitly.
2. Vue page deep links are `/runtime/#conversations`, `/runtime/#runtime`, and
   `/runtime/#legacy`. Direct reload restores page identity, while existing native/
   standalone/Legacy selection storage remains separate. Unknown initial fragments
   default to native; ordinary focus-anchor changes do not switch the current page.
   These are page links, **not** arbitrary path routes or shareable conversation IDs.
3. Added host-only Vue minimize/maximize-or-restore/close controls. The bridge is
   attached on mount or `pywebviewready`, has an action whitelist, duplicate-action
   gate and truthful host-error/no-host states. Ordinary browsers show disabled
   controls; no synthetic bridge was injected for browser QA. The titlebar has a
   PyWebView drag region and interactive no-drag elements.
4. Fixed the existing Python window API's explicit restore state, so a subsequent
   maximize does not incorrectly perform another restore. This has unit evidence,
   not actual native-window evidence.
5. Native group rename now uses a labeled application dialog with focused input,
   empty-name/duplicate-save guards, pending-action cancellation protection and
   visible errors. It no longer depends on the unverified JavaScript prompt from D.
   Legacy rename/delete and native destructive confirmation behavior are not rewritten.
6. Fixed malformed runtime asset paths: a percent-encoded null byte previously
   raised `ValueError` and disconnected the request; it now returns a controlled 404.
   Resolved-path containment still rejects traversal, absolute drive and missing files.

## Route and data ownership / rollback

| Entry | Owner / behavior |
|---|---|
| Desktop launch URL `/` | unchanged Legacy default, original frameless controls |
| `/legacy[/]` | identical old HTML; old assets also remain at their original root URLs |
| `/runtime/` and the three hash pages | built Vue bundle; page identity restored by frontend |
| `/api/*` | same-origin proxy to separately bound Legacy loopback console |
| `/api/v1/*` | native RunService API, before the compatibility catch-all |
| `conversations.sqlite3` | existing Legacy history, never reinterpreted as native |
| `runtime.sqlite3` | native conversations/events/run ownership |
| `provider-settings.json` | shared public profile presentation; no automatic probe/migration |

The browser-facing Host and same-Origin guard runs before native/proxy routes.
The proxy translates an already validated Origin to the console's separate port;
it does not relax the console guard. Compatibility assets retain CSP, nosniff and
no-store headers. No broad SPA path fallback or arbitrary filesystem exposure was added.

The rollback path is already available: open `/legacy/` (or unchanged `/`) without
copying either DB, converting checkpoints, or removing native audit records. The
daily installed/portable EXE was not rebuilt or overwritten in this increment.

## Fresh acceptance evidence

Ignored root: `.bench-results/local-native-20261003/` (work began October 3 and
continued across midnight; this report uses the current October 4 client date).

| Gate | Actual result / evidence |
|---|---|
| Frozen full Python suite | **676 passed, 2 skipped, 1 warning**, 381.99 s; `e-full01.log` |
| Source stability | `e-source-before.json` and `e-source-after.json` identical; final evidence assertion repeats comparison |
| Frontend | **56 passed / 7 files**; `e-front-final.log` |
| Typecheck / build | pass; `e-typecheck-final.log`, `e-build-final.log`; Vite 7.3.6 |
| Focused web/proxy/window seams | **30 passed, 1 warning**; `e-routes03.log` |
| Ruff / diff-check | pass; `e-ruff-final.log`, `e-diff-final.log` |
| Machine summary | `e-result.json` |

Source inventory SHA-256:
`f728c93a82c36cb4af4f99fca89fb61eca5d47b9960a647e619fdd3b8f5bed6b`.
Generated assets are `index-BKvV4ZSj.css` and `index-Cl6RSzew.js`. This remains a
dirty-source inventory, **not** clean-commit/CI or packaged-release proof.
Two skips are unavailable host symlink creation; the warning is the existing
Starlette/AnyIO `BlockingPortal` deprecation. None is counted as passed.

Actual Codex in-app browser tests used a fresh disposable `browser-e01/workspace`,
seeded independent Legacy messages and a pre-existing **Mock** profile, not Lv's data:

- Created, renamed, refreshed and reselected a native group; API/store name remains
  `E renamed persistently`. Empty-name save was disabled and Escape cancelled without
  mutation. This closes **D's native group-prompt browser gap**.
- Selected/refreshed all three explicit page identities, restored the existing
  profile, and read both old message sentinels through Vue Legacy history and the
  standalone `/legacy/` entry. The old entry also retained selected history on reload.
- Opened and closed model settings without saving or pressing connection test.
  Fixture settings bytes were unchanged. Explicit native no-call provider count and
  native accepted-run count were both **0** (`browser-e01/state.json`). External sync/
  async compatible provider entry points were forbidden, and fixture profiles were
  exclusively Mock. No paid model endpoint/key was present or used.
- Browser window controls were visible and disabled as expected without PyWebView.
  At outer width 320 px, client/body/root scroll width were **305 px**, no duplicate
  DOM IDs or horizontal root overflow (`layout-320.json`, `controls-320.png`).
  `compatibility-final.png` shows the old history/profile plus the host-only controls.

The fixture server shut down normally with launcher exit **0**. Both API and Legacy
listeners closed; owned helper process list is empty. All disposable SQLite DBs
passed integrity check, exclusive reopening and WAL checkpoint (`e-result.json`,
`e-helper-closure.json`). This is source-server cleanup, not native EXE closure.

## Failure provenance and remaining gates

New tests first demonstrated missing compatibility routes, the existing restore-state
bug and missing frontend seams (`e-red-python.log`, `e-red-frontend.log`). The malformed
asset test then reproduced the null-byte request crash (`e-routes02.log`) before its
minimal repair. Hash focus-anchor behavior has its own red/green regression test.
The initial QA launcher had an incorrect provider import and exited before setup;
it was corrected without production changes. No failing assertion was weakened.

**Not passed or switched:** real PyWebView controls/drag/maximize/close, new packaged
window sizes/DPI/scaling, destructive visible-history GUI flows, actual Skill/MCP
GUI paths and packaged process-tree closure. These remain G parity gates, not SSR
or browser pass claims. Preserve the root Legacy default while they are pending.

Next is **F's original Local Mode DoD audit**, including startup owner isolation and
explicit retention policy, then G's frozen consolidated matrices/isolated packaging/
native QA. Only after required parity passes may E switch the default; H then delivers
one reviewed batch with remote HEAD and exact CI. Original matrix denominator180,
factory81/99 and service126/54 are unchanged, not newly rerun here. Paid/human
evaluation remains separate and unexecuted.
