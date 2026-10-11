# Phase 06 - Quotes, request and wait: specification

Status: spec in review
Approved by the owner:
Master plan: `../0_plan_maestro.md`

## Goal
The quotes module notices when stored customer orders ask for more units of a product than the warehouse holds, sends a quote request for the missing units to several suppliers, and waits for their replies over several simulated days with its state saved in the SQLite checkpoint.
While it waits, a simulated clock drives reminders to the suppliers that have not replied and a deadline that closes the request, so a wait of days runs in seconds and replays the same way every time.
One command runs the whole wait end to end with no key, no network and no cost, and each request ends ready for the phase 07 decision with the supplier replies attached, or flagged for review when nobody answered.
It is needed now because phase 05 closed the orders module and stores orders without looking at stock, and phases 04 and 05 left automatic deadlines and reminders waiting for this simulated clock.
It shows the master plan capability "persistent state" over a long wait: the thread survives separate processes and days of simulated time.

## Scope
The recommended option of each open decision is assumed below; each decision id points to "Open decisions".
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

## Open decisions
Questions the owner must resolve before approval; empty on approval.
The recommended option is marked; the scope and criteria above are written for it.

### D1 - What triggers a quote request
- A (recommended): a `quotes scan` command over the shared database: units asked by stored `received` orders minus `on_hand`, one request per SKU with a positive shortfall and none open. Trade-off: links the modules only through the database, leaves the orders module untouched and keeps composition for phase 10, but needs a command to run.
- B: a reorder point per product (`on_hand` below a fixed minimum), independent of orders. Trade-off: classic stock policy and simpler, but ignores customer demand and needs a new minimum column chosen by hand.
- C: the orders `store` node starts the request when a line exceeds stock. Trade-off: immediate and realistic, but changes phase 05 graphs, recordings and gates and does the orchestrator's job early.

### D2 - How many suppliers and how they are modelled
- A (recommended): three fictional suppliers in new tables, each selling a set of product families, with overlap so every product has at least two suppliers. Trade-off: enough to compare offers in phase 07 and small to seed and test.
- B: five suppliers with per-product coverage. Trade-off: richer comparison, but more seed data and more files per request with little extra to show.
- C: one supplier per family. Trade-off: simplest, but leaves nothing to compare in phase 07.

### D3 - Simulated clock
- A (recommended): one current time stored in a `sim_clock` table, moved forward only by `clock advance`, read by all quotes code; `quotes tick` delivers the current time to waiting threads. Trade-off: shared across processes and visible in the database, fully reproducible.
- B: a fixed start time plus an offset in an environment variable. Trade-off: no table, but the time is not shared across processes unless every command gets the same variable, and it is easy to forget.
- C: real time with the deadlines scaled to minutes. Trade-off: feels live, but tests and the demo depend on waiting and are not reproducible.

### D4 - How the request and reminder emails are written
- A (recommended): fixed English templates, no model call. Trade-off: no cost, no recordings and no new evaluation of text quality; the texts are plain.
- B: Claude Haiku 4.5 drafts each request and reminder. Trade-off: more natural text and more agent work to show, but adds cost, recordings and a text-quality evaluation for a message whose content is fixed anyway.

### D5 - Supplier replies in this phase, and the wait schedule
- A (recommended): replies are received in this phase as files that only mark the supplier as replied, with no extraction; a 5-day deadline with one reminder on day 3 to suppliers that have not replied; early close when all replied. Trade-off: the wait reacts to real events and phase 07 gets the reply files ready to extract, at small cost.
- B: no replies in this phase; every request waits until the deadline and phase 07 adds replies. Trade-off: smaller, but the wait never reacts to an event and the demo shows only reminders and timeouts.
- C: as A, with a 7-day deadline and reminders on days 2 and 5. Trade-off: closer to a patient buyer and shows repeated reminders, with one more rule to test.

### D6 - Shape of the demo
- A (recommended): one `quotes-demo` command that runs a scripted timeline, each step in its own process, over sample orders and replies in `examples/quotes/`, ending with one request answered after a reminder and one with no offers. Trade-off: one command shows the saved state and the clock, with subprocess overhead of a few seconds.
- B: a single-process demo. Trade-off: faster and simpler, but does not show that the wait survives a restart.

### D7 - Evaluation of the module
- A (recommended): a deterministic `quote_wait` suite of at least 20 scripted timelines graded by the final database and outbox, gated at 100% with critical errors 0. Trade-off: fits the deterministic module and the phase 05 recovery suites, with no audit needed.
- B: tests only, no evaluation. Trade-off: less work, but breaks the master plan rule that every module has an evaluation in `npm run check`.

### D8 - Observability criterion
- A (recommended): no LangSmith criterion in this phase, since the module calls no model and the trace quota is exhausted until about 2026-11-05. Trade-off: no blocked criterion, but no trace evidence for this module until phase 10 or 11.
- B: a criterion with a live trace of `quotes-demo` showing the thread across processes, delivered with the pending catch-up change after the quota reset. Trade-off: adds observability evidence, but leaves a criterion blocked for weeks.

### D9 - Cost
- A (recommended): no live model cost in this phase, consistent with D4 A. Trade-off: none beyond the plain templates.
- B: a ceiling of 0.5 USD, needed only if D4 B is chosen. Trade-off: covers drafting and recording about 100 texts with margin.
