# Phase 10 - Goods receipt and serving: specification

Status: approved (frozen)
Approved by the owner: 2026-10-15 (option A chosen for D1 to D9)
Master plan: `../0_plan_maestro.md`

## Goal
When a supplier delivery note is reconciled, its goods go into stock, the pending customer orders that stock now covers are served, and our delivery note to each served customer is issued.
The phase is deterministic: no model is called, so it has no prompt, no recording and no live cost.
One command runs the step end to end, from the sample supplier documents to the issued customer delivery notes, with no key and no network.
It is needed now because phases 06 to 09 buy the missing goods and check the supplier documents, but nothing yet adds the goods to `stock`, takes them out for customers or tells a customer what was sent; phase 11 invoices the customer from the delivery notes issued here.
It shows the master plan capabilities "persistent state", "failure recovery" and "structured evaluation" across the orders and purchase sides of the shared database.
The scope and criteria below are written for the recommended option of each open decision; a different choice changes the marked items.

## What exists today
- `stock` holds one `on_hand` per SKU and no code changes it after `seed`; stored customer orders keep status `received` and are never served.
- `quotes.shortfalls` computes, per SKU, the units of `received` orders minus `on_hand` minus the units of every earlier quote request, and starts a request for a positive remainder.
- A quote request is per SKU and is not linked to the customer orders that caused it; a purchase order has one line (`purchase_orders.sku`, `quantity`) and a status that the schema limits to `issued`.
- A delivery note is a `supplier_documents` row with kind `delivery_note` and its `document_lines`; it is linked to a purchase order by the reference it carries.
- A reconciliation exists per invoice (`reconciliations`), with the delivery note it used in `delivery_note_id` and a result `matched`, `discrepancies` or `awaiting_delivery_note`; phase 08 uses the first linked delivery note of an order.
- A `discrepancies` invoice may have a `claims` row (`awaiting_approval`, `sent`, `rejected`); claims are about the invoice and do not change the delivery note.
- `reconcile-demo` seeds seven purchase orders from `examples/invoices/purchase_orders.json` and has no customer orders.

## Scope
Each decision id points to "Open decisions".
In:
- Receipt candidates (D1): a linked delivery note becomes a receipt when a reconciliation references it with result `matched` or `discrepancies`, whatever the claim status; a delivery note with no invoice yet, an invoice `awaiting_delivery_note`, an unlinked document and a note already received are not candidates.
- Received quantities (D2): each delivery note line with a catalog SKU and a whole positive quantity adds that quantity to `stock.on_hand`, including a short delivery, an over-delivery and a SKU not on the purchase order.
  - A note with a line that has no catalog SKU or a quantity that is not a whole number is not received; it writes a `failures` row with status `needs_review` and step `goods_receipt`, and the other notes go on.
- Short deliveries (D3): one receipt per purchase order, as phase 08 allows one delivery note per order; a short delivery closes the order short and no later note is awaited.
  - `quotes.shortfalls` subtracts only the requested units not yet received, so units that entered stock are not counted twice and the unreceived remainder of a short delivery becomes a shortfall again on the next `quotes scan`; with no receipts the result is the same as in phase 06.
- Orders to serve (D4): every customer order with status `received` is a candidate, including orders that stock already covered before this phase.
- Allocation (D5), deterministic: orders are taken by ascending order id (arrival order); an order is served only when `on_hand` covers every one of its lines; an order that cannot be fully served is skipped, keeps status `received` and does not block later orders.
- Serving: a served order takes its quantities out of `on_hand`, gets status `served`, and gets one customer delivery note; stock never goes below zero; one transaction per order.
- No human approval (D6): receipt and serving run without a pause; the person sees the result in the command output and the listing.
- Customer delivery note (D7):
  - Stored in a new table `customer_delivery_notes` (number `CDN-NNNN`, order, customer, issue time from the simulated clock, document file) and its lines in `customer_delivery_note_lines` (SKU, quantity, sale unit), with no prices, since invoicing is phase 11.
  - Written as a plain ASCII text document `CDN-NNNN.txt` in a customer delivery note folder, with our company, the customer name, code and city, the order number, the issue date, and per line the SKU, product name, quantity and sale unit, and a blank signature line for the customer.
