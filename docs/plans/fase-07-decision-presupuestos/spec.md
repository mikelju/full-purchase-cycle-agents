# Phase 07 - Quotes, decision: specification

Status: approved
Approved by the owner: 2026-10-13 (in chat, D11 option A)
Master plan: `../0_plan_maestro.md`

## Goal
The quotes module reads the supplier replies that phase 06 attached to each quote request, extracts one offer per reply with Claude Haiku 4.5, compares the offers with a fixed rule and recommends one with its reason.
A person approves the recommendation, chooses another offer or rejects the request from the command line, after a pause with saved state, and an approval issues one purchase order and writes it as an email to the supplier.
The purchase order holds what phase 08 needs to match an invoice and a delivery note: supplier, SKU, quantity, unit price, currency and expected delivery date.
One command runs the whole quotes module end to end, from the out-of-stock scan to the purchase order, in replay with no key, no network and no cost.
It is needed now because phase 06 leaves requests `ready_for_decision` with unread replies, and phase 08 depends on purchase orders.
It shows the master plan capabilities "human-in-the-loop" and "structured evaluation" in the quotes module, next to the persistent wait of phase 06.

## Scope
Each decision id points to "Owner decisions (2026-10-13)".
In:
- Offer extraction (D1):
  - A new model task, offer extraction, through the existing client in live, record and replay modes with Claude Haiku 4.5, one call per supplier reply, with the request SKU, product name, sale unit and quantity in the prompt and the reply body as the message.
  - Extracted fields: whether the reply makes an offer or declines, quantity offered in sale units, unit price per sale unit, currency, whether the price includes VAT and the VAT rate when stated, lead time as a number of days and whether they are working days, and the price text as written.
  - The model converts a price per pack, per box or per piece into a price per sale unit of the product, as phase 01 converts quantities into sale units.
  - Deterministic usability check in code after extraction: an offer is usable when it offers, has a positive quantity, a positive price, currency EUR, a lead time, and a net price that can be computed; any other offer is stored as unusable with its reason (declined, no price, currency, no lead time, VAT rate missing, extraction failed).
  - A reply whose extraction fails the schema check after the client retries is stored as unusable with reason `extraction_failed` and writes a `failures` row with status `needs_review` and step `offer_extraction`; the other replies of the request go on.
- Offers storage: a new `quote_offers` table, one row per replied supplier of a request, with the extracted fields, the net unit price, usable or not with its reason, and the reply file.
- Comparison and recommendation (D2):
  - Deterministic, no model call.
  - Unusable offers are discarded from the recommendation and shown with their reason.
  - Offers covering the full requested quantity come first; among them the lowest total price, which is the net unit price times the requested quantity; ties go to the shorter lead time in calendar days, then to the lower supplier code.
  - A partial offer is recommended only when no usable offer covers the full quantity; among partial offers the same order applies, with the total over the offered quantity; no order is split between suppliers.
  - The recommendation carries a reason text from a fixed template that names the rule that decided it and the compared totals.
- Decision graph, a new LangGraph subgraph `quote_decision` with the SQLite checkpointer and its own thread per request (D3):
  - `extract`: extracts and stores the offers of every replied supplier.
  - `compare`: picks the recommendation, or none when no offer is usable.
  - `approve`: pauses with `interrupt` until the human answers approve, choose another supplier's offer or reject.
  - `issue`: writes the purchase order and its email; `close`: closes a rejected request.
- Quote request statuses: `quote_requests` gains `awaiting_approval`, `ordered` and `rejected` next to the phase 06 statuses.
- Commands (D3, D5):
  - `quotes decide` starts the decision of every `ready_for_decision` request with no decision yet, prints per request the offers with their usability, the recommendation and its reason, and then lists every `no_offers` request as waiting for a person, with no purchase order and no new request.
  - `quotes approve QR-NNNN` approves the recommendation; `quotes approve QR-NNNN --supplier SUP-NNN` chooses that supplier's usable offer instead; `quotes reject QR-NNNN` closes the request as `rejected` with no purchase order.
  - A request with no usable offer pauses with no recommendation; only `reject` is accepted for it.
