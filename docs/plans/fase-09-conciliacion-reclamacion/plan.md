# Phase 09 - Reconciliation, claim: plan and results

Status: in progress
Spec: `spec.md` (approved 2026-10-14, frozen)
Base: branch `fase-09-conciliacion-reclamacion` at `d05c2af`, which holds the phase 08 work over `main` at `e48bdec` (phase 08 is not merged yet); the unchanged-behaviour reference of C18 is the code at `d05c2af`.

## Design notes
Written for the owner decisions D1 to D10 of 2026-10-14 recorded in the spec.
- New module `purchase_cycle/claims.py`: candidates, claim facts and amount in dispute, drafting call, fact check, template, storage, the `claim` graph (`build_claim_graph` with the nodes `draft`, `review`, `send`, `close`), and the `draft_claims`, `approve_claim`, `reject_claim`, `claim_of` functions used by the CLI and the evaluations.
- New module `purchase_cycle/review.py`: the side-agnostic `review` node (D6, C11).
  It takes `state["message"]` with `to`, `subject` and `body`, calls `interrupt({"message": ...})`, and returns `decision` (`approve`, `edit` or `reject`) and `final_body` (the draft for `approve`, the given text for `edit`, None for `reject`).
  It imports only `langgraph`; a test asserts it imports no `claims`, `reconciliation`, `reconcile_match`, `suppliers` or `db` symbol.
- Candidates (C3): `reconciliations` rows with result `discrepancies` and no `claims` row, in invoice id order.
  An invoice that moves from `awaiting_delivery_note` to `discrepancies` on a later scan is found by the same query on the next `reconcile claims`.
- Sign convention of `discrepancies.amount`, verified against `reconcile_match.classify` at `d05c2af` (point (a)):
  - `price`: `amount = sum((invoice price - order price) * quantity)` over the lines that deviate in the same direction as the largest deviation; positive means the invoice charges more than the order.
    A price row with no stated invoice price has `found`, `unit_amount` and `amount` null.
  - `quantity`: `amount = (invoiced quantity - expected quantity) * order unit price`, where expected is the order or the delivered quantity, whichever differs more; positive means we are invoiced for more.
  - `extra_line`: `amount` is the line total (or unit price times quantity), always positive, null when the line has no price; `sku` is null and `text` holds the free product text when the line did not resolve.
  - `missing_line`: `amount = ordered quantity * order unit price`, always positive, but it is never charged, so it is in our favour.
  - Observed in the phase 08 demo database: `price` +20.00, `quantity` -25.00, `extra_line` +10.00 and +108.00, `missing_line` +108.00.
- Amount in dispute (D5): the sum of `amount` over `price` and `quantity` rows with a positive amount and over `extra_line` rows with a non-null amount, rounded to cents.
  Negative price and quantity rows and `missing_line` rows are listed for correction and not added; a null amount is listed with no amount.
  An invoice whose rows are all in the supplier's favour gets a claim with an amount in dispute of 0.00 EUR asking for a corrected invoice; this follows D5 and is noted in the README limitations.
- Claim facts: a dict with the supplier name, the supplier invoice number (`supplier_documents.number`), the purchase order number (`reconciliation.order_reference`), the invoice reference `INV-NNNN`, the discrepancy rows (`sku` or `text`, `type`, `expected`, `found`, `amount`) and the amount in dispute.
  The prompt holds these facts as one JSON object with sorted keys and amounts formatted with two decimals, so the recording key is stable.
- Model task `CLAIM_DRAFTING` in `llm.py`: name `claim_drafting`, tool `record_claim_body`, instructions `CLAIM_DRAFTING_INSTRUCTIONS`, Pydantic schema `ClaimBody(body: str)` with `extra="forbid"`; path constant `CLAIM_DRAFTING_RECORDINGS_PATH` in `config.py` pointing to `evals/recordings/claim_drafting.jsonl`.
  The instructions ask for plain ASCII English, every amount written as `1234.56 EUR` with no thousands separator, every difference named by SKU (or product text) with its amount, the amount in dispute, and the request for a corrected invoice or a credit note; no greeting or signature rules beyond that.
  No existing task, prompt or recording is touched.
