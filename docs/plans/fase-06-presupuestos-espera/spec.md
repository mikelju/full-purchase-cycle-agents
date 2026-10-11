# Phase 06 - Quotes, request and wait: specification

Status: approved
Approved by the owner: 2026-10-11 (via Lavish)
Master plan: `../0_plan_maestro.md`

## Goal
The quotes module notices when stored customer orders ask for more units of a product than the warehouse holds, sends a quote request for the missing units to several suppliers, and waits for their replies over several simulated days with its state saved in the SQLite checkpoint.
While it waits, a simulated clock drives reminders to the suppliers that have not replied and a deadline that closes the request, so a wait of days runs in seconds and replays the same way every time.
One command runs the whole wait end to end with no key, no network and no cost, and each request ends ready for the phase 07 decision with the supplier replies attached, or flagged for review when nobody answered.
It is needed now because phase 05 closed the orders module and stores orders without looking at stock, and phases 04 and 05 left automatic deadlines and reminders waiting for this simulated clock.
It shows the master plan capability "persistent state" over a long wait: the thread survives separate processes and days of simulated time.

## Scope
Each decision id points to "Owner decisions (2026-10-11)".
In:
- Suppliers (D2):
  - New `suppliers` table (code, name, email) and `supplier_families` table (supplier code, product family), seeded with three fictional Spanish suppliers whose families overlap so every catalog product is sold by at least two of them.
  - The seed stays safe to re-run; existing tables and their seed are not changed.
- Simulated clock (D3):
  - New `sim_clock` table holding one current simulated time, starting at a fixed date on seed.
  - A `clock` CLI with `show` and `advance --days N` or `--hours N`; the clock never moves backwards.
  - The quotes code reads time only from this clock and writes it explicitly into its rows; the existing tables keep their `datetime('now')` defaults.
- Out-of-stock detection (D1):
  - A `quotes scan` command computes, per SKU, the units asked by stored orders with status `received` minus `on_hand`; every SKU with a positive shortfall and no open quote request starts one quote request thread for exactly that shortfall.
  - Deterministic, no model call; a re-run of `scan` with nothing new starts nothing.
- Quote request graph, a new LangGraph subgraph `quote_request` with the SQLite checkpointer (D4, D5):
  - `send`: records the request in a new `quote_requests` table (id, SKU, quantity, thread id, status, created at, reminder at, deadline) and one `quote_request_suppliers` row per supplier of the product family (sent at, reminded at, replied at, reply file), and writes one quote request email per supplier as an `.eml` file in a supplier outbox folder, from a fixed text template with the request reference in the subject.
  - `wait`: pauses with `interrupt` until an event arrives: a supplier reply or a clock tick.
  - A tick at or after the reminder time sends one reminder email to every supplier that has not replied; a tick at or after the deadline closes the request.
  - The request closes early when every supplier has replied.
  - Final status `ready_for_decision` when at least one supplier replied, or `no_offers` when none did, which also writes a `failures` row with status `needs_review` and step `quote_wait`, reusing the phase 05 table.
- Supplier replies (D5):
  - A `quotes receive <folder>` command reads supplier reply `.eml` files; a reply whose subject holds an open request reference and whose sender is a supplier of that request resumes the thread, marks that supplier as replied and stores the reply file path.
  - The reply body is not read or extracted; offer extraction is phase 07.
  - A reply with an unknown reference, a closed request or a sender who is not a supplier of the request is rejected with a reason and changes nothing; the same reply received twice changes nothing the second time.
- A `quotes tick` command resumes every waiting thread with the current simulated time; `clock advance` does not tick by itself.
- A `quotes list` command shows every request with SKU, quantity, status, suppliers replied and pending, next reminder and deadline.
- Full module demo `purchase-cycle quotes-demo` over sample orders and supplier replies in `examples/quotes/`, with each step in its own process to show the saved state (D6).
- A new deterministic evaluation `quote_wait` inside `npm run check` next to the nine existing ones (D7).
- README sections for the quotes module, the clock, the commands, the demo, the evaluation and the known limitations.

Out:
- Offer extraction, comparison, human approval and purchase orders: phase 07.
- Any model call; the request and reminder texts come from fixed templates (D4).
- Real email sending or receiving, a mail server or any network listener.
- Reserving or decrementing stock when an order is stored, and receiving goods: the orders module stays as phase 05 left it.
- Starting quote requests from inside the orders graphs; the orchestrator of phase 10 composes the modules.
- Business days, holidays or time zones; the clock counts calendar days and hours.
- Clarification deadlines for the phase 04 threads; the simulated clock is used only by the quotes module in this phase.
- Changes to the phase 01 to 05 prompts, recordings, datasets, tables and gates.

