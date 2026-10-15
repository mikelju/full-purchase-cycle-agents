# 008 - Quotes flow animation

Status: integrated (merged via PR #15)

## Contract
Goal and reason:
The animation of change 005 shows the orders side.
The owner wants a second, complementary animation for the quotes part (phases 06-07), the second of a series: orders (005), quotes (this one), later invoice reconciliation and the general orchestration.
It shows a shortfall that becomes three supplier quote requests, the three replies, the decision graph comparing them, the recommendation and the human approval that issues the purchase order.
The owner approved the draft design on 2026-10-13 and asked to polish two details before recording it: the redundant "declined" text on SUP-001 (now "no offer", the DECLINED pill stays) and the white band behind the figures inside the decision cards.
Out of scope:
Code, tests, datasets and evaluations; any other change to the design; the invoice and orchestration animations.
Constraints / decisions that need the owner:
None; no new dependency or cost (Playwright and ffmpeg are used from outside the project).
The flow is conceptual, accepted by the owner, with these simplifications against the code:
- SUP-001 declines in the animation, whereas in `quotes-demo` that supplier never replies.
- The wait for replies is compressed and shows no reminders or deadline.
- Lead times are shown in working days, while the code compares calendar days.
- The orders graph appears without the clarification round.
Current authorization and limits:
Owner request 2026-10-13 to deliver as branch `cambio-008-quotes-flow-animation` and a PR to `main`, stacked on PR 14 (change 007) because it uses the `docs/assets/flow-animation/` folder.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `docs/assets/quotes-flow.gif` is 960x540, loops forever and stays under about 4 MB; `docs/assets/quotes-flow.mp4` is 1280x720, H.264, yuv420p | `ffprobe` and file sizes |
| C2 | `docs/assets/flow-animation/quotes-flow.html` is the source; `record.py` records either page and the README of that folder lists both pages and the exact ffmpeg commands | The folder and its README |
| C3 | In the GIF, SUP-001 shows the DECLINED pill and "no offer" and the decision cards have no lighter band behind the figures; text is legible | Three frames extracted from the GIF (decision cards, awaiting approval, final) |
| C4 | The README shows the GIF, links the MP4 and says the flow is conceptual | README |
| C5 | The simplifications above are recorded in this file | This file |
| C6 | `npm run check` exits 0 | `.evidence/cambio-008/check.txt` (git-ignored) |

## Plan
- [x] 1. Polish the draft, store the page, make `record.py` take a page name; C2.
- [x] 2. Record frames and encode GIF and MP4; C1, C3.
- [x] 3. README and folder README; C4.
- [x] 4. `npm run check`, push and PR to `main`; C6.

## Decisions
Removing the white band exposed the "email reply" waiting text, which it used to hide; the text is now part of the waiting group and fades out when the reply arrives.
`record.py` writes frames to `frames/<page name>/` and cleans that folder first, so two pages never mix.
The installed ffmpeg on PATH lacked `palettegen`, so a full build was used and its commands are recorded in the folder README.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| - | not run | - | - | Media and docs only, owner approved the design | - |

## Delivery
See the PR description for results per criterion.
