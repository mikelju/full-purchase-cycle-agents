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
- [ ] 6. `quotes receive <folder>` with reference and sender checks and duplicate handling (C6) - check: one test per case: accepted reply sets `replied_at` and the file path; unknown reference, closed request and foreign sender are rejected with a reason and leave rows, outbox and checkpoint state unchanged; the same reply twice changes nothing the second time.
  Evidence:
- [ ] 7. `quotes tick` and reminders (C7) - check: scripted timelines counting outbox files: a tick before day 3 writes nothing; a tick at or after day 3 writes one reminder to each supplier that has not replied and none to those that replied; a second tick at the same or a later time before the deadline writes nothing new.
  Evidence:
- [ ] 8. Closing: early close when every supplier replied, deadline close as `ready_for_decision` or `no_offers` with its `failures` row, no email and rejected replies after close (C8) - check: one test per outcome counting `quote_requests` status, `failures` rows with status `needs_review` and step `quote_wait`, and outbox files before and after a later tick and reply; a single tick past the deadline sends no reminder.
  Evidence:
- [ ] 9. Cross-process wait and real-time independence (C9, C3) - check: a test runs scan, receive, clock advance and tick each in its own subprocess on the same database and checkpoint files and compares the final rows and outbox bytes with the same timeline run in one process; a test runs one timeline twice with the real-time source of the process patched to two different dates and compares rows and outbox bytes.
  Evidence:
- [ ] 10. `quotes list` (C10) - check: a test on a temporary database with requests `waiting`, `ready_for_decision` and `no_offers` checks SKU, quantity, status, replied and pending suppliers, next reminder and deadline per row.
  Evidence:

### Batch C - Evaluation `quote_wait`
- [ ] 11. At least 20 scripted timelines in `evals/datasets/quote_wait/` with expected final states, meeting the spec coverage minimums (at least 3 each for all replied, some replied, none replied, late reply, duplicate reply, re-run scan and repeated tick, and at least 3 with a process restart) (C11) - check: a test over the scenario files counts the total and each coverage tag and fails below a minimum.
  Evidence:
- [ ] 12. Runner, deterministic grader over the database, supplier outbox and `failures` table, critical-error counter for the eight spec errors, scenario success with Wilson interval, report columns of phase 05, and registration in `harness.SUITES` (C11, C1) - check: tests that inject each critical error into a measured result and prove a non-zero exit of `purchase-cycle eval --suite quote_wait`; a test that a success below 100% exits non-zero; `npm run eval` report with ten evaluations saved as `.evidence/fase-06/eval-quote-wait.txt`, `quote_wait` 100% with 0 critical errors and the Wilson upper bound of the zero-event rate.
  Evidence:

### Batch D - Demo, documentation and secrets
- [ ] 13. Sample orders and supplier replies in `examples/quotes/` and `purchase-cycle quotes-demo` with each step in its own process, printing per step the simulated time, requests started, emails written, replies received and every request status; it ends with one request `ready_for_decision` after a reminder and one `no_offers` (C12) - check: a pytest inside `check` runs the demo with the network blocked and asserts both end states; output saved as `.evidence/fase-06/quotes-demo.txt`.
  Evidence:
- [ ] 14. README sections in English for the quotes module, the simulated clock, the `clock` and `quotes` commands, `quotes-demo`, how `quote_wait` was built and run, and the known limitations of the spec (C15) - check: followed step by step in the clean clone of increment 16.
  Evidence:
- [ ] 15. Secret scan of the supplier seed, sample replies, outbox examples and scenario files, asserting they are tracked (C14) - check: `tests/test_secrets.py` gains a phase 06 test.
  Evidence:

### Batch E - Validation and closing
- [ ] 16. `npm run check`, unchanged-behaviour evidence and fresh-clone run with the Anthropic and LangSmith variables unset and no `.env` (C1, C13, C15) - check: `uv sync`, `npm ci` and `npm run check` in a clean clone with ten evaluations in replay; phase 01 to 05 tests unchanged and passing; the nine earlier evaluations with every gate PASS; `git diff --stat ada28ab -- evals/recordings evals/datasets evals/baselines` empty apart from the new `quote_wait` files; `orders-demo` replay output equal to increment 1 once run-specific ids are masked.
  Evidence:
- [ ] 17. Adversarial review with `sdd-review` through `sdd-delivery` (at most two rounds), findings and fixes recorded below with regression tests; re-run `npm run check` after the fixes.
  Evidence:
- [ ] 18. Results per criterion in the table below, master plan row 06 status, open items and limitations, candidate learnings; delivery on the branch and a PR to `main`, never a merge.
  Evidence:

## Deviations
| ID | Summary | Affects criteria | Status |
|---|---|---|---|

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
