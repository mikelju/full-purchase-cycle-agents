# Phase 08 - Reconciliation, detection: specification

Status: draft
Approved by the owner: pending
Master plan: `../0_plan_maestro.md`

## Goal
A new reconciliation module reads the supplier documents that arrive after a purchase order: the invoice and the delivery note.
It extracts their lines with Claude Haiku 4.5, matches them against the `purchase_orders` row of phase 07 with deterministic code, and classifies every difference as price, quantity, extra line or missing line.
Each invoice ends with one stored result: `matched`, or `discrepancies` with one row per difference and its type.
One command runs the module end to end over a folder of simulated supplier documents, in replay with no key, no network and no cost.
It is needed now because phase 07 issues purchase orders with the supplier, SKU, quantity, unit price and expected delivery, and phase 09 needs classified differences to draft a claim.
It shows the master plan capability "structured evaluation" on a three-way match, and it reuses the document reading of phase 03.
Drafting a claim, human approval and the orchestrator are phases 09 and 10 and stay out.

## Scope
Each decision id points to "Open decisions"; the scope is written for the recommended option of each.
In:
- Supplier documents (D2): simulated files in an inbox folder, `.txt`, text-based `.pdf` and `.xlsx`, one document per file, read with the attachment readers of `email_order.py` (`pdf_text`, `xlsx_text`); the file name carries nothing, the content states whether it is an invoice or a delivery note.
- Document extraction (D1, D3): a new model task `supplier_document_extraction` through the existing client in live, record and replay modes with Claude Haiku 4.5, one call per document.
  - Extracted fields: document kind (invoice or delivery note), supplier document number, purchase order reference as written, issue date, currency, and per line the SKU or product text as written, quantity, unit price (invoice only) and line total (invoice only).
  - Deterministic checks in code after extraction: the kind is known, the document has at least one line, quantities are positive numbers, and the currency is EUR; a document that fails the schema check after the client retries is stored as `extraction_failed` with a `failures` row with status `needs_review` and step `document_extraction`; the other documents go on.
- Storage: new tables `supplier_documents` (one row per file: kind, number, purchase order reference, supplier code, issue date, currency, source file, content hash, extraction status), `document_lines` (one row per line: document, line number, SKU, quantity, unit price), `reconciliations` (one row per invoice: purchase order, result, reconciliation time) and `discrepancies` (one row per difference: reconciliation, SKU, type, expected and found values, amount).
- Linking a document to a purchase order (D4): by the purchase order number written in the document (`PO-NNNN`); a document with no readable reference, an unknown number, or a supplier that differs from the order's supplier is stored as `unlinked` with its reason and creates no reconciliation.
- Matching and classification (D5, D6), deterministic, no model call, one reconciliation per invoice:
  - Lines are matched by SKU; product text with no catalog SKU is resolved with the existing catalog matching of phase 02 and, if still unresolved, becomes an extra line.
  - Price difference: the invoice unit price differs from the purchase order unit price by more than the tolerance; the difference is stored per unit and in total.
  - Quantity difference: the invoice quantity differs from the ordered quantity, or from the delivered quantity on the delivery note when one exists (invoiced above delivered is a difference even if it equals the order).
  - Extra line: an invoice line whose SKU is not on the purchase order.
  - Missing line: a purchase order SKU absent from the invoice.
  - An invoice line may carry several types at once (price and quantity); each is a separate `discrepancies` row.
  - Three-way rule: the invoice is compared with the purchase order for price and lines, and with the delivery note and the purchase order for quantity; when no delivery note exists yet the result is `awaiting_delivery_note` and no quantity check against delivery is made.
  - Result per invoice: `matched` (no difference), `discrepancies` (at least one), `awaiting_delivery_note`, or `unlinked` (no reconciliation row); the reconciliation time comes from the simulated clock.
- Reconciliation graph, a new LangGraph subgraph `reconcile` (D7): `read` (documents from the folder), `extract`, `link`, `match`, `store`; no pause and no interrupt.
- Commands (D8): `reconcile scan FOLDER` processes every new document in the folder (idempotent by file content hash), `reconcile list` shows each invoice with its purchase order, result and discrepancy counts by type, `reconcile show INV-NNNN` prints the classified differences of one invoice; `reconcile-demo` runs the whole module.
- Sample documents: a versioned folder `examples/invoices/` with a handful of invoices and delivery notes in the three formats covering the matched case and each difference type, written for the purchase orders that the demo database holds.
- Two new evaluations inside `npm run check` next to the twelve existing ones: `document_extraction` in replay and the deterministic `reconciliation_matching` (D9).
- README sections for the module, the commands, the tolerance, the demo, the evaluations and the known limitations.

