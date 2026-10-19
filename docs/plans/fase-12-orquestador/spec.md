# Phase 12 - Orchestrator: specification

Status: approved (frozen)
Approved by the owner: 2026-10-19 (option A for D1 to D5, with the owner amendment to D2 recorded under "Resolved decisions")
Master plan: `../0_plan_maestro.md`

## Goal
One top-level LangGraph graph runs the whole purchase cycle over the shared database: customer orders from the three channels, quotes for what is short of stock, the purchase order, supplier reconciliation and claim, goods receipt and serving, customer invoicing and sales reconciliation.
Today each module runs on its own command and each module demo seeds the data the earlier modules would have produced (for example `receipt-demo` inserts purchase orders directly); nothing shows one customer order flowing through every module.
One command runs an end-to-end demo in replay with no key, no network and no cost, and one command builds an evaluation metrics dashboard from the replayed evaluation suites.
It is needed now because the eleven earlier phases left every module working on the shared database; phase 13 needs the composed graph, the end-to-end demo and the dashboard for the portfolio showcase.
It shows the master plan capabilities "persistent state", "human-in-the-loop", "failure recovery", "observability" and "structured evaluation" at the level of the whole cycle, reusing every module as it is instead of parallel mechanisms.
The scope and criteria below follow the owner decisions of 2026-10-19 recorded under "Resolved decisions".

## What exists today
- Modules and their graphs, each with the SQLite checkpointer and one thread per item:
  - Orders: `web_form_order`, `email_order` and `whatsapp_order`, with the `clarification` subgraph (an `interrupt` that waits for the customer answer) and recovery; `route` dispatches a mixed inbox folder to them and is plain Python, not a graph.
  - Quotes: `quote_request` (scan of shortfalls, supplier replies, reminders and deadlines on the simulated clock) and `quote_decision` (offer extraction and comparison, human approval with `interrupt`, purchase order).
  - Supplier reconciliation: `reconcile` (document extraction and matching against the purchase order) and `claim` (drafting with fact check, phase 09 `review` pause, send or close).
  - Goods receipt: `receipt` (reconciled delivery notes into stock, serving waiting orders, customer delivery notes).
  - Sales: `sales` (invoice, signed notes, sales matching) and `customer_response` (drafting with fact check, `review` pause, send or close).
- The modules already hand off through the shared database: stored orders feed `quotes scan` shortfalls, an approved quote creates the purchase order that supplier documents reconcile against, a reconciled delivery note feeds `receipts run`, a served order gets the delivery note that `sales invoice` bills.
- Every step has a CLI command in `src/purchase_cycle/cli.py`, `receipts_cli.py` or `sales_cli.py`; human decisions are taken with `clarify answer`, `quotes approve` or `reject`, `reconcile approve` or `reject` and `sales approve` or `reject`.
- Module demos (`orders-demo`, `quotes-demo`, `reconcile-demo`, `receipt-demo`, `sales-demo`) run each step as a subprocess on a work folder and replay recorded model answers; each starts from seeded data, not from the output of the previous module.
- Twenty evaluation suites run in replay inside `npm run check`; each prints a text report (metrics, 95% Wilson intervals, gates, baseline) and returns an exit code; no suite writes a machine-readable result.
- Phoenix local tracing (change 013) with `PHOENIX_TRACING=true`; LangGraph Studio for the three channel graphs (change 004); flow animations in `docs/assets/`.
- The LangSmith trace quota is exhausted until about 2026-11-05.

## Scope
Each decision id points to "Resolved decisions".
In:
- Orchestrator graph (D1): a new LangGraph graph `purchase_cycle` with the SQLite checkpointer and one thread per cycle run, whose nodes are the cycle stages in order:
  - `orders` (route the new items of an inbox folder to the channel graphs), `quotes` (scan shortfalls, read new supplier replies, deliver the simulated time, decide the requests ready for decision), `reconcile` (scan new supplier documents, draft claims), `receipts` (receive and serve), `sales` (invoice, read new signed notes, draft responses).
  - Each stage calls the same module functions the CLI commands call, with the same database, checkpoint file and folders; no stage copies module logic, and module threads keep their existing ids, so the existing commands still see them.
  - Stages hand off only through the shared database and the folders; the orchestrator holds no business data in its own state beyond per-stage counts.
  - A stage that reaches a human decision leaves that module thread paused, as the module command does; the orchestrator takes no decision, lists every pending decision with the command that resolves it and ends the run.
  - The orchestrator does not move the simulated clock; `clock advance` stays the operator's step.
  - Every stage is idempotent: a run with nothing new changes no row and writes no file.
