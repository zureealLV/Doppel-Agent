# Runtime matrix progress — paused 2026-09-30

## Status and scope

- Paused at Lv's explicit request: `先停止 并记录进度`.
- Recorded at 2026-09-30 21:38 +08:00. Goal status is `paused`.
- Preserve unfinished work. No further implementation, test runs, staging, commits, or pushes after the pause request; only this progress record is added.
- No running test process remains. Resume only after an explicit user request.
- Continue the existing plan, rather than creating a replacement roadmap:
  `D:/Codex Program files/Agent/Doppel-Agent/docs/plans/2026-09-23-v0.15-live-runtime-matrix.md`.

## Last accepted checkpoint

- HEAD: `e3e499566e52548399950143618c732299c64b2a`.
- Last accepted source update: test-only cancellation drain watchdog repair, following patch-01 acceptance.
- Its final GitHub Actions run `36720307998` succeeded for this exact commit; verified before beginning patch-02.
- Committed checkpoint evidence: local full suite **383 passed, 1 skipped**; CI Python 3.11 and 3.12 **384 passed each**; repeated lifecycle **140/140 per Python version**; MCP **33/33 per Python version**; frontend **5 passed**, typecheck/build and shared gates passed.
- Those figures apply to the committed checkpoint, NOT to the current unfinished patch-02 working tree.
- App version remains **0.14.4**, manifest **1.2**, capability **1.1**. Accepted patch-01 fixture/task audit schema is **1.2**; proposed patch-02 schema **1.3** is not implemented.

### Accepted changes and preserved failure evidence

1. `42ac87ea2b9be2490ad5fadcfa725cedace7200d`: grounded patch-01 three-file fixture, Graph/Deep native write-only approval flows. Source CI `36715622090` succeeded.
2. `90c7e0ed43025f808d8348596badb06444621350`: clean patch-01 evidence and plan update. CI `36717450006` failed on two repeated lifecycle test watchdog timeouts; retained as failure evidence, not rerun away.
3. `e3e499566e52548399950143618c732299c64b2a`: separated finite test drain watchdog from latency measurement, with slow-I/O, stuck-wait and normal-completion controls. Final CI `36720307998` succeeded. No production cancellation/SLA change.

Patch-01 clean native evidence remains bound to `42ac87ea...`, not relabeled as the later watchdog commit:

- `D:/Codex Program files/Agent/Doppel-Agent/bench/reports/2026-09-30-protocol-1.2-patch01.md`
- `D:/Codex Program files/Agent/Doppel-Agent/bench/reports/2026-09-30-protocol-1.2-patch01.json`
- `D:/Codex Program files/Agent/Doppel-Agent/bench/reports/2026-09-30-protocol-1.2-patch01-safety-audit.json`

## Original plan completion boundary

- Task 5B: **6/13 originally remaining fixtures accepted**: nav-04, tdd-01 through tdd-04, patch-01.
- Still pending: **patch-02, patch-03, approval-01, approval-02, mcp-01, mcp-02, cancel-01**.
- Next phases remain Task 5C global freeze/adjudication, paid/full parity, and the original Tasks 28–29 UI/visual/v1 claim audit.
- Full mode is still blocked; live capacity **9/12** is infrastructure coverage, not paid model quality evidence.
- Factory 81, service 126, and original 180 eligibility results must not be presented as live quality results.
- Paid endpoint/model/key, price/budget and human verdict prerequisites remain outstanding. No paid/external model calls were made for this unfinished fixture work.

## Uncommitted patch-02 work retained

Current untracked paths before adding this record:

```text
bench/cases/runtime/fixtures/patch-02/
tests/test_runtime_refactor_fixture.py
```

Fixture files already written:

- Public: `service.py`, `helpers.py`, `test_public.py`, `LICENSE.txt`.
- Hidden oracle: `target_tests.py`, `regression_tests.py`, `public_check.py`.
- **Not yet written:** `fixture.json`, `oracle/reference_changes.json`.
- **Not yet changed:** fixture loader, native patch harness, evidence validator, task audit or existing audit assertions.
- New unit file contains **24 planned tests**; this does not mean 24 passed.

### Independent seed proof completed

Patch-02 is a synthetic behavior-preserving two-file refactor, not a discovered production bug. Seed public behavior is correct; refactor/delegation boundaries are intentionally red. The external frozen oracles were run against a separate public-only temporary workspace before loader/reference implementation:

| Oracle | Tests | Assertion failures | Errors | Exit |
| --- | ---: | ---: | ---: | ---: |
| target_tests.py | 2 | 2 | 0 | 1 |
| regression_tests.py | 5 | 0 | 0 | 0 |
| public_check.py | 5 | 0 | 0 | 0 |

Frozen oracle SHA-256 values:

```text
target_tests.py     0ef1572ecfaf58c4227ed0360b9df41d34891a7819ebf65c7011f985461c4f92
regression_tests.py d5142b1a314e1a726c3493ba14ee0a09e8809752b8906f4853c8bc99f3024e93
public_check.py     edac59a9f4c531df78fea9049f2dab19873f3e084f62ce3466c664662a830014
```

The only new pytest run was the intended registry RED:

```powershell
uv run --extra agent --extra dev python -m pytest -q tests/test_runtime_refactor_fixture.py -k seed
```

Result: **1 failed, 23 deselected**, `ValueError("unsupported task fixture")`. Log:
`D:/Codex Program files/Agent/Doppel-Agent/.bench-results/patch02-loader-red.log`.
This loader failure is separate from the independently established behavioral/structural seed proof. Current dirty-tree full-suite acceptance is **not established**.

## Exact resume point

1. Finish patch-02 reference changes and metadata. Edit only `service.py` and `helpers.py`; preserve public tests/license. Dynamically delegate both service APIs to helpers, centralize text validation, preserve signatures and prefix-first TypeError behavior.
2. Add a bounded schema 1.3 `behavior_preserving_refactor` / `synthetic_refactor` profile. Reject wrong versions/counts, boolean counts, escaped paths, bad hidden hashes and command grants. Preserve patch-01 schema/digest and legacy fixture behavior.
3. Extend the shared native Graph/Deep harness using existing write-only approval: four public reads → propose patch → pause → close/reconstruct → approve once. No new command/execute permission. Refactor evidence must show frozen public tests green before AND after, rather than claim test-mutation sensitivity.
4. Extend pure evidence validation for exact 5 public/5 regression tests, two structural targets and protected-file/receipt hashes. Exercise comment-only, service-only and changed-error-priority negative controls in both modes, plus evidence mutations.
5. Add patch-02 Graph/Deep to task audit: expected 11 runs, schema 1.3. Run the 24 new tests and existing focused/full/safety/repeat/frontend gates; do not treat expected counts as observed results.
6. Only after passing gates: write clean-source evidence/report, task-scoped commit/push, verify remote HEAD and exact-commit CI, and update the existing plan. Proceed to patch-03 only after patch-02 acceptance.

No patch-02 acceptance, publication, production fix, paid quality result or full-matrix readiness is claimed by this record.
