# Phase 11 - Customer invoicing and sales reconciliation: specification

Status: approved (frozen)
Approved by the owner: 2026-10-16 (option A for D1 to D4, D6, D7 and D9; option B for D5 and D8)
Master plan: `../0_plan_maestro.md`

## Goal
Every customer delivery note issued in phase 10 gets our invoice to the customer, priced from the catalog.
When the customer returns the signed delivery note with incidents (missing, extra or damaged units), the sales side reconciles order, delivery note and invoice with the phase 08 matching and classifies each difference.
For every invoice with differences, the module prepares a response to the customer and the correcting document (credit note or corrective invoice), and a person approves, edits or rejects it through the phase 09 review pause, with saved state that survives a process restart.
Claude Haiku 4.5 writes the body of the response from structured facts, and deterministic code checks it in the phase 09 style: every incident, the right references, every correcting document with its total and no amount outside the facts; a draft that fails the check falls back to a fixed template.
An approved response is written as an `.eml` to a customer outbox together with its document file; a rejected one writes nothing.
One command runs the step end to end, from the phase 10 delivery notes to the decided responses, in replay with no key, no network and no cost.
It is needed now because phase 10 serves orders and issues delivery notes, but nothing bills the customer or handles what the customer says arrived; the orchestrator of phase 12 composes this step with the other modules.
It shows the master plan capabilities "human-in-the-loop", "persistent state" and "structured evaluation" on the sales side of the shared database, reusing the phase 08 matching, the phase 09 drafting pattern and the phase 09 review node instead of parallel mechanisms.
The scope and criteria below are written for the chosen option of each decision in "Resolved decisions".

## What exists today
- A served order has status `served`, one `customer_delivery_notes` row `CDN-NNNN` and its `customer_delivery_note_lines` (SKU, quantity, sale unit, no price), and a text file `CDN-NNNN.txt` with a blank signature line.
- `order_lines` hold SKU and quantity with no price; the only price on the sales side is `products.price_eur`; `customers` hold name, city, contact name, email and phone.
- `reconcile_match.classify(order, invoice_lines, note_lines)` takes a one-line order (`sku`, `quantity`, `unit_price`) and returns the result (`matched`, `discrepancies`, `awaiting_delivery_note`) and the difference rows (price, quantity, extra line, missing line); it is pure code with no database access.
- `review.review` pauses a plain message (to, subject, body) with `interrupt` and returns `approve`, `edit` with a new body, or `reject`; it imports no supplier or invoice code, as phase 09 C11 requires.
- The phase 09 claim graph (`draft`, `review`, `send` or `close`), its idempotent nodes, its `.eml` writer to the supplier outbox and its `approve`, `--body-file` and `reject` commands are the pattern this phase copies for the customer.
- The `supplier_documents`, `reconciliations`, `discrepancies` and `claims` tables are purchase-side only: they reference purchase orders and suppliers.
- The WhatsApp outbox, the supplier outbox and the customer delivery note folder exist in `data/`; there is no customer email outbox.
- `receipt-demo` ends with served orders and issued delivery notes from `examples/receipts/customer_orders.json`.

## Scope
Each decision id points to "Resolved decisions".
In:
- Invoicing (D2): one customer invoice `SINV-NNNN` per customer delivery note that has none, issued without a pause, with one line per delivery note line (SKU, quantity, unit price from `products.price_eur` at issue time, line total) and the net total in EUR; the issue time comes from the simulated clock.
  - Stored in new tables `customer_invoices` (number, delivery note, order, customer, issued time, net total, document file) and `customer_invoice_lines`; written as a plain ASCII text document `SINV-NNNN.txt` in a new customer invoice folder in `data/`, in the style of the phase 10 delivery note.
