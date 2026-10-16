# 016 - Spanish flow animations

Status: delivered, in review

## Contract
Goal and reason:
The owner will publish a four-post Spanish LinkedIn series about this project (change 015).
Post 1 uses the orders animation, post 2 the quotes one and post 3 the reconciliation one, so each needs a Spanish copy.
A previous attempt showed broken accents and letter ñ.
Out of scope:
The English pages, GIFs, MP4s and the root README, which stay as they are; code, tests, datasets and evaluations; phase 11.
Constraints / decisions that need the owner:
No new dependency or cost (Playwright and ffmpeg are used from outside the project, as in changes 008 and 011).
Current authorization and limits:
Request of 2026-10-16 from the session that runs the LinkedIn series: build the change, push branch `cambio-016-spanish-flow-animations` and open a PR to `main`; never merge.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `docs/assets/purchase-cycle-flow-es`, `quotes-flow-es` and `reconcile-flow-es` exist as GIF (960x540, loops forever) and MP4 (1280x720, H.264, yuv420p) | `ffprobe` and file sizes |
| C2 | The three Spanish sources sit next to the English ones, declare `<meta charset="utf-8">` and keep the same layout and timeline; visible texts are in natural Spanish (Spain) while product codes, file names, amounts and identifiers stay | The `-es.html` files and the frame checks |
| C3 | `record.py` loads every declared font family and weight (with accented sample text) before the first frame, and its English output is unchanged | Frame comparison of the English orders page with the old and new recorder |
| C4 | Frames extracted from the Spanish GIFs show correct and legible accents and ñ in a consistent font, and no text overflows its box | Frames under `.evidence/cambio-016/` (git-ignored) |
| C5 | The English GIFs, MP4s and the root README are untouched; the folder README lists the Spanish pages and their commands | `git diff --stat` and the folder README |
| C6 | `npm run check` exits 0 | Check output (outside the repository) |

## Plan
- [x] 1. Font loading in `record.py` and English output comparison; C3.
- [x] 2. Spanish copies of the three pages with charset; C2.
- [x] 3. Record and encode the three pairs; check frames; C1, C4.
- [x] 4. Folder README; C5.
- [ ] 5. `npm run check`, adversarial review, push and PR to `main`; C6.

## Decisions
Translation rule: plain words are translated; snake_case identifiers, table names, the database file, folder names (`documents/`, `later/`, `supplier_outbox/`), CLI commands (`quotes decide`, `quotes approve QR-0001`, `reconcile list`), product codes, document numbers, file names and amounts stay as in the English pages.
So statuses with an underscore (`awaiting_approval`, `ready_for_decision`) and channel values (`email`, `whatsapp`, `web_form`) stay, while one-word statuses are translated (`received` to `recibido`, `sent` to `enviada`).
Customer and supplier names come from the dataset and keep its spelling without accents (for example `Clinica Dental Arenal`).
Graph, subgraph and node labels are translated like plain words (`clarification` to `aclaración`, `quote_request` to `solicitud de presupuesto`), within the node width: `email intake` becomes `canal email`, `quotes scan` becomes `revisar stock` and `shortfalls` becomes `déficit`.
In the reconciliation page the result pills, the database chips and the `reconcile list` chips use the same Spanish names (`conciliada`, `con discrepancias`, `pendiente de albarán`), except that the list chip shortens the last one to `pendiente` to fit; the discrepancy types are shown through a name map (`precio`, `cantidad`, `sobrante`, `ausente`) while the internal keys stay.
`sobrante` and `ausente` replace `línea extra` and `falta línea` because at 960 px wide the accent of `í` in the 10.5 px database chips blurs away and reads as `linea`.
Two defects of the English reconciliation page were fixed only in the Spanish copy, because the English files stay untouched:
- The awaiting case showed the stale `60` of the previous case in the delivery note cell instead of `none yet`; the Spanish copy shows `aún no`.
- The invoice chip `inv  F-2026-1001linked` had no space before the status; the Spanish copy pads one more character (`fac  F-2026-1001 enlazada`).
- The second scan set the INV-0011 result and its note but never showed them, so the middle panel stayed empty; the Spanish copy shows `CONCILIADA` and the note, which is also where the only ñ appears (`no añade otra fila`).
All three remain in the English page as a candidate follow-up.
Root cause of the glyph glitch: the pages had no charset declaration, so a UTF-8 file read from disk can be decoded with another encoding; and `record.py` only waited for `document.fonts.ready`, which covers the faces used at load time, so a weight or glyph subset first needed later could load mid-capture and leave frames in a fallback font.
The fix is the charset meta in the Spanish pages (the English pages are pure ASCII and do not need it) and, in `record.py`, a `document.fonts.load` call per declared family, style and weight with a sample of accented letters, up to three tries each, failing the capture if one never loads.
The list comes from the stylesheet itself (7 combinations), so no hand-kept list goes stale.
A first version loaded all 45 declared faces, including unused Cyrillic, Greek and Vietnamese subsets; one of them failed on the network and aborted a capture, so it was narrowed to the faces the text needs.
The status of change 014 is set to integrated in this branch's first commit, as step 4 of the SDD Lite flow requires.

