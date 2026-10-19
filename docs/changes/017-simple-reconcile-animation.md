# 017 - Simple reconcile animation

Status: integrated (merged via PR #27)

## Contract
Goal and reason:
The reconciliation animation of change 011 packs five reconciliations and a second scan into about 38 seconds and cannot be followed without pausing it.
The owner wants a shorter, simpler version that can be followed at normal speed from start to end, showing which graph node each step goes through and what happens in it.
It keeps only two cases: the clean case, labelled as such, and one case that does not match, followed by what happens next.
It is made in English and in Spanish; the Spanish one is the video of post 3 of the LinkedIn series (change 015).
Redesign of 2026-10-17: the owner found the first delivered version still confusing (too much text, pausing needed, unclear why the second case fails if the delivery note had arrived).
The new version is a conceptual flow with almost no text: three inputs (order, delivery note, invoice), their quantity, price and total extracted and compared, and a claim subgraph inside the same graph that only lights up in round 2.
Orders-style rebuild of 2026-10-17: the owner asked for the node style of the orders animation, the comparison table, and a claim that drafts an email, has a person approve it and sends it to a Supplier panel, where a three-line card shows the discrepancy and the credit-note request.
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
| C2 | The recording lasts at most 40 seconds and shows two rounds through one connected graph: in round 1 everything matches and the flow ends in the register node; in round 2 the invoice charges 40 units when the delivery note shows 35, and the flow goes through the claim subgraph of the same graph | Frame count and extracted frames |
| C3 | The only on-screen text is node names, the three document names, quantity, price and total figures, check and cross marks, a small round tag, the column headers, the yes and no branch labels, the claim frame name, the currency unit, the person label, the Supplier panel label and the three lines of the claim email card (no document IDs, no captions); one node is active at a time and each step is held at least 1.5 seconds | Timeline constants in the page and extracted frames |
| C4 | The Spanish page follows the translation rules of change 016 and its frames show correct, legible accents in a consistent font, with no text overflowing its box | Frames under `.evidence/cambio-017/` (git-ignored) |
| C5 | Every repository reference to the reconciliation animation matches the approved replace-or-add decision, and the README describes what the new animation shows | `git grep reconcile-flow` and the README diff |
| C6 | `npm run check` exits 0 | Check output (outside the repository) |

## Plan
- [x] Script approved by the owner in a Lavish page (2026-10-16, all five answers as recommended)
- [x] English page built and recorded (`c890b76`): 499 frames at 15 fps, 33.27 s, story 31.25 s
- [x] Spanish page built and recorded (`c890b76`): same timeline, same length
- [x] References updated (README, folder README, LinkedIn plan if approved) (`cb630dd`)
- [x] First version: adversarial review and `npm run check` (two rounds, see below; `npm run check` on `f0d6f41`: EXIT 0, 1288 tests passed, log in `.evidence/cambio-017/check-017.txt`; the later commit only touches this document)
- [x] Redesign: new script approved by the owner in a Lavish page (2026-10-17, all four answers as recommended)
- [x] Redesign: English and Spanish pages rebuilt and recorded (`6694391`; re-recorded in `8629aa1` after review round 1 with the progress bar on the measured story length): 460 frames at 15 fps, 30.67 s, story 28.65 s
- [x] Redesign: references updated (README, folder README, post 3 sentence) (`d6a0133`)
- [x] Redesign: adversarial review and `npm run check` (two rounds, see below; `npm run check` on `5042562`: EXIT 0, 1288 tests passed, log in `.evidence/cambio-017/check-017-redesign.txt`; the later commit only touches this document)
- [x] Orders style: owner decisions in a Lavish page (2026-10-17, see Decisions)
- [x] Orders style: English and Spanish pages rebuilt in the visual style of the orders animation with the claim email (`ef6e413`) and recorded: 536 frames at 15 fps, 35.73 s, story 33.70 s
- [x] Orders style: frames inspected on per-second contact sheets, no defects found; GIF and MP4 encoded in both languages
- [x] Orders style: references updated (folder README and README alt text; the post 3 sentence in the LinkedIn plan stays true and is unchanged)
- [x] Orders style: adversarial review round 5 (see below); fixes applied, both languages re-recorded and re-encoded
- [x] Orders style: `npm run check` on the fixed pages (`npm run check` on `4af9463`: EXIT 0, 1288 tests passed, log in `.evidence/cambio-017/check-017-round5.txt`; the later commit only touches this document)

Build notes and evidence of the orders-style rebuild (contact sheets in `.evidence/cambio-017/orders-style/`, probes and recorder logs in `.evidence/cambio-017/`, all git-ignored):
- C1: both GIFs are 960x540 with the loop block written by `-loop 0`, 536 frames, 1.13 MB (EN, 1127026 bytes) and 1.15 MB (ES, 1149807 bytes); both MP4s are 1280x720, h264, yuv420p, 536 frames, 0.56 MB (EN, 559465 bytes) and 0.55 MB (ES, 552035 bytes), re-recorded after review round 5 (`ffprobe-orders-style-reconcile-flow{,-es}.{gif,mp4}.txt`); encoded with the commands of the folder README and the full ffmpeg 8.0.1 build.
- C2: 536 frames at 15 fps = 35.73 s (GIF 35.74 s), under 40 s; the story lasts 33.70 s by the recorder clock (`story ms 33700` at frame 506 in `record-reconcile-flow{,-es}.html.log`, `LOOP` = 33700 in both pages), followed by the 2 s hold.
  The contact sheets (`sheet-reconcile-flow{,-es}-{1,2,3}.png`, one frame per second) show round 1 (100 x 4.20 = 420.00 in all three rows, every cell green) ending in Register and End, and round 2 (40, 35, 40 x 5.00) going through Match? no, Compute difference (-25.00 EUR chip), Draft email, Approve (person check), Send and the claim End node, with Register greyed.
  On Send the envelope flies along the edge into the Supplier panel and opens the three-line card (checked on frames 452 to 464 at full size).
- C3: timeline constants `T`, identical in both pages: documents 1.6 s in total (they turn teal one after another and are never active), extract 3.0 s, compare 2.8 s (round 1) and 3.4 s (round 2), decision 1.6 s, register 1.8 s, compute difference 2.4 s, draft email 2.0 s, approve 2.0 s, send 3.0 s (envelope flight 1.3 s), end 1.6 s, final hold 2.0 s; the shortest step hold is 1.6 s (the three document nodes are not steps: they turn teal about 0.53 s apart within one 1.6 s step and are never active).
  Each `step` sets one node active and sets it done before the next step starts, so the page state never has two active nodes; on screen the previous node fades out of amber over the 0.3 s CSS transition while the next one lights up, which the contact sheets show as a brief overlap and which is judged acceptable against C3 (logical state one active node, overlap under 0.3 s).
  On-screen text matches the reworded C3 except for the frame headers, labelled below as a deviation: the frames show no document IDs and no captions; the tokens that carry the figures from the documents to the table briefly cross the Extract node and the table while in flight.
  The constants add up to 33.0 s; the measured 33.70 s includes the round resets and the step bodies on the recorder's virtual clock.
- Deviation from C3: the frame headers of the orders style, "reconciliation graph" ("grafo de conciliación") above the main graph and "supplier" ("proveedor") above the Supplier panel besides its name, are not in the C3 list; they come from the orders animation style the owner asked for and appear in the owner-approved Lavish mockup, and they are flagged to the owner in the delivery report.
  The person label comes from the owner-approved claim step "Approve (person)" (Lavish option 1), not from the C3 option text.
- C4: the Spanish page differs from the English one in its strings block, charset, title and number format (4,20, 420,00, -25,00 €); the frames show Conciliación, Albarán, ¿Cuadra?, sí, Reclamación, Calcular diferencia, Diferencia -25,00 € and «Solicitamos un abono» legible in one font, and the table and card text inside their boxes at 960x540 (`sheet-reconcile-flow-es-*.png`, final frame scaled to 960x540).
- C5: the folder README describes the new claim steps and the length of about 36 s, and the README alt text adds the email, the approval and the send; the post 3 sentence (two rounds, -25,00 €, se pide un abono al proveedor) is still true and unchanged; `git grep reconcile-flow` shows no other reference to update.
- C6: `npm run check` on `4af9463` exits 0 with 1288 tests passed (`.evidence/cambio-017/check-017-round5.txt`).
- Builder assumptions (not decided by the owner): the document nodes turn teal one after another over 1.6 s and are never amber-active; red is used only in table cells and the card difference line; both end nodes are labelled End (Fin); compute difference 2.4 s, draft 2.0 s, approve 2.0 s, send 3.0 s, end 1.6 s; in round 2 the Pedido (Order) row cells for quantity and total stay uncoloured, as they are not compared against the delivery note.

Build notes and evidence of the redesign, superseded by the orders-style rebuild (frames and contact sheets in `.evidence/cambio-017/redesign/`, git-ignored):
- C1: both GIFs are 960x540 with the NETSCAPE loop block written by `-loop 0`, 1.28 MB (EN) and 1.28 MB (ES); both MP4s are 1280x720, h264, yuv420p, 0.54 MB (EN) and 0.55 MB (ES); 460 frames each (`probe.txt`, recording of `8629aa1`).
- C2: 460 frames at 15 fps = 30.67 s, under 34 s; the story (round 1 plus round 2) lasts 28.65 s by the recorder clock (`lastStoryMs` = 28650 in `record-log.txt`, both languages), followed by the 2 s hold on the final state; the contact sheets show round 1 ending in register and end, and round 2 going through compute difference and ask for credit into the same end node.
- C3: timeline constants `T` in both pages: cards 1.6 s, extract 3.0 s, compare 2.8 s (round 1) and 3.4 s (round 2), decision 1.6 s, register 1.8 s, compute difference 2.4 s, ask for credit 2.0 s, end 1.6 s, final hold 2.0 s; the incoming edge pulses during the first 0.45 s of each node's hold, so one node is active at a time.
  The exception is extract, which has no single incoming edge: its three card edges pulse at 0, 0.7 and 1.4 s of its hold.
  On-screen text is limited to the round tag, the three document names, node names, column headers, figures, yes and no branch labels, the claim frame name and check and cross marks.
- C4: the Spanish page differs from the English one only in its strings block, charset and title, with Spanish number format (4,20, 420,00, -25,00 €); the final frame scaled to 960x540 shows Albarán, ¿Cuadra?, Sí and Reclamación legible and nothing overflowing its box.
- C5: `git grep reconcile-flow` shows the README (new alt text and description), the folder README and the LinkedIn plan (post 3 sentence reworded to the two rounds) pointing to the replaced files.
- Deviation from C3: besides the literal C3 list, the screen shows the column headers (Quantity, Price, Total), the yes and no branch labels, the claim frame name and the EUR unit (the euro sign in Spanish).
  They come from the owner-approved Lavish script and mockup (a table of quantity, price and total, yes and no branches, a framed claim subgraph); C3 itself is not reworded, and the deviation is flagged to the owner in the delivery report.
  Closed on 2026-10-17: the owner reworded C3 to allow these elements (see Decisions).
- Story length: the timeline constants add up to 28.0 s but the measured story is 28.65 s.
  The round 1 compare body (13 sequential waits, about 3.07 s) outlasts its 2.8 s hold by about 0.27 s; the rest is one extra 60 Hz tick per step-level wait on the recorder's virtual clock (review round 2, timeline simulated with the same rAF and tween semantics).
  C3 still holds: the steps stay sequential and every hold is at least 1.6 s.
  The progress bar now uses the measured length (`LOOP` = 28650 ms in both pages, review round 1), so it fills exactly when the story ends; the before and after MP4s match above the bar (PSNR about 65 dB, encoder noise) and differ only in the bar strip.

Build notes and evidence of the first version, superseded by the redesign (frames, contact sheets and probes in `.evidence/cambio-017/`, git-ignored):
- C1: both GIFs are 960x540, loop count 0, 1.48 MB (EN) and 1.50 MB (ES, re-recorded after review round 1); both MP4s are 1280x720, h264, yuv420p, 499 frames.
- C2: 499 frames at 15 fps = 33.27 s (story 31.25 s, `LOOP` = 31250 ms); exactly two reconciliations, then the claim scene.
- C3: node holds logged by the browser during recording (`scene-times-en.txt`), case 1 read 1.43, extract 2.23, link 1.42, match 2.62, store 1.43 s; case 2 read 0.92, extract 1.64, link 0.94, match 3.62, store 1.41 s.
  Measured on the delivered GIF, the caption of each node stays on screen at least 1.6 s for every case 1 node and 1.07 s for case 2 read and link (re-checked on the case 2 contact sheet in review round 2); the fully lit node is shorter (about 0.8 s for case 2 read) because of the 0.3 s fill transition.
  The token is shown only between nodes and hides on arrival.
- C4: the Spanish page differs from the English one only in its strings block, charset and title; contact sheet and accent crops show no overflow.
- Deviations from the approved table: from case 1 on, scenes start about 1 s later (token travel and case title cards; node holds are exact).
  The Spanish `reconciliations` chip says "discrepancias" to fit the chip; the stamp says "CON DISCREPANCIAS".
  The long Spanish closing caption shrinks itself to about 17 px to fit the strip.
- C5: `git grep reconcile-flow` shows the README, the folder README and the LinkedIn plan pointing to the replaced files; changes 011 and 016 keep their historical figures.

## Decisions
Owner answers of 2026-10-17 in the Lavish page `.lavish/cambio-017-correo.html` (git-ignored):
- **Claim steps:** Compute difference (-25.00 EUR chip), Draft email, Approve (person avatar labelled person), Send, End, inside the Claim subgraph.
- **Email:** on Send an envelope flies to a Supplier panel and opens a card with three lines: "Invoice 40 units · Delivery note 35 units", "Difference -25.00 EUR" in red and "Please send a credit note".
- **C2:** maximum length raised from 34 s to 40 s.
- **C3:** reworded to also allow the email card lines, the Supplier label, the person label and the extras flagged earlier (column headers, yes and no labels, Claim frame, currency unit), which closes the labelled deviation from C3.
- **Style and table:** the pages use the visual style of the orders animation (`purchase-cycle-flow.html`) and keep the comparison table.
- Kept: one node active at a time, each step held at least 1.5 s, no document IDs, no captions.

Owner answers of 2026-10-17 in the Lavish redesign page (all as recommended; the length and the claim steps are superseded by the answers above):
- **What fails in round 2:** fewer units arrived than were invoiced: order 40 x 5.00 = 200.00, delivery note 35 x 5.00 = 175.00, invoice 40 x 5.00 = 200.00; the invoice quantity and total turn red against the delivery note and the claim asks for a -25.00 EUR credit.
- **Claim:** a framed subgraph inside the same graph, grey until round 2, wired to the same end node.
- **Languages:** English and Spanish, same file names as now.
- **Length:** about 30 seconds, about 2 seconds per step.

Note on the Spanish wording: the Lavish script the owner approved labels the purchase order "Pedido" (the owner's own wording, and post 3 also says "pedido"), so this animation uses "Pedido" instead of the "orden de compra" of change 016; flagged to the owner in the delivery report.

Redesign script (conceptual, superseded by the orders-style rebuild):
| Time | Scene |
|---|---|
| 0-12 | Round 1: the order, delivery note and invoice cards light up; extract pulls quantity, price and total of each into the compare table (100 x 4.20 = 420.00 for all three); every cell turns green, the yes branch and the register node light up; the claim subgraph stays grey |
| 12-28 | Round 2: order 40 x 5.00 = 200.00, delivery note 35 x 5.00 = 175.00, invoice 40 x 5.00 = 200.00; the invoice quantity and total turn red, the no branch lights up, the claim subgraph runs: compute difference (-25.00 EUR), ask the supplier for a credit; end |
| 28-30 | Hold on the final state, then loop |

Owner answers of 2026-10-16 for the first version (superseded by the redesign where they conflict):
Owner answers of 2026-10-16 in the Lavish script page:
- **Script:** approved as shown; it is the timeline below.
- **Replace, not add:** the new animation replaces the current one under the same file names (`reconcile-flow.html`, `reconcile-flow-es.html` and their GIF and MP4), so the README and the LinkedIn plan keep pointing to the right files and only one version is maintained; the old one stays in Git history.
  The three defects of the English page recorded in change 016 disappear with it, so the proposed task to fix them was withdrawn.
- **Mismatch case:** INV-0008 (`inv-003-gauze.xlsx`, PO-0003): 35 gauzes invoiced when 40 were ordered and delivered, a quantity difference of -25.00 EUR. It reads at a glance and post 3 already quotes it.
- **What happens next:** a closing scene shows the claim graph of phase 09 in small (draft, review by a person, send).
- **Post 3 text:** the sentence that describes the video is reworded to the two cases.

First version timeline, superseded (seconds, about 30 s of story plus the 2 s recording tail):
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

Conceptual simplifications (as in change 011, the flow is conceptual): the delivery notes do not ride the graph, they are shown as already stored; only two of the demo reconciliations appear; in the first version the claim step was a separate graph shown as a summary (since the redesign it is a subgraph of the same graph).

Owner decisions of 2026-10-17 on the orders-style delivery (Lavish page, the recommended option in all five, no notes):
- The frame headers "reconciliation graph" and "supplier" stay; the C3 deviation is accepted.
- The supplier card keeps -25.00 EUR (-25,00 € in Spanish), the approved figure quoted by post 3.
- The Order row stays uncoloured in round 2: colour marks only what is compared.
- The Spanish labels are approved as they are.
- The post 3 sentence in `docs/linkedin/series-plan.md` stays unchanged.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | Local, two subagents in clean context | `b42d833..aae58ae` | Correctness; evidence | Correctness: 3 menor (Spanish captions said `pedido` next to the `orden de compra` header, against change 016; `store` timers not tied to `runId`, so a restart could light the discrepancy rows of the previous pass; missing comma in the folder README). Evidence: 1 menor (C3 holds quoted from the browser log without saying so) | All confirmed and fixed: `orden de compra` in every Spanish caption (the participle "pedido 40, facturado 35" stays), intro title shortened to fit, timers guarded by `runId` in both pages, comma added, C3 provenance stated; Spanish page re-recorded and frames checked (English recording unchanged, the timer guard does not change it) |
| 2 | Local, one subagent in clean context | `aae58ae..f0d6f41` (fix commit) | Correctness | None blocking; 1 menor: the C3 caption hold measured on the GIF was not re-checked by the reviewer, and its sentence mixed "in case 1" and "for case 2" | Confirmed as wording only: the case 2 contact sheet shows the `read` and `link` captions on screen about 1.07 s, sentence reworded |
| 3 (redesign 1) | Local, two subagents in clean context | `f0d6f41..b05adac` | Correctness; evidence | None blocking; correctness: 2 menor (Spanish page says "Pedido" instead of change 016's "orden de compra"; progress bar `LOOP` = 28000 fills 0.65 s before the 28.65 s story ends). Evidence: 5 menor (stale "Spanish captions" pending line; wrong cause for the 0.65 s gap; edge-pulse statement false for extract; on-screen text beyond the C3 list not labelled as a deviation; no saved probe or recorder log) | All confirmed: `LOOP` = 28650 in both pages and both languages re-recorded (`8629aa1`); probe and recorder log saved; build notes, pending line, extract exception and a labelled C3 deviation fixed (`5042562`); "Pedido" kept because the owner-approved script uses it, recorded under Decisions and flagged to the owner |
| 4 (redesign 2) | Local, one subagent in clean context | `b05adac..5042562` (fix commits) | Correctness; evidence | None blocking; 2 menor: the build notes said no step body outlasts its hold, but the round 1 compare body lasts about 3.07 s against 2.8 s; the redesign rounds were not yet in this table | Both confirmed and fixed in this document: story length note reworded, these rows added; bar, media, probe and figures verified without defects |
| 5 (orders style) | Local, four subagents in clean context (two from the session before an automatic hand-over, two after) | `b13ac2a..6fdbbc7` | Correctness; evidence | None blocking. Correctness: the Spanish `no` edge ran through the end of the label RECLAMACIÓN (C4); Spanish branch labels capitalised (Sí/No) against English yes/no; the Send-to-Supplier edge and the `no` edge stayed at full opacity in round 1 while the claim subgraph was dimmed; figure tokens covered the document labels while flying; "uds" without its period; the frame headers (reconciliation graph, supplier) are outside the C3 list; the card shows -25.00 where the real claim email states 25.00 EUR in dispute; the Order row stays uncoloured in round 2. Evidence: the redesign script table and the conceptual simplifications line not marked superseded; the goal did not describe the orders-style rebuild; plan items out of order; the two-amber fade not judged against C3; the 1.6 s shortest hold ignored the 0.53 s document cadence; the person label attributed to the C3 option | Fixed: `no` edge moved to x=200, Spanish labels lowercase, `no` edge, its label and `se-sup` moved into the claim group, tokens start at the node edge, "uds.", both languages re-recorded (536 frames, story 33.70 s, `LOOP` unchanged) and re-encoded; document wording fixed and the fade judged acceptable (0.3 s CSS transition, same as the orders animation). Labelled as a C3 deviation for the owner: the frame headers. Left to the owner: the -25.00 sign (owner-approved figure, quoted by post 3) and the uncoloured Order row (builder assumption) |

## Delivery
Branch `cambio-017-simple-reconcile-animation`, PR to `main` (see the PR description for results per criterion).
Pending: none for this change; the owner decided the five open points on 2026-10-17 (see Decisions).
Follow-up outside this change: `docs/linkedin/series-plan.md:18` points post 3 to the English MP4 although the post 3 video is the Spanish one (change 015); the post 3 questions inherited from change 015 are also outside this change.
