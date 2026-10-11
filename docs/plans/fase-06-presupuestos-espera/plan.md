# Phase 06 - Quotes, request and wait: plan and results

Status: in execution since 2026-10-11; batch A done, batch B in progress
Spec: `spec.md` (frozen, approved by the owner on 2026-10-11)
Base: branch `fase-06-presupuestos-espera` from `main` at commit `ada28ab`.

## Design notes
Written for the owner decisions of 2026-10-11 recorded in the spec (option A in D1 to D8, option B in D9 with a 5 USD ceiling and no model call planned).
- New module `purchase_cycle/quotes.py`: supplier lookups, shortfall computation, the fixed English request and reminder templates, the supplier outbox writer, the reply reader and `build_quote_request_graph` with the nodes `send`, `wait` and `close` (D1, D4, D5).
- New module `purchase_cycle/sim_clock.py`: `now(conn)` and `advance(conn, days=0, hours=0)` over the `sim_clock` table; nothing in the quotes code calls `datetime.now`, `time.time` or SQLite `datetime('now')` (D3).
- New tables in `db.SCHEMA`: `suppliers`, `supplier_families`, `sim_clock`, `quote_requests` and `quote_request_suppliers`; `db.seed` inserts the three suppliers, their families and the clock start 2026-10-12 09:00 with `INSERT OR IGNORE`, so a re-run changes nothing; the existing tables, `TABLES` and their seed rows stay as they are (D2, D3).
- Supplier families are chosen against the 31 families in `catalog.py`, with overlap so every family has at least two suppliers.
- Demand per SKU is the sum of `order_lines` quantities of orders with status `received` (the `STATUS` constant of the three channel modules); shortfall is demand minus `stock.on_hand`; a SKU with a request in status `waiting` gets no new request (D1).
- Review round 1, finding 1 (C4): the shortfall to request is demand minus `on_hand` minus the units of every earlier request for the SKU, whatever its status, and a request starts only for a positive remainder, so a rescan with no new orders starts nothing even after a close and new orders start a request for just the new units (owner decision, recommended option).
- Review round 1, finding 2 (C5, C7, C8): `quotes scan` first resumes every `waiting` request whose thread has not reached `wait` (no checkpoint, or next node `send`), so a send that failed partway is finished by the next scan without a second email or row; the resumed request is not listed as started (owner decision, recommended option).
- Review round 1, finding 3 (C13): the earlier-phase test `tests/test_whatsapp_eval.py` lists `quote_wait` in the `eval all` suite order because `ALL_SUITES` grew; recorded as deviation DV-01 (owner decision, recommended option).
- Request reference `QR-NNNN` from the `quote_requests` id, thread id `quote-QR-NNNN`; reminder time is created at plus 3 days and deadline created at plus 5 days, both stored in the row at `send` (D5).
- The graph state carries the creation time given by `scan`, and every resume payload carries the event and the current simulated time, so a node re-run on resumption gives the same rows and files.
- `wait` is one `interrupt` resumed by `Command(resume={"event": "reply", ...})` or `Command(resume={"event": "tick", "now": ...})`; the CLI checks the database before resuming, so a rejected reply never reaches the thread.
- Idempotence: outbox file names are keyed (`QR-NNNN-request-<supplier code>.eml`, `QR-NNNN-reminder-<supplier code>.eml`), and `reminded_at`, `replied_at` and the request status are written only when empty or still `waiting`.
- The `.eml` `Date` header comes from the simulated clock, not from `email.utils.formatdate()` with the real time, so outbox bytes do not depend on the real time (C3).
- A reply is accepted while its request is `waiting`; the deadline takes effect at the first tick at or after it, as the spec states; a "late reply" timeline is a reply after that closing tick (C6, C8).
- A tick at or after the deadline closes the request and sends no reminder, even when the reminder was never sent, because no email may follow the close (C7, C8).
- `no_offers` writes a `failures` row through `db.park_failure` with channel `quotes`, source the request reference, step `quote_wait` and status `needs_review` (C8).
- Supplier rows live in a new data module `purchase_cycle/suppliers.py`, not in `catalog.py`, because that file's rows feed the prompts and its header ties changes to new recordings; family i goes to suppliers i and i + 1 (mod 3) and every fourth family also to the third, over the 32 families the catalog actually has (decided in execution, batch A).
- New config path `config.SUPPLIER_OUTBOX_DIR = DATA_DIR / "supplier_outbox"` next to `OUTBOX_DIR`.
- `quotes list` shows `reminder_at` as the next reminder only while the request waits and no reminder was sent, and `-` otherwise, because D5 sends one reminder per request (decided in execution, batch B).
- The C3 real-time test patches `time.time` and `datetime.datetime` in-process instead of running under another system clock, because a test cannot change the machine clock (decided in execution, batch B).
- CLI in `cli.py`: `clock show`, `clock advance --days N | --hours N`, `quotes scan`, `quotes receive <folder>`, `quotes tick`, `quotes list` and `quotes-demo`, each with `--db`, `--checkpoints` and, where it writes email, `--outbox`, following the phase 05 commands; the graph uses `graph.sqlite_checkpointer`.
- New evaluation module `purchase_cycle/evaluation/quote_wait_eval.py` with scenario files in `evals/datasets/quote_wait/`, registered in `harness.SUITES` and `ALL_SUITES` and left out of `UPLOAD_SUITES`, like `failure_recovery` and `order_scenarios`; it reuses `stats.wilson_interval` and `stats.target_cells` and the phase 05 report columns (D7).

