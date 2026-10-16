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
| C3 | `record.py` loads every declared font face before the first frame, and its English output is unchanged | Frame comparison of the English orders page with the old and new recorder |
| C4 | Frames extracted from the Spanish GIFs show correct accents and ñ in a consistent font, and no text overflows its box | Frames under `.evidence/cambio-016/` (git-ignored) |
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
Node labels are translated within the node width: `email intake` becomes `canal email`, `quotes scan` becomes `revisar stock` and `shortfalls` becomes `déficit`.
In the reconciliation page the result pills and the database chips use Spanish names (`CONCILIADA`, `CON DISCREPANCIAS`, `PENDIENTE DE ALBARÁN`), and the discrepancy types are shown through a name map (`precio`, `cantidad`, `línea extra`, `falta línea`) while the internal keys stay.
Two defects of the English reconciliation page were fixed only in the Spanish copy, because the English files stay untouched:
- The awaiting case showed the stale `60` of the previous case in the delivery note cell instead of `none yet`; the Spanish copy shows `aún no`.
- The invoice chip `inv  F-2026-1001linked` had no space before the status; the Spanish copy pads one more character (`fac  F-2026-1001 enlazada`).
Both remain in the English page as a candidate follow-up.
Root cause of the glyph glitch: the pages had no charset declaration, so a UTF-8 file read from disk can be decoded with another encoding; and `record.py` only waited for `document.fonts.ready`, which covers the faces used at load time, so a weight or glyph subset first needed later could load mid-capture and leave frames in a fallback font.
The fix is the charset meta in the Spanish pages (the English pages are pure ASCII and do not need it) and loading every declared face in `record.py`; with the 45 faces of the Google Fonts stylesheet this is simpler than a hand-kept list of families and weights.

## Evidence
- C1: `ffprobe` gives, for `purchase-cycle-flow-es`, GIF 960x540 521 frames 34.7 s (1,764,431 bytes) and MP4 h264 1280x720 yuv420p 521 frames (835,535 bytes); `quotes-flow-es` GIF 960x540 589 frames 39.3 s (1,325,520 bytes) and MP4 h264 1280x720 yuv420p 589 frames (742,571 bytes); `reconcile-flow-es` GIF 960x540 579 frames 38.6 s (2,507,754 bytes) and MP4 h264 1280x720 yuv420p 579 frames (1,178,603 bytes). Each GIF has one NETSCAPE2.0 loop block (loop forever). Frame counts match the English pages.
- C2: the three `-es.html` files are built from the English ones by about 140 exact string replacements (each checked to match the expected number of times), plus the delivery note cell fix; the first line of each is `<meta charset="utf-8">`.
- C3: the English orders page was recorded with the recorder of `main` and with the new one: 521 frames each, 507 byte-identical, and the other 14 differ by at most 5 of 255 per channel (anti-aliasing noise in glow areas), with no glyph change. The new recorder reports 45 font faces loaded.
- C4: frames 120, 300 and 470 (orders), 200, 420, 520 and 580 (quotes) and 160, 215, 280, 450, 545 and 578 (reconciliation, final encoding; frames 110, 200, 290, 350, 420 and 560 were checked on the first encoding, before the delivery note cell fix) were extracted from the GIFs and checked: `ACLARACIÓN`, `déficit`, `GRAFO DE DECISIÓN`, `única`, `GRAFO DE CONCILIACIÓN`, `albarán`, `línea extra`, `aún no` and `PENDIENTE DE ALBARÁN` render in Inter and JetBrains Mono like the surrounding text, with no box overflow. Frame 450 of the English reconciliation GIF shows the stale `60` defect for comparison.
- C5: `git status` lists no change to the English GIFs and MP4s or to the root README; the folder README lists the Spanish pages, their `record.py` commands and the ffmpeg substitution.
- C6: pending.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| - | pending | - | - | - | - |

## Delivery
Pending.