- Commands, in a new `cycle` group:
  - `cycle run [--mode] FOLDERS...` runs one cycle pass and prints, per stage, what it processed and every pending decision; a second run with nothing new changes nothing.
  - `cycle-demo` (D2) runs one versioned end-to-end scenario on one database, each step in its own process, with four customer orders that take distinct paths through the cycle:
    - Order A, web form, clean path: every line in stock; served from stock, delivery note, customer invoice, signed delivery note received in full, sales reconciliation `matched`.
    - Order B, email, clarification: one ambiguous line raises a clarification question; the scripted customer answer completes the order; served from stock, invoiced, and the signed note reports damaged units, which gives a customer response with a credit note that the operator approves.
    - Order C, WhatsApp, quote and claim: a line short of stock starts quote requests; supplier replies arrive over simulated days and the operator approves one offer, which gives the purchase order; the supplier invoice charges a higher unit price than the purchase order, which gives a claim that the operator approves and sends; the reconciled delivery note puts the goods into stock and serves the order, which gets its delivery note and invoice.
    - Order D, web form, short delivery: a line short of stock goes through an approved quote and a purchase order; the supplier invoices and delivers fewer units than ordered, the quantity difference gives a claim that the operator rejects; the receipt puts the short quantity into stock, the order stays waiting, and the next pass starts a quote request for the remainder, which the demo leaves pending.
  - It prints each stage pass, each pending decision with its command and each scripted operator decision, and ends with a summary of each order from intake to its final state: A matched, B with its credit note, C served and invoiced after its claim, D waiting with its new quote request.
- Evaluation (D4): a new deterministic scenario suite `purchase_cycle` inside `npm run check`, run through the CLI functions on a temporary database with a test double of the model client that serves recorded answers and counts calls.
- Dashboard (D3): `npm run eval` also writes one machine-readable summary per suite to a git-ignored folder under `data/`; a new command `purchase-cycle dashboard` builds from those summaries one self-contained static HTML page with, per suite, the case or scenario count, every metric with its 95% Wilson interval, every gate with its threshold and verdict, critical errors, the baseline date and the per-category figures, using the Python standard library and inline SVG.
- README sections for the orchestrator, the `cycle` commands, `cycle-demo` in replay and record mode, the dashboard, the local Phoenix trace of the orchestrator, the `purchase_cycle` evaluation and the known limitations.

Out:
- Changes to module behaviour, prompts, recordings, datasets, baselines and gates of phases 01 to 11.
- A single top-level thread that nests every module thread and carries their interrupts (D1 option B).
- Automatic decisions, automatic clock movement, schedulers, daemons, queues or a web server for the orchestrator.
- Real email, WhatsApp or file transfer; payment, as in phase 11.
- LangGraph Studio registration of the orchestrator and a new flow animation.
- An interactive or hosted dashboard, LangSmith experiments and a versioned dashboard snapshot; the showcase material is phase 13.
- The LangSmith trace of this phase, left to the catch-up change after the quota resets (D5).
- The defects logged in change 018 (CLD-0149, the SHARPS-15 hallucination on EML-0215 and EML-0237).

### Evaluation design
- `purchase_cycle` (D4): at least 16 hand-built scripted scenarios, versioned with their input folders and expected final state, run through the CLI functions on a temporary database.
  - Coverage, at least 2 each: an order served from stock straight to its invoice; an order short of stock that goes through quotes, purchase order, receipt and invoice; a pass that stops at each kind of pending decision (clarification, quote approval, claim, customer response) and continues after the decision; a second pass with nothing new; a crash after a stage followed by a new pass; a process restart between stages; a rejected quote or claim that leaves the order waiting; a supplier short delivery that starts a new quote request.
  - Grader: deterministic, reads the shared database, the folders, the `failures` table and the printed output.
  - Critical errors per scenario: a decision taken by the orchestrator; a duplicated row or file after a second pass or a resumption; a stage that ran before the stage it depends on had stored its output; a model call on a second pass or a resumption; a final state that differs from running the module commands one by one on the same inputs.