- Storage: new tables `goods_receipts` (one row per delivery note: purchase order, received time, status `received` or `failed`) and `goods_receipt_lines` (SKU, quantity); the phase 01 to 09 tables keep their columns and constraints, and the only new values in old tables are `orders.status = 'served'` and the changed `stock.on_hand`.
- Receipt graph, a new LangGraph subgraph `receipt` with the SQLite checkpointer and one thread per run: `receive` (every candidate note), `serve` (the allocation pass), `issue` (delivery note files); no interrupt; every node is idempotent, so a re-run or a resumption after a crash adds no stock, serves no order twice and writes no second file.
- Commands (D9), in a new `receipts` group:
  - `receipts run` receives every candidate note, serves what stock covers and issues the delivery notes, printing each receipt, each served order and each skipped order with the missing units; a second run with nothing new changes nothing.
  - `receipts list` shows each receipt and each customer delivery note; `receipts show CDN-NNNN` prints one customer delivery note.
- Demo (D9): a new `receipt-demo` that seeds the `reconcile-demo` purchase orders and a versioned file of customer orders, runs the phase 08 scan over the sample documents, then `receipts run`, a later scan with `examples/invoices/later/`, and `receipts run` again, each step in its own process.
- One new deterministic evaluation `goods_receipt` inside `npm run check` next to the sixteen existing ones (D8).
- README sections for the receipt and serving step, the allocation rule, the commands, the customer delivery note, the demo, the evaluation and the known limitations.

Out:
- Customer invoices, prices on the delivery note, sales reconciliation, signed delivery notes returned by the customer and their incidents, which are phase 11.
- Sending the delivery note to the customer by email or WhatsApp, and PDF output.
- Partial serving of an order, backorder lines, priorities by customer and reservation of incoming goods for specific orders.
- Several delivery notes or several invoices per purchase order, and waiting for the remainder of a short delivery.
- Returning goods to the supplier, damaged goods, lot or expiry tracking, warehouse locations and stock valuation.
- Changing the claim flow of phase 09, or receiving goods depending on a claim decision.
- Changes to `purchase_orders.status`, whose schema allows only `issued`.
- The orchestrator, which is phase 12.
- Changes to the phase 01 to 09 prompts, recordings, datasets, baselines and gates, and to the phase 08 matching.

### Evaluation design
- `goods_receipt` (D8): at least 30 hand-built scripted scenarios, versioned with their starting stock, customer orders, purchase orders, supplier documents as extracted rows (no model) and expected final state, run through the CLI functions on a temporary database.
  - Coverage, at least 2 each: a `matched` note received; a `discrepancies` note received with a claim in each status; no receipt for an invoice `awaiting_delivery_note`, an unlinked document or a note with no invoice; a note that becomes a candidate after a later scan; a short delivery and the shortfall it leaves for `quotes scan`; an over-delivery; a SKU not on the purchase order; a note parked for an unknown SKU or a fractional quantity; several orders served in arrival order; a large older order skipped while a later one is served; an order that stock covered before the receipt; an order with several lines where one is short; a second `receipts run`; a crash between `receive` and `serve` followed by a resumption; a process restart between scan and `receipts run`.
  - Grader: deterministic, reads `stock`, `orders`, the new tables, the `failures` table, the delivery note folder and the printed output.
  - Critical errors per scenario: negative stock; a stock change per SKU that differs from received minus served units; a note received twice or a receipt for a note that is not a candidate; an order served twice or with more than one delivery note; a served order not fully covered; delivery note lines that differ from the order lines; an order served out of arrival order when an earlier one could be served; a delivery note file without its row or a row without its file; a model call.
  - Gates: scenario success 100% and critical errors 0, since the step is deterministic, with the phase 05 report columns and a 95% Wilson interval on scenario success.