- Signed delivery notes (D1): the customer returns a plain text file in a fixed form, read by deterministic code, that names the delivery note `CDN-NNNN`, the signer and the signing date, and either states "received in full" or lists incidents, one per line, with SKU, incident type (`missing`, `extra`, `damaged`) and units.
  - Files arrive in an inbox folder and are processed once by content hash; a file that names no known delivery note, a note already signed, or a note of another customer is stored as `unlinked` with its reason; a file that does not follow the form, or names a SKU outside the catalog, or a non-whole or non-positive quantity, writes a `failures` row with status `needs_review` and step `signed_note`, and the other files go on.
  - Stored in new tables `signed_delivery_notes` (delivery note, signer, signing date, source file, content hash, status) and `signed_note_incidents` (SKU, type, units).
- Accepted quantities: per SKU, the units the customer accepts are the delivery note units minus `missing` minus `damaged` plus `extra`; a `missing` or `damaged` count above the delivered units is a form failure.
- Sales matching (D3), deterministic, no model call, one sales reconciliation per invoice once its signed note is stored:
  - The phase 08 rules are applied with order = the customer order lines at the invoice unit prices, invoice = our invoice lines, delivery note = the accepted quantities; types and amounts are the phase 08 ones.
  - `classify` is generalised to take an order with several lines; phase 08 calls it with its one line, so phase 08 results, tests and the `reconciliation_matching` evaluation stay the same.
  - An `extra` SKU that is not on the delivery note is an accepted line with no invoice line; the generalised rule reports it as a quantity difference in the customer's debt (invoiced 0, accepted N) so that no new type is added.
  - Each difference row keeps the incident type that caused it (`missing`, `extra`, `damaged`) next to the phase 08 type, so the response can say what happened.
  - An invoice with no signed note yet has result `awaiting_delivery_note` and no rows; a "received in full" note gives `matched`.
  - Stored in new tables `sales_reconciliations` (invoice, signed note, result, reconciliation time) and `sales_discrepancies` with the phase 08 columns plus the incident type; the purchase-side tables are not altered.
- Correcting documents (D4): the amounts are computed by code from the difference rows, never by a model or a person.
  - Units invoiced and not accepted (`missing`, `damaged`) give a credit note `CN-NNNN` with negative lines and total; units accepted and not invoiced (`extra`) give a corrective invoice `CI-NNNN` with positive lines and total; an invoice with both gets both documents in one response.
  - Each document references the original invoice `SINV-NNNN`, is stored in a new table `customer_corrections` with its lines, and its text file is written only on approval.
- Response facts: one response per invoice with result `discrepancies`, from structured facts built by code: the customer name, the invoice and delivery note numbers, each incident with its SKU, product name, incident type and units, each correcting document with its number, lines and total, and the invoice net total.
  - Recipient is the customer email from `customers`, subject is fixed with the invoice and delivery note numbers, both set by code, not by the model.
- Response drafting (D5): a new model task `response_drafting` through the existing client in live, record and replay modes with Claude Haiku 4.5, one call per response, with the facts as one structured object in the prompt; the answer is the email body only.
  - Deterministic fact check in code after drafting, in the style of the phase 09 `check_draft` and with its amount pattern reused unchanged: the body names the invoice and delivery note numbers, every incident SKU with its units, every correcting document number with its total, contains no money amount outside the facts (compared within half a cent), and is ASCII text under a fixed length.
  - A draft that fails the schema check after the client retries, or fails the fact check, is replaced by a fixed English template filled by code from the same facts (customer name, references, each incident, each document with its number and total, and a closing line), stored with source `template`, and writes a `failures` row with status `needs_review` and step `response_drafting`; the other responses go on.
  - Stored in a new table `customer_responses` (sales reconciliation, recipient, subject, draft body, draft source `model` or `template`, final body, edited flag, status `awaiting_approval`, `sent` or `rejected`, drafted and decided times from the simulated clock, outbox file).