- Recipient, subject and sender by code (C4): recipient `suppliers.email` of the order supplier, subject `CLAIM_SUBJECT = "Claim on invoice {invoice} for purchase order {order}"`, sender `quotes.BUYER`, as the phase 07 purchase order.
- Fact check `check_draft(body, facts) -> list[str]` in `claims.py`, deterministic, an empty list meaning pass (C5):
  - the invoice number and purchase order number appear;
  - every discrepancy row is named by its SKU, or by its product text when the SKU is null, and its absolute amount appears when it is not null;
  - the amount in dispute appears;
  - `corrected invoice` or `credit note` appears, ignoring case;
  - every money amount in the body is within 0.005 of the absolute value of some fact value (expected, found, unit amount, amount, amount in dispute);
  - the body is ASCII and at most `MAX_CLAIM_BODY = 3000` characters.
  A money amount is read by one fixed pattern `AMOUNT_PATTERN`: a number with a decimal point or comma and exactly two decimals, or any number directly preceded or followed by `EUR` or `euro(s)`; integers with no currency word (quantities, SKU digits, document numbers) are not money amounts.
  The same pattern and checks are the grader of `claim_drafting`, defined once in `claims.py` and imported by the evaluation.
- Template fallback: `CLAIM_TEMPLATE`, fixed English text filled from the same facts, with one line per difference; a test asserts that every template filled from the dataset facts passes `check_draft`.
  An `InvalidModelOutput` after the client retries, or a draft that fails `check_draft`, stores the template with source `template` and parks one `failures` row through `db.park_failure` with channel `reconcile`, step `claim_drafting`, thread `claim-INV-NNNN` and status `needs_review`; the other claims go on.
  A `MissingRecording` is not a drafting failure and stops the command, as in phase 08.
- Table `claims` in `db.SCHEMA`, `CREATE TABLE IF NOT EXISTS` (C2): `id`, `reconciliation_id` unique not null references `reconciliations`, `recipient`, `subject`, `draft_body`, `draft_source` checked in (`model`, `template`), `final_body` nullable, `edited` integer 0 or 1, `status` checked in (`awaiting_approval`, `sent`, `rejected`), `drafted_at`, `decided_at` nullable, `outbox_file` nullable.
  `db.TABLES` and `row_counts` stay as they are, so the seed counts do not change; no phase 08 table is altered.
- Graph `claim`: one thread per invoice, `thread_id` `claim-INV-NNNN`, checkpointed with `graph.sqlite_checkpointer`.
  - `draft` reads the stored claim when one exists (no model call), else drafts, checks, stores the row `awaiting_approval` with `drafted_at` from `sim_clock.now`, and puts the message in the state.
  - `review` is `review.review`.
  - `send` writes `CLAIM-INV-NNNN-<supplier code>.eml` through `quotes._write_email` (an existing file is kept) in the supplier outbox and sets `sent`, `final_body`, `edited`, `decided_at` and `outbox_file`; `close` sets `rejected` and `decided_at` and writes nothing.
  - `send` and `close` change the row only from `awaiting_approval`, so a re-run node writes nothing twice.
- Commands, following `quote_decision.approve_request` and `reject_request` (`_paused`, `_resume`, `DecisionRefused`):
  - `reconcile claims [--mode] [--recordings] [--db] [--checkpoints]` invokes the graph for every candidate until the pause and prints each draft with its facts, amount in dispute and source; a candidate whose thread already exists is resumed with no input; a second run prints that nothing is to be drafted.
  - `reconcile approve INV-NNNN [--body-file FILE] [--outbox]` checks the claim exists, is `awaiting_approval` and its thread waits at `review`, reads and refuses an empty or blank file before any change, then resumes with `{"action": "approve"}` or `{"action": "edit", "body": text}`.
  - `reconcile reject INV-NNNN` resumes with `{"action": "reject"}`.
  - Refusals exit 1 with one message on stderr and change nothing.
