# Resumed whole-major verification — attempt04 (2026-10-04)

Lv's explicit “如果受限了 就验证吧” resumes the deferred verification.
This is the existing A–H/N1–N6 scope, not a new roadmap, release or paid evaluation.
Version remains0.14.4 and all earlier attempts are preserved.

**Current boundary:** attempt04 source/offline and package checks passed, but its
actual native startup failed at Chromium-restricted port6665. The source has now
been repaired and15 focused desktop cases pass. Attempt05 source/offline25/25 now
passes, as does its11/11 package cycle. Actual native gates remain open.
Attempt04 is not promoted or overwritten.

## Fresh source/offline acceptance

Evidence: `.bench-results/g-local-20261004-04/validation-final.json` and each
named gate log. The driver terminated successfully;25/25 expected exits passed.
Source inventory SHA-256:
`65ffbc52c487f6a85ee7af08cd17f51e8ee329521bb09eaacf59aa5927ff686b`.
Before/after source equality, guarded-input equality and daily `.dist/DoppelAgent`
content/timestamps equality are true. The current navigation migration/API/store/
Vue and toolbar-close source increments are included; attempt03 is not reused.

| Scope | Fresh result |
| --- | --- |
| Full Python |750 passed,2 skipped,1 warning;361.24 seconds pytest duration |
| Frontend |78 passed in7 files |
| Repeated lifecycle |107 collected IDs, all passed in each of5 rounds; includes workspace-selection lifecycle |
| Repeated local MCP lifecycle |23 collected IDs, all passed in each of3 rounds |
| Ruff / compileall / offline lock / Vue types / build / diff hygiene | Passed |
| Supported factory controls |81/81 supported paths,99 exclusions, original universe180 |
| Supported RunService controls |126/126 supported paths,54 exclusions, original universe180 |
| Fault controls |10/10 |
| Burst load |100 submitted/completed, observed active peak4 with configured limit4 |
| Mock smoke / fixture and safety audits / preflight | Passed; scripted controls, not model quality |
| Full-mode guard | Expected exit2 refusal; not a completed full180 matrix |

The2 skips are host-unavailable symlink creation in
`tests/runtime/test_deep_backend.py:38` and `tests/test_runtime_review_inputs.py:49`.
The warning is Starlette's deprecated AnyIO BlockingPortal alias; not suppressed.
Preliminary checks also recorded22 related Python cases and all78 frontend cases,
then regenerated Vue assets before the consolidated source freeze.

No unexpected external Python socket-connect attempts were recorded by the trusted
loopback-only guard; paid calls0. This is not OS traffic attestation, a provider
account cap or quality adjudication. Credentials were removed by environment names,
without recording values or reading real settings. No dependency downloads or
changes to frozen capability/oracle/accounting contracts were authorized.

## Package/native/delivery boundary

Fresh packaging passed11/11 under `.artifacts/g-local-20261004-04/` with independent
wheel/sdist reconstruction, noneditable external-checkout installation, locked
dependency parity and windowed onedir EXE/source/asset linkage checked.
Attempt03 binaries do not contain the latest increment and are not promoted.

N1–N6 actual current-package histories/approvals/effects/Skills/MCP/lifecycle,
default compatibility, final review/one batch commit/push/exact remote HEAD/CI
remain incomplete. No final v1.0 claim, release/tag, daily replacement or next-major
chat fork follows merely from the25 source gates. Paid evaluation remains separately
gated by the original budget/currency/usage/capability/human-review requirements.

## Actual native failure and targeted repair

New EXE PID68592 was launched on the preserved disposable fixture workspace.
Fresh process/window selection linked it to the new package, not the daily app.
The default native window visibly showed `ERR_UNSAFE_PORT` at
`http://127.0.0.1:6665/`. Evidence is retained in
`.bench-results/g-local-20261004-04/native-current01/unsafe-port-native-red01.json`
and its screenshot. This is a startup failure, not successful N1 acceptance.