- Response graph, a new LangGraph subgraph `customer_response` with the SQLite checkpointer and one thread per invoice, `response-SINV-NNNN`: `draft` (drafts, checks and stores the response and the planned documents, or reads back a stored response so a re-run calls no model), `review` (the phase 09 `review.review` unchanged), `send` (writes the `.eml` to a new customer outbox in `data/` with the documents named in the body and their text files in the invoice folder, sets `sent`), `close` (sets `rejected`, writes nothing).
  - An edited text changes only the email body; the amounts and documents stay the ones computed by code.
- Sales graph, a new LangGraph subgraph `sales` with the SQLite checkpointer and one thread per run: `invoice` (every delivery note with no invoice), `read` (signed notes of the folder), `link`, `match`, `store`; no interrupt and no model call; every node is idempotent.
- Commands (D7), in a new `sales` group:
  - `sales invoice` issues every missing invoice; `sales scan FOLDER` reads the signed notes and reconciles; a second run of either with nothing new changes nothing.
  - `sales responses [--mode]` drafts a response for every `discrepancies` invoice with none, prints each draft with its facts and source, and leaves it paused; a second run drafts nothing and calls no model; `sales approve SINV-NNNN [--body-file FILE]` and `sales reject SINV-NNNN` decide it, with the phase 09 refusal rules.
  - `sales list` shows each invoice with its delivery note, result, incident counts by type and response status; `sales show SINV-NNNN` prints the invoice, the incidents, the differences, the documents and the response.
- Demo (D7): a new `sales-demo` that runs the `receipt-demo` steps to the issued delivery notes, then `sales invoice`, `sales scan` over a versioned folder of signed notes, `sales responses`, and scripted operator decisions (one approved, one approved with an edited text, one rejected), each step in its own process; in replay the drafts come from a demo recording made once in live mode.
- Three new evaluations inside `npm run check` next to the seventeen existing ones: the deterministic `sales_reconciliation` and `customer_response` (D6), and `response_drafting` in replay (D5).
- README sections for invoicing, the signed delivery note form, the sales matching, the correcting documents, the response drafting with its fact check and template fallback, the approval, the `sales` commands, `sales-demo` in replay and live mode, the local Phoenix trace, the evaluations and the known limitations.

Out:
- Payment, payment reminders, due dates, accounting entries and bank reconciliation, as the master plan states.
- VAT, discounts, customer price lists and price differences on the sales side: every price comes from the catalog, so the price type never appears.
- Stock changes from incidents: re-sending missing or damaged goods, returns of damaged or extra goods to stock, and write-offs.
- Customer replies to our response, disputes of a credit note, follow-ups and reminders.
- Grouped or periodic invoices, several invoices or several signed notes per delivery note, and partial or later signatures.
- Real email sending or receiving, PDF output and scanned or handwritten signed notes.
- Changes to the phase 09 claim flow and to the phase 10 receipt and serving step and delivery note file.
- The orchestrator, which is phase 12.
- Changes to the phase 01 to 10 prompts, recordings, datasets, baselines and gates; the phase 08 matching changes only by the generalisation of D3, with the same results; the phase 09 fact check code is reused, not changed.
- The LangSmith trace and the LangSmith experiment of `response_drafting`, left to a later change (D8).

### Evaluation design
- `sales_reconciliation` (D6): at least 30 hand-built scripted scenarios, versioned with their served orders, delivery notes, signed note files and expected final state, run through the CLI functions on a temporary database.
  - Coverage, at least 2 each: one invoice per delivery note with catalog prices and totals; "received in full" giving `matched`; an invoice with no signed note `awaiting_delivery_note`; `missing` units; `damaged` units; `extra` units of a delivered SKU; an `extra` SKU not on the note; several incident types on one note; several lines with one incident; a credit note only, a corrective invoice only, and both; an unlinked signed note (unknown note, note already signed, other customer); a form failure parked as `needs_review`; a second `sales invoice` and `sales scan`; a crash inside the sales graph followed by a resumption; a process restart between invoicing and scan.
  - Grader: deterministic, reads the new tables, the invoice folder, the `failures` table and the printed output.
  - Critical errors per scenario: an invoice line or total that differs from delivery note units times catalog price; more than one invoice per delivery note; a difference row whose amount differs from the phase 08 rule; a missed or invented incident; a correcting document whose total differs from the sum of its differences; a signed note linked to the wrong delivery note; a model call.