- `reconcile list` adds `claim=<status>` to the line of an invoice that has a claim; `reconcile show INV-NNNN` adds a claim block (status, source, edited, recipient, subject, draft body, final body, outbox file) after the phase 08 output; an invoice with no claim prints byte for byte what it printed at `d05c2af` (C10).
- Recordings (point (c)): the evaluation records into the new `evals/recordings/claim_drafting.jsonl` and the demo into the new `evals/recordings/claim_demo.jsonl` (`RECONCILE_DEMO_CLAIM_RECORDINGS` in `cli.py`); `reconcile_demo.jsonl` and `document_extraction.jsonl` are not rewritten, so `git diff --stat d05c2af -- evals/recordings evals/datasets evals/baselines` lists added files only.
- Demo (point (b)): at `d05c2af` the phase 08 demo ends with seven reconciliations, four of them `discrepancies` (INV ids of `N-77002` price, `LV-0303` quantity, `F-2026-1004` extra line, `N-77005` extra and missing line), two `matched` and one `awaiting_delivery_note`, observed by running `reconcile-demo` in replay on 2026-10-14.
  Four candidates are enough for the three decisions, so the sample documents and phase 08 output do not change.
  The claim steps, each in its own process: `reconcile claims`, `reconcile list`, `reconcile approve` on the first claim, `reconcile approve --body-file examples/invoices/claim_edit.txt` on the second, `reconcile reject` on the third, `reconcile show` of each decided claim; the fourth claim stays `awaiting_approval` and the summary prints it as still waiting.
  Which invoice gets which decision is fixed in the demo code by invoice number, so the edited text in `claim_edit.txt` names its own invoice.
- `claim_drafting` evaluation: modules `evaluation/claim_dataset.py` (seeded planning script, `SEED = 20261014`, ten categories of at least 6 cases, facts made from catalog SKUs and prices and run through `reconcile_match.classify`, automatic validation, blind export for the clean-context second pass, fixed stratified 25% development and 75% test split) and `evaluation/claim_eval.py` (runner, grader on the model draft before any fallback, metrics with Wilson intervals, critical errors, McNemar regression gate, baseline, `upload_datasets`), modelled on `offer_dataset.py`, `offer_eval.py` and `document_eval.py`.
  Data in `evals/datasets/claim_drafting/` (`plan.jsonl`, `dataset.jsonl`, `second_pass_review.jsonl`), baseline in `evals/baselines/claim_drafting.json`.
- `claim_approval` evaluation: module `evaluation/claim_approval_eval.py` and at least 24 scenarios `evals/datasets/claim_approval/CA-NNN.json`, run through the `claims` functions on a temporary database seeded with stored reconciliations, with drafts from a test double of the model client that counts calls; deterministic grader on the database, outbox, `failures` and printed output, critical errors counted by an oracle apart from the module, as `reconcile_eval.py` does.
- Registration in `harness`: `SUITES` grows by `claim_drafting` then `claim_approval` at the end; `claim_drafting` joins `UPLOAD_SUITES` (C17 needs its uploaded splits), `claim_approval` does not; `ALL_SUITES` gains `claim_approval` when its scenarios land and `claim_drafting` last once its baseline is recorded.
- Earlier tests that pin the suite order change additively (point (d)), each recorded as a deviation when it happens:
  - `tests/test_document_eval.py` asserts `ALL_SUITES[-1] == "document_extraction"` and `ran[-1] == "document_extraction"`;
  - `tests/test_reconcile_eval.py` asserts `ALL_SUITES[-2:] == ("reconciliation_matching", "document_extraction")`;
  - `tests/test_offer_eval.py` asserts `ran[-4:]` ending with `document_extraction`;
  - `tests/test_whatsapp_eval.py` lists the whole `eval all` order;
  - `tests/test_web_form_eval.py::test_upload_command_uploads_every_suite_and_keeps_the_worst_exit` needs a stub for `claim_eval.upload_datasets`.
  - `tests/test_reconcile_demo.py` gains the claim end state; C18 allows it, and it is still listed as a deviation for traceability.
- Cost: about 15 calls per development round for up to three rounds, about 45 for the test record and 4 for the demo, under 1 USD expected against the 5 USD ceiling (D10).

