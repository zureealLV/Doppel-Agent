# Troubleshooting

[简体中文](TROUBLESHOOTING_CN.md) · [User guide](USER_GUIDE.md)

| Symptom | Safe next step / boundary |
|---|---|
| EXE fails after extraction | Extract the entire archive to a writable location; keep `_internal` next to the EXE. Do not overwrite old data. Record version/error instead of repeatedly force-killing processes. |
| API offline / project read failed | Check that the full desktop package was launched and allow startup to finish. A frontend-only browser has no native directory/window bridge. The indicator means local API connectivity, not model/MCP/cloud health. |
| Missing conversation | Check project, native versus Legacy entry, group and archive filters; search with `Ctrl+K`. A failed read does not mean history is empty. Do not delete databases. |
| Project switch blocked | Finish older Legacy tasks, resolve drafts/unsaved edits, and wait for original requests, saved selections and cleanup. Acknowledging a switch does not bypass these barriers. Grants do not transfer. |
| Unknown submission / locked form | Preserve the original input/key. Use the existing original-request retry or explicit acknowledge-exit flow. Timeout is not proof of nonexecution; do not create duplicates to bypass the lock. |
| Late receipt after hiding | Explicitly display/adopt the original receipt; do not resend. A hidden/closed page or aborted HTTP request does not prove backend drain. |
| Pending approval / recovery | Inspect that run's details and available approve/reject/cancel/resume actions. Check paths, commands and sources. Do not edit ledgers, delete checkpoints or autoapprove old requests. |
| Close preparation incomplete | Wait for conversation selection, settings, child/report, changes or extension originals. Handle unknown state through its own explicit acknowledgement. Do not force kill or treat a vanished window as proof every child has exited. |
| Model connection failure | Verify profile, exact model, compatible URL and service support. Test connection is explicit and may cost money. Never attach Keys, `.env`, DPAPI files or full headers to an issue. |
| Unknown Token/cost | Missing provider usage or declared tariff can yield unknown. 0 is not free. A declared estimate is neither an account hard cap nor a verified invoice. |
| Limited Git snapshot / external metadata denied | Verify workspace; linked metadata needs native selection/confirmation. Browser controls cannot bypass it. Snapshots do not promise Git clean/filter/rename semantics. |
| Inverse / verification unavailable | Check source run, retained preimage, current bytes, quiescence, configured verification argv and permissions. Preview and approve afresh. Resolve original unknown receipts before retrying. |
| Empty/stale MCP or Skills catalog | Explicitly read configuration/cache, select the intended server, then confirm probe/catalog actions. Empty cache is not an empty server. Refresh may connect/start local processes; a catalog is not execution acceptance. |
| Crowded small window | Collapse inspector, composer options and explanatory details. Help is scrollable. Specify native/Legacy page and dimensions when reporting; do not bypass safety prompts for layout. |

## Useful feedback

**Help → Feedback** opens [GitHub Issues](https://github.com/zureealLV/Doppel-Agent/issues). Include the About version, Windows/WebView2 version if known, page, minimal steps, expected/actual result, window dimensions and redacted screenshots/logs. Distinguish Mock reproduction from real-service failure.

Never submit API Keys, authorization headers, `.env`, DPAPI content, real project databases, complete remote catalogs or private code. The app does not automatically upload logs and has no built-in update check, cloud status, performance tracker or task manager.

Keep failed/unknown evidence. Engineering tests and UI acceptance do not replace full native lifecycle and model-quality evaluation.
