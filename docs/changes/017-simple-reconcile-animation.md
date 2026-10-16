# 017 - Simple reconcile animation

Status: in progress

## Contract
Goal and reason:
The reconciliation animation of change 011 packs five reconciliations and a second scan into about 38 seconds and cannot be followed without pausing it.
The owner wants a shorter, simpler version that can be followed at normal speed from start to end, showing which graph node each step goes through and what happens in it.
It keeps only two cases: the clean case, labelled as such, and one case that does not match, followed by what happens next.
It is made in English and in Spanish; the Spanish one is the video of post 3 of the LinkedIn series (change 015).
Out of scope:
The orders and quotes animations; code, tests, datasets and evaluations; phase 11.
Constraints / decisions that need the owner:
The scene-by-scene script, whether the new animation replaces the current one, and any change to the approved post 3 text.
No new dependency or cost (Playwright and ffmpeg are used from outside the project, as in changes 008, 011 and 016).
Current authorization and limits:
Request of 2026-10-16 from the owner, relayed by the session that plans phase 05: build the change from `main`, show the script in a Lavish page and wait for approval before recording, push branch `cambio-017-simple-reconcile-animation` and open a PR to `main`; never merge.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | The English and Spanish animations exist as GIF (960x540, loops forever, under about 4 MB) and MP4 (1280x720, H.264, yuv420p) | `ffprobe` and file sizes |
| C2 | The recording lasts at most 34 seconds and shows exactly two reconciliations: one labelled as the correct case that ends matched, and one that ends with discrepancies, followed by what happens next | Frame count and extracted frames |
| C3 | Every node the invoice passes through is highlighted while active, with a one-line caption of what it does in that case, held on screen for at least 1.2 seconds (0.9 seconds for `read` and `link` in the second case, already seen in the first); the moving token never covers a node label | Timeline constants in the page and extracted frames |
| C4 | The Spanish page follows the translation rules of change 016 and its frames show correct, legible accents in a consistent font, with no text overflowing its box | Frames under `.evidence/cambio-017/` (git-ignored) |
| C5 | Every repository reference to the reconciliation animation matches the approved replace-or-add decision, and the README describes what the new animation shows | `git grep reconcile-flow` and the README diff |
| C6 | `npm run check` exits 0 | Check output (outside the repository) |

## Plan
- [x] Script approved by the owner in a Lavish page (2026-10-16, all five answers as recommended)
- [ ] English page built and recorded
- [ ] Spanish page built and recorded
- [ ] References updated (README, folder README, LinkedIn plan if approved)
- [ ] Adversarial review and `npm run check`

## Decisions
Owner answers of 2026-10-16 in the Lavish script page:
- **Script:** approved as shown; it is the timeline below.
- **Replace, not add:** the new animation replaces the current one under the same file names (`reconcile-flow.html`, `reconcile-flow-es.html` and their GIF and MP4), so the README and the LinkedIn plan keep pointing to the right files and only one version is maintained; the old one stays in Git history.
  The three defects of the English page recorded in change 016 disappear with it, so the proposed task to fix them was withdrawn.
- **Mismatch case:** INV-0008 (`inv-003-gauze.xlsx`, PO-0003): 35 gauzes invoiced when 40 were ordered and delivered, a quantity difference of -25.00 EUR. It reads at a glance and post 3 already quotes it.
- **What happens next:** a closing scene shows the claim graph of phase 09 in small (draft, review by a person, send).
- **Post 3 text:** the sentence that describes the video is reworded to the two cases.

Approved timeline (seconds, about 30 s of story plus the 2 s recording tail):
| Time | Scene | Caption under the graph (English) |
|---|---|---|
| 0-2 | Title; graph idle; orders and delivery notes already stored | "Reconciling an invoice: order, delivery note and invoice" / "the delivery note arrived first and is already stored" |
| 2-4 | "Case 1 - everything matches"; `inv-001-nitrile.txt` enters | |
| 4-5 | `read` (1.4 s) | "file fingerprint (SHA-256): never processed twice" |
| 5-8 | `extract` (2.2 s), extracted lines appear | "Claude Haiku reads the invoice: GLV-NIT-M, 100 x 4.20 EUR" |
| 8-9 | `link` (1.4 s) | "the invoice quotes PO-0001: order found" |
| 9-12 | `match` (2.6 s), ledger turns green | "order 100 x 4.20 / delivery note 100 / invoice 100 x 4.20: all agree" |
| 12-13 | `store` (1.4 s), `reconciliations` row | "reconciliation stored, no discrepancies" |
| 13-15 | MATCHED stamp (1.8 s) | |
| 15-16 | "Case 2 - does not match"; `inv-003-gauze.xlsx` enters | |
| 16-19 | `read`, `extract`, `link` (0.9, 1.6, 0.9 s) | "new fingerprint" / "reads: GAUZE-NS-5, 35 x 5.00 EUR" / "quotes PO-0003: order found" |
| 19-23 | `match` (3.6 s), invoice cell turns red | "order 40 / delivery note 40 / invoice 35: quantity difference, -25.00 EUR" |
| 23-24 | `store` (1.4 s), `reconciliations` and one `discrepancies` row | "reconciliation with discrepancies and one discrepancy row" |
| 24-27 | DISCREPANCIES stamp (2.2 s) | |
| 27-30 | What happens next: small graph draft, review (person), send | "next: a claim email to the supplier is drafted, a person approves it and it is sent" |

Conceptual simplifications (as in change 011, the flow is conceptual): the delivery notes do not ride the graph, they are shown as already stored; only two of the demo reconciliations appear; the claim step is a separate graph shown as a summary.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Delivery
