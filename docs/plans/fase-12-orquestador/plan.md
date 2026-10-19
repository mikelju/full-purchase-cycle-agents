# Phase 12 - Orchestrator: plan and results

Status: in progress
Spec: `spec.md` (approved 2026-10-19, frozen)
Base: branch `fase-12-orquestador` at `a8186f4` (change 018 merged into `main` through PR #29); the unchanged-behaviour reference of C11 is the code at `a8186f4`.

## Design notes
Written for the owner decisions of 2026-10-19 recorded in the spec: option A for D1 to D5, with the D2 amendment (four demo orders, 5 USD ceiling).
- New module `purchase_cycle/cycle.py`: the stage functions, the `purchase_cycle` graph and `run_cycle(db_path, checkpoint_path, folders, client, interrupt_after=None)` used by the CLI, the evaluation and the demo.
  - Stages in order, each a node that calls the existing module functions with the paths the CLI commands pass:
    - `orders`: `router.run_inbox` on the order inbox folder.
    - `quotes`: `quotes.scan`, `quotes.receive` on the supplier replies folder, `quotes.tick`, then `quote_decision.decide`.
    - `reconcile`: `reconciliation.scan` on the supplier documents folder, then `claims.draft_claims`.
    - `receipts`: `receipts.run_receipts`.
    - `sales`: `sales.run_sales`, then `customer_responses.draft_responses`.
  - The exact signatures are read from the CLI handlers in `cli.py`, `receipts_cli.py` and `sales_cli.py`; where a handler does more than call one function (for example opening the checkpointer), the stage reproduces that call sequence, never the module logic.
  - Orchestrator state holds per-stage counts and the list of pending decisions only; thread id `cycle-run-NNNN`, the last run resumed when it stopped before its end, as `sales.run_sales` does.
  - Pending decisions are read after the run from the module threads paused at an `interrupt` (clarification, quote approval, claim review, response review), each printed with the command that resolves it (`clarify answer`, `quotes approve|reject`, `reconcile approve|reject`, `sales approve|reject`).
- New module `purchase_cycle/cycle_cli.py` with `add_cycle_commands(sub)` (`cycle run [--mode] FOLDERS...`), registered from `cli.main` as `sales_cli` is; `cli.py` only gets the registration line, the `cycle-demo` handler and the `dashboard` parser.
- New evaluation `purchase_cycle/evaluation/purchase_cycle_eval.py` with scenarios in `evals/datasets/purchase_cycle/`, following the `goods_receipt_eval` and `sales_reconciliation_eval` scenario, oracle and gate patterns; it uses a counting test double of the model client fed with hand-written answers, never a live call.
- Summaries: `harness` writes `data/eval_summaries/<suite>.json` after each suite with the figures it already prints; `data/` is already git-ignored; the printed report and exit codes stay byte for byte the same.
- New module `purchase_cycle/dashboard.py`: reads the summaries and writes `data/dashboard.html`, standard library only, inline CSS and SVG, no external URL.
- Demo `cycle-demo` (D2): versioned inputs in `examples/cycle/` and recording `evals/recordings/cycle_demo.jsonl`; four orders on one database:
  - A (web form, in stock): served, invoiced, signed note received in full, sales `matched`.
  - B (email, ambiguous line): clarification answered by the scripted customer reply, served, invoiced, signed note with damaged units, response with credit note approved.
  - C (WhatsApp, short of stock): quote requests, supplier replies over simulated days, offer approved, purchase order, supplier invoice with a higher unit price, claim approved and sent, receipt serves C, invoiced.
  - D (web form, short of stock): quote approved, purchase order, supplier invoices and delivers fewer units, claim rejected, receipt adds the short quantity, D stays waiting, next pass starts a quote request for the remainder, left pending.
  - Scripted operator steps between passes: `clock advance`, the clarification answer, `quotes approve`, `reconcile approve` (C) and `reject` (D), dropping the supplier documents and signed notes into their folders, `sales approve`.
- Cost (D2): expected about 20 to 30 model calls for one record run, about 0.15 USD at Claude Haiku 4.5 list prices (1 USD per million input tokens, 5 USD per million output tokens); the phase ceiling is 5 USD.

## Rules for executors
- Execution starts from the approved frozen spec; changing a criterion needs a deviation approved by the owner.
- Each increment is sized for one Sonnet writer with a context ceiling of about 100k tokens; it starts in a fresh session from this file and Git, marks its increment `[x]` with its evidence and commits with a standalone `git commit -m "Phase 12: <increment>"` and no co-author line.
- Run git from the repository root, one command per call, no `cd` prefix; never push, merge or write to `main` outside increment 18.
- Failing tests first: each increment adds its tests, runs them to see them fail for the expected reason, then implements.
- Targeted checks during an increment (`uv run pytest -q <files>`, `uv run ruff check` and `uv run ruff format --check` on the touched files); the coordinator runs `npm run check` between batches, and the next batch only starts from a green check.
- `npm run check` takes about 15 minutes: run it in the background with output to a log file under `.evidence/fase-12/`, read only the tail, and never start two at once.
- Every offline command runs with `unset ANTHROPIC_API_KEY LANGSMITH_API_KEY` first and makes no model call; only increment 14 uses the owner's key.
- The Windows machine can raise subprocess launch errors (exit code 0xC0000142) when several suites or subprocess tests run concurrently; rerun the failing command alone before treating it as a failure, and note the rerun in the evidence.
- Search before reading; read large files by chunks (`cli.py`, `claims.py`, `receipts.py`, `sales.py`, `quote_decision.py`, `harness.py` are long); reuse `graph.sqlite_checkpointer`, `db.park_failure`, `sim_clock`, the module `_paused` helpers, the `*_demo` handlers in `cli.py` as the pattern for `cycle-demo`, the phase 11 recording test helper, `stats.wilson_interval`.
- No new dependencies; no change to phase 01 to 11 behaviour, prompts, recordings, datasets, baselines, gates, sample documents or graphs; the only earlier code changes allowed are the `cli.main` registration, the `cycle-demo` and `dashboard` parsers, the harness registration and the summary writer; every earlier test edit is a deviation recorded below.
- The agent never reads `.env`; a blocked command is reported, not worked around.
- Evidence files go to `.evidence/fase-12/` (git-ignored, so commits hold the plan line only).

## Increments
Each increment leaves the product working and covers concrete criteria.
This file is the durable state: a new session resumes from here and from Git.

### Batch A - Baseline (offline)
- [x] 1. Unchanged-behaviour baseline before any code change (C11) - files: `docs/changes/018-*.md` (status line only, set to "integrated (merged via PR #29)" after confirming with `gh pr list --state merged`); check: `orders-demo`, `quotes-demo`, `reconcile-demo`, `receipt-demo` and `sales-demo` in replay with `--workdir` outside the repo, keys unset, output saved as `.evidence/fase-12/<demo>-before.txt`; `uv run purchase-cycle eval` output saved as `.evidence/fase-12/eval-before.txt` with every exit code noted.
  - Evidence: PR #29 confirmed MERGED with `gh pr list --state merged`; change 018 status line set. Five demos replay with `--workdir` in the temp folder, keys unset, all exit 0 (orders 123 lines, quotes 135, reconcile 352, receipt 118, sales 245). `purchase-cycle eval --mode replay --split test` exit 0, 38 PASS, 0 FAIL, `failures: 0` (`.evidence/fase-12/eval-before.txt`).

### Batch B - Orchestrator graph and command (offline)
- [x] 2. Stage functions and the `purchase_cycle` graph (C2) - files: new `src/purchase_cycle/cycle.py`, new `tests/test_cycle.py`; check: `uv run pytest -q tests/test_cycle.py`: the compiled graph has the nodes `orders`, `quotes`, `reconcile`, `receipts`, `sales` in that order with the SQLite checkpointer; for a small world (one order per channel in stock, one short of stock) one `run_cycle` gives the same rows (every table, ids included) and files (outbox, notes, invoices, paths normalised) as the module commands run one by one on a copy of the same inputs; the model client is a counting test double with hand-written answers.
  - Evidence: `uv run pytest -q tests/test_cycle.py` 3 passed; graph nodes in order with the SQLite checkpointer; one `run_cycle` over 4 orders (web form, email, WhatsApp in stock, web form short of stock with a quote request) gives the same rows (wall-clock stamps masked) and files as `cli.main` route, quotes, reconcile, receipts and sales commands on a copy, with equal model call sequences.
- [x] 3. Hand-off through the shared database (C3) - files: `tests/test_cycle.py`; check: one test follows one order id short of stock across passes (with the test performing `clock advance`, offer approval and dropping supplier documents between passes): quote request, approved offer, purchase order, reconciled delivery note, stock and served order, customer delivery note, customer invoice; each assertion reads the tables, not the printed output.
  - Evidence: `uv run pytest -q tests/test_cycle.py` 4 passed; one web form order short of stock followed across three `run_cycle` passes (with `clock advance`, `quotes approve` and dropped reply, invoice and delivery note) from quote request to purchase order, reconciled note, goods receipt, served order, customer delivery note and invoice, all asserted from the tables; no change to `cycle.py` needed.
- [x] 4. Pending decisions listed, never taken, and continuation (C4) - files: `src/purchase_cycle/cycle.py`, `tests/test_cycle.py`; check: one test per pause kind (clarification, quote approval, claim, customer response): after the pass the module thread is paused in its SQLite checkpoint, the run result and printed output list it with its resolving command, and no decision row changed; after the existing decision function, the next pass continues from there; a test asserts `cycle.py` calls no approve, reject or answer function.
  - Evidence: `uv run pytest -q tests/test_cycle.py` 9 passed; `run_cycle` returns `pending` (kind, reference, thread id, approve/reject or answer/close commands) read from the rows; per pause kind the module thread is paused in the SQLite checkpoint, a second pass changes no decision row, and after the existing decision function the next pass continues; AST test shows `cycle.py` calls no approve, reject or answer function. Found for increment 5: a pass over an unchanged doubtful web form file asks a new clarification (thread of the new run id), so idempotence needs a fix there. `run_cycle` prints nothing; printing is increment 6.
- [x] 5. Idempotence and crash resumption (C5) - files: `src/purchase_cycle/cycle.py`, `tests/test_cycle.py`; check: a second pass with nothing new changes no row, writes no file and makes zero model calls; for each stage, an injected crash after it (`interrupt_after`) then a new pass ends with the same rows and files as an uninterrupted pass, and the resumption makes zero model calls.
  - Evidence: `uv run pytest -q tests/test_cycle.py` 15 passed: a second pass changes no row, writes no file (sizes and mtimes) and makes zero model calls; for each of the five stages a pass stopped after it and run again ends with the same rows and files as an uninterrupted pass with zero resumption calls. Fixes: web form files already run by an earlier thread are skipped through `router.run_inbox(skip=...)` (the module command itself still asks again on a repeated web form file), and `whatsapp_order.write_outbox` no longer rewrites an identical reply.
- [ ] 6. `cycle run` command and restart across processes (C2, C4, C6) - files: new `src/purchase_cycle/cycle_cli.py`, `src/purchase_cycle/cli.py` (registration line only), new `tests/test_cycle_cli.py`; check: `uv run pytest -q tests/test_cycle_cli.py`: `cycle run` prints per stage what it processed and every pending decision with its command; a second run prints nothing new; each pass and each decision as a separate `python -m purchase_cycle.cli` subprocess against the same database and checkpoint files gives the same final rows and files as the in-process sequence, with the recordings file removed before the decision subprocesses so any model call fails the test.
  - Evidence:

### Batch C - Orchestrator evaluation (offline)
- [ ] 7. `purchase_cycle` suite: at least 16 scenarios, runner, grader, critical-error oracle, gates and registration (C8, C1) - files: new `src/purchase_cycle/evaluation/purchase_cycle_eval.py`, new `evals/datasets/purchase_cycle/`, new `tests/test_purchase_cycle_eval.py`, `src/purchase_cycle/evaluation/harness.py` (`SUITES` and `ALL_SUITES` at the end, not `UPLOAD_SUITES`), suite-order tests (deviation, additive only); check: `uv run pytest -q tests/test_purchase_cycle_eval.py` counts at least 2 scenarios per coverage tag of the spec "Evaluation design" and proves a non-zero exit for each injected critical error (decision taken, duplicate after second pass or resumption, stage before its input, model call on a second pass or resumption, final state different from the module command sequence); `uv run purchase-cycle eval --suite purchase_cycle` report with the Wilson interval saved as `.evidence/fase-12/purchase-cycle-replay.txt`.
  - Evidence:

### Batch D - Dashboard (offline)
- [ ] 8. One machine-readable summary per suite (C9, C11) - files: `src/purchase_cycle/evaluation/harness.py` (summary writer only), new `tests/test_eval_summaries.py`; check: `uv run pytest -q tests/test_eval_summaries.py`: after a suite run, `data/eval_summaries/<suite>.json` holds the case or scenario count, every metric with its Wilson interval, every gate with threshold and verdict, critical errors, baseline date and per-category figures, equal to the printed figures; a failed or missing suite is recorded as such; a test on a temporary output folder asserts the printed report and exit code are unchanged by the writer; `uv run purchase-cycle eval` output compared with `eval-before.txt` from increment 1, saved as `.evidence/fase-12/eval-summaries.txt`.
  - Evidence:
- [ ] 9. `purchase-cycle dashboard` static HTML (C9) - files: new `src/purchase_cycle/dashboard.py`, `src/purchase_cycle/cli.py` (parser and handler only), new `tests/test_dashboard.py`; check: `uv run pytest -q tests/test_dashboard.py` over fixture summaries: one section per suite of `ALL_SUITES` (twenty-one), every value equal to its summary, missing and failed suites shown as such, no `http`, `https`, `src=` to a remote host or `<link>` to an external resource, inline SVG only; then `uv run purchase-cycle dashboard` after `npm run eval`, the page opened in the browser pane with the network not needed, screenshot saved as `.evidence/fase-12/dashboard.png`.
  - Evidence:

### Batch E - Demo, documentation and secrets (offline)
- [ ] 10. `cycle-demo` with the four-order scenario (C7) - files: `src/purchase_cycle/cli.py` (handler and parser), new `examples/cycle/` (web form orders A and D, email order B, WhatsApp order C, the scripted clarification reply, supplier replies, supplier invoices and delivery notes for the two purchase orders with C at a higher unit price and D short, signed notes for A in full and B with damaged units), new `tests/test_cycle_demo.py`; check: `uv run pytest -q tests/test_cycle_demo.py` with the network blocked, each step in its own process, a temporary recordings file written by the test with hand-written answers keyed by the demo prompts (phase 11 increment 15 approach), asserting from the database the four final states (A matched, B credit note sent, C claim sent and order invoiced, D waiting with a pending quote request) and the printed stage passes, pending decisions, operator decisions and one summary line per order; record in the evidence the number of model calls the run asks for (the input of the cost estimate of increment 14).
  - Evidence:
- [ ] 11. Earlier demos unchanged (C11) - check: the five module demos rerun in replay with `--workdir` outside the repo, keys unset, saved as `.evidence/fase-12/<demo>-after.txt` and compared with increment 1 once run ids and paths are masked; `git diff --stat a8186f4 -- evals/recordings evals/datasets evals/baselines` with `--diff-filter=MDR` empty.
  - Evidence:
- [ ] 12. Documentation and secrets (C12, C14) - files: `README.md`, `tests/test_secrets.py` (new test only), `docs/security.md` (new SEC entries only where the phase adds a surface, for example the dashboard HTML escaping); check: `uv run pytest -q tests/test_secrets.py` lists every new file of the phase (source, `examples/cycle/`, scenario files, dashboard fixtures, and `evals/recordings/cycle_demo.jsonl` once it exists); README sections for the orchestrator and its stages, `cycle run`, `cycle-demo` in replay and record mode, the dashboard, the local Phoenix trace, the `purchase_cycle` evaluation and the known limitations; demo cost figures left as placeholders filled in increment 14.
  - Evidence:

### Batch F - Live record and trace (owner's key, LangSmith tracing off)
All live spend of the phase happens here, once, after `npm run check` is green with batches A to E.
- [ ] 13. Pre-flight cost check (C13) - check: from the call count of increment 10, estimate the record cost at 0.006 USD per call and write it here with the running phase total (0 USD before this batch); proceed only if the estimate is under 1 USD; confirm `LANGSMITH_TRACING` is false and the LangSmith key empty in the shell that will run the record, without reading `.env`.
  - Evidence:
- [ ] 14. `cycle-demo --mode record` once live (C7, C13) - files: new `evals/recordings/cycle_demo.jsonl`, `tests/test_cycle_demo.py` (switch to the versioned recording, remove the temporary-recording writer), `README.md` (cost figures); check: one record run by the owner or with the owner present, output saved as `.evidence/fase-12/cycle-demo-record.txt` with calls, input and output tokens and cost at Haiku 4.5 list prices; the four final states reached; `git diff --stat -- evals/recordings` shows only the new file; replay with no key gives the same end state (`.evidence/fase-12/cycle-demo-replay.txt`); `uv run pytest -q tests/test_cycle_demo.py tests/test_secrets.py` pass with no key; running phase total written here.
  - Stop rule: before every live run, the running phase total plus 1 USD must stay at or under 5 USD, and at most three record runs are made; a run that ends without the four final states is not repeated until its cause is fixed offline; if a run reports more than twice the calls counted in increment 10 or more than 1 USD, stop and ask the owner.
  - Evidence:
- [ ] 15. Local Phoenix trace of a replay `cycle run` (C10) - check: outside `check`, with `npm run phoenix` running, `PHOENIX_TRACING=true` and LangSmith tracing off, one replay `cycle run` on the demo world; spans read back from the Phoenix API into `.evidence/fase-12/c10-spans.txt` show one trace with a `purchase_cycle` span, one child span per stage and the module node spans under their stage; no cost; if the module spans do not nest under their stage, record a deviation before closing.
  - Evidence:

### Batch G - Validation and closing
- [ ] 16. `npm run check` in a fresh clone with no key and no `.env` (C1, C11) - check: `npm ci`, `uv sync` and `npm run check` in a fresh clone outside the repo (`--no-hardlinks`), Anthropic and LangSmith variables unset, run in the background with the log at `.evidence/fase-12/fresh-clone-check.log`, tail read only; twenty-one evaluations in replay with every gate PASS; `git diff --stat a8186f4 -- evals/recordings evals/datasets evals/baselines` lists added files only.
  - Evidence:
- [ ] 17. Adversarial review with `sdd-review` through `sdd-delivery`, at most two rounds, over `a8186f4..HEAD` with new files included; findings and fixes recorded below with regression tests; `npm run check` rerun after the fixes (background, one at a time); a third round needs an owner decision.
  - Evidence:
- [ ] 18. Results per criterion, master plan row 12 status, open items and limitations, candidate learnings; delivery per `.agents/skills/sdd-delivery/SKILL.md`: push the branch to `origin` and open a PR to `main` with `--body-file` (goal, criteria with evidence, review table, pending items), never merge or approve.
  - Evidence:

## Deviations
| ID | Summary | Affects criteria | Status |
|---|---|---|---|

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Results
Per criterion: command or path run, observed result and reference to the evidence.
Open items, limitations and what could not be checked, said plainly.

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.