- `customer_response` (D6): at least 24 hand-built scripted scenarios, run through the CLI functions on a temporary database, with the drafts given by a test double of the model client that counts calls, so no model is called.
  - Coverage, at least 2 each: a response drafted for a `discrepancies` invoice; none for `matched` and `awaiting_delivery_note`; a model draft that fails the fact check and falls back to the template; approve; approve with an edited text; an empty edited text refused; reject; a second approve or reject; a command on an invoice with no response; a second `sales responses`; a process restart between draft and decision; a response with both documents.
  - Critical errors per scenario: an email or document file for a rejected or undecided response; more than one email per invoice; a sent text that differs from the approved text; a document amount changed by an edited text; a response status that does not match the outbox; a model call on a re-run or a resumption.
- Gates for both: scenario success 100% and critical errors 0, since both steps are deterministic, with the phase 05 report columns and a 95% Wilson interval on scenario success.
- `response_drafting` (D5), built as the phase 09 `claim_drafting`, with a seeded planning script, automatic validation of every case and a clean-context second-pass review:
  - At least 60 cases, each a set of response facts made from real catalog SKUs and prices and run through the generalised `classify` and the correcting document rule, so every case is a response this step can produce.
  - Ten categories of at least 6: `missing` only, `damaged` only, `extra` of a delivered SKU, `extra` SKU not on the delivery note, `missing` and `damaged` on one line, several incident types over several SKUs, a credit note over several lines, a corrective invoice over several lines, both documents in one response, and amounts with many decimals or thousands.
  - Fixed stratified split of about 25% development and 75% test; only the development split tunes the instructions, and published figures and gates come from the test split.
- Metrics and grader for `response_drafting`, all deterministic, scored on the model draft before any template fallback, with the fact check of "Scope" as the grader:
  - Reference accuracy (invoice and delivery note numbers), incident coverage (share of incidents whose SKU and units appear), document accuracy (share of correcting documents whose number and total appear), invented amount rate (drafts with a money amount outside the facts), and case pass rate (every check right).
  - Critical errors: a missing incident, a wrong or invented amount, a missing or wrong correcting document total, or a wrong invoice or delivery note number, since a sent response with them would be wrong.
  - Every metric with a 95% Wilson interval on the test split, globally and per category, with its counting unit, and the Wilson upper bound for a zero-event rate, as phase 05 set.
- Gates for `response_drafting`, as phase 09:
  - Absolute gate in the test split on case pass rate of at least 95%, if the baseline measured in this phase with the final tuned instructions reaches it; otherwise execution stops and the owner decides, recorded as a deviation.
  - Tuning ends before the test split is recorded; the instructions do not change after the test baseline is measured.
  - Regression gate by exact McNemar test per case against the stored baseline, failing on a significant drop (p < 0.05).
  - Critical errors reported with their rate and Wilson upper bound and a target of 0 fixed before measuring, reported as met or not.