## Rules for executors
- Each batch is sized for one writer agent with about 90k tokens of context; it starts in a fresh session from this file and Git, implements its increments in order, marks each one `[x]` with its evidence and commits before the context runs out.
- Failing tests first: each increment adds its tests, runs them to see them fail for the expected reason, then implements.
- Before closing a batch, `npm run check` passes (pytest quiet) and the batch commit is made; the next batch only starts from a green check.
- Search before reading; read large files by chunks; reuse `db.park_failure`, `graph.sqlite_checkpointer`, `stats.wilson_interval` and the phase 05 report and critical-error patterns.
- No new dependencies and no model calls (D4); the D9 ceiling of 5 USD is a limit, not a budget, and no increment plans to spend from it.
- No change to phase 01 to 05 prompts, recordings, datasets, baselines, tables or gates (C13).
- Evidence files go to `.evidence/fase-06/`.

## Increments
Each increment leaves the product working and covers concrete criteria.
This file is the durable state: a new session resumes from here and from Git.
Execution order: batches A to E.

### Batch A - Baseline evidence, suppliers, clock and shortfall
- [x] 1. Unchanged-behaviour baseline before any code change (C13) - check: `orders-demo` in replay output saved as `.evidence/fase-06/orders-demo-before.txt` and `npm run eval` output as `.evidence/fase-06/eval-before.txt`, both from commit `ada28ab` code.
  Evidence: run at `e747bbf`, whose `src/` and `evals/` equal `ada28ab` (`git diff --stat ada28ab e747bbf` touches only `docs/plans/`), with the Anthropic and LangSmith variables unset; `orders-demo` exit 0, five orders stored, no parked failures; `npm run eval` exit 0 with every gate PASS in the nine evaluations; files in `.evidence/fase-06/` (git-ignored).
- [x] 2. `suppliers` and `supplier_families` tables and their seed: three fictional Spanish suppliers, every catalog family sold by at least two (C2) - check: new tests in `tests/test_seed.py` fail first, then pass: tables exist, three suppliers, every product in `products` has at least two suppliers, seeding twice gives the same rows, `row_counts` of the existing tables and their rows unchanged.
  Evidence: two new tests in `tests/test_seed.py` failed first (no `suppliers` table), then `uv run pytest -q tests/test_seed.py` 8 passed; ruff check and format clean.
- [x] 3. `sim_clock` table, `sim_clock.now` and `advance`, and the `clock show` and `clock advance --days N | --hours N` commands (C2, C3) - check: new `tests/test_sim_clock.py`: seed time 2026-10-12 09:00, days and hours advance, a zero or negative step fails with a message and leaves the row unchanged, one test reads the advanced time from a second process through `subprocess`.
  Evidence: new `tests/test_sim_clock.py` failed first on the missing `sim_clock` module, then `uv run pytest -q tests/test_sim_clock.py tests/test_seed.py` 21 passed; ruff check and format clean.
  The clock is stored as `YYYY-MM-DD HH:MM:SS` in a single row with id 1; `advance` rejects any negative part or an all-zero step with a "only moves forward" message; `clock advance` takes exactly one of `--days` and `--hours`; like `clarify list`, the `clock` commands run the idempotent seed first, so a missing database starts at 2026-10-12 09:00.
