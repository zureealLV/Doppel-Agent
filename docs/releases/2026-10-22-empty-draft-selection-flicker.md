# Empty-draft selection-save flicker fix

## Root cause and scope

The conversation component polls `NativeWorkspaceController.refreshRun()` every
700 ms. For an empty draft, both the selected run and the fallback run are empty.
The previous membership check nevertheless called `selectRun("")` on every poll,
which queued an identical workspace-selection PUT and toggled the visible saving
status. This was a frontend preference-write loop, not model streaming or GPU
rendering failure.

Background refresh now selects the fallback only when it differs from the current
selection. Explicit selections still persist; failed saves remain visible and can
be retried explicitly. A genuinely removed audit selection persists its fallback
once. No status text was hidden and no backend behavior was changed.

## Validation on 2026-10-22

- Controller and mounted-component regressions failed before the fix: a poll
  set `selectionSaving` and rendered the saving-history text.
- Four new regression tests cover unchanged drafts, mounted status, failed-save
  repair and removed-audit fallback. Full frontend suite: **546 passed**, 57 files.
- Candidate frontend build includes successful vue-tsc and Vite checks.
- Edge loaded the production frontend against an isolated actual native API with
  MockProvider. After creating a draft, conversation GET counts advanced from 32
  to 52 while workspace-selection PUT stayed at **1**. No run was started and no
  paid model request or credential inspection was performed.
- Browser screenshot: `.bench-results/selection-flicker-ui-20261022/stable-empty-draft.png`.
  A still screenshot alone is not proof against flicker; the request counts and
  mounted regression provide the accompanying behavioral evidence.
- Isolated candidate build succeeded; manifest source hashes match current input
  files and `daily_app_unchanged` is true. Frozen executable `--help` exited 0.
- Desktop shortcut was backed up and redirected to the candidate, preserving its
  workspace argument and working directory. The existing user window was not
  terminated; reopening through the shortcut is required to use the new bundle.

Candidate: `.artifacts/selection-flicker-20261022-01/desktop/DoppelAgent/DoppelAgent.exe`.

Evidence logs are local ignored files under `.bench-results/selection-flicker-*`;
the build manifest is inside the candidate folder. This is browser/API validation
plus a frozen CLI smoke, **not** full native-window acceptance. The backend Python
suite was not rerun for this frontend-only patch.