- No model evaluation, dataset split, baseline or McNemar gate is added, since nothing is generated by a model.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the seventeen evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | `seed` creates the four new tables; seeding twice gives the same rows; the other tables and their rows are unchanged | Pytest tests on a temporary database |
| C3 | `receipts run` creates one receipt per delivery note referenced by a `matched` or `discrepancies` reconciliation, whatever its claim status, and none for an invoice `awaiting_delivery_note`, an unlinked document, a note with no invoice or a note already received; a note that becomes a candidate on a later scan is received on the next run | Pytest tests on a temporary database counting `goods_receipts` rows per case |
| C4 | A receipt adds to `stock.on_hand` the delivered quantity of every note line, for short, exact and over-deliveries and for a SKU not on the purchase order; a note with an unknown SKU or a fractional quantity adds nothing and writes a `failures` row with status `needs_review` and step `goods_receipt`, and the other notes are still received | Pytest tests comparing stock before and after with expected values, and reading `failures` |
| C5 | After a short delivery, `quotes scan` starts a request for the unreceived remainder when `received` orders still need it; with no receipts, `quotes.shortfalls` returns the same rows as before the phase | Pytest tests on a temporary database; the phase 06 shortfall tests unchanged |
| C6 | The serving pass takes `received` orders by ascending id and serves an order only when stock covers every line; a skipped order keeps status `received` and does not stop a later order that stock covers; a served order gets status `served` and its quantities leave `on_hand`; stock never goes below zero | Pytest tests with orders in several stock situations, comparing statuses and stock with expected values |
| C7 | Every served order gets one `customer_delivery_notes` row `CDN-NNNN` with lines equal to its order lines and one ASCII text file with the fields listed in "Scope" and no prices; the issue time comes from the simulated clock | Pytest tests comparing the rows and file with expected values |
| C8 | A second `receipts run` with nothing new changes no row and writes no file; a run interrupted after `receive` or after `serve` and resumed from its checkpoint ends with the same rows, stock and files as an uninterrupted run; receipt and serving call no model | Pytest tests with an injected crash and a resumption, and a model client double that fails on any call |
| C9 | Receipt and serving survive separate processes: the scan, `receipts run` and a later scan and run each in a new process against the same database and checkpoint files give the same final rows, stock and files as the same steps in one process | Pytest test that runs each step as a subprocess and compares with an in-process run |
| C10 | `receipts list` prints each receipt with its purchase order and received lines and each customer delivery note with its order and customer; `receipts show CDN-NNNN` prints the note; an unknown reference fails with a message and changes nothing | Pytest tests on a temporary database |
| C11 | `goods_receipt` holds at least 30 scenarios with the coverage of "Evaluation design"; `npm run eval` reports scenario success with its Wilson interval and critical errors per scenario, and exits non-zero when success is below 100% or any critical error is counted | Pytest tests over the scenario files and tests that inject each critical error and prove a non-zero exit; replay report saved as evidence |
| C12 | `receipt-demo` runs from the sample supplier documents and the versioned customer orders to the issued delivery notes, each step in its own process; it prints the stock before and after each run, each receipt, each served and skipped order and each delivery note; it ends with at least one note received from a `matched` invoice, one from a `discrepancies` invoice, one note received only after the later scan, one short delivery, one order served that had waited for stock, one order skipped for lack of stock and no receipt for the unlinked documents; it needs no key and no network | Run with the network blocked through a pytest inside `check`; the scan uses the existing `reconcile-demo` recording; output saved as evidence |
| C13 | With the LangSmith variables set, a `receipt-demo` run in live mode appears in LangSmith as one trace per `receipts run` with one span per node | Trace link and the owner's screenshot as evidence; blocked by the trace quota until about 2026-11-05 |
| C14 | Phase 01 to 09 behaviour is unchanged: their tests pass unchanged; the sixteen existing evaluations replay with every gate PASS; `git diff --stat` on their recordings, datasets and baselines is empty; `orders-demo`, `quotes-demo` and `reconcile-demo` print the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C15 | No secret is versioned: the new customer orders file, scenario files and delivery note examples contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C16 | The README explains in English the receipt candidates, the received quantities, the allocation rule, the customer delivery note, the `receipts` commands, `receipt-demo`, how the evaluation was built and run, and the known limitations below; master plan row 10 shows the phase status | Follow the new README sections in a clean clone; compare row 10 with the phase status |