- Purchase orders (D4):
  - A new `purchase_orders` table, one row and one line per order since a request covers one SKU: number `PO-NNNN`, request, supplier, SKU, quantity, unit price before VAT, currency, total, expected delivery date, issue time and status `issued`.
  - The quantity is the requested quantity for a full offer and the offered quantity for a partial one.
  - The issue time comes from the simulated clock, and the expected delivery date is the issue date plus the lead time, counting working days as Monday to Friday.
  - One purchase order email per order as an `.eml` file in the supplier outbox, from a fixed English template, addressed to the supplier with the order number and request reference in the subject and the SKU, product name, sale unit, quantity, unit price, currency, total and expected delivery in the body; nothing is sent.
  - The request moves to `ordered`; stock and customer order statuses are not changed.
- `quotes list` also shows the new statuses, the recommended supplier and the purchase order number.
- Full module demo: `quotes-demo` gains the decision steps after the phase 06 steps, each in its own process: decide, the operator's approval and the issued purchase order (D9).
- Two new evaluations inside `npm run check` next to the ten existing ones: `offer_extraction` in replay and the deterministic `quote_decision` (D7).
- First plan increment: the stale statuses in the README and the master plan are aligned, and master plan row 07 is set to in progress (D10).
- README sections for the decision step, the commands, the purchase order, the demo, the evaluations and the known limitations.

Out:
- Splitting an order between suppliers, counter-offers, negotiation or a second round of quotes after a rejection.
- Automatic re-quote of `no_offers` or `rejected` requests.
- Currency conversion; offers in another currency are shown but not usable.
- Minimum order quantities, volume discounts beyond the price that applies to the requested quantity, payment terms and shipping costs.
- Goods receipt, stock updates and customer order status changes: phase 08 brings the delivery note.
- Real email sending or receiving.
- Starting the decision from the orders or the quote request graphs; the orchestrator of phase 10 composes the modules.
- Changes to the phase 01 to 06 prompts, recordings, datasets, baselines and gates, and to the phase 06 graph.

### Evaluation design
- `offer_extraction` (D7), built with the phase 01 method (seeded planning script, agent-written texts, automatic validation, clean-context second-pass review):
  - At least 100 supplier replies in ten categories of at least 10: plain full offer, price per pack or per piece rather than per sale unit, partial quantity, decline, missing price, other currency, VAT included or mentioned, lead time in weeks or working days, several price tiers, and chatty replies with unrelated numbers (references, phone numbers, dates).
  - Each reply is labelled with the expected fields and the expected usability and reason; requests use real catalog SKUs and quantities.
  - ASCII English text, consistent with the rest of the data.
  - Fixed stratified split of about 25% development and 75% test, as in phases 03 and 05: the development split is the only data used to tune the instructions, and published figures and gates come from the test split.
- Metrics and graders for `offer_extraction`, all deterministic:
  - Per field: offer or decline accuracy, quantity accuracy, unit price accuracy (within half a cent), currency accuracy, VAT accuracy and lead time accuracy, each over the replies where the field is expected.
  - Usability accuracy: share of replies whose computed usability and reason equal the expected ones.
  - Reply exact match: share of replies with every expected field right.
  - Critical errors: a reply judged usable with a wrong unit price, quantity or currency, since it would reach a purchase order.
  - Every metric with a 95% Wilson interval on the test split, globally and per category, with its counting unit, and the Wilson upper bound for a zero-event rate, as phase 05 set.
- Gates for `offer_extraction`:
  - Absolute gates in the test split: reply exact match and unit price accuracy at least 95% each, if the baseline measured in this phase with the final tuned instructions reaches them; otherwise execution stops and the owner decides, recorded as a deviation.
  - Tuning ends before the test split is recorded; the instructions do not change after the test baseline is measured.
  - Regression gate: exact McNemar test per reply against the stored baseline, failing on a significant drop (p < 0.05).
  - Critical errors are reported with their rate and Wilson upper bound and a target of 0 fixed before measuring, reported as met or not.
