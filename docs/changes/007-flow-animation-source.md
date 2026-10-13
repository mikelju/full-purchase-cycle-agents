# 007 - Purchase cycle flow animation source

Status: ready locally (PR to `main`, pending owner merge)

## Contract
Goal and reason:
Change 005 stored the final GIF and MP4 of the flow animation but left the source page and the recorder script outside the repository.
The owner asked on 2026-10-13 to keep both in the repository so the animation can be regenerated later.
Out of scope:
Code, tests, datasets and evaluations; the frames, backups, dark versions and the GIF/MP4 encoding commands, which were never scripted; changing the animation itself.
Constraints / decisions that need the owner:
None; Playwright and ffmpeg are not added as dependencies.
The only edits to the original files are path fixes and the lint fixes the repository linter requires in `record.py`.
Current authorization and limits:
Owner request 2026-10-13 to open this change and deliver it as branch `cambio-007-flow-animation-source` and a PR to `main`.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `docs/assets/flow-animation/purchase-cycle-flow.html` is byte for byte the page that produced the GIF and MP4 of 005; `record.py` differs only by the page path, the `frames` folder creation and ruff import order and line wrapping | md5 of the page; diff of `record.py` against the original |
| C2 | `docs/assets/flow-animation/README.md` says what the files are, that the flow is conceptual, how to capture frames, that encoding used ffmpeg with unrecorded commands, and that Playwright and ffmpeg are not dependencies | The README |
| C3 | The `frames` folder the script creates is git-ignored | `git check-ignore` on a path inside it |
| C4 | `npm run check` exits 0 | `.evidence/cambio-007/check.txt` (git-ignored) |

## Plan
- [x] 1. Copy the page and the script, fix paths, lint and format; C1, C3.
- [x] 2. Write the README; C2.
- [x] 3. `npm run check`, push and PR to `main`; C4.

## Decisions
Frames are written inside the folder and git-ignored (one `.gitignore` line) because it is simpler than choosing an external path.
The script was not run: it needs Chrome and Playwright, which are not installed here and not project dependencies.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| - | not run | - | - | Copy of owner-verified files; no product code | - |

## Delivery
See the PR description for results per criterion.
