# Small-window Legacy layout repair — 2026-10-08

## Problem and reproduction
The supplied screenshot was the Legacy history page, not the native conversation page. Prior Codex CSS applied the native fixed-bottom compact-composer layout to Legacy's expanded templates/permissions form. The form consumed the message viewport and its controls could fall outside smaller windows. Legacy also missed the native conversation grid's responsive breakpoints.

Real original EXE source3048 / SHA ee2379 observed with the reported cramped Legacy screen. Normal Alt+F4 close: original session42996 / EXE74176 original handle actualexit0 before source edits.

Actual headless Edge rendering of original SSR/CSS fixtures reproduced all4 Legacy failures at1264x812,884x582,760x520,640x480; native also clipped the empty welcome at640x480. Source/DOM boundaries and original failure receipts retained, not inferred from synthetic Vue tests.

## Fix
- Explicit `legacy-workspace` layout identity and shared two-column responsive grid, without changing controllers or moving Legacy history into native runtimes.
- Legacy composer now uses compact textarea/model/effort/send toolbar; templates and original default-OFF explicit grants remain in a collapsed menu. Runtime/history-context explanation stays available behind its info icon.
- Legacy run status, pending approvals and own audit stay inside the scrolling message region, never after the bottom composer. Existing decisions and confirmation paths unchanged.
- Short-height spacing adjusted for both conversation pages; messages cannot flex-shrink their individual contents.

## Evidence and candidate
- New SSR/structure regression observed RED -> GREEN. Geometry original failures -> **8/8 PASS**; composer **138px** folded at1264x812 and884x582, send controls inside viewport, no horizontal overflow or clipped empty welcome in the4 tested sizes.
- Frontend **515/515,51 files**; types0 (first fixture `ProjectIdentity` typing failure preserved, fixture corrected); build0; diff-check0 with pre-existing CRLF warnings.
- Vite JS506.37kB >500kB warning retained, not suppressed.
- Isolated build original session17015, build subprocess72520 original wait exit0. SOURCE **3019c4d1d53659a3901a909ac2685ebf28ee9f2e6be1d354dff8a016852e5072**, **662 inputs**, stable before/after build and after launch. Backend sources unchanged versus prior candidate.
- EXE SHA256 **de4bdc0175fc72bcd7d06469ec2785bf4530e8850cd4625708a7dfaed6b6ef7b**. New original launcher **session46182 / EXE66460** handed to Lv, no exit claimed.
- Actual native startup/API online observed. Lv actively navigated to independent Runtime; two user-input interlocks prevented the requested native Legacy verification action. Assistant stopped inputs instead of taking control. **Native Legacy visual acceptance remains Lv's task**, distinct from the successful real-browser geometry tests.
- New launcher: `D:/Codex Program files/Agent/Doppel-Agent/.artifacts/workbench-small-window-20261008-01/user-acceptance/Launch-Acceptance.cmd`.
- Receipts and synthetic headless screenshots: `D:/Codex Program files/Agent/Doppel-Agent/.bench-results/workbench-small-window-20261008-01`.

Daily `.dist/DoppelAgent`, real data/keys and historical receipts untouched. Same disposable ART05 Mock workspace/catalog reused; no tasks submitted, grants/settings toggled or paid model calls by assistant. No commit/push. Whole S0-S10 remains ACTIVE; no full backend/matrix/G3 or final-release claim.
