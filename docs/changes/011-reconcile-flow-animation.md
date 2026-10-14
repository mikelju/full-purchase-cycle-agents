# 011 - Reconciliation flow animation

Status: proposed - design pending owner approval

## Contract
Goal and reason:
The animations of changes 005 and 008 show the orders and quotes sides.
The owner wants a third, complementary animation for the invoice reconciliation part (phase 08), the third of a series: orders (005), quotes (008), reconciliation (this one) and later the general orchestration.
It shows delivery notes and invoices read from a folder, extracted by the model, linked to their purchase order, matched in plain code (order vs delivery note vs invoice) and stored as matched, discrepancies or awaiting delivery note.
A second scan of the `later/` folder completes an awaiting invoice.
Out of scope:
Code, tests, datasets and evaluations; GIF and MP4 encoding, READMEs and any change to the other animations; the orchestration animation.
Constraints / decisions that need the owner:
The draft design needs owner approval before any media is produced.
No new dependency or cost (Playwright is used from outside the project).
The flow is conceptual, with these simplifications against the code:
- 5 of the 7 reconciliations of the real demo are shown; INV-0009 and INV-0014 are omitted, as are the two unlinked documents (`unknown_order` and `supplier_mismatch`).
- Extraction is one step, without model retries or `extraction_failed`.
- Each order has one or two SKUs.
- The tolerance (0.01 EUR per unit for price, 0 for quantity) is shown as one line.
- The awaiting example is INV-0011, completed by the later delivery note, whereas INV-0014 stays awaiting at the end of the real demo.
Current authorization and limits:
Owner request to prepare the draft on branch `cambio-011-reconcile-flow-animation`; no push and no PR until the owner approves the design.

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
- [ ] 2. Owner approves the design; C3.
- [ ] 3. Record frames and encode GIF and MP4; C1.
- [ ] 4. README and folder README; C2, C4.
- [ ] 5. `npm run check`, push and PR to `main`; C6.

## Decisions
The moving token is hidden while a node is active so it never covers the node label.
The database chips for delivery note, invoice, lines and reconciliation are cleared at the start of each case.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| - | not run | - | - | Media and docs only, design pending owner approval | - |

## Delivery