## Evidence
All of it was taken after the last change to the pages and the recorder; frames and logs are in `.evidence/cambio-016/` (git-ignored).
- C1: `ffprobe` gives, for `purchase-cycle-flow-es`, GIF 960x540 521 frames 34.7 s (1,750,933 bytes) and MP4 h264 1280x720 yuv420p 521 frames (833,185 bytes); `quotes-flow-es` GIF 960x540 589 frames 39.3 s (1,308,510 bytes) and MP4 h264 1280x720 yuv420p 589 frames (740,526 bytes); `reconcile-flow-es` GIF 960x540 579 frames 38.6 s (2,562,336 bytes) and MP4 h264 1280x720 yuv420p 579 frames (1,198,725 bytes). Each GIF has one NETSCAPE2.0 loop block (loop forever). Frame counts match the English GIFs.
- C2: the three `-es.html` files were built from the English ones by exact string replacements, each asserted to match the expected number of times (scripts `tr_purchase.py`, `tr_quotes.py` and `tr_reconcile.py`, 138 replacements), then edited for the review findings and the two reconciliation fixes; the first line of each is `<meta charset="utf-8">`.
- C3: the English orders page was recorded with the recorder of `main` and with the final one (`english-compare.txt`): 521 frames each, 509 byte-identical, and the other 12 differ by at most 5 of 255 per channel (anti-aliasing noise in glow areas), with no glyph change. The final recorder reports 7 family and weight combinations loaded.
- C4: frames 120, 300 and 470 (orders), 200, 420 and 520 (quotes) and 110, 215, 330, 450, 545 and 578 (reconciliation) were extracted from the final GIFs (`contact-sheet.png` and the single frames) and checked: `ACLARACIÓN`, `déficit`, `GRAFO DE DECISIÓN`, `única`, `GRAFO DE CONCILIACIÓN`, `albarán`, `aún no`, `PENDIENTE DE ALBARÁN` and `no añade otra fila` render in Inter and JetBrains Mono like the surrounding text, with no box overflow (`zoom-ene.png` enlarges the ñ). The small accented chips that blurred at GIF size were reworded (see Decisions).
- C5: `git diff --name-only origin/main` lists no English GIF, MP4 or root README; the folder README lists the Spanish pages, their `record.py` commands and the ffmpeg substitution.
- C6: see Delivery.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | Local, two subagents in clean context | `origin/main...e150eef` | Correctness; evidence | Correctness: 4 menor (font preload aborts on any failed subset, `quote_request` against the stated rule, `declina` as a status, three names for one status). Evidence: 2 importante (no ñ in any page; `línea` reads as `linea` in GIF chips), 5 menor (status, C3 and C2 not re-checkable, 014 status edit, two names for the purchase order) | All confirmed except the 014 edit (required by the flow) and the C3 re-check (comparison now logged); fixed: font loading narrowed with retries (the all-faces version failed once on the network), rule reworded, `declinada`, harmonised status names, `sobrante`/`ausente`, natural ñ shown in the second scan, `orden de compra` header, scripts and comparison kept in `.evidence/` |

## Delivery
Pending.