### Evaluation design
- `quote_wait`: at least 20 hand-built scripted timelines, versioned with expected final states, each a sequence of orders, scans, clock advances, supplier replies and ticks run through the CLI functions on a temporary database (D7).
- Coverage: at least 3 timelines each for all suppliers replying before the deadline, some replying, none replying, a late reply after the deadline, a duplicate reply, a re-run scan and a tick repeated at the same time, and at least 3 with a process restart between steps.
- Grader: deterministic, reads the database, the supplier outbox and the `failures` table, not the node sequence.
- Metrics: scenario success rate with its 95% Wilson interval at scenario level, and critical errors per scenario: a second open request for the same SKU, a request for a SKU with no shortfall, a request quantity different from the shortfall, a reminder to a supplier who already replied, more than one reminder per supplier, any email after the request closed, a request left waiting after its deadline tick, and a lost or duplicated thread after a restart.
- Gates: scenario success 100% and critical errors 0, since the module is deterministic; the report shows the target, whether it is met, the counting unit and the Wilson upper bound of a zero-event rate, as phase 05 set.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the ten evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | `seed` creates the `suppliers`, `supplier_families`, `sim_clock`, `quote_requests` and `quote_request_suppliers` tables; three suppliers are seeded; every catalog product is sold by at least two suppliers; seeding twice gives the same rows; the existing tables and their rows are unchanged | Pytest tests on a temporary database |
| C3 | `clock show` prints the simulated time; `clock advance --days N` and `--hours N` move it forward and persist it across processes; a zero or negative step fails with a message and changes nothing; the quotes code gives the same results whatever the real time is | Pytest tests, one through a subprocess, and a test that runs the same timeline under two different real times |
| C4 | `quotes scan` starts one request per SKU whose units asked by `received` orders exceed `on_hand`, for exactly the shortfall, and none for a SKU with enough stock; a second `scan` with no new orders starts nothing; a SKU with an open request gets no second one; no model is called | Pytest tests on a temporary database counting rows, threads and model client calls |
| C5 | Each new request writes one `.eml` file per supplier of the product family to the supplier outbox, addressed to the supplier email, with the request reference in the subject and the SKU, product name, sale unit, quantity and deadline in the body; the thread then waits in an `interrupt` with its state in the SQLite checkpoint | Pytest tests reading the outbox files and the checkpoint state |
| C6 | `quotes receive` marks a supplier as replied when the subject holds an open request reference and the sender is a supplier of that request, storing the reply file path; an unknown reference, a closed request and a foreign sender are rejected with a reason and change nothing; a reply received twice changes nothing the second time | Pytest tests per case counting rows and comparing checkpoint state |
| C7 | A `quotes tick` at or after the reminder time writes one reminder to each supplier that has not replied and none to those that replied; a second tick at the same or a later time before the deadline writes no new reminder; a tick before the reminder time writes nothing | Pytest tests on scripted timelines counting outbox files |
| C8 | When every supplier has replied, the request closes as `ready_for_decision` at once; at a tick at or after the deadline it closes as `ready_for_decision` with at least one reply, or as `no_offers` with none, which also writes a `failures` row with status `needs_review` and step `quote_wait`; after closing, no email is written and later replies are rejected | Pytest tests per outcome counting rows and outbox files |
| C9 | A request survives separate processes: scan, receive and tick each run in a new process against the same database and checkpoint files, and the final state equals the same timeline run in one process | Pytest test that runs each step as a subprocess and compares the final rows and outbox with an in-process run |
| C10 | `quotes list` shows every request with SKU, quantity, status, suppliers replied and pending, next reminder and deadline | Pytest test on a temporary database with requests in each status |
| C11 | `quote_wait` holds at least 20 timelines with the coverage in "Evaluation design"; `npm run eval` reports it with scenario success, its Wilson interval and critical errors per scenario, and exits non-zero when its success is below 100% or any critical error is counted | Pytest tests over the scenario files and tests that inject each critical error and prove a non-zero exit; replay run report saved as evidence |
| C12 | `quotes-demo` runs the sample timeline with each step in its own process and prints, per step, the simulated time, the requests started, the emails written, the replies received and the status of every request; it ends with at least one request `ready_for_decision` after a reminder and one `no_offers`; it needs no key and no network | Run with the network blocked through a pytest inside `check`; output saved as evidence |
| C13 | Phase 01 to 05 behaviour is unchanged: their tests pass unchanged, the nine existing evaluations replay with every gate PASS, `git diff --stat` on their recordings, datasets and baselines is empty, and `orders-demo` prints the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C14 | No secret is versioned: the supplier seed, sample replies, outbox examples and scenario files contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C15 | The README explains in English the quotes module, the simulated clock, the `clock` and `quotes` commands, `quotes-demo`, how `quote_wait` was built and run, and the known limitations below | Follow the new README sections in a clean clone |

