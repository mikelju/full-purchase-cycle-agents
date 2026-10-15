# 011 - Reconciliation flow animation

Status: integrated (merged via PR #19)

## Contract
Goal and reason:
The animations of changes 005 and 008 show the orders and quotes sides.
The owner wants a third, complementary animation for the invoice reconciliation part (phase 08), the third of a series: orders (005), quotes (008), reconciliation (this one) and later the general orchestration.
It shows delivery notes and invoices read from a folder, extracted by the model, linked to their purchase order, matched in plain code (order vs delivery note vs invoice) and stored as matched, discrepancies or awaiting delivery note.
A second scan of the `later/` folder completes an awaiting invoice.
Out of scope:
Code, tests, datasets and evaluations; any change to the other animations; the orchestration animation.
Constraints / decisions that need the owner:
The owner approved the draft design on 2026-10-14 ("está perfecta") with two fixes: the long invoice file name touching the edge of the documents box, and the stale awaiting result and invoice file still shown during the later scan.
No new dependency or cost (Playwright is used from outside the project).
The flow is conceptual, with these simplifications against the code:
- 5 of the 7 reconciliations of the real demo are shown; INV-0009 and INV-0014 are omitted, as are the two unlinked documents (`unknown_order` and `supplier_mismatch`).
- Extraction is one step, without model retries or `extraction_failed`.
- Each order has one or two SKUs.
- The tolerance (0.01 EUR per unit for price, 0 for quantity) is shown as one line.
- The awaiting example is INV-0011, completed by the later delivery note, whereas INV-0014 stays awaiting at the end of the real demo.
Current authorization and limits:
Owner approval on 2026-10-14 to deliver as branch `cambio-011-reconcile-flow-animation` and a PR to `main`.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `docs/assets/reconcile-flow.gif` is 960x540, loops forever and stays under about 4 MB; `docs/assets/reconcile-flow.mp4` is 1280x720, H.264, yuv420p | `ffprobe` and file sizes |
| C2 | `docs/assets/flow-animation/reconcile-flow.html` is the source; `record.py` records it and the README of that folder lists the three pages and the ffmpeg commands | The folder and its README |
| C3 | In the GIF, the moving token never covers a node label, the database chips show only the current case and text is legible | Frames extracted from the GIF (extract, matched, discrepancy, awaiting, final) |
| C4 | The README shows the GIF, links the MP4 and says the flow is conceptual | README |
| C5 | The simplifications above are recorded in this file | This file |
| C6 | `npm run check` exits 0 | `.evidence/cambio-011/check.txt` (git-ignored) |

## Plan
- [x] 1. Draft the page in the visual language of `quotes-flow.html`; fix token and stale chips; record frames to review.
- [x] 2. Owner approves the design (2026-10-14) with two fixes; apply them; C3.
- [x] 3. Record frames and encode GIF and MP4; C1.
- [x] 4. README and folder README; C2, C4.
- [x] 5. `npm run check`, push and PR to `main`; C6.

## Decisions
The moving token is hidden while a node is active so it never covers the node label.
The database chips for delivery note, invoice, lines and reconciliation are cleared at the start of each case.
The file name font shrinks to fit the card (at most 10 px, 128 px wide), so `inv-006-thermometers.xlsx` stays inside the box.
The second scan clears the left list and the result, keeps the order and invoice figures of INV-0011 and clears the database chips before the delivery note rides through.
The token fade-out was removed (it only fades in) so no ghost token lingers over a node label.
The installed ffmpeg on PATH lacks `palettegen`, so the full build under WinGet (`Gyan.FFmpeg`, 8.0.1) was used with the README commands.

## Evidence
- C1: `ffprobe` of the MP4 gives h264, 1280x720, yuv420p, 579 frames, 38.6 s; the GIF is gif, 960x540, 579 frames, 38.6 s, with the NETSCAPE loop block (loop forever). Sizes: GIF 2,449,396 bytes (2.3 MiB), MP4 1,164,133 bytes (1.1 MiB), against quotes 1,266,427 and 671,383 bytes; the GIF is under 4 MB.
- C2: `docs/assets/flow-animation/README.md` lists the three pages, the `record.py` command for this one and the ffmpeg commands (same `quotes-flow` to `reconcile-flow` substitution).
- C3: frames 40, 110, 200, 290, 350, 420, 480, 510, 545 and 578 of the capture checked (extract, matched, discrepancy, delivery-note-only case, awaiting, second scan, final): the token never covers a node label, the database chips show only the current case, the file name fits the box, and during the second scan the left list is empty and the middle panel shows no stale result.
- C4: `README.md` embeds `docs/assets/reconcile-flow.gif`, links the MP4 and says the flow is conceptual, in the reconciliation section.
- C5: simplifications listed in the contract above.
- C6: `npm run check` exit 0 (1032 tests passed, all evaluation gates PASS), output kept outside the repository in the session scratchpad (`check-011.txt`). A first run had 36 subprocess failures (Windows exit 3221225794) caused by overlapping check runs in the same session; the clean rerun passed.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| - | not run | - | - | Media and docs only, owner approved the design | - |

## Delivery
PR: https://github.com/mikelju/full-purchase-cycle-agents/pull/19
See the PR description for results per criterion.