Root cause: `desktop.launch_desktop` accepted every OS-assigned `bind(0)` port,
including Chromium's default restricted ports. The
[primary Chromium port policy](https://raw.githubusercontent.com/chromium/chromium/main/net/base/port_util.cc)
confirms6665–6669 are restricted. No browser flags, policy changes, security bypass
or WebView exception were used. The native Alt+F4 close was followed by fresh
process absence, all7 SQLite integrity/exclusive reopen, protected history equality
and installed-owner reacquisition/release evidence in `normal-close-red01.json`.
This proves failed-start cleanup only, not active-run/toolbar/full native acceptance.

Six deterministic startup cases reproduced the unsafe navigation before the fix
(`unsafe-port-regression-red02.log`). An initial test-harness import-cache failure
is separately retained in `unsafe-port-regression-red.log`, not disguised as a
product failure. The corrected tests import the API before isolated sys.modules
patches so subsequent parametrizations remain independent.

The repair holds a safe OS-assigned IPv4 loopback socket open through uvicorn
startup, closes every rejected candidate, retries at most64 times and cleans the
existing Legacy server if allocation fails. It never probes then rebinds an
unreserved port and never weakens host/origin or browser safety policy. The static
default list was checked2026-10-04; this is not a guarantee against future or
enterprise-specific additional browser restrictions.

Focused acceptance:15 desktop cases passed (including13 new definitions), covering
startup rejection, consecutive ports, bounded exhaustion and Legacy cleanup,
allocation-failure socket cleanup and a real kernel loopback listener. Ruff passed.
The first focused Ruff semicolon failure was retained and repaired, not suppressed.
Whole-source/package/native acceptance now moves to fresh attempt05.

## Post-repair attempt05 source/offline acceptance

The new driver terminated exit0 with25/25 expected exits. Inventory SHA-256:
`9b01fbefd2a6e62cd8fdb341ad4479a41dfe0e2e8fa66460de1352c8aabd98e6`.
Source, guarded-input and daily-installation equality all passed. Python763 passed,
2 host-symlink skips and1 retained Starlette warning,332.47 seconds pytest duration;
frontend78/7 files. Lifecycle coverage now includes the15 desktop cases:
122 collected IDs ×5 passed; MCP23 ×3 passed. Factory81/81 and RunService126/126
supported controls retain99/54 exclusions in each original180-path universe.
Fault10/10,100-job load, safety/fixture/preflight/Mock/static/type/build/diff gates
passed; full-mode refusal remains expected exit2, not paid/full-matrix evaluation.
No unexpected external Python connect attempts were recorded; paid calls0, with
the same trusted-guard/non-OS-attestation boundary. Fresh attempt05 packages and
actual native startup/history/lifecycle/effects/delivery are not implied by this.

## Post-repair package completion and Escape boundary

Attempt05 packaging terminated exit0,11/11 steps passed. Independent sdist/wheel
member equality, source-content hashes, external-checkout noneditable runtime smoke,
89 applicable locked dependencies with9 platform exclusions, windowed onedir
EXE normalized compiled code/assets linkage and daily-installation stability passed.
The EXE is `.artifacts/g-local-20261004-05/desktop02/DoppelAgent/DoppelAgent.exe`,
SHA-256 `00a5734f55ee16ab124a070eea30223d1221f7f98ee61a0b2787663c3f41bcc7`.

It was launched on the preserved disposable fixture workspace, recorded PID129020.
Physical Escape stopped Computer Use on the next window-selection call. No further
native input was issued. The repaired page/window and normal exit were not observed;
process state after interruption is unknown. Later verification must freshly recover
owned identities, not assume cleanup or treat the launch as N1–N6 acceptance.

Lv then requested next-version construction before immediate verification and
selected “开始下一大版本；我会补充具体功能范围”. New-major requirements await
that human scope; no features, speculative Server Mode, version promotion or chat
fork have been invented. Existing Local Mode/N1–N6 acceptance remains incomplete.

Lv subsequently delegated next-major scope design to the assistant based on the
original project vision, including stack/features/UI. The proposed
[Local Coding Workbench plan](../plans/2026-10-04-next-major-product-vision.md)
now defines that scope and construction sequence. This is documentation only,
not another source/native verification, new implementation or release promotion;
no preserved receipt or original N1–N6 requirement is replaced.