Out:
- Claim drafting, supplier emails about differences and any human approval, which are phase 09.
- The orchestrator and any link from the quotes graph to this one, which are phase 10.
- Payment, accounting entries, credit notes, tax checks beyond the stated currency, and invoice total arithmetic beyond a reported flag.
- Partial deliveries over several delivery notes, several invoices for one order and purchase orders with more than one line (D4).
- Stock updates from the delivery note and changes to customer orders.
- Real email receiving, OCR of scanned PDFs and image attachments.
- Changes to the phase 01 to 07 prompts, recordings, datasets, baselines and gates.

### Evaluation design
- `document_extraction` (D1, D10), built with the phase 01 method (seeded planning script, agent-written texts, automatic validation, clean-context second-pass review):
  - At least 100 documents, written as `.txt` with a share rendered to PDF and Excel for the format check, in ten categories of at least 10: clean invoice, delivery note, price per pack against per unit, several lines, quantity in another sale unit, VAT lines and totals that must not become product lines, chatty header and footer text, order reference written in different ways, extra freight or discount line, and messy layouts (columns, wrapped lines).
  - Each document is labelled with the expected kind, reference, currency and lines; documents use real catalog SKUs and the sale units of the catalog.
  - ASCII English text; fixed stratified split of about 25% development and 75% test, as in phases 03, 05 and 07; only the development split tunes the instructions and the published figures and gates come from the test split.
- Metrics for `document_extraction`, all deterministic: kind accuracy, reference accuracy, line recall and line precision by SKU, quantity accuracy, unit price accuracy (within half a cent) over the lines expected, and document exact match.
  - Critical errors: a line with a wrong quantity or unit price, or a missing or invented line, since it would create a false discrepancy or hide a real one.
  - Every metric with a 95% Wilson interval on the test split, globally and per category, with its counting unit, and the Wilson upper bound for a zero-event rate, as phase 05 set.
- Gates for `document_extraction`:
  - Absolute gates in the test split on document exact match and unit price accuracy of at least 95% each, if the baseline measured in this phase with the final tuned instructions reaches them; otherwise execution stops and the owner decides, recorded as a deviation.
  - Tuning ends before the test split is recorded; the instructions do not change after the test baseline is measured.
  - Regression gate by exact McNemar test per document against the stored baseline, failing on a significant drop (p < 0.05).
  - Critical errors reported with their rate and Wilson upper bound and a target of 0 fixed before measuring, reported as met or not.
- `reconciliation_matching` (D5, D9): at least 40 hand-built scripted cases, versioned with the expected result and the expected list of discrepancies, run through the matching and the CLI functions on a temporary database, with the extracted documents given by a test double so no model is called.
  - Coverage: at least 3 each for a perfect match, a price difference over and under the tolerance, quantity above and below the order, invoiced above delivered with the order met, extra line, missing line, price and quantity on one line, several types in one invoice, no delivery note yet, unknown order, wrong supplier, a duplicate scan and a restart between scan and show.
  - Grader: deterministic, reads the database and the printed result; a case passes when the result and the set of (SKU, type, amount) discrepancies equal the expected ones.
  - Critical errors per case: a difference not reported (false match); a difference reported that does not exist; a wrong type; two reconciliations for one invoice; a reconciliation for an unlinked document; a model call during matching.
  - Gates: case success 100% and critical errors 0, since the module is deterministic, with the phase 05 report columns.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the fourteen evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | `seed` creates the tables of this phase; seeding twice gives the same rows; the other tables and their rows are unchanged | Pytest tests on a temporary database |
