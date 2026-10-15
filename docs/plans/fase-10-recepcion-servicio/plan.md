# Phase 10 - Goods receipt and serving: plan and results

Status: in progress
Spec: `spec.md` (approved 2026-10-15, frozen)
Base: branch `fase-10-recepcion-servicio` at `06e4397` (phase 09 merged into `main` through PR #20); the unchanged-behaviour reference of C14 is the code at `06e4397`.

## Design notes
Written for the owner decisions D1 to D9 (option A) of 2026-10-15 recorded in the spec.
- New module `purchase_cycle/receipts.py` holds the step: candidates, receiving, the shortfall helper, serving, the customer delivery note, the `receipt` graph, and the `run_receipts`, `list_receipts` and `show_delivery_note` functions used by the CLI, the evaluation and the demo.
  It imports no `llm` symbol, and a test asserts this.
- New module `purchase_cycle/receipts_cli.py` with `add_receipt_commands(sub)` and the `receipts` handlers, registered from `cli.main` the same way `evaluation.cli.add_eval_commands` is, so `cli.py` only gets the registration line and the `receipt-demo` handler.
- Tables in `db.SCHEMA`, all `CREATE TABLE IF NOT EXISTS` (C2):
  - `goods_receipts`: `id`, `delivery_note_id` unique not null references `supplier_documents`, `purchase_order_id` not null references `purchase_orders`, `received_at` not null, `status` checked in (`received`, `failed`).
  - `goods_receipt_lines`: `receipt_id` references `goods_receipts`, `line_no`, `sku` references `products`, `quantity` integer checked `> 0`, primary key (`receipt_id`, `line_no`).
  - `customer_delivery_notes`: `id`, `order_id` unique not null references `orders`, `customer_code` not null references `customers`, `issued_at` not null, `document_file` not null.
  - `customer_delivery_note_lines`: `note_id` references `customer_delivery_notes`, `line_no`, `sku`, `quantity` integer checked `> 0`, `sale_unit` not null, primary key (`note_id`, `line_no`).
  - `db.TABLES` and `row_counts` stay as they are, so the seed counts do not change.
- Seed and stock: `db.seed` writes `stock` with `INSERT OR REPLACE` today, and every CLI command reseeds, so a `reconcile scan` after `receipts run` would reset `on_hand`.
  Increment 2 changes the stock insert to `INSERT OR IGNORE`: a fresh database gets the same rows as before, a second seed gives the same rows, and a seed on a used database keeps the received and served stock.
  This is a phase 01 code change (DV-C); `tests/test_seed.py` must pass unchanged, and if any earlier test relies on a reseed resetting stock, execution stops and a deviation is recorded.
- Candidates (D1, C3): delivery notes (`kind = 'delivery_note'`, `status = 'linked'`) referenced by `reconciliations.delivery_note_id` with result `matched` or `discrepancies`, with no `goods_receipts` row, ordered by document id.
  The claim table is not read.
  An unlinked document, a note with no invoice, an invoice `awaiting_delivery_note` and a note already received (or already failed) are not candidates.
  A note that a later scan links to an invoice becomes a candidate through the same query on the next run.
- Receiving (D2, C4): one transaction per note.
  Every line must have a catalog SKU (present in `products`) and a whole positive quantity (`quantity == int(quantity) > 0`).
  If so, the note gets a `goods_receipts` row `received` with `received_at` from `sim_clock.now`, one `goods_receipt_lines` row per line, and `stock.on_hand += quantity` per line, all in one transaction; short, exact and over-deliveries and SKUs not on the purchase order are all received as delivered.
  If not, the note gets a `goods_receipts` row `failed` with no lines and no stock change, and `db.park_failure` writes one `failures` row with thread `goods-receipt-DOC-NNNN`, channel `receipts`, step `goods_receipt`, status `needs_review` and the reason; the other notes go on.
  A failed note is not retried by later runs; the README lists this as a limitation.
- Shortfalls (D3, C5): `quotes.shortfalls` keeps its shape; the `requested` subquery counts only the quote requests whose purchase order has no `goods_receipts` row with status `received`.
  With no receipts the subquery returns the same sum as before, so the phase 06 rows are unchanged; after a receipt, the units of that request leave `requested` and the received units are in `on_hand`, so a short delivery leaves its remainder as a shortfall.
- Serving (D4, D5, C6): `received` orders by ascending id; an order is served only when `on_hand` covers every line (lines of the same SKU summed); a served order, in one transaction, decrements `on_hand`, sets `orders.status = 'served'` only if it is still `received`, and inserts its `customer_delivery_notes` row and lines (sale unit from `products`) with `issued_at` from `sim_clock.now` and `document_file` set to the planned path.
  A skipped order keeps `received` and its missing units per SKU are returned for the output; it does not stop later orders.
  The stock `CHECK (on_hand >= 0)` stays as a last guard.
- Customer delivery note file (D7, C7): `CDN-NNNN.txt` from the row id, plain ASCII, written by `issue` for every row whose file is missing, with our company name (one constant `COMPANY` in `receipts.py`), the customer name, code and city, the order number `ORD-NNNN`, the issue date, one line per SKU with product name, quantity and sale unit, and a blank signature line; no prices.
  Default folder `config.CUSTOMER_DELIVERY_NOTES_DIR = DATA_DIR / "customer_delivery_notes"`; every command takes `--notes-dir`.
  Non-ASCII catalog text is transliterated or replaced by a fixed rule, tested on the full catalog and customer list.
- Graph `receipt` (C8): `build_receipt_graph(db_path, notes_dir, checkpointer=None, interrupt_after=None)` with nodes `receive`, `serve`, `issue`, edges in that order, no interrupt in normal use, compiled with `name="receipt"`.
  Every node reads the database before acting, so a re-run adds no stock, serves no order twice and writes no second file; the state holds only what is printed (receipts, served orders, skipped orders with missing units, issued files).
  Thread ids `receipt-run-NNNN`; `run_receipts` looks up the highest existing receipt thread in the checkpointer and, when its state still has a next node, resumes it with `invoke(None)` before starting a new thread; otherwise it starts the next number.
  The crash of C8 is injected with `interrupt_after=["receive"]` or `["serve"]`, as phase 05 does with `graph.build_graph`.
- Commands (D9, C10), group `receipts`, each with `--checkpoints` where it runs the graph and `--notes-dir`:
  - `receipts run` prints each receipt (`GR-NNNN`, delivery note number, `PO-NNNN`, lines), each failed note with its reason, each served order with its `CDN-NNNN`, each skipped order with its missing units, and the stock before and after for the SKUs touched; a run with nothing new prints that nothing changed.
  - `receipts list` prints each receipt with its purchase order and lines and each customer delivery note with its order and customer.
  - `receipts show CDN-NNNN` prints the file text; an unknown or malformed reference exits 1 with one message on stderr and changes nothing.
- Evaluation `goods_receipt` (D8, C11): module `evaluation/goods_receipt_eval.py`, scenarios `evals/datasets/goods_receipt/GR-NNN.json`, modelled on `claim_approval_eval.py`: scenario setup through SQL inserts of extracted rows (no model), steps run through the `receipts` functions or `python -m purchase_cycle.cli` for process steps, a `scan` step that inserts a later delivery note and its reconciliation, a `quotes_scan` step through `quotes.shortfalls`, a `crash` step through `interrupt_after`, a deterministic grader and a critical-error oracle written apart from `receipts.py`.
  Reuses `email_eval._ci`, `_pct`, `recovery_eval._rate`, `stats.target_cells` and `zero_event_note`; target 1.0 and 0 critical errors; a model client double that fails on any call is installed for every scenario.
- Registration (C1, C11): `goods_receipt` is added last to `SUITES`, left out of `UPLOAD_SUITES`, and inserted in `ALL_SUITES` right before `offer_extraction`.
  That position leaves the tails pinned by `tests/test_claim_eval.py`, `tests/test_document_eval.py`, `tests/test_reconcile_eval.py` and `tests/test_offer_eval.py` unchanged; only `tests/test_whatsapp_eval.py::test_eval_all_runs_the_whatsapp_suite_after_its_baseline`, which lists the whole order, changes, recorded as DV-B.
- Demo (C12): `receipt-demo` seeds a fresh work folder, the purchase orders, and the customer orders of the new `examples/receipts/customer_orders.json` through `db.insert_order` (channel `demo`, status `received`), then runs as subprocesses: `reconcile scan examples/invoices/documents` with `RECONCILE_DEMO_RECORDINGS`, `receipts run`, `reconcile scan examples/invoices/later`, `receipts run`, `receipts list`, and `receipts show` of each note.
  At `06e4397` every sample delivery note delivers the full purchase order quantity (A-5001 100, AL-3002 50, E-0303 40, A-5004 30, AL-3005 60, later E-0306 20), so the reconcile-demo purchase orders give no short delivery and C12 cannot be met as written; see DV-A.
  Seed stock at `06e4397`: GLV-NIT-M 74, MASK-IIR 221, GAUZE-NS-5 54, GEL-500 118, SAL-500 244, THERM-DIG 15, BND-ELA-10 107.
  Proposed customer orders, in id order: CLI-001 GLV-NIT-M 150 (waits for A-5001, served on run 1); CLI-002 MASK-IIR 10 (covered before any receipt, served on run 1); CLI-003 GAUZE-NS-5 120 (never covered, skipped on both runs with its missing units); CLI-004 THERM-DIG 25 (skipped on run 1, served on run 2 after E-0306 arrives with the later scan); CLI-001 GEL-500 20 plus SAL-500 30 (two lines, served on run 1).
  The executor confirms the exact numbers against the seed before writing the file.

## Rules for executors
- Execution starts from the approved frozen spec; changing a criterion needs a deviation approved by the owner.
- Each batch is sized for one Sonnet writer with a context ceiling of about 100k tokens; it starts in a fresh session from this file and Git, implements its increments in order, marks each one `[x]` with its evidence and commits each increment with a standalone `git commit -m "Phase 10: <increment>"` and no co-author line.
- Failing tests first: each increment adds its tests, runs them to see them fail for the expected reason, then implements.
- Targeted checks during a batch (`uv run pytest -q <files>`); the coordinator runs `npm run check` between batches.
- Run checks and tests with `ANTHROPIC_API_KEY` and `LANGSMITH_API_KEY` unset (`unset ANTHROPIC_API_KEY LANGSMITH_API_KEY`); no batch of this phase needs a key.
- The Windows machine can raise subprocess launch errors (exit code 0xC0000142) when several suites or subprocess tests run concurrently; rerun the failing command alone before treating it as a failure, and note the rerun in the evidence.
- Search before reading; read large files by chunks (`cli.py`, `reconciliation.py`, `claim_approval_eval.py` are long); reuse `db.park_failure`, `db.insert_order`, `graph.sqlite_checkpointer`, `sim_clock.now` and `sim_clock.text`, `reconciliation.seed_demo_orders`, `reconciliation.order_reference`, the `cmd_reconcile_demo` step runner pattern, and the `claim_approval_eval` scenario, oracle and gate patterns.
- No new dependencies; no model call anywhere in this phase; the agent never reads `.env`.
- No change to phase 01 to 09 prompts, recordings, datasets, baselines, gates, sample documents, graphs or matching; the only earlier code changes allowed are the stock insert of `db.seed` (increment 2), the `requested` subquery of `quotes.shortfalls` (increment 4), the `cli.main` registration lines and the harness registration; every earlier test edit is a deviation.
- Evidence files go to `.evidence/fase-10/`.

## Increments
Each increment leaves the product working and covers concrete criteria.
This file is the durable state: a new session resumes from here and from Git.

### Batch A - Baseline, schema and receiving
- [x] 1. Unchanged-behaviour baseline before any code change (C14) - check: `orders-demo`, `quotes-demo` and `reconcile-demo` replay output saved as `.evidence/fase-10/orders-demo-before.txt`, `quotes-demo-before.txt` and `reconcile-demo-before.txt`, `npm run eval` output as `eval-before.txt`, and the hashes of the tracked files under `evals/recordings`, `evals/datasets` and `evals/baselines` as `evals-files-before.txt`, all from `06e4397`.
  - Evidence: `.evidence/fase-10/` orders-demo-before.txt (exit=0), quotes-demo-before.txt (exit=0; first attempt was killed with 137 during a sequential loop and rerun alone), reconcile-demo-before.txt (exit=0), eval-before.txt (exit=0), evals-files-before.txt (895 entries); replay with `--workdir` outside the repo, keys unset.
- [x] 2. The four tables and seed behaviour (C2) - files: `src/purchase_cycle/db.py`, `tests/test_seed.py` (new tests only); check: `uv run pytest -q tests/test_seed.py`: the four tables exist and are empty after `seed`; seeding twice gives the same rows in every table; a database built with the phase 09 schema gains the tables on reseed without losing rows; a changed `stock.on_hand` survives a reseed; the status check rejects unknown values; a second receipt for one delivery note and a second delivery note for one order are rejected; the existing seed tests pass unchanged; `tests/test_quotes*.py`, `tests/test_ui.py` and `tests/test_reconcile*.py` still pass.
  - Evidence: `uv run pytest -q tests/test_seed.py` 29 passed (22 existing unchanged, 7 new); `uv run pytest -q tests/test_quotes*.py tests/test_ui.py tests/test_reconcile*.py` 137 passed (394 s); ruff check and format clean; DV-C applied (`INSERT OR IGNORE` for `stock`).
- [x] 3. Candidates and receiving (C3, C4) - files: new `src/purchase_cycle/receipts.py`, new `tests/test_receipts.py`; check: `uv run pytest -q tests/test_receipts.py`: on a temporary database built with SQL inserts, candidates are the linked notes of `matched` and `discrepancies` reconciliations with a claim in each status and with no claim, and none for an invoice `awaiting_delivery_note`, an unlinked document, a note with no invoice or a note already received; a note linked by a later reconciliation becomes a candidate; receiving adds the delivered quantity for a short, exact and over-delivery and for a SKU not on the purchase order; an unknown SKU and a fractional quantity add nothing, write a `failed` receipt and one `failures` row with status `needs_review` and step `goods_receipt`, and the next note is still received; a second call receives nothing; the module imports no `llm` symbol.
  - Evidence: `uv run pytest -q tests/test_receipts.py` 7 passed; ruff check and format clean.
- [x] 4. Shortfalls after a receipt (C5) - files: `src/purchase_cycle/quotes.py` (`shortfalls` only), new tests in `tests/test_receipts.py`; check: `uv run pytest -q tests/test_receipts.py tests/test_quotes.py tests/test_quotes_demo.py`: with no receipts `shortfalls` returns the same rows as the phase 06 query on several fixtures (a copy of the old query in the test is the oracle); after a short delivery with `received` orders still needing the SKU, `quotes.scan` starts a request for exactly the unreceived remainder; after an exact delivery that covers the orders, no request starts; `tests/test_quotes.py` passes unchanged.
  - Evidence: `uv run pytest -q tests/test_receipts.py tests/test_quotes.py tests/test_quotes_demo.py` 17 passed (10 in test_receipts.py; the short-delivery test failed first on the old query); extra `tests/test_quote_request.py tests/test_quote_decision.py` 62 passed; ruff check and format clean.

### Batch B - Serving, delivery note and graph
- [x] 5. Serving pass (C6) - files: `src/purchase_cycle/receipts.py`, `tests/test_receipts.py`; check: `uv run pytest -q tests/test_receipts.py`: orders are taken by ascending id; an order with several lines where one is short is skipped and keeps `received`; a large older order is skipped while a later one is served; an order covered before any receipt is served; served orders get `served` and their quantities leave `on_hand`; stock never goes below zero; the skipped orders report their missing units per SKU; a second pass serves nothing.
  - Evidence: `uv run pytest -q tests/test_receipts.py` 15 passed (5 new; all failed first with no `serve`); ruff check and format clean; `serve(conn)` returns served orders and skipped orders with missing units, the note row is added in increment 6.
- [x] 6. Customer delivery note rows and file (C7) - files: `src/purchase_cycle/receipts.py`, `src/purchase_cycle/config.py` (`CUSTOMER_DELIVERY_NOTES_DIR` only), `tests/test_receipts.py`; check: `uv run pytest -q tests/test_receipts.py`: every served order has one `CDN-NNNN` row with lines equal to its order lines and sale units from the catalog; the file holds the company, customer name, code and city, `ORD-NNNN`, the issue date from the simulated clock, each SKU with name, quantity and sale unit and a signature line, no price and no non-ASCII byte (checked for every catalog product and customer); an existing file is not rewritten.
  - Evidence: `uv run pytest -q tests/test_receipts.py` 20 passed (5 new, failed first for the missing `issue`/`notes_dir`); ruff check and format clean; `serve(conn, notes_dir)` now writes the note rows in the order transaction, `issue(conn)` writes the files; one note line per order line; `COMPANY` is a placeholder name (no company name existed in the repo).
- [x] 7. The `receipt` graph and `run_receipts` (C8) - files: `src/purchase_cycle/receipts.py`, `tests/test_receipts.py`; check: `uv run pytest -q tests/test_receipts.py`: one run receives, serves and issues; a second run changes no row and writes no file; a run interrupted after `receive` and after `serve` (`interrupt_after`) and then resumed by `run_receipts` ends with the same rows, stock and files as an uninterrupted run; the thread `receipt-run-NNNN` is stored in the SQLite checkpoint; a model client double that fails on any call is never reached.
  - Evidence: `uv run pytest -q tests/test_receipts.py` 25 passed (5 new incl. 2 parametrized crash cases, failed first for the missing graph); ruff check and format clean; `run_receipts(db_path, notes_dir, checkpointer, interrupt_after=None)` returns the thread state (receipts, served, skipped, issued); the model double patches `llm.ModelClient` init and call.

### Batch C - Commands and restart
- [x] 8. `receipts run`, `receipts list` and `receipts show` (C3, C10) - files: new `src/purchase_cycle/receipts_cli.py`, `src/purchase_cycle/cli.py` (registration only), new `tests/test_receipts_cli.py`; check: `uv run pytest -q tests/test_receipts_cli.py`: `run` prints each receipt, failed note, served order with its note and skipped order with its missing units; a second `run` prints that nothing changed; `list` prints each receipt with its purchase order and lines and each note with its order and customer; `show CDN-NNNN` prints the file text; an unknown and a malformed reference exit 1 with a message and change nothing; `reconcile list` output is unchanged.
  - Evidence: `uv run pytest -q tests/test_receipts_cli.py` 6 passed; ruff check and format clean; `add_receipts_commands(sub)` in `receipts_cli.py` registered from `main`; options `--checkpoints` and `--notes-dir` (default `config.CUSTOMER_DELIVERY_NOTES_DIR`) on `run`; a run with no receipt, serving or file prints `nothing changed` (skipped orders are still listed).
- [ ] 9. Restart across processes (C9) - files: `tests/test_receipts_cli.py`; check: `uv run pytest -q tests/test_receipts_cli.py`: on a database seeded with stored documents and reconciliations (SQL inserts, no model), the steps `receipts run`, a later document and reconciliation insert, and `receipts run` again run as separate subprocesses against the same database and checkpoint files, and the final rows, stock and files equal the same steps run in one process.
  - Evidence:

### Batch D - Evaluation `goods_receipt`
- [ ] 10. At least 30 scenarios with the spec coverage, runner, grader, critical-error oracle and gates (C11) - files: new `src/purchase_cycle/evaluation/goods_receipt_eval.py`, new `evals/datasets/goods_receipt/`, new `tests/test_goods_receipt_eval.py`; check: `uv run pytest -q tests/test_goods_receipt_eval.py`: a test counts the coverage tags of the spec (at least 2 each); one injection per critical error (negative stock, stock change not equal to received minus served, a note received twice or a non-candidate received, an order served twice or with two notes, a served order not covered, note lines different from order lines, an order served out of arrival order, a file without its row or a row without its file, a model call) and a wrong expectation each prove a non-zero exit of `purchase-cycle eval --suite goods_receipt`; replay report with scenario success, Wilson interval and critical errors saved as `.evidence/fase-10/eval-goods-receipt.txt`.
  The suite is added to `SUITES` and `_suite` in `harness.py` but not yet to `ALL_SUITES`.
  - Evidence:
- [ ] 11. Registration in `ALL_SUITES` (C1, C11) - files: `src/purchase_cycle/evaluation/harness.py`, `tests/test_whatsapp_eval.py` (DV-B); check: `uv run pytest -q tests/test_goods_receipt_eval.py tests/test_whatsapp_eval.py tests/test_offer_eval.py tests/test_document_eval.py tests/test_reconcile_eval.py tests/test_claim_eval.py`: `goods_receipt` is in `SUITES`, in `ALL_SUITES` right before `offer_extraction`, not in `UPLOAD_SUITES`; only the full-order list of `tests/test_whatsapp_eval.py` changes; `npm run eval` replays seventeen evaluations with every gate PASS, saved as `.evidence/fase-10/eval-after-11.txt`.
  - Evidence:

### Batch E - Demo
Before increment 12 the owner decides DV-A; without an answer the batch does not start.
- [ ] 12. `receipt-demo` (C12) - files: `src/purchase_cycle/cli.py` (handler and parser), new `examples/receipts/customer_orders.json`, the file chosen in DV-A, new `tests/test_receipt_demo.py`; check: `uv run pytest -q tests/test_receipt_demo.py`: the demo seeds the purchase orders and customer orders, runs the scan, `receipts run`, the later scan, `receipts run`, `list` and `show`, each in its own process with the existing `reconcile_demo.jsonl` recording; it prints the stock before and after each run, each receipt, each served and skipped order and each note; it ends with a note received from a `matched` invoice, one from a `discrepancies` invoice, one received only after the later scan, one short delivery, one order served that waited for stock, one order skipped for lack of stock and no receipt for the unlinked documents, and exits non-zero when any of these is missing; the test blocks the network as `tests/test_reconcile_demo.py` does; `git diff --stat -- evals/recordings` is empty; output saved as `.evidence/fase-10/receipt-demo.txt`.
  - Evidence:
- [ ] 13. Earlier demos unchanged (C14) - check: `orders-demo`, `quotes-demo` and `reconcile-demo` replay output compared with increment 1 once run ids and folder paths are masked; `uv run pytest -q tests/test_reconcile_demo.py tests/test_quotes_demo.py tests/test_orders_demo.py` passes unchanged; comparison saved as `.evidence/fase-10/demo-comparison.txt`.
  - Evidence:

### Batch F - Documentation, secrets and security
- [ ] 14. README sections in English for the receipt candidates, received quantities, short deliveries and the quote scan, the allocation rule, the customer delivery note, the `receipts` commands, `receipt-demo`, how `goods_receipt` was built and run with its figures, and the known limitations of the spec plus the failed-note and first-pass notes (C16) - files: `README.md`; check: followed step by step in the clean clone of increment 17; `check` and `eval` now say seventeen evaluations; status line and phase table updated; transcript `.evidence/fase-10/readme-walkthrough.txt`.
  - Evidence:
- [ ] 15. Secret scan of the new files (C15) - files: `tests/test_secrets.py` (new test only); check: `uv run pytest -q tests/test_secrets.py`: a phase 10 test lists `examples/receipts/customer_orders.json`, the DV-A file, `evals/datasets/goods_receipt` and the new modules, asserts they are tracked and hold no keys or auth headers; no file is added under `examples/invoices/`, so the phase 08 count test is unchanged.
  - Evidence:
- [ ] 16. Security notes (C15) - files: `docs/security.md`; check: new SEC entries only where the phase adds a surface: customer data written to local delivery note files, a wrong extracted quantity going into stock with no human check, and `--notes-dir` path handling; each with severity and mitigation, or a line stating none applies.
  - Evidence:

### Batch G - Validation and closing
- [ ] 17. `npm run check`, unchanged-behaviour evidence and fresh-clone run with the Anthropic and LangSmith variables unset and no `.env` (C1, C14) - check: `npm ci`, `uv sync` and `npm run check` in a fresh clone of the branch outside the repo; seventeen evaluations in replay with every gate PASS; earlier tests passing apart from the deviations; `git diff --stat 06e4397 -- evals/recordings evals/datasets evals/baselines` lists only the added `goods_receipt` files; output saved as `.evidence/fase-10/fresh-clone-check.txt` and `recordings-diff-stat.txt`.
  - Evidence:
- [ ] 18. LangSmith trace (C13) - check: blocked by the trace quota until about 2026-11-05; recorded as pending and delivered with the catch-up change of earlier phases.
  - Pending:
- [ ] 19. Adversarial review with `sdd-review` through `sdd-delivery` (at most two rounds), findings and fixes recorded below with regression tests; re-run `npm run check` after the fixes.
  - Evidence:
- [ ] 20. Results per criterion in the table below, master plan row 10 status, open items and limitations, candidate learnings; delivery on the branch and a PR to `main`, never a merge.
  - Evidence:

## Deviations
| ID | Summary | Affects criteria | Status |
|---|---|---|---|
| DV-A | The reconcile-demo sample delivery notes all deliver the full purchase order quantity, so C12 "one short delivery" cannot happen with the reconcile-demo purchase orders. Proposed: `receipt-demo` seeds `examples/receipts/purchase_orders.json`, a copy of `examples/invoices/purchase_orders.json` with PO-0003 GAUZE-NS-5 at 50 instead of 40, so E-0303 (40) is a short delivery and INV-0008 stays `discrepancies`; documents and recording unchanged | C12 | approved by the owner 2026-10-15 (option A) |
| DV-B | `tests/test_whatsapp_eval.py::test_eval_all_runs_the_whatsapp_suite_after_its_baseline` lists the whole `eval all` order and gains `goods_receipt` before `offer_extraction`; mechanical edit, no assertion weakened | C14 | approved by the owner 2026-10-15 (option A) |
| DV-C | `db.seed` writes `stock` with `INSERT OR IGNORE` instead of `INSERT OR REPLACE`, so a reseed by any command keeps received and served stock; fresh databases get the same rows | C2, C14 | approved by the owner 2026-10-15 (option A) |

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Results
Per criterion: command or walkthrough run, observed result and evidence reference.
Open items, limitations and what could not be checked, stated plainly.

| ID | Result | Evidence |
|---|---|---|
| C1 | | |
| C2 | | |
| C3 | | |
| C4 | | |
| C5 | | |
| C6 | | |
| C7 | | |
| C8 | | |
| C9 | | |
| C10 | | |
| C11 | | |
| C12 | | |
| C13 | | |
| C14 | | |
| C15 | | |
| C16 | | |

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.