## Rules for executors
- Execution starts from the approved frozen spec; changing a criterion needs a deviation approved by the owner.
- Each batch is sized for one Sonnet writer with a context ceiling of about 100k tokens; it starts in a fresh session from this file and Git, implements its increments in order, marks each one `[x]` with its evidence and commits each increment with a standalone `git commit -m "Phase 09: <increment>"` and no co-author line.
- Failing tests first: each increment adds its tests, runs them to see them fail for the expected reason, then implements.
- Targeted checks during a batch (`uv run pytest -q <files>`); before closing a batch, `npm run check` passes and the next batch only starts from a green check.
- Run checks and tests with `ANTHROPIC_API_KEY` and `LANGSMITH_API_KEY` unset (`unset ANTHROPIC_API_KEY LANGSMITH_API_KEY`), except in batch H, which runs with the owner's key present and LangSmith tracing off.
- The Windows machine can raise subprocess launch errors (exit code 0xC0000142) when several suites or subprocess tests run concurrently; rerun the failing command alone before treating it as a failure, and note the rerun in the evidence.
- Search before reading; read large files by chunks (`cli.py` and `reconciliation.py` are long); reuse `quote_decision._paused`, `_resume` and `DecisionRefused` patterns, `quotes._write_email`, `db.park_failure`, `graph.sqlite_checkpointer`, the `llm` record and replay client and `MODEL_RETRY`, `reconcile_match.classify`, `reconciliation.invoice_reference`, `order_reference` and `invoice_id_from_reference`, `sim_clock`, `stats.wilson_interval`, `stats.mcnemar_exact`, `stats.target_cells` and the phase 05 report and critical-error patterns.
- No new dependencies; the 5 USD ceiling of D10 is a limit, not a budget, and live calls stop before a run that would pass it; the agent never reads `.env`, and live runs that need the owner's key are run by the owner or with the owner present.
- No change to phase 01 to 08 prompts, recordings, datasets, baselines, gates, sample documents, graphs or matching, except the tests named in the design notes, each recorded as a deviation.
- Evidence files go to `.evidence/fase-09/`.

## Increments
Each increment leaves the product working and covers concrete criteria.
This file is the durable state: a new session resumes from here and from Git.

### Batch A - Baseline, schema and claim content
- [x] 1. Unchanged-behaviour baseline before any code change (C18) - check: `orders-demo`, `quotes-demo` and `reconcile-demo` replay output saved as `.evidence/fase-09/orders-demo-before.txt`, `quotes-demo-before.txt` and `reconcile-demo-before.txt`, `npm run eval` output as `eval-before.txt`, `reconcile list` and `reconcile show` of every invoice of the demo database as `reconcile-show-before.txt`, and the tracked files under `evals/recordings`, `evals/datasets` and `evals/baselines` as `evals-files-before.txt`, all from `d05c2af`.
  - Evidence: files saved in `.evidence/fase-09/` (git-ignored); orders-demo, quotes-demo and reconcile-demo exit 0; eval replay exit 0, 28 PASS and 0 FAIL lines; 854 tracked eval files hashed.
- [x] 2. Table `claims` and its seed behaviour (C2) - files: `src/purchase_cycle/db.py`, `tests/test_seed.py` (new tests only); check: `uv run pytest -q tests/test_seed.py`: the table exists and is empty after `seed`, seeding twice gives the same rows, the other tables including the four phase 08 ones keep their rows, a database built with the phase 08 schema gains the table on reseed without losing rows, the status and source checks reject unknown values and a second claim for one reconciliation is rejected.
  - Evidence: `uv run pytest -q tests/test_seed.py` 21 passed.
- [x] 3. Candidates, claim facts and amount in dispute (C3, C4) - files: new `src/purchase_cycle/claims.py`, new `tests/test_claims.py`; check: `uv run pytest -q tests/test_claims.py`: candidates are the `discrepancies` reconciliations with no claim and none for `matched`, `awaiting_delivery_note` or unlinked documents; an invoice moved to `discrepancies` by a later delivery note becomes a candidate; facts hold every discrepancy row of the invoice; the amount in dispute follows the D5 rule with one test per sign case (positive and negative price, positive and negative quantity, priced and unpriced extra line, missing line, price row with no stated price, mixed invoice).
  - Evidence: `uv run pytest -q tests/test_claims.py tests/test_seed.py` 35 passed; ruff check and format clean.

### Batch B - Drafting, fact check and template
- [x] 4. Task `CLAIM_DRAFTING`, schema, prompt and recordings path (C4) - files: `src/purchase_cycle/llm.py`, `src/purchase_cycle/config.py`, `src/purchase_cycle/claims.py`, `tests/test_claims.py`; check: `uv run pytest -q tests/test_claims.py tests/test_llm.py`: one model call per claim with hand-written recordings; the prompt holds the facts JSON and no recipient or subject; recipient and subject are set by code; record and replay both work, and a missing recording raises `MissingRecording`; the existing tasks and their recording keys are unchanged.
  - Evidence: `uv run pytest -q tests/test_claims.py tests/test_llm.py` 52 passed; ruff check and format clean.