- Live model experiment (D5, D8, D9): the development tuning rounds and one record run of the test split with Claude Haiku 4.5, with the owner's key and LangSmith tracing off; the record run stores the recordings and the baseline that `check` replays, and its report with calls, tokens and cost is saved as evidence.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the twenty evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | `seed` creates the new sales tables; seeding twice gives the same rows; the other tables and their rows are unchanged | Pytest tests on a temporary database |
| C3 | `sales invoice` issues one `SINV-NNNN` per customer delivery note with no invoice, with lines equal to the delivery note lines, unit prices equal to `products.price_eur`, line totals and net total rounded to the cent, its issue time from the simulated clock and one ASCII text file; a second run issues nothing | Pytest tests comparing rows and file with expected values |
| C4 | `sales scan FOLDER` stores each signed note in the fixed form once by content hash with its incidents; an unknown note, a note already signed or a note of another customer is `unlinked` with its reason; a file out of form, an unknown SKU, a non-whole quantity or more missing or damaged units than delivered writes a `failures` row with status `needs_review` and step `signed_note`, and the other files are still read | Pytest tests per case reading the new tables and `failures` |
| C5 | Each invoice with a signed note gets one sales reconciliation whose result and difference rows equal the generalised `classify` applied to order lines, invoice lines and accepted quantities, each row with its incident type; an invoice with no signed note is `awaiting_delivery_note`; a "received in full" note gives `matched` | Pytest tests with expected rows per incident type and combination |
| C6 | The generalised `classify` returns the same result and rows as before for every phase 08 case: the phase 08 tests pass unchanged and `reconciliation_matching` replays with every gate PASS | Test run and eval run, as evidence |
| C7 | A `discrepancies` invoice gets a credit note for units invoiced and not accepted and a corrective invoice for units accepted and not invoiced, each referencing the invoice, with lines and total computed from the difference rows | Pytest tests comparing `customer_corrections` rows with expected values |
| C8 | `sales responses` drafts one response per `discrepancies` invoice with none through one `response_drafting` call in live, record or replay mode, addressed by code to the customer email with the fixed subject; a draft that passes the fact check is stored with source `model`; a draft that fails the schema check or the fact check (missing incident, wrong reference, missing or wrong document total, invented amount) is stored from the template with source `template` and a `failures` row with status `needs_review` and step `response_drafting`, and the other responses are still drafted; it leaves the thread `response-SINV-NNNN` paused in the phase 09 `review` node with its state in the SQLite checkpoint; a second run drafts nothing and calls no model | Pytest tests with recorded valid and failing answers reading rows, `failures` and checkpoint state, and counting model client calls |
| C9 | `sales approve SINV-NNNN` writes one `.eml` to the customer outbox and the document files, and sets `sent`; with `--body-file FILE` the email carries the file text, the draft is kept and the response is marked edited, and the document amounts are unchanged; an empty file is refused; `sales reject SINV-NNNN` sets `rejected` and writes no file; any command on an invoice with no response or a decided response fails with a message and leaves one email at most | Pytest tests comparing outbox and document files and rows before and after |
| C10 | Every sales node is idempotent: a second run with nothing new changes no row and writes no file; a sales run interrupted after any node and resumed ends with the same rows and files as an uninterrupted run; the `sales` graph, the commands other than `sales responses` and every resumption call no model | Pytest tests with an injected crash and a resumption, and a model client double that fails on any call |
| C11 | The sales step survives separate processes: invoice, scan, responses and each decision in a new process against the same database and checkpoint files give the same final rows and files as the same steps in one process; resuming calls no model | Pytest test that runs each step as a subprocess and compares with an in-process run, with the recordings file removed for the decision steps |
| C12 | `sales list` and `sales show SINV-NNNN` print the fields listed in "Scope"; an unknown reference fails with a message and changes nothing | Pytest tests on a temporary database |
| C13 | `sales_reconciliation` holds at least 30 scenarios and `customer_response` at least 24, with the coverage of "Evaluation design"; `npm run eval` reports scenario success with its Wilson interval and critical errors per scenario, and exits non-zero when success is below 100% or any critical error is counted | Pytest tests over the scenario files and tests that inject each critical error and prove a non-zero exit; replay report saved as evidence |
| C14 | `sales-demo` runs from the versioned customer orders to the decided responses, each step in its own process; it prints each invoice, each signed note with its incidents, each difference, each correcting document, each draft with its source and each decision; it ends with at least one `matched` invoice, one invoice `awaiting_delivery_note`, one credit note, one corrective invoice, one response sent as drafted, one sent with an edited text, one rejected and one unlinked signed note; it needs no key and no network | Run with the network blocked through a pytest inside `check`; the drafts come from a demo recording made once in live mode; output saved as evidence |
| C15 | With `PHOENIX_TRACING=true`, the local Phoenix of change 013 running and LangSmith tracing off, one live `sales-demo` run sends its traces to the local Phoenix: one trace per sales run with one span per `sales` node, and for one response the drafting process with the `draft` and `review` spans and one model-call span of Claude Haiku 4.5, and the decision process with the `review` and `send` or `close` spans | Spans of that run read back from the Phoenix API and saved as evidence, as change 013 did; the LangSmith trace is left to a later change (D8) |
| C16 | Phase 01 to 10 behaviour is unchanged: their tests pass unchanged; the seventeen existing evaluations replay with every gate PASS; `git diff --stat` on their recordings, datasets and baselines is empty; `orders-demo`, `quotes-demo`, `reconcile-demo` and `receipt-demo` print the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C17 | No secret is versioned: the new recordings, signed note examples, edited body file, outbox examples, dataset and scenario files contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C18 | The README explains in English the invoice, the signed note form, the accepted quantities, the sales matching and its reuse of phase 08, the correcting documents, the response drafting with its fact check and template fallback, the approval, the `sales` commands, `sales-demo` in replay and live mode, how to see its trace in the local Phoenix, how the three evaluations were built and run, and the known limitations below; master plan row 11 shows the phase status | Follow the new README sections in a clean clone; compare row 11 with the phase status |
| C19 | The versioned `response_drafting` dataset holds at least 60 cases in the ten categories of "Evaluation design", at least 6 per category, with a fixed stratified split; every case passes the automatic validation and its incidents, differences and documents equal the output of the generalised `classify` and the correcting document rule; re-running the planning script with the same seed gives the same plan | Pytest tests over the dataset and plan files; generator output saved as evidence |
| C20 | `npm run eval` in replay mode reports `response_drafting` with the metrics of "Evaluation design", 95% Wilson intervals on the test split globally and per category, critical errors with their Wilson upper bound, and exits non-zero when an absolute or regression gate fails | Run the command; pytest tests that force each new gate to fail prove the non-zero exit |
| C21 | The baseline of Claude Haiku 4.5 on the `response_drafting` test split is measured with the final instructions, stored with its per-case results, and the gates are set by the rule in "Evaluation design"; every live run of the phase is logged with its calls, tokens and cost, and the phase total stays under 5 USD (D9) | Stored baseline file, the live run reports and the running cost total in `plan.md`, saved as evidence |

