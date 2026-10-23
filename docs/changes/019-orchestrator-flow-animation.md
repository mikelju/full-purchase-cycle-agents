# 019 - Orchestrator flow animation

Status: ready locally (commit on the work branch; adversarial review, check, push and PR pending)

## Contract
Goal and reason:
The three flow animations show one module each: orders (change 005), quotes (change 008) and reconciliation (changes 011 and 017), each with a Spanish copy (change 016).
Change 011 already named the general orchestration as the fourth of the series.
This change adds that fourth animation: the `purchase_cycle` graph (`src/purchase_cycle/cycle.py`, first graph of `docs/graphs.md`) running its five stages in order (`orders`, `quotes`, `reconcile`, `receipts`, `sales`) over the shared database, in passes, with the decisions a person takes between passes.
The README "Architecture" section shows it instead of the orders GIF, and the README links the four animations in English and Spanish.
Out of scope:
The existing six pages, GIFs and MP4s and `record.py` (unless a recorder bug blocks the capture, which is then logged under Decisions); code, tests other than `tests/test_readme.py`, datasets, evaluations and recordings; `docs/plans/*`, `docs/linkedin/series-plan.md` and any HANDOFF file; LinkedIn post wording.
Constraints / decisions that need the owner:
No new dependency or cost: Playwright and ffmpeg are used from outside the project, as in changes 008, 011, 016 and 017; the cycle data come from a replay run of `cycle-demo`, with no model call.
The scene script below is the planner's proposal; see Open decisions.
Current authorization and limits:
Owner request relayed on 2026-10-23 by the intermediary session: build the change on branch `claude/orchestrator-video-892c94`, push it and open a PR to `main`; never merge.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `docs/assets/orchestrator-flow.gif` and `docs/assets/orchestrator-flow-es.gif` are 960x540, loop forever and are under 4 MB each; `docs/assets/orchestrator-flow.mp4` and `docs/assets/orchestrator-flow-es.mp4` are 1280x720, H.264, yuv420p; the recording lasts at most 45 seconds (the `record.py` cap) | `ffprobe` output and file sizes |
| C2 | The sources `docs/assets/flow-animation/orchestrator-flow.html` and `orchestrator-flow-es.html` use the visual language of `reconcile-flow.html` (same `:root` tokens, fonts, node states idle, active amber, done teal, frame headers, progress bar, `#clean` mode, `window.lastStoryMs`) and differ from each other only in the strings block, the charset meta and the `<title>`; the Spanish page follows the translation rules of change 016 | `diff` of the two pages; the pages themselves |
| C3 | The graph on screen has exactly the nodes and edges of the real orchestrator: start, `orders`, `quotes`, `reconcile`, `receipts`, `sales`, end, in that order, with one database panel all stages read and write; in every pass shown, the five stages light up one at a time in that order, and the orchestrator never takes a decision (each decision is shown as a person acting between passes) | Extracted frames; `docs/graphs.md` first graph |
| C4 | Every figure, status and reference on screen comes from the replay run of `cycle-demo` (`.evidence/cambio-019/cycle-demo.txt`, git-ignored) and matches it | That file next to the page strings block |
| C5 | No frame of the four media shows an email address, a file system path, a user name, a key or token, or a thread id; the strings blocks hold none either | Leak grep over both pages and sampled frames of every GIF and MP4 (list in Evidence) |
| C6 | Accents and ñ in the Spanish frames render legibly in the page font and no text overflows its box in either language | Sampled frames and one zoomed crop |
| C7 | `README.md` "Architecture" embeds `docs/assets/orchestrator-flow.gif` instead of `docs/assets/purchase-cycle-flow.gif`; the README has a short list linking the four animations, one line each, with the English and the Spanish GIF and MP4; the README stays at most 250 lines with its ten `##` headings unchanged | README diff; `tests/test_readme.py` |
| C8 | `tests/test_readme.py` checks the orchestrator GIF in "Architecture" and that the eight GIF and MP4 files of the four animations are linked; it passes | Test run output |
| C9 | The folder README `docs/assets/flow-animation/README.md` lists the new pages, their `record.py` commands and the ffmpeg substitution | That file |
| C10 | `npm run check` exits 0 and CI is green on the PR | Check log outside the repository; PR checks |