- [x] 5. Fact check, amount pattern and template fallback (C5) - files: `src/purchase_cycle/claims.py`, `tests/test_claims.py`; check: one test per rule of `check_draft` passing and failing (missing reference, missing SKU, missing product text of an unresolved line, missing amount, missing amount in dispute, no requested action, invented amount, amount off by more than half a cent, amount off by less, decimal comma, `EUR` before and after, integers not read as money, non-ASCII, too long); the filled template passes the check for every sign case; a schema-invalid answer after retries and a failing draft each store source `template` and one `failures` row with status `needs_review` and step `claim_drafting`, while the next claim is still drafted with source `model`.
  - Evidence: `uv run pytest -q tests/test_claims.py tests/test_llm.py tests/test_seed.py` 105 passed; ruff check and format clean.

### Batch C - Graph, review node and commands
- [x] 6. Side-agnostic `review` node (C11) - files: new `src/purchase_cycle/review.py`, new `tests/test_review.py`; check: `uv run pytest -q tests/test_review.py`: a one-node graph runs `review` on a plain message with each answer (approve, edit, reject) and returns the decision and final body; an import check on the module source finds no supplier, invoice, reconciliation or database import.
  - Evidence: `uv run pytest -q tests/test_review.py` 4 passed; ruff check and format clean.
- [x] 7. The `claim` graph with `draft`, `review`, `send` and `close` (C6, C7, C8) - files: `src/purchase_cycle/claims.py`, `tests/test_claims.py`; check: after drafting the row is `awaiting_approval` and the thread `claim-INV-NNNN` waits at `review` in the SQLite checkpoint; approve writes one keyed `.eml` with the supplier address, fixed subject and draft body and sets `sent`; edit sends the given text, keeps the draft and marks edited; reject writes nothing; re-running `draft`, `send` or `close` writes no second row or email and calls no model.
  - Evidence: `uv run pytest -q tests/test_claims.py tests/test_review.py` 60 passed; ruff check and format clean.
- [x] 8. `reconcile claims`, `reconcile approve [--body-file]` and `reconcile reject` (C3, C7, C8) - files: `src/purchase_cycle/cli.py`, `src/purchase_cycle/claims.py`, new `tests/test_claims_cli.py`; check: `uv run pytest -q tests/test_claims_cli.py`: `claims` drafts one claim per candidate and prints draft, facts and source; a second run drafts nothing with the recordings removed and a call counter at 0; empty and blank body files are refused and change nothing; a second approve or reject, an unknown id, an invoice with no claim and a decided claim each exit 1 with a message and leave at most one email; `reconciliations`, `discrepancies`, purchase orders and stock are unchanged by every claim command.
  - Evidence: `uv run pytest -q tests/test_claims_cli.py` 7 passed; with test_claims, test_review and `tests/test_reconcile*.py` 143 passed; ruff check and format clean.
- [x] 9. Restart across processes (C9) - files: `tests/test_claims_cli.py`; check: `scan`, `claims` and each decision run as separate subprocesses against the same database and checkpoint files, and the final `claims` rows and outbox files equal the same steps run in one process; the resumption subprocesses run with the recordings file removed, so any model call fails the test.
  - Evidence: `uv run pytest -q tests/test_claims_cli.py` 8 passed; subprocess steps run with the recordings file removed after `claims`.
- [x] 10. `reconcile list` and `reconcile show` with claims (C10) - files: `src/purchase_cycle/cli.py`, `src/purchase_cycle/reconciliation.py` only if the list helper needs the claim status, new tests in `tests/test_claims_cli.py`; check: claims in each status show their status in `list` and their draft, final text, source and status in `show`; for invoices with no claim the output equals the phase 08 output, and `tests/test_reconcile_show.py` and `tests/test_reconcile_results.py` pass unchanged.
  - Evidence: `uv run pytest -q tests/test_claims_cli.py tests/test_reconcile_show.py tests/test_reconcile_results.py tests/test_claims.py` 83 passed; test_reconcile_demo and test_reconcile_scan pass; ruff clean.

### Batch D - Evaluation `claim_drafting`, dataset and grader
- [x] 11. Seeded planning script and at least 60 cases in the ten categories, fixed stratified split, automatic validation and a clean-context second-pass review (C12) - files: new `src/purchase_cycle/evaluation/claim_dataset.py`, `evals/datasets/claim_drafting/`, new `tests/test_claim_dataset.py`; check: `uv run pytest -q tests/test_claim_dataset.py` counts categories (at least 6 each), the split, recomputes every case's discrepancy rows with `reconcile_match.classify` and its amount in dispute with the D5 rule, and re-runs the plan with the same seed for an identical plan; generator output saved as `.evidence/fase-09/claim-dataset.txt`; the second pass is run by a clean-context subagent on the blind export and each disagreement is resolved as `label_fixed` or `annotator_wrong`.
  - Evidence: `uv run pytest -q tests/test_claim_dataset.py`; blind second pass of 80 cases by a clean-context annotator: agree 80, label_fixed 0, annotator_wrong 0, pending 0 (annotator notes on tied quantities, unrounded dispute sum and price plus quantity did not change any value beyond half a cent); `second_pass_review.jsonl` written by `claim-dataset review`.
