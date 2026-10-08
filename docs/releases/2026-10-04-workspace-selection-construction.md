# Workspace-owned native selection — construction only (2026-10-04)

## Why this belongs to the original scope

Task D step2 requires reopening the selected native conversation and run, not
only the most recently updated conversation. Source inspection shows desktop
startup binds a fresh ephemeral API port, while the native controller previously
stored these IDs only in origin-scoped localStorage. This is a source-contract
gap, **not a reproduced GUI defect or a new runtime benchmark claim**.

Lv explicitly deferred verification and computer operation. This increment only
edits repository source, regression definitions and documentation. No application,
provider, probe, test, compiler, package verifier or GUI operation was executed.

## Candidate implementation

- Additive runtime schema migration4 adds a singleton selected-conversation row
  and per-native-conversation selected-run preferences. Navigation stores only
  foreign-key IDs, never messages, credentials, prompts or permission grants.
  It does not import Legacy data or reorder history by modifying updated_at.
- Native store validates visible conversation existence and exact run ownership
  in one transaction. Archived/active conversations may be viewed without
  releasing leases or executing turns. Clear and visible-delete clear the active
  pointer; deleting one conversation removes only its navigation preference,
  leaving adjacent preferences and immutable runtime audit intact.
- Versioned GET/PUT `/api/v1/workspace-selection` use strict schemas and existing
  same-origin middleware. Extra data and cross-conversation run IDs are rejected.
  The service owns/drains SQLite navigation IO before workspace-owner release;
  cancelled HTTP requests cannot orphan a preference write during shutdown.
- Native Vue initialization prefers workspace-owned selection over browser IDs.
  Browser IDs are only a migration fallback when no server choice exists; an
  explicitly cleared/tombstoned choice is not resurrected from a stale browser.
  An active/interrupted run takes precedence over an older audit selection.
  The archived sidebar reloads when opening changes the archived view.
- Controller preference writes are serialized; a slow older click cannot arrive
  after the newer queued choice. Saving/failure is visible without resubmitting a
  model request. A failure to load a saved choice is reported, not silently
  substituted with an unrelated recent conversation.

This stores committed navigation preferences, not a universal crash guarantee.
Pending asynchronous UI selection writes have a visible saving state. Abrupt
termination before a write commits still needs the original crash/close tests.
The increment does not add pixel scroll restoration, distributed state, new
runtime capabilities, universal exactly-once effects or a model-quality result.

## Regression definitions written, not executed

- Five disposable-store cases: older archived conversation/run reopening without
  reordering/effects; invalid/cross-conversation atomic rejection; clear/tombstone
  and adjacent audit preservation; active lease preservation; additive v3 upgrade.
- Two API cases: strict/owned roundtrip and invalid request rejection; same-origin
  gates and zero hidden turn/credential access for navigation.
- One owned-service case: repeated cancellation drains a blocked SQLite preference
  worker before owner handoff, then allows reopening the committed selection.
- Six frontend cases: fresh-origin restoration; stale-browser clear; failed saved
  load without unrelated fallback; ordered slow writes; sanitized failure/retry;
  active-run priority and explicit empty/clear selection.
- The existing migration rollback test now injects the next unused migration
  number, retaining all atomic rollback/version-marker assertions.

**Fourteen new regression definitions; zero executed in this increment.** No
assertion, skip or acceptance row was waived. The count describes written cases,
not a passing test denominator.

## Source/package boundary and next authorized verification

Attempt03's25/25,742 Python/59 frontend and11/11 package receipts describe the
previous frozen source only. Its wheel, sdist, EXE and generated frontend bundle
do **not** contain this increment. They remain preserved, not overwritten or
promoted. Do not call the old source hash the current source identity.

When the human requests verification again: generate the new frontend bundle,
freeze all changed source/tests/package inputs in a new non-overwriting attempt,
run the whole-major source/offline suite including these cases, and build/link
fresh wheel/sdist/noneditable/EXE artifacts. The repeated lifecycle selector must
include `tests/test_workspace_selection_lifecycle.py`. Then execute the unchanged
full N1–N6 parity/cleanup/delivery queue, including fresh-origin older archived
conversation/older audit restoration, clear/delete and ownership drain controls.
Use disposable data only; no paid calls, daily EXE replacement or premature fork.

Construction is advanced; verification, release and the full goal remain open.

## Follow-up: toolbar close integrates the preference queue (unverified)

The previous increment queued UI preference saves, while the existing toolbar
close directly called the host destroy bridge. Those two source paths were not
connected. The source-only follow-up adds a before-close barrier through
App → DesktopControls → native workspace, plus the matching completion cleanup:

- Drain the latest preference-queue tail, including later queued writes, before
  invoking the host's `window_action('close')`. Loading/opening/transition/acceptance
  must settle; a running or interrupted turn is not itself a close prerequisite.
  Backend busy shutdown/cancellation/drain semantics remain unchanged.
- Freeze page/composer/navigation inputs during close preparation; duplicate
  window actions are gated. Save failures keep the window open and show a stable
  message; reselecting can repair the preference before an explicit retry.
- Bound preparation to5000ms and clear its timer. Timeout does not cancel the
  already-owned preference transaction, force close the window, automatically
  retry a model call or invoke the host later when the old promise resolves.
- Re-check the host binding after waiting. Detached/replaced bindings are not
  invoked using a stale reference. Completion/failure releases the UI close gate.

Five bridge cases and eight native-controller cases (including four parameterized
pending states) were written: delayed drain, duplicate close, rejection/retry,
bounded timeout/late resolution, host replacement, non-close actions, queued
selection, failure repair, busy-run preservation, pending-state rejection and
queue-tail extension. **13 additional cases; still zero run.** Together with the
earlier14, this increment series contains27 new regression definitions, not27
passing tests. No compiler/build/checker was invoked.

This is the **Vue toolbar close** source path, not proof that every OS exit path
has identical behavior. Alt+F4/system close, abrupt process termination, actual
native focus/inert/scaling and busy seven-store/process cleanup remain explicit
N4/G6 requirements. Do not claim universal crash durability or count bridge mocks
as packaged native exit acceptance. Existing attempt03 artifacts still lack both
source-only increments; their earlier acceptance numbers are not current proof.