## Plan
Executor notes for every step: run `unset ANTHROPIC_API_KEY LANGSMITH_API_KEY` in the same Bash call before any command; one git command per Bash call, no `git -C`, no `cd ... &&` chains; never open `.env`; keep evidence in `.evidence/cambio-019/` (git-ignored, check with `git check-ignore .evidence/cambio-019/x`).

- [x] 1. Data.
  Run the replay demo into a folder outside the repository and keep its output:
  `uv run purchase-cycle cycle-demo --workdir <scratchpad>/cycle-demo > .evidence/cambio-019/cycle-demo.txt 2>&1`, exit 0, about 25 s, no key and no network.
  The planner ran it on 2026-10-23 (exit 0); the figures the script below uses are from that run: 14 steps, passes `cycle-run-0001` to `0007`, the summary lines `order 1 (A): served, invoice matched`, `order 2 (C): served, invoice awaiting_delivery_note`, `order 3 (D): received, not invoiced`, `order 4 (B): served, invoice discrepancies, credit note sent`, `supplier claims: 1 rejected, 1 sent`, `quote requests waiting: QR-0003 GLV-NIT-XS x1`.
  The output holds example email addresses and absolute paths: never copy those lines into a page.
- [x] 2. English page `docs/assets/flow-animation/orchestrator-flow.html`; C2, C3, C4.
  Copy `reconcile-flow.html` as the skeleton (style block, `S` strings block, `T` timeline constants, `step` helper, progress bar with `LOOP`, `window.lastStoryMs`, `#clean`) and replace the SVG and the story.
  Layout at 1280x720: frame "orchestrator graph" with a vertical or left-to-right chain start, `orders`, `quotes`, `reconcile`, `receipts`, `sales`, end (labels exactly as the node names in `cycle.py`), each stage with a one-line counts chip on the side; frame "database" (`business.db`) with chips for orders, quote requests, purchase orders, reconciliations and claims, customer invoices; a small "person" lane for the decisions between passes; a pass tag (`pass 1` to `pass 7`).
  Script (proposal, about 38 s of story plus the 2 s recorder tail):
  | Scene | What lights up | Chips (from the replay run) |
  |---|---|---|
  | Pass 1 | the five stages in order, each held about 1 s | orders: 4 items, 3 stored, 1 paused; quotes: 2 started, 2 waiting; reconcile: nothing new; receipts: 1 served, 1 issued; sales: 1 invoiced, 1 reconciled |
  | Person | clarification answered (`clarify answer`) | order 4 stored |
  | Passes 2 and 3 | the five stages fast (about 0.4 s each), one tag shows the clock moved 6 days | quotes: 2 replies accepted, 2 decided |
  | Person | `quotes approve QR-0001`, `QR-0002` | PO-0001, PO-0002 |
  | Pass 4 | the five stages in order | reconcile: 4 documents, 2 claims drafted; receipts: 2 received, 1 served, 1 issued; sales: 1 invoiced, 1 reconciled |
  | Person | `reconcile approve INV-0002`, `reconcile reject INV-0004` | claims: 1 sent, 1 rejected |
  | Passes 5 to 7 | the five stages fast | sales: 2 signed notes, 2 reconciled, 1 corrections, 1 responses drafted; person `sales approve SINV-0002` before pass 7 |
  | Final | hold | four order lines: A served, matched; B served, discrepancies, credit note sent; C served, awaiting delivery note; D received, not invoiced |
  Rules: one node active at a time; each fully lit stage hold at least 0.4 s in fast passes and at least 0.9 s in passes 1 and 4; no captions besides node names, chips, the pass tag, the person lane label and the frame headers; no email address, path, thread id or file name of the run output.
  Set `LOOP` to the story length measured by `record.py` (`story ms` line) after the first capture and re-record once.