- [x] 12. Runner, grader, metrics, critical errors, report and gates (C13) - files: new `src/purchase_cycle/evaluation/claim_eval.py`, `src/purchase_cycle/evaluation/harness.py`, new `tests/test_claim_eval.py`, `tests/test_web_form_eval.py` (DV); check: `uv run pytest -q tests/test_claim_eval.py tests/test_web_form_eval.py`: the grader scores the model draft before any fallback; the report shows reference accuracy, discrepancy coverage, amount in dispute accuracy, requested action, invented amount rate and case pass rate with Wilson intervals globally and per category and the zero-event upper bound; tests that force the absolute gate, the McNemar regression gate and a missing baseline to fail prove a non-zero exit of `purchase-cycle eval --suite claim_drafting`; missing recordings exit 2 and a baseline below the threshold exits 3, as `document_eval` does.
  The suite is in `SUITES` and `UPLOAD_SUITES` but out of `ALL_SUITES` until increment 17; the `eval-upload` stub is recorded as a deviation.
  - Evidence: `uv run pytest -q tests/test_claim_eval.py tests/test_web_form_eval.py` 23 passed (synthetic recordings, no model calls); ruff check and format clean; the baseline, recordings and `ALL_SUITES` entry wait for increment 17.

### Batch E - Evaluation `claim_approval` and wiring
- [x] 13. At least 24 scenarios in `evals/datasets/claim_approval/` with the spec coverage, runner, deterministic grader, critical-error counter and gates (C15) - files: new `src/purchase_cycle/evaluation/claim_approval_eval.py`, `evals/datasets/claim_approval/`, new `tests/test_claim_approval_eval.py`; check: `uv run pytest -q tests/test_claim_approval_eval.py`: a test counts the coverage tags (at least 2 each, as listed in the spec); one injection per critical error (email for a rejected or undecided claim, two emails or claims for one invoice, sent text different from the approved text, claim for an invoice with no discrepancies, status not matching the outbox, model call on a re-run or resumption) and a wrong expectation each prove a non-zero exit; replay report saved as `.evidence/fase-09/eval-claim-approval.txt`.
  Evidence: 35 scenarios, replay 35/35 success, 0 critical errors, 14 tests pass, ruff clean; `.evidence/fase-09/eval-claim-approval.txt`.
- [x] 14. Registration of `claim_approval` (C1, C15) - files: `src/purchase_cycle/evaluation/harness.py`, `tests/test_document_eval.py`, `tests/test_reconcile_eval.py`, `tests/test_offer_eval.py`, `tests/test_whatsapp_eval.py` (DV); check: `uv run pytest -q tests/test_claim_approval_eval.py tests/test_document_eval.py tests/test_reconcile_eval.py tests/test_offer_eval.py tests/test_whatsapp_eval.py`: `claim_approval` is in `SUITES` and last in `ALL_SUITES`, not in `UPLOAD_SUITES`; the four earlier tests change only the expected tail of the order, recorded as deviations; `npm run eval` replays fifteen evaluations with every gate PASS. Evidence: 77 tests pass (`tests/test_claim_approval_eval.py` plus the four earlier-phase files); `npm run eval` replays 15 evaluations, every gate PASS, saved as `.evidence/fase-09/eval-after-14.txt`; deviations D3-D6.

### Batch F - Demo extension
- [x] 15. `reconcile-demo` with the claim steps (C16) - files: `src/purchase_cycle/cli.py`, new `examples/invoices/claim_edit.txt`, new tests in `tests/test_claims_cli.py`; check: with a temporary recordings file written by the test for the four demo prompts, the demo runs the phase 08 steps, then `claims`, `list`, the three decisions and `show`, each in its own process; it ends with one `sent` as drafted, one `sent` edited, one `rejected`, one `awaiting_approval`, and no claim for the `matched`, `awaiting_delivery_note` and unlinked documents; the phase 08 part of the output equals `reconcile-demo-before.txt` once paths and run ids are masked.
  The demo reads `evals/recordings/claim_demo.jsonl` by default; the pytest on the versioned file lands in increment 18.
  - Evidence: `uv run pytest -q tests/test_reconcile_demo.py tests/test_claims_cli.py tests/test_claims.py tests/test_review.py` 76 passed (rerun alone, no launch errors); ruff check and format clean; phase 08 part of the demo output identical to `.evidence/fase-09/reconcile-demo-before.txt` once the header line (paths) is dropped; the demo fails with a clear message when the claim recordings file is missing; the new option `--claim-recordings` (default the versioned file) lets the tests use a temporary recordings file.