- [x] 4. `quote_requests` and `quote_request_suppliers` tables and `quotes.shortfalls(conn)` over `received` orders and `stock` (C2, C4) - check: new `tests/test_quotes.py` cases on a temporary database: shortfall equals demand minus `on_hand`, a SKU with enough stock gives none, orders with another status are ignored, a SKU with a `waiting` request is skipped.
  Evidence: new `tests/test_quotes.py` failed first on the missing `quotes` module, then `uv run pytest -q tests/test_quotes.py tests/test_seed.py tests/test_sim_clock.py` 27 passed; ruff check and format clean; full `uv run pytest -q` 649 passed at batch A close (`npm run check` left to the coordinator).
  `quote_requests` times carry no `datetime('now')` default, so the quotes code must write them from the clock; a partial unique index allows only one `waiting` request per SKU as a schema guard against the first critical error; `shortfalls` returns rows in SKU order.

### Batch B - Quote request graph and its events
- [x] 5. `build_quote_request_graph` with `send` and `wait`, request and reminder templates, supplier outbox writer and `quotes scan` (C4, C5) - check: tests fail first, then pass: `scan` starts one thread per short SKU with the exact quantity, a second `scan` starts nothing, a stub model client counts zero calls; one `.eml` per family supplier with the supplier address, `QR-NNNN` in the subject and SKU, product name, sale unit, quantity and deadline in the body; the checkpoint shows the thread paused in `wait`; re-running `send` on resumption writes no second file or row.
  Evidence: new `tests/test_quote_request.py` failed first on the missing `quotes.scan` and `quotes` command, then `uv run pytest -q tests/test_quote_request.py tests/test_quotes.py tests/test_sim_clock.py tests/test_seed.py` 35 passed; ruff check and format clean.
  Evidence:
- [x] 6. `quotes receive <folder>` with reference and sender checks and duplicate handling (C6) - check: one test per case: accepted reply sets `replied_at` and the file path; unknown reference, closed request and foreign sender are rejected with a reason and leave rows, outbox and checkpoint state unchanged; the same reply twice changes nothing the second time.
  Evidence: seven new receive cases in `tests/test_quote_request.py` failed first on the missing `quotes.receive`, then `uv run pytest -q tests/test_quote_request.py tests/test_quotes.py` 22 passed; ruff check and format clean.
  Evidence:
- [x] 7. `quotes tick` and reminders (C7) - check: scripted timelines counting outbox files: a tick before day 3 writes nothing; a tick at or after day 3 writes one reminder to each supplier that has not replied and none to those that replied; a second tick at the same or a later time before the deadline writes nothing new.
  Evidence: four new tick cases in `tests/test_quote_request.py` failed first on the missing `quotes.tick`, then `uv run pytest -q tests/test_quote_request.py` 20 passed; ruff check and format clean.
  Evidence:
- [x] 8. Closing: early close when every supplier replied, deadline close as `ready_for_decision` or `no_offers` with its `failures` row, no email and rejected replies after close (C8) - check: one test per outcome counting `quote_requests` status, `failures` rows with status `needs_review` and step `quote_wait`, and outbox files before and after a later tick and reply; a single tick past the deadline sends no reminder.
  Evidence: four new closing cases in `tests/test_quote_request.py` failed first with the request still `waiting`, then `uv run pytest -q tests/test_quote_request.py` 24 passed; ruff check and format clean.
  Evidence:
- [x] 9. Cross-process wait and real-time independence (C9, C3) - check: a test runs scan, receive, clock advance and tick each in its own subprocess on the same database and checkpoint files and compares the final rows and outbox bytes with the same timeline run in one process; a test runs one timeline twice with the real-time source of the process patched to two different dates and compares rows and outbox bytes.
  Evidence: two new tests in `tests/test_quote_request.py` over an eight-step CLI timeline (scan, reply, reminder tick, duplicate reply, deadline tick on two SKUs, ending `ready_for_decision` and `no_offers`) compare `quote_requests`, `quote_request_suppliers`, `sim_clock`, `failures` without its `datetime('now')` times, outbox bytes and each thread's checkpoint values and next node.
  Both passed on their first run, since increments 5 to 8 already read time only from the clock and keep state in SQLite; to prove the real-time test can fail, the `.eml` `Date` was switched to `datetime.now()` and the test failed, then the change was reverted.
  `uv run pytest -q tests/test_quote_request.py tests/test_sim_clock.py` 39 passed; ruff check and format clean.
  The real-time source is patched in-process (`time.time` and `datetime.datetime` in the `datetime` module and the loaded `purchase_cycle` and `langgraph` modules), because a subprocess cannot change the system clock; the reply inbox is one shared folder so `reply_file` paths match across runs.