| C3 | Extraction makes one call per document through the existing client in live, record and replay modes and stores one `supplier_documents` row with its `document_lines`; the text, PDF and Excel versions of the same document give the same stored lines; an answer that does not fit the schema stores `extraction_failed` with a `failures` row with status `needs_review` and step `document_extraction`, and the other documents are still processed | Pytest tests with recorded valid and invalid answers counting model calls and rows, over the three formats |
| C4 | A document is linked to a purchase order by the written `PO-NNNN`; a missing or unknown reference or a different supplier stores it as `unlinked` with its reason and no reconciliation | Pytest tests per case reading rows |
| C5 | An invoice that equals the purchase order and the delivery note is `matched` with no `discrepancies` row | Pytest test with the expected result |
| C6 | Each difference is classified with its type: price (with the per-unit and total amount), quantity (against the order or the delivery note), extra line and missing line; a line can carry price and quantity together; a price within the tolerance is not a difference | Pytest tests per type and at the tolerance boundary reading the `discrepancies` rows |
| C7 | An invoice with no delivery note yet is `awaiting_delivery_note`; a later scan that brings the delivery note completes the same reconciliation instead of creating a second one | Pytest test reading rows before and after the second scan |
| C8 | Matching and classification call no model and are deterministic: the same stored documents give the same rows and printed output on a second run, and a second `scan` of the same folder adds no document, reconciliation or discrepancy and calls no model | Pytest tests counting model client calls and rows before and after |
| C9 | `reconcile list` and `reconcile show INV-NNNN` print the result, the purchase order and each discrepancy with type, SKU and amounts; a restart of the process between `scan` and `show` prints the same | Pytest tests, one with the steps in separate subprocesses |
| C10 | The versioned `document_extraction` dataset holds at least 100 documents in the ten categories of "Evaluation design", at least 10 per category, with a fixed split, and every item passes the automatic validation; re-running the planning script with the same seed gives the same plan | Pytest tests over the dataset and plan files; generator output saved as evidence |
| C11 | `npm run eval` in replay mode reports `document_extraction` with the metrics of "Evaluation design", 95% Wilson intervals on the test split globally and per category, critical errors with their Wilson upper bound, and exits non-zero when an absolute or regression gate fails | Run the command; pytest tests that force each new gate to fail prove the non-zero exit |
| C12 | The baseline of Claude Haiku 4.5 on the `document_extraction` test split is measured with the final instructions, stored with its per-document results, and the gates are set by the rule in "Evaluation design" | Stored baseline file and the live run report, saved as evidence |
| C13 | `reconciliation_matching` holds at least 40 cases with the coverage of "Evaluation design"; `npm run eval` reports case success with its Wilson interval and critical errors per case, and exits non-zero when success is below 100% or any critical error is counted | Pytest tests over the case files and tests that inject each critical error and prove a non-zero exit; replay report saved as evidence |
| C14 | `reconcile-demo` runs the sample folder from the scan to the stored results with each step in its own process and prints the extracted documents, each result and the classified differences; it ends with at least one `matched` invoice, one invoice per difference type, one `awaiting_delivery_note` and one `unlinked` document; it needs no key and no network | Run with the network blocked through a pytest inside `check`; output saved as evidence |
| C15 | With the LangSmith variables set, a live `reconcile-demo` appears in LangSmith as one trace per document with one span per node, and a live run of `document_extraction` is logged as an experiment against its uploaded dataset splits | Trace and experiment links, and the owner's screenshot, as evidence; blocked by the trace quota until about 2026-11-05 |
| C16 | Phase 01 to 07 behaviour is unchanged: their tests pass unchanged; the twelve existing evaluations replay with every gate PASS; `git diff --stat` on their recordings, datasets and baselines is empty; `orders-demo` and `quotes-demo` print the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C17 | No secret is versioned: the new recordings, sample documents, dataset and case files contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C18 | The README explains in English the module, the matching rules and tolerance, the commands, the demo in replay and live mode, how both evaluations were built and run, and the known limitations below | Follow the new README sections in a clean clone |

## Constraints and risks
- No new dependency is expected: `pypdf` and `openpyxl` are already used by phase 03, the model client serves record and replay, and the matching is plain Python.
- Live model cost ceiling of 5 USD for the phase (D11), a limit and not a budget; the expected cost is under 1 USD with prompt caching, about 100 calls over the tuning rounds on the development split, about 75 for the test record and a few for the demo.
- Live runs need the owner's Anthropic and LangSmith keys in `.env`; the agent never reads that file.
- The LangSmith trace quota is expected to reset around 2026-11-05; C15 is blocked until then and is delivered with the pending catch-up criteria of earlier phases.
- The most costly extraction errors are a price per pack read as a price per unit and a VAT or freight line read as a product line; each has its own dataset category and counts as a critical error.
- A false match (a real difference classified as matched) is the worst matching error, since phase 09 would never claim it; the matching gate demands zero.
- The delivery note is small and often has no prices, so the quantity check against delivery depends on the SKU being readable in it.
- Like earlier phases, the text writer and the evaluated model belong to the same family, so extraction results may be optimistic.
- Phase 07 tables are not altered; this phase only reads `purchase_orders`, `suppliers` and `products`.
- `seed` of a database made by phase 07 code needs the new tables; `CREATE TABLE IF NOT EXISTS` adds them without touching the old ones.