### Batch G - Live tuning, records and baselines (owner's key, tracing off)
This batch runs with the owner's key present, `LANGSMITH_TRACING` unset or false (`config.configure_tracing` forces it off in replay and the runs set it off explicitly), and stops before any run that would take the phase total over 5 USD.
- [x] 16. Instruction tuning on the development split, at most three rounds (C14, D10) - files: `src/purchase_cycle/llm.py` (`CLAIM_DRAFTING_INSTRUCTIONS` only); check: each live development run saved as `.evidence/fase-09/claim-dev-rN.txt` with its calls and cost; tuning ends when the development case pass rate reaches 95% or after round 3; the instructions are frozen and committed before increment 17.
  - Evidence: one live round on the 20 development claims, `.evidence/fase-09/claim-dev-r1.txt`: case pass rate 100.0% (20/20), 0 critical errors, 20 calls, 21115 input and 4490 output tokens, about 0.04 USD at Haiku 4.5 list prices; the 95% target was met in round 1, so `CLAIM_DRAFTING_INSTRUCTIONS` is frozen unchanged. Running phase total: 20 calls, about 0.04 USD of the 5 USD ceiling.
- [x] 17. Haiku 4.5 baseline on the test split (C13, C14) - files: `evals/recordings/claim_drafting.jsonl` (new), `evals/baselines/claim_drafting.json` (new), `src/purchase_cycle/evaluation/harness.py`, `tests/test_claim_eval.py`, `tests/test_whatsapp_eval.py` and `tests/test_offer_eval.py` (DV); check: one record run of the test split with `purchase-cycle eval --suite claim_drafting --mode record --split test --set-baseline`, live report saved as `.evidence/fase-09/claim-baseline-live.txt` with calls, tokens and cost; the measured case pass rate and critical errors are written here before any gate is set.
  If the case pass rate reaches 95%, the absolute gate is 95%; otherwise execution stops, the owner decides and a deviation is recorded.
  After a pass, `claim_drafting` joins `ALL_SUITES` last, and `npm run eval` replays sixteen evaluations with every gate PASS, saved as `.evidence/fase-09/eval-replay.txt`.
  - Evidence: one record run on the 60 test claims, `.evidence/fase-09/claim-baseline-live.txt`: case pass rate 95.0% (57/60, Wilson [86.3, 98.3]), reference accuracy 100.0%, discrepancy coverage 97.8%, amount in dispute accuracy 98.3%, action accuracy 100.0%, invented amount rate 0.0%; 3 critical errors (5.0%, Wilson [1.7, 13.7]; target 0 not met, reported only): CLM-044 missing amount in dispute, CLM-066 and CLM-072 a missing difference by product text; 60 calls, 63332 input and 13405 output tokens, about 0.13 USD. The pass rate reaches 95%, so the absolute gate is 95.0% (on the edge: exactly 57 of 60). Running phase total: 80 calls, about 0.17 USD. `claim_drafting` is last in `ALL_SUITES`; `npm run eval` replays sixteen evaluations, exit 0, every gate PASS (`.evidence/fase-09/eval-replay.txt`); earlier-phase order tests updated as D8 to D11.
- [ ] 18. Demo recording, recorded once live and replayed (C16) - files: `evals/recordings/claim_demo.jsonl` (new), `tests/test_reconcile_demo.py` (DV); check: `reconcile-demo --mode record` writes four rows to the new file only, and `git diff --stat -- evals/recordings/reconcile_demo.jsonl` is empty; the phase 08 demo test gains the claim end state and a test that the claim recordings hold one row per demo claim; it runs `reconcile-demo` in replay with the network blocked inside `check`; output saved as `.evidence/fase-09/reconcile-demo.txt`; phase total cost written here.