- [x] 3. Spanish page `orchestrator-flow-es.html`; C2, C6.
  Copy the English page, add `<meta charset="utf-8">` as the first line, translate the `<title>` and only the `S` block, with the rules of change 016: node names (`orders`, `quotes`, `reconcile`, `receipts`, `sales`) stay as code identifiers because they are the graph node names; statuses with an underscore and references (QR-0001, PO-0001, INV-0002, SINV-0002) stay; plain words are translated (pass to `pasada`, person to `persona`, database to `base de datos`, served to `servido`, delivery note to `albarán`, credit note to `abono`); Spanish number format if any decimal appears.
  Check: `diff orchestrator-flow.html orchestrator-flow-es.html` shows only the charset, title and strings block.
- [x] 4. Leak grep on the sources; C5.
  `grep -n -E "@|[A-Za-z]:[\\/]|/Users/|mikel|sk-ant|lsv2|thread|cycle-run-|\.eml|example\.com" docs/assets/flow-animation/orchestrator-flow*.html` must print nothing except the Google Fonts `@` free lines (there should be none; CSS has no `@` in these pages unless `@media`/`@keyframes` is added, which is then listed by hand as harmless).
- [x] 5. Capture and encode; C1.
  `uv run --with playwright python docs/assets/flow-animation/record.py orchestrator-flow.html > .evidence/cambio-019/record-en.log 2>&1` and the same with `orchestrator-flow-es.html` (`record-es.log`); needs Chrome and network for the fonts; frames land in `docs/assets/flow-animation/frames/<page>/` (git-ignored).
  The ffmpeg on PATH may lack `palettegen`; use the full build under WinGet (`Gyan.FFmpeg`, found with `ls "$LOCALAPPDATA/Microsoft/WinGet/Packages/" | grep -i ffmpeg`) as in change 011.
  From the repository root, with `F=docs/assets/flow-animation/frames/orchestrator-flow` (then `orchestrator-flow-es`), run the three commands of the folder README with `quotes-flow` replaced by `orchestrator-flow` (or `orchestrator-flow-es`).
  Probe: `ffprobe -v error -show_entries stream=codec_name,width,height,pix_fmt,nb_frames -show_entries format=duration,size <file>` for the four files into `.evidence/cambio-019/ffprobe.txt`; GIF loop block: `grep -c NETSCAPE2.0 <gif>` gives 1.
- [x] 6. Frame inspection; C3, C5, C6.
  Extract one frame every 2 s from each of the four files: `ffmpeg -v error -i docs/assets/orchestrator-flow.gif -vf "select=not(mod(n\,30))" -vsync vfr .evidence/cambio-019/frames-en-gif/f%03d.png`, and the same for the `-es` GIF and both MP4s (folders `frames-es-gif`, `frames-en-mp4`, `frames-es-mp4`).
  Read every extracted PNG (about 20 per file) and check: the active stage order in each pass, the chips against `cycle-demo.txt`, no email, path, user name, key or thread id, no overflow; zoom one Spanish frame with accents (`ffmpeg -i <png> -vf "crop=480:270:X:Y,scale=960:540" zoom-es.png`).
  Record the frame numbers checked and what each shows in Evidence.
- [x] 7. READMEs and test; C7, C8, C9.
  `README.md` line 107: replace `![Purchase cycle flow](docs/assets/purchase-cycle-flow.gif)` with `![Orchestrator flow: five stages in order over one database](docs/assets/orchestrator-flow.gif)`; keep the sentence above it.
  Replace line 136 ("Material in Spanish: ...") with a short block, no new `##` heading, keeping the exact words "Material in Spanish" for the guides:
  ```
  Flow animations (English and Spanish):
  - Orchestrator: [GIF](docs/assets/orchestrator-flow.gif), [MP4](docs/assets/orchestrator-flow.mp4); Spanish [GIF](docs/assets/orchestrator-flow-es.gif), [MP4](docs/assets/orchestrator-flow-es.mp4).
  - Customer orders: same four links for `purchase-cycle-flow`.
  - Supplier quotes: same four links for `quotes-flow`.
  - Invoice reconciliation: same four links for `reconcile-flow`.

  Material in Spanish: the guides [cycle-demo-visual-es.html](docs/assets/cycle-demo-visual-es.html) and [trace-dashboard-guide-es.html](docs/assets/trace-dashboard-guide-es.html).
  ```
  The README grows by about 6 lines (166 to about 172, limit 250).
  In `tests/test_readme.py`, in `test_readme_leads_with_evaluation_discloses_and_links_spanish_material` or a new test, assert that `"docs/assets/orchestrator-flow.gif"` is in the Architecture section (between `## Architecture` and `## How it is built`) and that `docs/assets/{name}{suffix}.{ext}` is in the README for the four names, both suffixes and both extensions; the existing link test already proves the files are versioned, so `git add` the media before running it.
  Folder README: add the new pages to the lists, the two `record.py` lines and the ffmpeg substitution, and name this change next to 005, 008, 011 and 017 in the conceptual note.
  Run `uv run pytest -q tests/test_readme.py`; it must pass.