## Constraints and risks
- No new dependency and no live model cost: the step is plain SQL and Python inside a LangGraph graph, and the text document uses the standard library.
- The LangSmith trace quota is exhausted until about 2026-11-05; C13 is pending at delivery, as C13 to C17 of phases 02 to 09, and is delivered with the catch-up change after the reset.
- Today no code decrements stock, so the first serving pass also serves orders that were always in stock; the demo and the README say so, and the scenarios cover it.
- Changing `quotes.shortfalls` touches phase 06 code; the rule is written so it returns the same rows when there are no receipts, and the unchanged phase 06 tests and `quotes-demo` output guard it.
- A crash between adding stock and serving, or between serving and writing the file, could double count or lose a note; each node commits per note or per order in one transaction and checks what is already stored before acting.
- Skipping an order that does not fit can starve a large old order while smaller later ones are served; the rule is visible and the listing shows the units it is missing.
- Receiving the delivered quantity when the invoice has open differences means stock follows the goods, not the claim; a wrong delivery note extraction (phase 08 DOC-060 and DOC-112 errors) would put a wrong quantity into stock, and only the receipt listing shows it.
- `customer_delivery_notes`, `goods_receipts` and their line tables are new; `CREATE TABLE IF NOT EXISTS` adds them to a database made by phase 09 code without touching the old tables.
- About 30 scenarios give a wide interval on scenario success; the gate is 100% because the step is deterministic.

## Assumptions
- The delivery note states what physically arrived; its lines are in the catalog sale units, as phase 08 assumes, so a delivered quantity maps one to one onto `on_hand`.
- "Reconciled" means a reconciliation row references the note; a claim on the invoice concerns money and does not hold the goods.
- The order number printed on the customer delivery note is `ORD-NNNN` from the order id, since orders have no reference yet; the delivery note number `CDN-NNNN` comes from its row id.
- The serving pass runs over all `received` orders on every `receipts run`, so an order that stock covers is served even if the run received nothing.
- Orders paused for clarification are not stored yet and so are not candidates; only stored `received` orders are.
- The customer delivery note folder is a new folder in `data/`, separate from the WhatsApp outbox and the supplier outbox; the demo writes to its own work folder.
- `npm run check` stays fast: the new evaluation runs in seconds.

## Known limitations
- Whole orders only: an order waits until every line is covered, and no partial delivery or backorder is made.
- One delivery note per purchase order; a short delivery is closed short and the remainder is bought again through `quotes scan`.
- Stock is a single number per SKU, with no reservation, location, lot or expiry.
- The customer delivery note is a local text file; nothing is sent to the customer.
- The phase 08 limitations remain: one invoice and one delivery note per order, fixed tolerances, and the DOC-060 and DOC-112 extraction errors until their change lands.

## Open decisions
None; every decision was resolved on approval.

## Resolved decisions
Owner 2026-10-15: option A (the recommended one) for D1 to D9; the scope and criteria above already follow it.
Each decision keeps its options and trade-offs for the record.
- D1 - What counts as a reconciled delivery note:
  - A (recommended): any delivery note referenced by a reconciliation with result `matched` or `discrepancies`, whatever the claim status; follows the master plan wording and does not hold goods that are physically in the warehouse while a money claim is open.
  - B: only `matched`, or `discrepancies` once the claim is decided (`sent` or `rejected`); stricter, but goods wait on a person's claim decision and the demo needs the claim steps first.
  - C: any linked delivery note, with or without an invoice; closest to a real warehouse, but drops the "reconciled" condition of the master plan and the link to phases 08 and 09.
