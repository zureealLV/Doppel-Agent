# New-conversation navigation flash

## Cause and fix

The previous polling fix stopped repeated preference writes but a legitimate new
conversation still saves its selection once. That save inserted a temporary
paragraph above the chat header, resizing the flex message area. Disabling and
reenabling busy controls also changed their opacity immediately.

The native chat now keeps a permanent, fixed-width selection-status slot in its
header. Saving never inserts a new layout row. Its visual reveal waits 300 ms;
fast successful writes stay visually quiet, while a slow save is still announced.
Transient disabled-button/input dimming has the same delay. Actual disabled
states, serialized persistence, close flushing and save-failure alerts remain.
Reduced-motion users retain the delay without the opacity fade.

## Current evidence (2026-10-22)

- Regression-first: the mounted layout/status test and style contract failed
  before the fix. Tests now retain the same header/message/composer nodes during
  a pending new-conversation save, assert exactly one write, and preserve failure
  alerts. These synthetic tests are not pixel-layout acceptance.
- Full frontend suite: **549 passed**, 57 files. Typecheck and Vite build passed.
- Used a **separate hidden in-app browser**, not the user's Edge browser, against
  a disposable native API workspace with MockProvider. No task/model was run.
- Fast click: 90 animation-frame samples, including two pending-save frames;
  header/message/composer geometry had one unique value and node identities were
  retained. Status opacity stayed 0; suggestion-button opacity stayed 1.
- Injected 800 ms selection-PUT delay: 180 samples, 174 pending-save frames; one
  unique geometry, retained nodes, saving indicator reached opacity 1. A later
  DOM snapshot confirmed completion and cleared status.
- Three UI clicks produced three conversation POSTs and three selection PUTs;
  subsequent polling did not add writes. No run-start request was made.
- Local frame logs and screenshot:
  `.bench-results/new-chat-flash-ui-20261022/{fast-frames.json,slow-frames.json,stable-new-chat.png}`.
- Isolated candidate `.artifacts/new-chat-flash-20261022-01` built successfully;
  all recorded source hashes matched and the daily app directory was unchanged.
  Frozen executable `--help` exited 0. Desktop shortcut was backed up and pointed
  at this candidate, retaining arguments and working directory.

This is browser/API and frozen CLI evidence, not full native-window acceptance.
The user's running desktop window was not terminated; reopen through the desktop
shortcut to load the new assets. No Python source changed or backend suite reran.
