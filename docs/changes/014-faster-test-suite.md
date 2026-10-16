# 014 - Faster test suite

Status: delivered, in review

## Contract
Goal and reason:
`npm run check` takes 20 to 55 minutes, and its pytest stage alone took 1675 s for 1288 tests at 1226c16 (change 013 evidence).
That is far more than unit tests of this size need, so a few slow tests probably dominate.
Owner request of 2026-10-16: measure the slowest tests and make the check faster without losing coverage.
Out of scope:
Changing what any test asserts, deleting or skipping tests, changing frozen phase criteria, the eval stage, and CI on GitHub.
Constraints / decisions that need the owner:
A new dependency (for example a parallel test runner) needs owner approval.
Merging, removing or marking tests as optional needs owner approval.
`npm run check` stays offline and free.
Current authorization and limits:
Owner approval of 2026-10-16 ("go ahead with the slow tests change"): build the change, push the branch and open a PR to `main`; never merge.

| ID | Observable criterion | Evidence at verification |
|---|---|---|
| C1 | The slowest tests and the cause of their cost are measured and recorded at the base commit | `pytest --durations` output at the base commit and a short cause summary in this document |
| C2 | The pytest stage of `npm run check` takes at most half of the base time measured in C1 on the same machine | Timed `pytest -q` runs before and after |
| C3 | The same tests are collected and pass: no test removed, skipped or with weaker assertions unless the owner approved it | Collected count before and after; diff of `tests/` reviewed for assertions |
| C4 | `npm run check` passes | `npm run check` exit 0 at the final commit |

## Plan
- [x] Mark change 013 as integrated (merged via PR #23)
- [x] Measure durations at the base commit and find the causes (C1)
- [x] Speed up the slowest tests or their shared setup (C2, C3) (1b9c0f6, 3834d2f, fee8475)
- [x] Verification, adversarial review and delivery (C4)

## Decisions
Causes measured at 73797ad (C1): `pytest -q` took 1674.66 s and about 24 tests took about 1250 s of it.
Every child process paid about 3.8 s to import `purchase_cycle.cli`, 2.65 s of it the `anthropic` SDK, imported at top level only for `isinstance` checks; about 260 child processes run across the suite.
The two eval-command tests stubbed 5 suites while `suite="all"` runs 17, so 12 ran for real and duplicated the eval stage of `npm run check` (about 350 s).
Several demo tests and fixtures ran the identical demo command more than once (about 140 s).
No sleeps, retry backoff or network timeouts were found.
- `anthropic` is imported lazily: an Anthropic error can only exist once the SDK is imported, so the checks look it up in `sys.modules`; `STEP_FAILURES` became `step_failures()`, evaluated in the `except` clause.
- The eval-command tests stub every suite and keep their assertions; the real replay of every suite now runs only in the eval stage of `npm run check`, not in pytest.
  As a result the Stop hook gate `check:puerta` (ruff and pytest, no eval stage) no longer catches a broken suite replay; `npm run check` before every delivery still does.
- Identical demo runs are shared through session and module fixtures used only for reading; tests that write work on a copy, and tests that vary inputs keep their own runs.
- No parallel runner was added: pytest-xdist would need owner approval and the target was met without it.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | Opus subagents | f1a616e..fee8475 | correctness, evidence | correctness none; evidence 1 important (the subset after-timings were not comparable with the full-suite before-timing) and 1 minor (the eval replay left pytest without a recorded decision) | the important one closed by the full-suite timing below; the minor one recorded in Decisions |

## Delivery
- Branch `cambio-014-slow-tests`.
- `npm run check` exit 0 at fee8475 in 770 s, with 1288 pytest tests passed in 639.67 s against 1674.66 s at 73797ad on the same machine (38 percent of the base time, C2).
- Collected tests: 1288 before and after; no assertion changed (C3).
- Only the known critical eval cases DOC-060, DOC-112, CLM-044, CLM-066 and CLM-072 are listed, as in change 013.
- Evidence in the ignored `.evidence/cambio-014/`: `durations-before.txt`, `durations-touched.txt`, `check.txt`.