## Constraints and risks
- No new dependency and no model cost are expected: `interrupt`, `Command` and the SQLite checkpointer are already in `langgraph`, and the emails use the standard library `email` package (D4).
- The live model cost ceiling for the phase is 5 USD (D9); with D4 A no model call is planned, so the expected cost is 0 USD.
- Five new tables in the shared schema; the existing tables are not changed, and `failures` is reused as it is.
- LangGraph re-runs an interrupted node from its start on resumption, so `send`, the reminder step and the close step must be idempotent; keyed outbox file names and the reminded-at and replied-at columns provide that.
- The stock seed gives some products zero units (`(i * 37) % 250`), so a small sample order already shows a shortfall; the sample orders are chosen against that seed.
- Demand counts every stored order with status `received`, since nothing ships or reserves stock yet; the same order keeps counting until phase 07 or later changes order statuses, which an open request per SKU keeps from causing duplicate requests.
- A thread waits with one `interrupt` resumed by two kinds of events; a confusion between them is the main design risk and is covered by C6 to C8 and the `quote_wait` critical errors.
- No LangSmith criterion is added: the module calls no model, and the trace quota is exhausted until about 2026-11-05 (D8).

## Assumptions
- One quote request covers one SKU; several short SKUs give several requests, even to the same supplier, which keeps the phase 07 comparison per product.
- The requested quantity is the shortfall itself, with no minimum order or rounding to packs.
- Supplier reply files are written by hand or by the demo, with the request reference in the subject, for example `Re: Quote request QR-0003`.
- The supplier is identified by the sender address of the reply file only, as the email channel identifies customers.
- The simulated clock starts at 2026-10-12 09:00 on seed, and the reminder and deadline offsets are measured from the request's creation time.
- The supplier outbox is a new folder next to the phase 05 outbox, and the supplier reply inbox a folder given to `quotes receive`.
- The quote request and reminder texts are short English templates in the module, like the phase 02 reply texts.
- `npm run check` stays fast: the new evaluation runs in seconds with no model call.

## Known limitations
- Suppliers are simulated by email files; nothing is sent or received over a network.
- The module reads no offer content; prices and delivery times are read in phase 07.
- Stock is not reserved or decremented, so demand is the sum of stored `received` orders.
- The clock has no business days, holidays or time zones, and nothing ticks by itself: an operator or the demo runs `quotes tick`.
- A reply is accepted on the subject reference and the sender address in the file; there is no sender authentication, as in the email channel.

## Owner decisions (2026-10-11)
The owner approved the spec via Lavish on 2026-10-11, chose the recommended option A in D1 to D6 and D8, chose option B in D9 with a raised ceiling, left D7 unanswered and accepted the assumptions above.
Each reason is the owner decision of 2026-10-11.
- D1 - What triggers a quote request: A, a `quotes scan` command over the shared database, units asked by stored `received` orders minus `on_hand`, one request per SKU with a positive shortfall and none open.
- D2 - How many suppliers and how they are modelled: A, three fictional suppliers in new tables, each selling a set of product families, with overlap so every product has at least two suppliers.
- D3 - Simulated clock: A, one current time in a `sim_clock` table, moved forward only by `clock advance` and delivered to waiting threads by `quotes tick`.
- D4 - How the request and reminder emails are written: A, fixed English templates, no model call.
- D5 - Supplier replies in this phase, and the wait schedule: A, reply files that only mark the supplier as replied, with no extraction; a 5-day deadline with one reminder on day 3 to suppliers that have not replied; early close when all replied.
- D6 - Shape of the demo: A, one `quotes-demo` command running a scripted timeline with each step in its own process over `examples/quotes/`.
- D7 - Evaluation of the module: A, a deterministic `quote_wait` suite of at least 20 scripted timelines gated at 100% with critical errors 0; recommended option, the owner approved the spec with their choices and left D7 unanswered.
- D8 - Observability criterion: A, no LangSmith criterion in this phase.
- D9 - Cost: B, with the owner's note to raise the ceiling to 5 USD so the phase is not caught short; the live model cost ceiling for the phase is 5 USD, and with D4 A no model call is planned, so the expected cost is 0 USD.
