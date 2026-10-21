# Native reply streaming and chat viewport repair (2026-10-21)

## Reported defects and repairs

- Native root model calls previously requested `stream: false`. They now request
  real Chat Completions SSE, decode bounded original chunks, and append visible
  content to the owned durable run event stream. Vue follows those events during
  execution; completion still projects exactly one final assistant message.
  Sync and async transports are supported. Explicit JSON responses remain a
  compatibility fallback, not a fake streaming animation. No partial-turn retry
  or tool replay is introduced. Reasoning and tool arguments are not displayed.
- The raw active-run sentence was legitimate execution/approval status, not an
  assistant answer. It is now a separate compact notice with an execution-details
  button. Permission, approval, unknown-submission and shutdown protections remain.
- Final-synthesis guidance used to be appended as a user message and saved into
  graph history. A later execution could mistake that historical instruction for
  current tool availability. The guidance is now temporary system context, with
  an execution-scoped reset for old poisoned history. Step budgets remain bounded;
  exhausting one emits a distinct notice rather than claiming a connection fault.
  A model cannot execute tool calls on the synthesis-only turn.
- Nested shell/page scrolling could push the composer away and expose an empty
  outer page. The shell now has a bounded viewport and `100vh` fallback; only the
  messages pane scrolls. Narrow inspectors are dismissible drawers. Live output
  follows the bottom only while the reader is already near it, with an explicit
  return-to-bottom button. The user's exact WebView feature support was not read,
  so the `dvh` fallback is a reproduced failure mode, not a proven sole cause.

## Verification boundaries

- Frontend: 56 files / 537 tests passed; Vue typecheck and production build passed.
- Provider suite: 278 tests passed, including real loopback HTTP first-content
  gates for urllib and httpx, framing, tool assembly, limits, cancellation and
  no-retry-after-partial-output checks. Latest streaming-only rerun: 22 passed.
- Native graph/deep/legacy SSE integration: 3 passed. Durable first content was
  observed while the run was still running and before its final history message.
- Original workbench report integration: 46 passed. The offline buffered endpoint
  now declares its actual JSON media type when the client requests SSE.
- Full-suite first pass: 2811 passed / 6 failed / 4 skipped. That process imported
  code before the last streaming-limit correction and overlapped source edits.
  All six failures were subsequently rechecked on the frozen final source:
  workbench integration 46 passed, provider streaming 22 passed, and the
  source-immutability fixture audit 1 passed (21 scripted paths). Final focused
  budget/native-SSE/recording rerun: 14 passed. A second full-suite run was not
  performed; do not describe the first pass as an all-green full regression.
- Changed Python files passed Ruff; `git diff --check` passed.
- Real Edge UI used the production frontend and an isolated loopback scripted
  provider: unfinished content appeared with the execution notice; 30 paragraphs
  scrolled inside the messages pane; scrolling to the top was preserved through
  completion; return-to-bottom worked. At viewport height 1274, body height stayed
  1274 and the composer bottom stayed 1254. Screenshots and logs are local under
  `.bench-results/stream-ui-20261021/`.
  The user's 1264 x 812 viewport was also checked: body height 812, composer bottom
  792, messages client height 520 with independently scrollable content of 1819.
- Initial UI fixture used httpx's short default timeout while deliberately holding
  completion at a manual gate. That disposable run failed as expected; the final
  UI proof used a 600-second fixture-only timeout and completed. Product timeouts
  were not relaxed to hide a failure.
- Candidate `0.15.0rc1` built via `scripts/build_candidate.py` into
  `.artifacts/streaming-scroll-20261021-01/`; wheel, sdist and PyInstaller succeeded.
  The frozen EXE started against an isolated workspace/catalog, served health and
  its bundled native UI, then exited on normal window-close. Source fingerprint
  matched its manifest. This is targeted acceptance, not a new general release
  or full model-quality evaluation. Existing daily binaries were not overwritten.
- The existing Desktop `Doppel Agent.lnk` now targets that verified candidate,
  preserving its original workspace arguments. The former shortcut is backed up
  as `.artifacts/streaming-scroll-20261021-01/Doppel Agent.previous.lnk`.
- Paid model calls: zero. No keys were read/decrypted by the acceptance scripts.

## Existing history

Historical assistant answers remain unchanged. The repair does not fabricate
missing review evidence or retroactively remove a legitimately pending approval.
New executions re-establish their own inspection budget and available tools.
