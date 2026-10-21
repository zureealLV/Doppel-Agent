# Public progress, unlimited tool turns, and quick model switching

Date: 2026-10-21. Revision of 0.15.0-rc.1; not a new model-quality claim.

## User-visible changes

- Native conversations retain each public model turn, interleaved with collapsed actual tool records. Starting another model call no longer replaces the previous paragraph. A stored final answer is rendered only once.
- A system acknowledgement appears while the accepted task starts; the model prompt requests a brief plan and meaningful evidence-based progress updates in the user's language. These are public summaries, not private chain-of-thought or provider reasoning fields. A model may still choose not to provide intermediate text.
- Durable `assistant.turn` events supplement actual token deltas, including providers that return buffered content. Completed-run replay pages through the event API instead of stopping at its first 500 events. Reload, conversation navigation, duplicate SSE events, and late reads retain their run/conversation fences. Historical runs predating recorded public content cannot have missing progress reconstructed.
- Tools show names and actual start/end state, never raw arguments/results in the collapsed transcript. Deep callbacks now correlate calls; reviewed Legacy calls publish the same actual boundaries. Child delegate output is not projected as root public paragraphs; existing call receipts remain intact.
- Native Legacy/Graph/Deep and the old console no longer apply quick/balanced/deep 6/8/12-turn presets. Default `max_steps=None`; direct callers can still opt into a finite test/benchmark budget. Deep's default model-call middleware cap and the framework's low recursion default are removed; LangGraph uses `sys.maxsize` as its technical iteration bound. Read-only child delegation also has no fixed model-turn cap. Concurrency/delegation admission, context compaction, cancellation, permissions, response-size and transport protections remain.
- Composer task options expose 10 / 30 / 60-minute deadlines (10-minute default). Unlimited turns are not unlimited time, tokens, spending or context. Effort is no longer a tool-turn budget.
- The model button beside the composer opens a searchable, provider-grouped list of saved profiles. It includes separate settings and add-custom-model actions, opens the current conversation's selected profile, and preserves the sidebar settings entry. The full form includes DeepSeek, OpenAI-compatible, local/Ollama-compatible, and custom-compatible categories. These buttons do not claim protocol support beyond the existing Chat Completions adapter, discover models remotely, populate unverified endpoint URLs, or run paid probes.
- API-key DPAPI storage, no-key-return, explicit tariff consent, original App settings-request ownership, unknown/delayed-reply fences, frozen running configuration, and archived-chat input restrictions are preserved.

## Verification

- Frontend: **542 passing**, 57 files; typecheck and production build passed. Build has the existing >500 kB chunk advisory, not an error.
- New long-task regression: **32 tool turns + final call**, each of Legacy, Graph and Deep. All 33 public turns retained; 32 paired tool identities; no budget-exhausted event. Combined new/budget regression: **11 passing**.
- Focused Core/graph/provider/Legacy suite: **80 passing**. Broader runtime suite initially found one obsolete exact event-list expectation (the newly added public turn); the expectation was updated, not the implementation rolled back.
- A full run exposed a Deep SDK checkpoint compatibility problem: its default delta message channel depends on older checkpoints that local retention removes. Deep now explicitly uses self-contained full message snapshots independently of any model-call limit, including child state schemas. History retention/restart, long-task and Deep runtime regression: **22 passing**. The pre-fix full run was superseded; the final full result follows below.
- Final full Python suite on the fixed source: **2,823 passing, 4 skipped**, 870.56 seconds. Two warnings: Starlette/AnyIO deprecation and the existing intentionally malformed Pydantic publication fixture. Ruff on all changed Python paths and staged diff whitespace checks passed.
- Real Edge browser, scripted loopback SSE provider only: two distinct live paragraphs with a tool record between them; default-collapsed tool details; final summary once; reload recovers both stages and both tool records. Quick model search, current-conversation profile selection and persistence after reload, plus direct new/custom configuration entrance visibly verified without saving secrets or probing a provider. At 1264x812 and 900x600, document scroll height equalled viewport height and the composer/menu stayed inside it. This is browser geometry/interaction proof, not native WebView pixels or model quality.
- Isolated offline candidate built: `.artifacts/progress-model-picker-20261021-02`. Wheel, sdist and PyInstaller EXE produced with source-input hashes stable; daily `.dist/DoppelAgent` unchanged. Frozen `--help` exits 0 (import/argument-parser smoke only, not full native lifecycle acceptance).
- Existing Desktop `Doppel Agent.lnk` points to the new candidate, preserving the original workspace arguments. Previous shortcut backed up inside the candidate directory.
- Local evidence (ignored): `.bench-results/progress-ui-20261021/`, `.bench-results/progress-frontend-final.log`, `.bench-results/progress-full-pytest.log`, candidate build manifest/logs. No API-key read/decrypt or external paid model call.

## Limits

A long review may still run out of its selected deadline or model context and must not claim unchecked areas are reviewed. The UI does not expose private reasoning. The quick menu searches saved profiles, not a live provider model catalogue. The build manifest intentionally stays build-only; browser testing and frozen CLI smoke do not elevate it to clean native-window lifecycle acceptance.