## Constraints and risks
- No new dependency: plain SQL and Python inside LangGraph graphs, the existing model client with record and replay, the review node of phase 09, the Phoenix instrumentation of change 013 and the standard library `email` package.
- Live model cost ceiling of 5 USD for the phase (D9), a limit and not a budget; the expected cost is under 1 USD: short answers, about 15 calls per tuning round on the development split for up to three rounds, about 45 calls for the test record and a few for the demo record; each live run logs its calls, tokens and cost, and no run starts that could take the phase total over the ceiling.
- Live runs need the owner's Anthropic key in `.env`; the agent never reads that file, and live runs are run by the owner or with the owner present, with LangSmith tracing off.
- An invented or wrong amount in a sent response is the worst error; the fact check stops it before the person sees the draft, the template fallback keeps the flow going, and the evaluation scores the model before the fallback so the check does not hide model errors.
- The fact check reads amounts and units from free text; a pattern that is too loose or too strict would pass a wrong draft or reject a good one; both directions have scenarios in `customer_response` and categories in `response_drafting`.
- Generalising `classify` touches phase 08 code; the phase 08 tests, the `reconciliation_matching` evaluation and `reconcile-demo` output guard it (C6, C16), and the change is limited to accepting several order lines.
- The phase 08 rule "invoiced above delivered" is the core of the sales side; an `extra` SKU absent from the delivery note needs the generalised rule to compare accepted lines with no invoice line, which phase 08 never had to do; with one-line purchase orders this case cannot appear on the purchase side, so phase 08 results do not change.
- LangGraph re-runs an interrupted node from its start on resumption, so `draft`, `send` and `close` must be idempotent: a stored response is read back instead of calling the model, and one response per invoice and keyed file names prevent duplicates.
- An edited response could contradict the computed documents; the documents are fixed by code and the edited text is sent as written, as in phase 09; only the person at review sees the mismatch.
- The sales tables are new; `CREATE TABLE IF NOT EXISTS` adds them to a database made by phase 10 code without touching the old tables.
- The LangSmith trace quota is exhausted until about 2026-11-05; by D8 C15 is closed with a local Phoenix trace, which departs from the 2026-10-15 rule that LangSmith serves the frozen trace criteria, and the LangSmith trace and the `response_drafting` experiment are left to a later change.
- Phoenix tracing is never set up inside `npm run check` or pytest (change 013 guard), so C15 evidence comes only from the live demo run outside `check`.
- Like phase 09, the case writer and the evaluated model belong to the same family; the cases are structured facts made by code, so the risk is low; about 45 test cases give a wide interval and per-category figures are indicative only.
- About 30 and 24 scenarios give wide intervals on scenario success; the gates are 100% because both scenario evaluations are deterministic.

