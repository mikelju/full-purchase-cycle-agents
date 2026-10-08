# 002 - Raise the stop hook timeout to 300 s

Status: ready locally

## Contract
Goal and reason:
The `Stop` hook runs `npm run check` through `.claude/hooks/stop-gate.mjs` with a 120 s hook timeout and a 110 s inner timeout.
On phase 04, `npm run check` alone takes about 117-158 s, so the hook times out after every turn even when all checks are green.
The owner decided on 2026-10-08 to raise the hook timeout to 300 s.
Out of scope:
The checks themselves, their speed and any other hook.
Constraints / decisions that need the owner:
None; the value was set by the owner.
Current authorization and limits:
Owner decision 2026-10-08; delivery is the work branch and a PR to `main`.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | The `Stop` hook entry for `stop-gate.mjs` in `.claude/settings.json` has `"timeout": 300` | `git diff` of `.claude/settings.json` |
| C2 | The `spawnSync` call in `stop-gate.mjs` uses `timeout: 290000`, 10 s under the hook timeout as before | `git diff` of `.claude/hooks/stop-gate.mjs` |
| C3 | `npm run check` exits 0 | Command output |

## Plan
- [x] Raise both timeouts and verify.

## Decisions
The inner timeout keeps the existing 10 s margin under the hook timeout so the gate can still report a timeout itself.
No test, script or README on `main` asserts the old values (searched for `110000` and `"timeout": 120`).

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | local | this change | correctness, consistency | none; two literal values, no other references | closed |

## Delivery
C1: `.claude/settings.json` now shows `"timeout": 300` on the `stop-gate.mjs` entry.
C2: `.claude/hooks/stop-gate.mjs` now calls `spawnSync` with `timeout: 290000`.
C3: `npm run check` on this branch (based on `main`) exited 0 in 127 s on 2026-10-08, already above the old 110 s inner limit.