- Gates: scenario success 100% and critical errors 0, since the suite is deterministic, with the phase 05 report columns and a 95% Wilson interval on scenario success.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the twenty-one evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | The `purchase_cycle` graph has the stages of "Scope" in that order, compiled with the SQLite checkpointer; each stage gives the same rows and files as the module commands it stands for, run one by one on the same inputs | Pytest tests comparing a `cycle run` with the sequence of module commands on a temporary database |
| C3 | Stages hand off through the shared database: in one run, a stored order short of stock gets a quote request; an approved offer gives the purchase order that the supplier documents reconcile against; the reconciled delivery note puts the goods into stock and serves the order; the served order gets its delivery note and its invoice | Pytest test following one order id through the tables |
| C4 | A run that reaches a clarification, a quote approval, a claim or a customer response leaves that module thread paused in its SQLite checkpoint, lists it with the command that resolves it and takes no decision; after the decision by the existing command, the next run continues from there | Pytest tests per pause kind reading checkpoint state, rows and printed output |
| C5 | Every stage is idempotent: a second run with nothing new changes no row, writes no file and calls no model; a run interrupted after any stage and run again ends with the same rows and files as an uninterrupted run, and the resumption calls no model | Pytest tests with an injected crash and a model client double that counts calls |
| C6 | The cycle survives separate processes: each run and each decision in a new process against the same database and checkpoint files gives the same final rows and files as the same steps in one process | Pytest test running each step as a subprocess and comparing with an in-process run |
| C7 | `cycle-demo` runs the four-order scenario of "Scope" end to end, each step in its own process; it prints each stage pass, each pending decision and each operator decision, and ends with one summary line per order showing the final state of "Scope" (A matched, B credit note sent, C claim sent and order invoiced, D waiting with a pending quote request for the remainder); it needs no key and no network | Run with the network blocked through a pytest inside `check` that asserts the four final states from the database; replay from the versioned recording `evals/recordings/cycle_demo.jsonl` made once in record mode (D2); output saved as evidence |
| C8 | `purchase_cycle` holds at least 16 scenarios with the coverage of "Evaluation design"; `npm run eval` reports scenario success with its Wilson interval and critical errors per scenario, and exits non-zero when success is below 100% or any critical error is counted | Pytest tests over the scenario files and tests that inject each critical error and prove a non-zero exit; replay report saved as evidence |
| C9 | `npm run eval` writes one summary per suite with the figures it prints; `purchase-cycle dashboard` builds one HTML file that shows all twenty-one suites with the fields of "Scope", whose values equal the summaries, loads no external resource and opens with no network; a missing or failed suite is shown as such | Pytest tests over the summaries and the HTML; screenshot of the page saved as evidence |
| C10 | With `PHOENIX_TRACING=true`, the local Phoenix of change 013 running and LangSmith tracing off, one replay `cycle run` sends one trace with a span for the `purchase_cycle` graph, a child span per stage and the module node spans under their stage | Spans of that run read back from the Phoenix API and saved as evidence, as change 013 did (D5) |
| C11 | Phase 01 to 11 behaviour is unchanged: their tests pass unchanged; the twenty existing evaluations replay with every gate PASS; `git diff --stat` on their recordings, datasets and baselines is empty; the five module demos print the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C12 | No secret is versioned: the new recordings, scenario files, demo inputs and dashboard test fixtures contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C13 | Every live run of the phase is logged with its calls, tokens and cost, and the phase total stays under 5 USD (D2) | Live run reports and the running cost total in `plan.md`, saved as evidence |
| C14 | The README explains in English the orchestrator and its stages, the `cycle` commands, `cycle-demo` in replay and record mode, the dashboard, the local Phoenix trace of a cycle run, how the `purchase_cycle` evaluation was built and run, and the known limitations below; master plan row 12 shows the phase status | Follow the new README sections in a clean clone; compare row 12 with the phase status |

## Constraints and risks
- No new dependency: LangGraph, SQLite, the existing model client with record and replay, the change 013 Phoenix instrumentation and the Python standard library for the dashboard.
- The end-to-end scenario needs supplier replies, supplier documents, a claim draft and a response draft that match the purchase order and invoice numbers this run creates, so the existing recordings do not fit; one record run of the four-order scenario with Claude Haiku 4.5, the model the project already uses, makes about 20 to 30 short calls (order intake, extraction and matching, the clarification, offer extraction, supplier document extraction, two claim drafts and one response draft) and is expected to cost about 0.15 USD, under 0.50 USD for the phase even with a repeated record run; the phase ceiling is 5 USD (D2), a limit and not a budget; the owner runs it with the key in `.env`, which the agent never reads, and LangSmith tracing off.
- Calling module functions from stages assumes they take the database, checkpoint and folder paths as arguments, as the CLI commands pass them; any module function that reads a fixed path is wrapped, not changed, and C11 guards behaviour.
- LangGraph re-runs an interrupted node from its start, so every stage must be idempotent; the modules are already idempotent per item, and C5 tests it at the stage level.
- Module threads keep their own checkpoints; the orchestrator thread only records stage progress, so a pending decision lives in one place.
- Writing a summary file from `npm run eval` must not change the printed report or the exit code of any suite; C11 compares them.
- Phoenix tracing is never set up inside `npm run check` or pytest (change 013 guard), so C10 evidence comes only from a run outside `check`.
- About 16 scenarios give a wide interval on scenario success; the gate is 100% because the suite is deterministic.