- [x] 10. `quotes list` (C10) - check: a test on a temporary database with requests `waiting`, `ready_for_decision` and `no_offers` checks SKU, quantity, status, replied and pending suppliers, next reminder and deadline per row.
  Evidence: two new tests in `tests/test_quote_request.py` failed first on the missing `list` choice of `quotes`, then passed with `quotes.list_requests` and `cmd_quotes_list`; `uv run pytest -q` over the quotes, clock, seed and clarify, failures and resume CLI tests 92 passed; ruff check and format clean.
  Each row prints reference, SKU, quantity, status, replied and pending supplier codes (`-` when none), next reminder and deadline; the next reminder is the request's `reminder_at` while it is `waiting` and no reminder was sent, and `-` otherwise, since D5 sends one reminder per request (decided in execution, batch B).
  The test raises stock after each close to keep three requests; this first read a closed request as no longer covering its units, which contradicts C4 and was corrected in review round 1 (finding 1), where a closed request keeps covering its units.

### Batch C - Evaluation `quote_wait`
- [x] 11. At least 20 scripted timelines in `evals/datasets/quote_wait/` with expected final states, meeting the spec coverage minimums (at least 3 each for all replied, some replied, none replied, late reply, duplicate reply, re-run scan and repeated tick, and at least 3 with a process restart) (C11) - check: a test over the scenario files counts the total and each coverage tag and fails below a minimum.
  Evidence: new `tests/test_quote_wait_eval.py` failed first on the missing `quote_wait_eval` module, then 3 passed: 24 timelines `QW-001` to `QW-024` built by `quote_wait_eval.build_items`, every tag at least 3, files equal to the build, tags consistent with steps and expected requests; ruff check and format clean.
  Each timeline holds its steps (`order`, `scan`, `advance`, `receive`, `tick`; a step flagged `new_process` runs in its own process) and the hand-written expected requests (reference, SKU, quantity, status, replied and reminded suppliers), the `failures` count and the outbox file names (decided in execution, batch C).
- [x] 12. Runner, deterministic grader over the database, supplier outbox and `failures` table, critical-error counter for the eight spec errors, scenario success with Wilson interval, report columns of phase 05, and registration in `harness.SUITES` (C11, C1) - check: tests that inject each critical error into a measured result and prove a non-zero exit of `purchase-cycle eval --suite quote_wait`; a test that a success below 100% exits non-zero; `npm run eval` report with ten evaluations saved as `.evidence/fase-06/eval-quote-wait.txt`, `quote_wait` 100% with 0 critical errors and the Wilson upper bound of the zero-event rate.
  Evidence (2026-10-11): 13 new tests in `tests/test_quote_wait_eval.py` failed first on the missing `CRITICAL_KINDS`, then 16 passed: each of the eight critical kinds injected into the step snapshots (or the final threads) of QW-004 exits 1 naming the kind, a wrong expectation exits 1, a clean timeline exits 0 with no network, and `cli.main(["eval", "--suite", "quote_wait"])` exits 0 at 100% per tag; ruff check and format clean.
  `npm run eval` (exit 0, 179 s) in `.evidence/fase-06/eval-quote-wait.txt` reports ten evaluations; `quote_wait` 24 scenarios, success 100.0% [86.2, 100.0], 0 failed scenarios with Wilson upper bound 13.8%, critical errors 0.
  The runner snapshots the database and outbox before and after each step; the shortfall for the quantity check is computed from `received` order lines minus `on_hand` (minus the units of every earlier request since review round 1), apart from `quotes.shortfalls`, and the injection tests call `quote_wait_eval.evaluate`, the function `eval --suite quote_wait` runs (decided in execution, batch C).
  At 100% success the zero-event rate is the failed scenarios, so its Wilson upper bound follows the `scenario_success` row (decided in execution, batch C).