## Assumptions
- Each purchase order has one line, as phase 07 creates it, so a missing line means the order SKU is absent from the invoice; extra lines are the multi-line cases.
- One invoice and at most one delivery note per purchase order; the reconciliation id is `INV-NNNN` from the `supplier_documents` id of the invoice.
- Prices are compared before VAT in EUR; an invoice that states VAT separately is read net, as in phase 07.
- The default price tolerance is 0.01 EUR per unit and the quantity tolerance is 0 (D6).
- Sample documents are plain generated files; no scanned or image PDF is read.
- The demo uses a seeded database that already holds the purchase orders the sample documents refer to, instead of re-running the quotes module.

## Known limitations
- Documents come from simulated files; nothing is received or sent over a network.
- One invoice and one delivery note per order; partial deliveries and split invoices are not matched.
- No credit notes, payment terms, tax rules, freight allocation or multi-currency handling.
- Differences are only detected and stored; no claim and no approval exist until phase 09.
- The tolerance is a fixed number, not learned per supplier.

## Open decisions
Questions the owner must resolve before approval; empty on approval.
The recommended option is marked; the scope and criteria above are written for it.

### D1 - Who reads the documents
- A (recommended): Claude Haiku 4.5 extracts lines from text, PDF and Excel, as in phases 03 and 07. Trade-off: shows the model on real layouts and fits the existing client, but needs a dataset and a baseline.
- B: documents are structured JSON, no model. Trade-off: cheap and fast, but no extraction challenge and a weaker showcase.
- C: a rule-based parser for fixed templates per supplier. Trade-off: deterministic, but brittle and hides the agent value.

### D2 - Where the documents come from
- A (recommended): files in an inbox folder, read with the phase 03 readers. Trade-off: minimal, reproducible, no email plumbing.
- B: `.eml` messages with the document as an attachment. Trade-off: closer to reality, but adds parsing already proven in phase 03 and bigger fixtures.

### D3 - Delivery note: separate document or field
- A (recommended): a separate document of its own kind, extracted by the same task. Trade-off: true three-way match and one extraction path, a bit more dataset.
- B: delivery quantity entered as a field by the warehouse through a CLI command. Trade-off: no extraction for it, but a manual step and a less realistic flow.
- C: skip the delivery note and match the invoice against the order only. Trade-off: smallest scope, but not the three-way match of the master plan row.

### D4 - Partial deliveries and several invoices per order
- A (recommended): out of scope; one invoice and one delivery note per order, a second one is stored as `unlinked` with reason `duplicate`. Trade-off: keeps the matching exact and small.
- B: sum several delivery notes and invoices per order. Trade-off: realistic, but adds states and many more cases.

### D5 - Line with price and quantity differences
- A (recommended): one `discrepancies` row per type, so a line can have two. Trade-off: simple for phase 09 to claim each.
- B: one row per line with a list of types. Trade-off: fewer rows, harder to count and grade.

### D6 - Tolerances
- A (recommended): price 0.01 EUR per unit absolute, quantity 0, both constants in code. Trade-off: strict and easy to explain.
- B: price 1% relative, quantity 0. Trade-off: closer to business practice, but hides small real overcharges on cheap items.
- C: configurable per supplier. Trade-off: flexible, but new configuration nobody asked for.

### D7 - Graph shape
- A (recommended): one subgraph `reconcile` with no pause, since claim approval belongs to phase 09. Trade-off: minimal.
- B: add an interrupt for a person to confirm each result. Trade-off: more human-in-the-loop, but duplicates phase 09.

### D8 - Demo and commands
- A (recommended): `reconcile scan`, `list`, `show` and `reconcile-demo` over versioned sample documents, each step in its own process. Trade-off: follows `quotes-demo`.
- B: only a demo command. Trade-off: less to build, less to explore.

### D9 - Evaluations
- A (recommended): `document_extraction` in replay plus a deterministic `reconciliation_matching` with at least 40 cases. Trade-off: mirrors phases 05 and 07, two new gates.
- B: only the matching evaluation, extraction covered by unit tests. Trade-off: cheaper, but no extraction metrics for the CV.

### D10 - Dataset size and owner audit
- A (recommended): at least 100 documents, 25% development and 75% test, no owner audit, as in phase 07. Trade-off: no owner time; labels follow from the planning script plus a clean-context review.
- B: 100 documents plus an owner audit of 20 documents as a new criterion. Trade-off: about 10 minutes of owner time.
- C: 60 documents. Trade-off: cheaper, intervals too wide per category.

### D11 - Cost ceiling
- A (recommended): 5 USD for the phase, as in phases 06 and 07.
- B: 3 USD. Trade-off: enough for the expected cost, with less margin for retuning.