## Assumptions
- The signed delivery note is the customer's statement of what arrived; we accept it as stated, with no check against our own records beyond the form rules.
- Prices are `products.price_eur`, net of VAT and in EUR, at the time of invoicing; the order has no price of its own, so the order side of the match uses the invoice unit price and no price difference can arise.
- Our delivery note says what we shipped; an `extra` incident means we shipped more than the note says, and the customer keeps it and pays for it through the corrective invoice.
- `missing` and `damaged` units are credited and not re-sent, and nothing returns to stock in this phase.
- Invoices are issued for every delivery note without waiting for the signature, as a real distributor bills on shipment; the signed note then triggers the correction.
- Numbers `SINV-NNNN`, `CN-NNNN` and `CI-NNNN` come from their row ids; commands use the invoice number, as phase 09 uses `INV-NNNN`.
- The demo decides by scripted operator steps in their own processes, standing for the person at the command line, as in phases 07 and 09.
- The edited text is the person's text and is sent as written; only an empty text is refused, and the fact check is not applied to it, as in phase 09.
- `npm run check` stays fast: the three new evaluations run or replay in seconds.

## Known limitations
- The signed note is a fixed text form; a free-text or scanned note is not read.
- No VAT, discounts or price lists; amounts are net catalog prices.
- One invoice and one signed note per delivery note; no grouped invoices.
- Incidents change money only: stock, re-delivery and returns are not handled.
- The response and its documents are local files; nothing is sent to the customer and no answer is handled.
- The tone and wording of a draft are not scored; only its facts are, and the person reviews every response before it is written.
- The trace of this phase is local to the owner's machine; the LangSmith trace and experiment come with a later change.
- The phase 08 limitations remain on the purchase side: one invoice and one delivery note per order, fixed tolerances, and the DOC-060 and DOC-112 extraction errors until their change lands.

## Open decisions
None; every decision was resolved on approval.

## Resolved decisions
Owner 2026-10-16: option A for D1, D2, D3, D4, D6, D7 and D9, and option B for D5 and D8; the scope and criteria above follow these choices, and the criteria affected by D5 B and D8 B (C1, C8, C10, C11, C14, C15, C17, C18 and the new C19 to C21) were rewritten on approval.
Real model cost for D5 B approved by the owner within the D9 ceiling.
Each decision keeps its options and trade-offs for the record; the chosen option is marked.
- D1 - How the signed delivery note is read:
  - A (recommended, chosen): a plain text file in a fixed form read by deterministic code, with form failures parked as `needs_review`; no cost, no recording and no new evaluation, and document extraction is already shown in phases 03 and 08.
  - B: free text, `.pdf` or `.xlsx` annotations read with the phase 08 readers and a new model task `signed_note_extraction` with Claude Haiku 4.5, a dataset of at least 60 cases, a baseline and a live cost; more realistic, but a new model evaluation, a live cost (see D9) and a pending LangSmith experiment.
  - C: a structured JSON file of incidents; smallest, but nothing a customer would return.