### Batch D - Demo, documentation and secrets
- [x] 13. Sample orders and supplier replies in `examples/quotes/` and `purchase-cycle quotes-demo` with each step in its own process, printing per step the simulated time, requests started, emails written, replies received and every request status; it ends with one request `ready_for_decision` after a reminder and one `no_offers` (C12) - check: a pytest inside `check` runs the demo with the network blocked and asserts both end states; output saved as `.evidence/fase-06/quotes-demo.txt`.
  Evidence (2026-10-11): new `tests/test_quotes_demo.py` failed first on the missing `quotes-demo` command, then passed (34 s): exit 0, empty stderr, no network attempt, nine processes (the demo and one per step), every per-step label printed, QR-0001 `ready_for_decision` with a reply from a reminded supplier, QR-0002 `no_offers` parked in `failures`, and the nine outbox names; ruff check and format clean.
  `uv run purchase-cycle quotes-demo` (exit 0) in `.evidence/fase-06/quotes-demo.txt`: scan, clock advance, receive, clock advance, tick with four reminders, receive, clock advance to day 5 and the closing tick.
  The two sample orders are loaded by the demo process itself into a fresh database in `--workdir` (default `data/quotes-demo`), like `orders-demo`, because no CLI command stores an order without a model call; the eight steps then run as `python -m purchase_cycle.cli` processes (decided in execution, batch D).
- [ ] 14. README sections in English for the quotes module, the simulated clock, the `clock` and `quotes` commands, `quotes-demo`, how `quote_wait` was built and run, and the known limitations of the spec (C15) - check: followed step by step in the clean clone of increment 16.
  Evidence (2026-10-11): README gains the phase 06 status line, `Request supplier quotes` with `The simulated clock`, `Commands` and `Run the quotes demo`, the `quote_wait` dataset and evaluation paragraphs, ten evaluations instead of nine, `quote_wait` in the not uploaded list and the five spec limitations under `Limits`.
  The commands block was run on a copy of the demo database: `clock show`, `quotes scan`, `quotes tick`, `quotes receive` (a reply to a closed request rejected with its reason) and `quotes list` work as written, and a second `scan` and `tick` wrote nothing new; the clean-clone walk-through stays with increment 16.
- [ ] 15. Secret scan of the supplier seed, sample replies, outbox examples and scenario files, asserting they are tracked (C14) - check: `tests/test_secrets.py` gains a phase 06 test.
  Evidence (2026-10-11): new `test_phase_06_files_are_tracked_and_hold_no_keys` passes with the other 7 secret tests (8 passed); it asserts `suppliers.py`, `quotes.py`, the 24 `quote_wait` timelines and the `examples/quotes/` files are tracked and hold no key pattern, and an untracked probe file in `examples/quotes/` made it fail with "is not versioned"; ruff check and format clean.
  Outbox emails are never versioned, so the test renders a request and a reminder through `quotes.scan` and `quotes.tick` on a temporary database and scans them (decided in execution, batch D).

### Batch E - Validation and closing
- [ ] 16. `npm run check`, unchanged-behaviour evidence and fresh-clone run with the Anthropic and LangSmith variables unset and no `.env` (C1, C13, C15) - check: `uv sync`, `npm ci` and `npm run check` in a clean clone with ten evaluations in replay; phase 01 to 05 tests passing and unchanged apart from deviation DV-01; the nine earlier evaluations with every gate PASS; `git diff --stat ada28ab -- evals/recordings evals/datasets evals/baselines` empty apart from the new `quote_wait` files; `orders-demo` replay output equal to increment 1 once run-specific ids are masked.
  Evidence:
- [ ] 17. Adversarial review with `sdd-review` through `sdd-delivery` (at most two rounds), findings and fixes recorded below with regression tests; re-run `npm run check` after the fixes.
  Evidence:
- [ ] 18. Results per criterion in the table below, master plan row 06 status, open items and limitations, candidate learnings; delivery on the branch and a PR to `main`, never a merge.
  Evidence:

## Deviations
| ID | Summary | Affects criteria | Status |
|---|---|---|---|
| DV-01 | The phase 05 test `tests/test_whatsapp_eval.py` (suite order of `eval all`) gains `quote_wait` at the end of its expected list, because `ALL_SUITES` grew in increment 12; the behaviour of the nine earlier suites is unchanged | C13 | Accepted by the owner (review round 1, finding 3) |

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Results
Per criterion: command or path run, observed result and evidence reference.
Pending items, limitations and what could not be checked, stated plainly.

| Criterion | Result | Evidence |
|---|---|---|

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.