## Assumptions
- "Composing the three subgraphs" means composing every module graph of phases 02 to 11 as stages of one graph, over the shared database.
- One cycle pass is an operator action, as each module command is today; time and decisions stay with the operator, so the demo scripts `clock advance` and the decisions in their own processes, as phases 07, 09 and 11 did.
- The dashboard is a local file for the owner and a source for phase 13; it is generated, not versioned.
- `npm run check` stays fast: the new suite and the summary files add seconds.

## Known limitations
- A cycle pass is started by a person; nothing runs on its own or on a schedule.
- Pending decisions are taken with the module commands; the orchestrator only lists them.
- The dashboard shows the latest replayed evaluation run; it keeps no history.
- The module limitations remain, including the change 018 logged defects.
- The trace of this phase is local to the owner's machine; the LangSmith trace comes with a later change.

## Open decisions
None; every decision was resolved on approval.

## Resolved decisions
Owner 2026-10-19 through `.lavish/fase-12-spec.html`: option A for D1, D2, D3, D4 and D5, with one amendment to D2.
The owner note on D2, verbatim: "no se pueden grabar mas pedidos para la demo? para que haya distintas situaciones grabadas... le damos una vuelta. se puede poner 5 dolares de presupuesto".
Applied on approval: the end-to-end demo grows from one followed order to the four orders of "Scope", each on a distinct path (clean order, clarification, quote approval with a price claim, short delivery with a rejected claim and a new quote request), recorded once live with Claude Haiku 4.5; the live ceiling of the phase is 5 USD instead of 1 USD; C7, C13 and "Constraints and risks" were rewritten accordingly.
Each decision keeps its options and trade-offs for the record; the chosen option is marked.
- D1 - How the orchestrator composes the modules:
  - A (recommended, chosen): one graph whose nodes are the cycle stages, each calling the module functions; module threads, checkpoints and commands stay as they are; pauses are listed and decided with the existing commands.
  - B: the module graphs nested as subgraphs of one top-level thread with fan-out per item, so interrupts surface in that thread; one place to resume, but it changes how every module stores its threads and risks the behaviour of phases 02 to 11.
- D2 - End-to-end demo data and its model answers:
  - A (recommended, chosen): a new coherent versioned scenario, recorded once live with Claude Haiku 4.5 and replayed afterwards; expected cost under 0.30 USD, ceiling 1 USD, LangSmith off. Amended by the owner: four orders on distinct paths, ceiling 5 USD.
  - B: no live spend, reusing only the existing example inputs and recordings; each stage is fed its own seeded inputs, as today's demos, so no single order flows through the whole cycle.
- D3 - Dashboard form:
  - A (recommended, chosen): one static HTML file built with the Python standard library and inline SVG from summaries written by `npm run eval`; no dependency, no cost, works offline.
  - B: an interactive Streamlit or Plotly app; nicer filtering, but a new dependency and a server to run.
  - C: LangSmith experiments; native, but the trace quota is exhausted until about 2026-11-05 and it covers only the model suites.
- D4 - Evaluation of the orchestrator:
  - A (recommended, chosen): a new deterministic scenario suite `purchase_cycle` of at least 16 scenarios with 100% and zero critical error gates, as phases 05, 08 and 11; it also appears on the dashboard.
  - B: pytest tests only, with no new suite; less work, but the phase adds no evaluation case, against the master plan rule that every phase does.
- D5 - Trace evidence:
  - A (recommended, chosen): C10 met with a local Phoenix trace of a replay cycle run, at no cost; the LangSmith trace goes to the catch-up change after about 2026-11-05.
  - B: C10 as a LangSmith trace criterion left pending until the quota resets, as phases 02 to 10.