- `quote_decision` (D7): at least 24 hand-built scripted scenarios, versioned with expected final states, run through the CLI functions on a temporary database, with the extracted offers given by a test double so no model is called.
  - Coverage: at least 3 each for a single full offer, a cheaper partial offer against a full one, a price tie decided by lead time, a price and lead time tie decided by supplier code, only partial offers, no usable offer, approve, choose another offer, a refused choice (unusable offer or a supplier with no offer), reject, approve twice and a process restart between decide and approve.
  - Grader: deterministic, reads the database, the supplier outbox, the `failures` table and the printed recommendation.
  - Critical errors per scenario: more than one purchase order for a request; a purchase order for a rejected, `no_offers` or unapproved request; a purchase order from an unusable offer; a purchase order whose supplier, quantity, unit price or expected delivery differs from the chosen offer; a recommendation that differs from the rule; a request status that does not match its purchase order; a model call on a re-run or a resumption.
  - Gates: scenario success 100% and critical errors 0, since the module is deterministic, with the phase 05 report columns.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the twelve evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | `seed` creates the `quote_offers` and `purchase_orders` tables and `quote_requests` accepts the statuses `awaiting_approval`, `ordered` and `rejected`; seeding twice gives the same rows; the other tables and their rows are unchanged | Pytest tests on a temporary database |
| C3 | Offer extraction makes one call per replied supplier through the existing client in live, record and replay modes and stores one `quote_offers` row per reply with the extracted fields, the net unit price, usability and reason; a decline, a missing price, another currency, a missing lead time and a VAT-included price with no rate are stored as unusable with that reason; an answer that does not fit the schema stores the offer as `extraction_failed` with a `failures` row with status `needs_review` and step `offer_extraction`, and the other replies are still extracted | Pytest tests with recorded valid and invalid answers counting model calls, `quote_offers` rows and `failures` rows |
| C4 | The recommendation follows the rule of D2: unusable offers are left out; a usable full offer always beats a partial one; among full offers the lowest total wins, then the shorter lead time, then the lower supplier code; a partial offer is recommended only when no full offer is usable; the reason text names the deciding rule and the totals; no model is called | Pytest tests per rule over fixed offer sets, counting model client calls |
| C5 | `quotes decide` starts one decision per `ready_for_decision` request, prints its offers, recommendation and reason, and leaves it `awaiting_approval` with the thread paused in an `interrupt` and its state in the SQLite checkpoint; it lists every `no_offers` request as waiting for a person and writes no purchase order for it; a second `decide` starts nothing and calls no model | Pytest tests on a temporary database reading rows, printed output, checkpoint state and model client calls |
| C6 | `quotes approve QR-NNNN` issues one purchase order with the recommended supplier, the SKU, the requested quantity (the offered one for a partial offer), the net unit price, currency, total, an expected delivery date equal to the simulated issue date plus the lead time with working days counted Monday to Friday, and status `issued`; it writes one `.eml` to the supplier outbox with the order number and request reference in the subject and the order fields in the body; the request becomes `ordered`; stock and customer orders are unchanged | Pytest tests comparing the `purchase_orders` row, the outbox file and the untouched tables with expected values |
| C7 | `quotes approve QR-NNNN --supplier SUP-NNN` issues the order from that supplier's usable offer; a supplier with no offer for the request or with an unusable offer is refused with a reason, changes nothing and leaves the request waiting for approval; a request with no usable offer accepts only `reject` | Pytest tests per case counting rows and outbox files and reading the checkpoint state |
| C8 | `quotes reject QR-NNNN` closes the request as `rejected` with no purchase order and no email; any command on a request that is not `awaiting_approval`, including a second approve, fails with a message and leaves one purchase order at most and one email at most | Pytest tests per case counting rows and outbox files before and after |
| C9 | A decision survives separate processes: decide and approve each run in a new process against the same database and checkpoint files, and the final rows and outbox equal the same steps run in one process; resuming does not call the model again | Pytest test that runs each step as a subprocess and compares with an in-process run, counting recorded calls |
| C10 | `quotes list` shows every request with its status, including the new ones, the recommended supplier and the purchase order number | Pytest test on a temporary database with requests in each status |
| C11 | The versioned `offer_extraction` dataset holds at least 100 replies in the ten categories of "Evaluation design", at least 10 per category, with a fixed stratified split, and every item passes the automatic validation; re-running the planning script with the same seed gives the same plan | Pytest tests over the dataset and plan files; generator output saved as evidence |
| C12 | `npm run eval` in replay mode reports `offer_extraction` with the metrics of "Evaluation design", 95% Wilson intervals on the test split globally and per category, critical errors with their Wilson upper bound, and exits non-zero when an absolute or regression gate fails | Run the command; pytest tests that force each new gate to fail prove the non-zero exit |
| C13 | The baseline of Claude Haiku 4.5 on the `offer_extraction` test split is measured with the final instructions, stored with its per-reply results, and the gates are set by the rule in "Evaluation design" | Stored baseline file and the live run report, saved as evidence |
| C14 | `quote_decision` holds at least 24 scenarios with the coverage of "Evaluation design"; `npm run eval` reports scenario success with its Wilson interval and critical errors per scenario, and exits non-zero when success is below 100% or any critical error is counted | Pytest tests over the scenario files and tests that inject each critical error and prove a non-zero exit; replay report saved as evidence |
| C15 | `quotes-demo` runs the sample timeline from the scan to the purchase order with each step in its own process and prints, for the decision steps, the extracted offers, the recommendation and reason, the approval and the issued order with its email; it ends with one request `ordered` with its purchase order and one `no_offers` listed for a person; the phase 06 steps print as before; it needs no key and no network | Run with the network blocked through a pytest inside `check`; output saved as evidence |
| C16 | With the LangSmith variables set, a live `quotes-demo` decision appears in LangSmith as one thread with the pause and the resumption and one span per node, and a live run of `offer_extraction` is logged as an experiment against its uploaded dataset splits | Trace and experiment links, and the owner's screenshot, as evidence; blocked by the trace quota until about 2026-11-05 (D8) |
| C17 | Phase 01 to 06 behaviour is unchanged: their tests pass unchanged apart from the phase 06 `quotes-demo` test, which gains the new end state; the ten existing evaluations replay with every gate PASS; `git diff --stat` on their recordings, datasets and baselines is empty; `orders-demo` prints the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C18 | No secret is versioned: the new recordings, sample replies, outbox examples, dataset and scenario files contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C19 | The README explains in English the decision step, the comparison rule, the `decide`, `approve` and `reject` commands, the purchase order, the extended `quotes-demo` in replay and live mode, how both evaluations were built and run, and the known limitations below; the README and master plan statuses of phases 02 to 06 match, and row 07 shows the phase status | Follow the new README sections in a clean clone; compare the README status lines with the master plan table |