### Batch H - Documentation and secrets
- [ ] 19. README sections in English for the claim step, the claim content and amount in dispute, the fact check and template fallback, the `claims`, `approve` and `reject` commands, the outbox email, the extended `reconcile-demo` in replay and live mode, how both evaluations were built and run, their figures and the known limitations (C20) - files: `README.md`; check: followed step by step in the clean clone of increment 21; `check` and `eval` now say sixteen evaluations; status line and phase table updated.
- [ ] 20. Secret scan of the new recordings, edited body file, outbox examples, dataset and scenario files, asserting they are tracked (C19) - files: `tests/test_secrets.py` (new test only); check: `uv run pytest -q tests/test_secrets.py`: a phase 09 test lists `claim_drafting.jsonl`, `claim_demo.jsonl`, the baseline, the new modules, `evals/datasets/claim_drafting`, `evals/datasets/claim_approval` and `examples/invoices/claim_edit.txt`, asserts they are tracked and hold no keys or auth headers.

### Batch I - Validation and closing
- [ ] 21. `npm run check`, unchanged-behaviour evidence and fresh-clone run with the Anthropic and LangSmith variables unset and no `.env` (C1, C18) - check: `npm ci`, `uv sync` and `npm run check` in a fresh clone of the branch outside the repo; sixteen evaluations in replay with every gate PASS; phase 01 to 08 tests passing apart from the tests named as deviations; `git diff --stat d05c2af -- evals/recordings evals/datasets evals/baselines` lists only added files; `orders-demo` and `quotes-demo` replay output equal to increment 1 once run ids and folder paths are masked; output saved as `.evidence/fase-09/fresh-clone-check.txt`, `recordings-diff-stat.txt` and the demo diffs.
- [ ] 22. LangSmith trace and experiment (C17) - check: blocked by the trace quota until about 2026-11-05; recorded as pending and delivered with the catch-up change of earlier phases.
- [ ] 23. Adversarial review with `sdd-review` through `sdd-delivery` (at most two rounds), findings and fixes recorded below with regression tests; re-run `npm run check` after the fixes.
- [ ] 24. Results per criterion in the table below, master plan row 09 status, open items and limitations, candidate learnings; delivery on the branch and a PR to `main`, never a merge.

## Deviations
| ID | Summary | Affects criteria | Status |
|---|---|---|---|
| D1 | `evaluation/cli.py` touched to register the claim-dataset commands, like earlier suites, though not listed in the allowed files | none | accepted |
| D2 | `tests/test_web_form_eval.py` (earlier phase): `test_upload_command_uploads_every_suite_and_keeps_the_worst_exit` gains the `claim_eval.upload_datasets` stub, since `claim_drafting` is now in `UPLOAD_SUITES` | none | accepted |
| D3 | `tests/test_document_eval.py` (earlier phase): `test_eval_all_runs_the_suite_last_once_its_baseline_is_recorded` expects `claim_approval` after `document_extraction` at the tail of `ALL_SUITES` | none | accepted |
| D4 | `tests/test_reconcile_eval.py` (earlier phase): `test_reconciliation_matching_is_a_local_suite` expects the last three of `ALL_SUITES` to end with `claim_approval` | none | accepted |
| D5 | `tests/test_offer_eval.py` (earlier phase): `test_eval_all_includes_the_suite_once_its_baseline_is_recorded` expects `claim_approval` at the end of the run order | none | accepted |
| D6 | `tests/test_whatsapp_eval.py` (earlier phase): `test_eval_all_runs_the_whatsapp_suite_after_its_baseline` expects `claim_approval` at the end of the run order | none | accepted |
| D7 | `tests/test_reconcile_demo.py` (earlier phase): the two demo tests pass a temporary claim recordings file built by a session fixture, and the first asserts the claim end state (allowed by C18) | none | accepted |
| D8 | `tests/test_document_eval.py` (earlier phase): `claim_drafting` expected last in `ALL_SUITES` and third from last in the run order | none | accepted |
| D9 | `tests/test_reconcile_eval.py` (earlier phase): the last four of `ALL_SUITES` end with `claim_drafting` | none | accepted |
| D10 | `tests/test_offer_eval.py` (earlier phase): the run order tail gains `claim_drafting` | none | accepted |
| D11 | `tests/test_whatsapp_eval.py` (earlier phase): the `eval all` order gains `claim_drafting` at the end | none | accepted |

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Results
Per criterion: command or walkthrough run, observed result and evidence reference.
Open items, limitations and what could not be checked, stated plainly.

| ID | Result | Evidence |
|---|---|---|

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.
