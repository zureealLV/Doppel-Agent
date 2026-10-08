# Build-first Local Mode candidate — 2026-10-04

Lv's current instruction is to finish construction/builds first and perform
testing together later. This report records a **build-only candidate**, not native
acceptance, N6 delivery or a v1.0 release. Existing acceptance gates are deferred,
not waived. Application version stays **0.14.4** and root stays Legacy.

## Reusable build entry point

- `scripts/build-candidate.ps1 -OutputDirectory .artifacts/<new-candidate>`
- `scripts/build_candidate.py` implements the build-only pipeline.
- Requires the existing project virtual environment, npm and uv. No automatic
  dependency installation; uv packaging uses offline/no-build-isolation mode.
- Rejects an existing output directory or a path outside this checkout's
  `.artifacts`. No deletion/overwrite of earlier candidates or daily `.dist`.
- Vue compiler/Vite → wheel and sdist → windowed onedir PyInstaller, with per-stage
  logs, exit codes, frozen source-input hashes and artifact hashes.
- No pytest, Vitest, runtime/GUI launch, provider probe or paid evaluation command.

## Actual construction results

All three build commands exited **0** in
`.artifacts/local-build-20261004-01/build-manifest.json`:

| Stage | Output |
| --- | --- |
| Vue compiler + Vite7.3.6 | `index-DphE2Ezn.js`, `index-BKvV4ZSj.css`, HTML |
| Python packages | `packages/doppel_agent-0.14.4-py3-none-any.whl`, 212008 bytes; `packages/doppel_agent-0.14.4.tar.gz`, 253869 bytes |
| Windows desktop | `desktop/DoppelAgent/DoppelAgent.exe`, 21284555 bytes, plus its required `_internal` directory |

Toolchain: Python3.11.15, PyInstaller6.22.3, contrib hooks2026.7,
setuptools79.0.1. EXE SHA-256:
`697b7a8afbba22c2a92fe39b314e915105bf46d918c6f61640aec0416260f612`.
Build-input inventory SHA-256:
`e54b84c865a8d3f26ed37631dd4037fbfdca4a56f8cd5ff33c7706c9d108171b`.
This manifest's inventory includes build scripts/metadata and is not the old
acceptance harness's source-hash namespace. Do not compare them as equal snapshots.
Inputs stayed frozen during packaging; daily app byte/size/mtime inventory stayed
identical. No existing native fixture/history was changed by this build.

The deliverable is **onedir**, not a standalone one-file binary: preserve the
entire `desktop/DoppelAgent` folder when copying it. Nothing was launched from
this candidate. PyInstaller's warning file and complete logs remain available for
the later package/import/runtime review; successful linking is not proof that all
runtime imports work.

## Deferred unified test/delivery phase

1. Revalidate new build-script boundaries and source/frontend suites, including
   all original consolidated gates and explicit unsupported/skip denominators.
2. Inspect wheel/sdist resources, rebuild from sdist, verify noneditable installed
   dependencies and package contents. The present output/hash checks are build
   bookkeeping, not installed-runtime acceptance.
3. Resume remaining N1 native deletion/adjacent audit and N2–N4 approval/effects,
   Skills/MCP, ownership/lifecycle, window/DPI and route parity. Human exact-draft
   confirmation was received; the later physical Escape stopped the prior UI
   turn before deletion. Do not claim deletion or lack of confirmation.
4. Only after parity passes, perform conditional N5 root switch, freeze its final
   sources, rebuild and accept that final package. Today's Legacy-root candidate
   cannot substitute for this changed-source cycle.
5. N6 consolidated review, bilingual documentation, one scoped commit/push and
   exact remote HEAD/CI remain after acceptance. Paid evaluation and final major-
   version/new-chat boundaries remain separate later steps.

Construction output is ready for the unified phase. **Tests not run in this
build; native/release acceptance false; no version bump, tag, push or default
switch.** Do not reinterpret historical tests as fresh tests of this candidate.
