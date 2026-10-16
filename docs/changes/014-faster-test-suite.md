# 014 - Faster test suite

Status: in progress

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
- [ ] Measure durations at the base commit and find the causes (C1)
- [ ] Speed up the slowest tests or their shared setup (C2, C3)
- [ ] Verification, adversarial review and delivery (C4)

## Decisions

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Delivery