- D2 - When the invoice is issued:
  - A (recommended, chosen): one invoice per delivery note at issue, before the signature, then corrected by a credit note or corrective invoice; matches the master plan trio and needs the correcting documents the plan names.
  - B: the invoice waits for the signed note and bills the accepted quantities; no correcting documents are ever needed, so the reconciliation and the approval have nothing to correct.
  - C: one grouped invoice per customer per period; closest to many distributors, but breaks the one trio per order the phase 08 matching assumes.
- D3 - How the phase 08 matching is reused for multi-line sales orders:
  - A (recommended, chosen): generalise `classify` to accept several order lines, with phase 08 calling it with its one line and unchanged results; one source of the matching rules, guarded by the phase 08 tests and evaluation.
  - B: call the current `classify` once per order line and merge the rows; phase 08 code untouched, but other order lines would show as extra lines and need filtering, which is fragile.
  - C: a separate sales matcher that copies the rules; no risk to phase 08, but a parallel mechanism that can drift.
- D4 - Which correcting document each difference gives:
  - A (recommended, chosen): a credit note for units invoiced and not accepted and a corrective invoice for units accepted and not invoiced, both referencing the original invoice; follows the master plan wording "credit note or corrective invoice".
  - B: one corrective invoice per incident note with a signed total, positive or negative, as Spanish "factura rectificativa" practice allows; one document type, but the plan's credit note disappears.
  - C: credit notes only, with `extra` units reported in the response and not billed; simplest, but the company gives away extra goods.
- D5 - Who drafts the response text:
  - A (recommended, not chosen): a fixed English template filled by code; no cost, deterministic, and model drafting with a fact check is already shown in phase 09.
  - B (chosen): Claude Haiku 4.5 drafts the body with the phase 09 fact check generalised and template fallback, a new `response_drafting` model evaluation of at least 60 cases with a baseline and McNemar gate; shows reuse of the model pattern, but adds a live cost (see D9), a recording and a pending LangSmith experiment.
- D6 - Evaluation shape:
  - A (recommended, chosen): two deterministic scenario evaluations, `sales_reconciliation` and `customer_response`, with 100% and zero critical error gates; mirrors phases 08 and 09 and keeps `check` free.
  - B: one combined deterministic evaluation of at least 40 scenarios; fewer files, but matching and approval failures are reported together.
- D7 - Commands and demo shape:
  - A (recommended, chosen): a new `sales` group (`invoice`, `scan`, `responses`, `approve`, `reject`, `list`, `show`) and a new `sales-demo` built on the `receipt-demo` steps; earlier demos and their tests stay unchanged.
  - B: the same commands, and `receipt-demo` extended with the sales steps; one demo for serving and billing, but the phase 10 demo test changes and the demo gets long.
  - C: the commands inside the `reconcile` group; fewer groups, but mixes supplier and customer references in one place.
- D8 - Trace evidence:
  - A (recommended, not chosen): C15 as a LangSmith trace criterion pending until the quota resets about 2026-11-05, as phases 02 to 10, with Phoenix used locally during development only; follows the strategic decision of 2026-10-15.
  - B (chosen): C15 met with a local Phoenix trace and the LangSmith trace left to the catch-up change; closes at delivery, but departs from "LangSmith stays for the frozen trace criteria".
- D9 - Live model cost ceiling, applies since D5 B is chosen:
  - A (recommended, chosen): 5 USD for the phase, a limit and not a budget, as phase 09.
  - B: 2 USD.
