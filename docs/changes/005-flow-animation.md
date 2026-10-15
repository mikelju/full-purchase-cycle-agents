# 005 - Purchase cycle flow animation

Status: integrated (merged via PR #12)

## Contract
Goal and reason:
The owner wants an animated picture of the graphs at work for the README and a LinkedIn post: three customers (email, WhatsApp, web form) whose orders run independently through the orders graph, one email clarification round trip, and the quotes graph with one shortfall that ends in a supplier quote request.
The animation was designed and approved by the owner on 2026-10-12 outside the repository; this change only stores the final recording and links it.
Out of scope:
Code, tests, datasets and evaluations; the animation source page and its recorder script, which stay outside the repository.
Constraints / decisions that need the owner:
None; no new dependency or cost.
The animation is a conceptual flow, accepted by the owner: today the email channel stores the question without sending it, the answer arrives through `clarify answer`, each channel is its own graph and `quotes scan` is a separate command until phase 10.
Current authorization and limits:
Owner request 2026-10-12 to save the video and the GIF of the final version; delivery is the work branch `cambio-005-flow-animation` and a PR to `main`.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `docs/assets/purchase-cycle-flow.gif` (960x540, loops) and `docs/assets/purchase-cycle-flow.mp4` (1280x720, H.264) are the final light version approved by the owner | `ffprobe` output; owner saw both files |
| C2 | The README shows the GIF under the status lines, links the MP4 and says the flow is conceptual | README; GitHub rendering of the PR branch |
| C3 | `npm run check` exits 0 | Command output |

## Plan
- [x] 1. Copy the recordings and link them from the README; C1, C2.
- [x] 2. `npm run check`, review of the diff, push and PR to `main`; C3.

## Decisions
The GIF is the README picture because GitHub renders it inline; the MP4 is kept for LinkedIn, where video plays better than GIF.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | local, 2026-10-12 | `main..b256842` | fresh lens | none | closed |

Round 1 checked the README image and video paths, the alt text, the conceptual-flow sentence and the recordings with `ffprobe`; no findings.
Out of scope, left for the start of phase 07: README line 9 still says phase 06 is in progress, and the master plan row 06 status predates the merge of PR #11.

## Delivery
Results per criterion:
- C1: met; `ffprobe` shows the GIF at 960x540 with 521 frames and infinite loop, and the MP4 at 1280x720 H.264, 521 frames, 34.73 s; both are the light version approved by the owner on 2026-10-12.
- C2: met; the README shows the GIF before "What it does", links the MP4 and states the flow is conceptual.
- C3: met; `npm run check` exit 0 at b256842 (731 tests passed), evidence in `.evidence/change-005/check-b256842.txt` (git-ignored).