## Constraints and risks
- No new dependency is expected: `interrupt`, `Command` and the SQLite checkpointer are in `langgraph`, the model client already serves record and replay, and the emails use the standard library `email` package.
- The live model cost ceiling for the phase is 5 USD (D6), a limit and not a budget; the expected cost is under 1 USD with prompt caching: about 75 calls for up to three tuning rounds on the development split (about 25 replies), about 75 calls for the test split record and a few calls for the demo.
- Live runs need the owner's Anthropic and LangSmith keys in `.env`; the agent never reads that file.
- The LangSmith trace quota is expected to reset around 2026-11-05; until then C16 is blocked, and it is delivered with phases 02 C13, 03 C13, 04 C15 and 05 C17 through the catch-up change after the reset (D8).
- `quote_requests` keeps its phase 06 columns and gets a wider status check; SQLite cannot change a check constraint in place, and `CREATE TABLE IF NOT EXISTS` leaves an old file as it was, so a local database made by phase 06 code is re-created by seeding a new file.
- LangGraph re-runs an interrupted node from its start on resumption, so `extract`, `issue` and `close` must be idempotent: stored offers are read back instead of calling the model again, and a unique purchase order per request and keyed outbox file names prevent duplicates.
- A price per pack against a price per sale unit is the most likely extraction error and the most costly one, since it changes the order total by the pack size; the dataset has its own category for it and the critical error counts it.
- Like earlier phases, the text writer and the evaluated model belong to the same family, so extraction results may be optimistic.
- About 75 test replies give a wide interval at reply level; the per-category figures are indicative only.

