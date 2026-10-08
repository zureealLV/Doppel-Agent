# Codex-reference layout candidate — 2026-10-08

Lv requested removal of pane-width sliders and traffic-light dots, compact icon tools with hover descriptions, and layout matching the supplied Codex screenshot. This supersedes the preceding classic two-pane styling request. No runtime, provider, permissions, data formats or dependency versions changed.

## Delivered UI
- 48px real-page icon rail, 280px conversation sidebar, central chat and bottom-centered 740px composer; purple-gray surfaces and conventional top-right window controls.
- Pane-width controls removed from native workspace, not just collapsed. Runtime/group controls moved into menus; full workspace path appears only as a title, not repeated visible lines.
- Search/review/archive/settings/project/context/manage/inspector tools use Lucide icons, native `title` hover help and accessible names. User-content titles/model names remain visible. No fake tools.
- Original live controllers, project-switch/close barriers, archived read-only state and explicit default-OFF grants/context selection retained.

## Current evidence
- Previous native original session28116 / EXE72028 normally closed via Alt+F4, original handle actualexit0 before source edits.
- Four new appearance tests observed RED then GREEN by vertical slice; initial regression missing runtime-identity text retained and repaired in the runtime menu.
- Full frontend **514/514, 50 files**, typecheck0, frontendbuild0, git diff-check0 (existing CRLF warnings retained).
- Vite JS **504.50kB** still produces the >500kB warning; build succeeds, threshold not suppressed.
- Isolated build original session91971, build subprocess70444 original wait actualexit0; frozen SOURCE **3048cabe1384141015e934b181a6c97373650f02738cbd30f51ca8848f7304ae**, **661 inputs**, same source before/after build and after launch. All backend sources unchanged versus previous style freeze.
- EXE SHA256 **ee2379c730da9918d30d58c0ed68a4d6cd08c4559fa205b4692fb3b7c14f29ef**. Real native EXE startup/layout screenshot and accessibility observed; API online, Mock profile, inspector closed, no sliders/traffic dots. Hover attributes/descriptions verified, actual tooltip-hover interaction not exercised.
- Latest original launcher **session42996 / EXE74176** stays open, actualexit not claimed. Reuses only the existing disposable Mock project-a/catalog from ART05; no tasks submitted or permissions changed.

## Handoff / boundaries
- New launcher: `D:/Codex Program files/Agent/Doppel-Agent/.artifacts/workbench-codex-layout-20261008-01/user-acceptance/Launch-Acceptance.cmd`
- Build/source/test/native receipts: `D:/Codex Program files/Agent/Doppel-Agent/.bench-results/workbench-codex-layout-20261008-01`.
- Daily `.dist/DoppelAgent`, real data/keys and old receipts untouched; zero paid model calls; no commit/push.
- UI appearance and build/startup only. No new full backend batch, G3, whole N1-N6/P/C/E/R acceptance or real-model-quality claims. Lv performs remaining acceptance. Whole S0-S10 goal remains ACTIVE and S10 review/release pending.
- Formal11's original generated-assets source-freeze failure remains FAIL, despite25 command gates passing; old Python2752/native18 subset receipts are not promoted to this new source.