- [ ] 8. Commit on the work branch (no co-author line), then one `npm run check` in the background with its log in `.evidence/cambio-019/check.txt`; exit 0; C10.
- [ ] 9. Adversarial review per `.agents/skills/sdd-review/SKILL.md` (lenses: correctness of the content against `cycle.py` and the run output; evidence and leaks), fix, re-record if a page changes, fill Evidence and the review table.
- [ ] 10. Push the branch, open the PR to `main` with results per criterion and evidence, wait for CI green; never merge.

## Open decisions
- Scene script: the table in step 2 condenses the 14 demo steps to 7 passes and 4 person scenes. Previous animations had their script approved in a Lavish page before recording (changes 011 and 017). Recommendation: show the script and a mockup frame to the owner through a Lavish page before step 5, unless the intermediary confirms the owner approved the content as stated in the request.
- Existing README reference: the orders GIF leaves "Architecture" but stays linked in the new animation list; no other file references it for the README (`git grep purchase-cycle-flow` to confirm before step 7).
- Node labels in Spanish: kept as the code node names (`orders`, ...) because the graph is generated from code; the alternative is translated labels as in change 016 (`pedidos`, `presupuestos`, ...). Recommendation: keep the code names, translate everything else.

## Decisions
- Scene script: approved for execution by the coordinator (intermediary session); no Lavish page. The owner reviews the video in the PR.
- Node labels in Spanish: the graph node names (`orders`, `quotes`, `reconcile`, `receipts`, `sales`) stay as code identifiers; everything else is translated.
- README: the animation list sits inside "Architecture" with no new `##` heading; the orders GIF leaves the section but stays linked in the list; "Material in Spanish" now names the two guides only.
- Pages harmless leak-grep hits: CSS `@keyframes` and `@media`, the SVG namespace URL and the Google Fonts links.

## Evidence
Local evidence in `.evidence/cambio-019/` (git-ignored).
- C1: `ffprobe.txt`: GIFs 960x540, 612 frames, 40.8 s, 1.96 MB (EN) and 2.03 MB (ES); MP4s 1280x720 h264 yuv420p, 40.8 s (story 38.7 s, under 45 s); `grep -c NETSCAPE2.0` gives 1 for both GIFs.
- C2: `diff` of the two pages differs only in charset meta, title and strings block (39 changed lines).
- C3, C6: `frames.md`: 21 sampled frames per file (one every 2 s), all viewed; stage order and no overflow; no ñ appears in the strings; accents í checked (`zoom-es.png`).
- C4: chips and figures compared with `cycle-demo.txt` (passes 1-7, final four order lines) in `frames.md`.
- C5: leak grep over both pages (only harmless CSS at-rules, SVG namespace and font links) and every sampled frame (see `frames.md`).
- C7: `README.md` 172 lines, ten `##` headings; Architecture embeds `orchestrator-flow.gif` and lists the 16 media links (all paths exist).
- C8: `uv run pytest -q tests/test_readme.py`: 8 passed.
- C9: `docs/assets/flow-animation/README.md` lists the new pages, record commands and ffmpeg substitution.
- C10: pending (`npm run check` and PR CI after review, steps 8-10).

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | local | HEAD~1..HEAD (22847a9) | content correctness; evidence and leaks | 11 (nits included): ES chips wording (entradas, guardados), ES pass 6 singulars, folder README conceptual sentence, evidence wording, database rows (orders, customer invoices), layout comment, dead code, LOOP comment, clock tag, README test, plan ticks | all 11 fixed; pages re-recorded, media re-encoded and re-inspected |

## Delivery
To fill at delivery.