## Assumptions
- One decision thread per request with thread id `decision-QR-NNNN`, separate from the phase 06 thread, so the phase 06 graph does not change.
- Prices are compared before VAT; a price stated with VAT included and a stated rate is converted to a net price in code.
- Working days in a lead time count Monday to Friday with no holidays; weeks count as 7 calendar days; the tie-break compares lead times in calendar days from the simulated issue date.
- An offer with no lead time is unusable, because the purchase order needs an expected delivery date.
- Only the price tier that applies to the requested quantity counts in a reply with several tiers.
- The choose-another path accepts any usable offer of the request, full or partial; the person sees the recommendation and its reason first.
- The purchase order and request references stay in English templates like the phase 06 emails; the order number comes from the `purchase_orders` id.
- The demo approves by a scripted operator step in its own process, standing for the person at the command line.
- `npm run check` stays fast: both new evaluations replay in seconds.

## Known limitations
- Offers come from simulated reply files; nothing is sent or received over a network.
- No currency conversion, minimum order quantity, shipping cost or payment terms are modelled.
- No order is split, and a partial order leaves the rest of the shortfall without a new request, because phase 06 nets every earlier request out of the shortfall whatever its status; the same holds for `rejected` and `no_offers` requests.
- Stock and customer orders are not updated when a purchase order is issued; the delivery note of phase 08 brings the goods in.
- Working days ignore holidays, as the simulated clock does.

## Open decisions
Questions the owner must resolve before approval; empty on approval.
The recommended option is marked; the scope and criteria above are written for it.

### D11 - Owner audit of the `offer_extraction` labels
- A (recommended): no owner audit; the labels follow from hand-written values in the planning script and pass the automatic validation and a clean-context second-pass review. Trade-off: no owner time, but no independent check of label quality, unlike phases 03 to 05.
- B: an owner audit of 20 random replies, at most 1 wrong label, with its Wilson interval, as a new criterion. Trade-off: about 10 minutes of owner time and a criterion blocked until the audit is done, in line with earlier model datasets.

## Owner decisions (2026-10-13)
The owner chose D1 explicitly and accepted the recommended option for everything else on 2026-10-13.
- D11 - Owner audit of the `offer_extraction` labels: option A, no owner audit (approved with the spec on 2026-10-13).
- D1 - Offer extraction: Claude Haiku 4.5 over each reply body with record and replay as in phases 02 and 03; tests and evaluations run in replay with no key, and live mode records.
- D2 - Comparison: deterministic, no model; unusable offers discarded and flagged; full-quantity offers first, then the lowest total, then the shorter lead time, then the supplier code; partial offers shown and chosen only when no full offer exists; no split orders.
- D3 - Human approval: a LangGraph `interrupt` as in phase 04, resumed from the command line with approve, reject or choose another supplier's offer; reject closes the request as `rejected` with no purchase order and no re-quote; the recommendation is shown with its reason.
- D4 - Purchase order: a new `purchase_orders` table with one line per order, supplier, SKU, quantity, unit price, currency, expected delivery from the simulated clock plus lead time and status `issued`, plus an outgoing `.eml` in the supplier outbox that is not sent; the request becomes `ordered`; no stock update and no customer order status change.
- D5 - `no_offers` requests: no purchase order; they stay listed for a person by `quotes decide`; no automatic re-quote.
- D6 - Cost: live model cost ceiling of 5 USD for the phase, as in phase 06.
- D7 - Evaluation: a new `offer_extraction` evaluation in replay over labelled replies with tricky categories, gates and Wilson intervals as in earlier phases, and a deterministic `quote_decision` evaluation of the comparison and the approve, reject and choose paths, including idempotence.
- D8 - Observability: a LangSmith trace and experiment criterion, pending and blocked by the trace quota until about 2026-11-05, delivered with the catch-up change of phases 02 to 05.
- D9 - Demo: `quotes-demo` extended end to end in replay: scan, wait, replies, decide, approve and purchase order.
- D10 - Stale statuses: the first plan increment aligns the README with the master plan (phases 02, 03 and 05 integrated, phase 06 integrated since PR #11) and sets master plan row 07 to in progress.