- D2 - Which quantities go into stock:
  - A (recommended): every note line with a catalog SKU as delivered, including short, over-delivery and SKUs not on the purchase order; stock matches what arrived; simple and predictable.
  - B: only the purchase order SKU, capped at the ordered quantity, with any excess parked as `needs_review`; protects against over-delivery but stock no longer matches the goods on the shelf.
  - C: receive only when the note quantity equals the order; simplest rule, but a short delivery puts nothing into stock.
- D3 - Short deliveries and the quote scan:
  - A (recommended): one receipt per purchase order, a short delivery closes the order short, and `quotes.shortfalls` subtracts only unreceived requested units so the remainder is bought again; small change to phase 06 code with the same result when there are no receipts.
  - B: same receipt rule, phase 06 code untouched; after any receipt `quotes scan` counts received units twice and under-orders, documented as a limitation.
  - C: keep the remainder outstanding and receive a second delivery note later; most realistic, but needs several delivery notes per order in phase 08 matching, which is out of this phase.
- D4 - Which customer orders are served:
  - A (recommended): every stored order with status `received`, including orders stock already covered; one simple rule and it fixes that no order has ever been served.
  - B: only orders with a SKU that had a quote request; narrower and closer to the "pending orders" wording, but orders that stock always covered are never served.
- D5 - Allocation rule when stock is short:
  - A (recommended): whole orders by arrival (ascending order id), skipping an order that does not fit so later ones can be served; no partial documents for phase 11, but a large old order can wait.
  - B: strict arrival order, stopping at the first order that does not fit; fairest, but one large order blocks every later one.
  - C: line-level partial serving with backorder lines; serves the most units, but adds backorders and partial delivery notes that phase 11 must invoice and reconcile.
  - D: by customer priority (for example clinics first); a business rule the owner would have to define, with no data for it today.
- D6 - Human approval before serving:
  - A (recommended): none; receipt and serving are deterministic and run directly; keeps the phase small, and human-in-the-loop is already shown in phases 04, 07 and 09.
  - B: one pause per run with approve or reject, reusing the phase 09 `review` node; shows reuse of the review pattern, but adds a pause, commands and scenarios.
- D7 - Format of our customer delivery note:
  - A (recommended): database rows plus a plain text document `CDN-NNNN.txt` in a delivery note folder; no dependency, readable, and phase 11 can read the signed copy back with the existing text readers.
  - B: database rows plus an `.eml` to the customer in a customer outbox with the note in the body, as phase 09 does for claims; reuses the email writer, but mixes the document with a message.
  - C: database rows plus a PDF; most realistic, but needs a new PDF writing dependency.
  - D: database rows only; smallest, but nothing a customer would see or sign.
- D8 - Model involvement and evaluation:
  - A (recommended): no model; one deterministic scenario evaluation `goods_receipt` with 100% and zero critical error gates; no cost, no recording, no baseline.
  - B: a model writes a short cover note to the customer, checked by code with a template fallback; adds a model evaluation, recordings and a live cost for text the customer does not need.
- D9 - Commands and demo shape:
  - A (recommended): a new `receipts` group (`run`, `list`, `show CDN-NNNN`) and a new `receipt-demo` that reuses the `reconcile-demo` sample documents and recording plus a versioned customer orders file; `reconcile-demo` and its test stay unchanged.
  - B: the same commands, and `reconcile-demo` extended with the receipt steps; one demo for the purchase side, but the phase 08 and 09 demo test changes again and the demo gets long.
  - C: the commands inside the `reconcile` group; fewer groups, but receipt and serving are not reconciliation and the orchestrator would compose them separately.
