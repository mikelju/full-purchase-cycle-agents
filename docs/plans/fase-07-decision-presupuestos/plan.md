# Phase 07 - Quotes, decision: plan and results

Status: in execution
Spec: `spec.md` (approved 2026-10-13, frozen)
Base: branch `fase-07-decision-presupuestos` from `main` at commit `723109c`.

## Design notes
Written for the owner decisions of 2026-10-13 recorded in the spec (D1 to D10) and the recommended option of the open decision D11.
- New module `purchase_cycle/quote_decision.py`: offer usability and net price, the comparison rule and reason template, the purchase order template and writer, and `build_quote_decision_graph` with the nodes `extract`, `compare`, `approve`, `issue` and `close` (D1 to D4).
- New model task `OFFER_EXTRACTION` in `llm.py` with its instructions, a Pydantic answer schema with `extra="forbid"` and its recordings file `evals/recordings/offer_extraction.jsonl`; the demo uses its own recordings file `evals/recordings/quotes_demo.jsonl`, as `orders_demo.jsonl` does; the existing tasks and recordings are not touched (D1, C17).
- The prompt holds the request SKU, product name, sale unit and requested quantity, not the catalog, since the product is known; the reply body is read from the stored `reply_file`, with the headers left out.
- New tables in `db.SCHEMA`: `quote_offers` (request id, supplier code, offers, quantity, unit price, currency, VAT included, VAT rate, lead time days, working days, price text, net unit price, usable, reason, reply file; primary key request and supplier) and `purchase_orders` (id, request id unique, supplier code, SKU, quantity, unit price, currency, total, expected delivery, issued at, status `issued`); `quote_requests` gets the wider status check (C2).
- Usability and net price are computed in code from the extracted fields, never by the model; reasons are fixed strings: `declined`, `no_price`, `currency`, `no_lead_time`, `vat_rate_missing`, `extraction_failed` (C3).
- An extraction failure parks one `failures` row per reply through `db.park_failure` with channel `quotes`, thread id `offer-QR-NNNN-SUP-NNN`, source the reply file, step `offer_extraction`, because that table keeps one row per thread id (C3).
- The model node uses the shared retry policy of phase 05 for transient errors; an answer that fails the schema is not retried and is stored as `extraction_failed`.
- `extract` reads back stored `quote_offers` rows and calls the model only for replies with no row, so a re-run on resumption calls nothing (C9).
- Comparison sorts usable offers by (not full, total, lead time in calendar days, supplier code); total uses the requested quantity for a full offer and the offered quantity for a partial one (C4).
- `approve` is one `interrupt` resumed by `Command(resume={"action": "approve" | "choose" | "reject", "supplier": ...})`; the CLI checks the request status and the chosen offer before resuming, so a refused command never reaches the thread (C7, C8).
- Purchase order number `PO-NNNN` from the `purchase_orders` id; outbox file `PO-NNNN-<supplier code>.eml` in `config.SUPPLIER_OUTBOX_DIR`, with the `Date` header from the simulated clock; the unique request id in `purchase_orders` makes `issue` idempotent (C6, C8).
- Expected delivery adds working days Monday to Friday, or calendar days, to the simulated issue date; a helper in `sim_clock.py` keeps time arithmetic in one place.
- CLI in `cli.py`: `quotes decide`, `quotes approve QR-NNNN [--supplier SUP-NNN]` and `quotes reject QR-NNNN`, each with `--db`, `--checkpoints` and, where it writes email, `--outbox`, following the phase 06 commands; `quotes list` gains the recommended supplier and order number columns.
- New evaluation modules `purchase_cycle/evaluation/offer_extraction_eval.py` and `quote_decision_eval.py`, with data in `evals/datasets/offer_extraction/` and `evals/datasets/quote_decision/`, registered in `harness.SUITES` and `ALL_SUITES`; `offer_extraction` joins `UPLOAD_SUITES`, `quote_decision` does not, like `quote_wait`; both reuse `stats.wilson_interval`, `stats.target_cells`, the McNemar test and the phase 05 report columns (D7).
- The phase 05 test that lists the `eval all` suite order (`tests/test_whatsapp_eval.py`) grows by the two new suites, as DV-01 of phase 06 did; it is recorded as a deviation when it happens.
- Sample data: `examples/quotes/` gains replies so that the demo has one request with a full and a partial offer and one with no reply.

## Rules for executors
- Execution starts only after the owner approves the spec and resolves D11; this plan is then updated to the frozen spec.
- Each batch is sized for one writer agent with about 90k tokens of context; it starts in a fresh session from this file and Git, implements its increments in order, marks each one `[x]` with its evidence and commits before the context runs out.
- Failing tests first: each increment adds its tests, runs them to see them fail for the expected reason, then implements.
- Before closing a batch, `npm run check` passes (pytest quiet) and the batch commit is made; the next batch only starts from a green check.
- Search before reading; read large files by chunks; reuse `db.park_failure`, `graph.sqlite_checkpointer`, the `llm` record and replay client, `stats.wilson_interval` and the phase 05 report and critical-error patterns.
- No new dependencies; the 5 USD ceiling of D6 is a limit, not a budget, and live calls stop before a run that would pass it; the agent never reads `.env`, and live runs that need the owner's key are run by the owner or with the owner present.
- No change to phase 01 to 06 prompts, recordings, datasets, baselines, tables or gates, except the wider status check and the named demo and suite-order tests (C17).
- Evidence files go to `.evidence/fase-07/`.

## Increments
Each increment leaves the product working and covers concrete criteria.
This file is the durable state: a new session resumes from here and from Git.

### Batch A - Statuses, baseline, schema and offer extraction
- [x] 1. Stale statuses (D10, C19) - check: README status lines for phases 02, 03 and 05 say integrated with their PR as the master plan does, phase 06 integrated (merged via PR #11) in both files, master plan row 07 `in progress`; a reading comparison saved in the plan evidence.
  Evidence: README lines 5 to 9 and master plan rows 06 and 07 edited; reading comparison in `.evidence/fase-07/status-comparison.txt` (phase 04 line also aligned to the same wording).
- [x] 2. Unchanged-behaviour baseline before any code change (C17) - check: `orders-demo` and `quotes-demo` replay output saved as `.evidence/fase-07/orders-demo-before.txt` and `quotes-demo-before.txt`, and `npm run eval` output as `eval-before.txt`, all from commit `723109c` code.
  Evidence: `.evidence/fase-07/orders-demo-before.txt`, `quotes-demo-before.txt` (both exit=0) and `eval-before.txt` (`npm run eval`, exit=0), run on HEAD 556a0e8 before any code change; the branch holds only docs commits over `723109c`, so the code is identical to it.
- [x] 3. `quote_offers` and `purchase_orders` tables and the wider `quote_requests` status check (C2) - check: new tests in `tests/test_seed.py` fail first, then pass: tables exist and are empty, the three new statuses are accepted, seeding twice gives the same rows, the other tables and rows unchanged.
  Evidence: `uv run pytest -q tests/test_seed.py` failed first (3 new tests, missing tables and status check), then 11 passed; full `pytest -q` 734 passed (`.evidence/fase-07/inc3-pytest.txt`); ruff check and format clean. Existing databases keep the old status check until reseeded from scratch (CREATE IF NOT EXISTS).
- [x] 4. `OFFER_EXTRACTION` task, instructions and schema, usability and net price in code, and `quote_offers` storage (C3) - check: new `tests/test_quote_decision.py` cases with hand-written recordings: one call per replied supplier, fields stored, each unusable reason, a schema-failing answer stored as `extraction_failed` with its `failures` row while the other replies are still extracted.
  Evidence: `uv run pytest -q tests/test_quote_decision.py` failed first (module missing), then 13 passed (one call per replied supplier, fields stored, each unusable reason, VAT net price, schema failure stored as `extraction_failed` with its `failures` row while the other replies go on, re-run calls no model, prompt has no headers or catalog); `npm run check` exit=0 in `.evidence/fase-07/check-batch-a.txt`. Hand-written recordings only; `evals/recordings/offer_extraction.jsonl` is not created yet.

### Batch B - Comparison, decision graph and commands
- [x] 5. Comparison rule and reason template (C4) - check: one test per rule over fixed offer sets: full beats a cheaper partial, lowest total, lead time tie, supplier code tie, partial only when no full offer, no usable offer gives no recommendation; zero model client calls.
  Evidence: new comparison tests in `tests/test_quote_decision.py` failed first (no `compare_offers`), then 24 passed in the file (full beats cheaper partial, lowest total, lead time tie in calendar days with working days over a weekend and null counted as calendar, supplier code tie, partial only without a full offer, single usable offer, no usable offer, zero model calls); `sim_clock.add_days` added; ruff clean.
- [x] 6. `build_quote_decision_graph` with `extract`, `compare` and `approve`, and `quotes decide` (C5) - check: tests on a temporary database: one decision per `ready_for_decision` request, printed offers, recommendation and reason, status `awaiting_approval`, thread paused in `approve` in the checkpoint, `no_offers` requests listed with no order, a second `decide` starts nothing and calls no model.
  Evidence: graph `build_quote_decision_graph` (extract with the phase 05 retry policy, compare, approve with interrupt, issue, close) and `quotes decide`; `tests/test_quote_decision_graph.py` covers printed offers and recommendation, status awaiting_approval, thread paused at `approve` in the checkpoint, `no_offers` listed with no order, second decide with the recordings deleted exits 0 (no model call), transient error retried; 39 passed with `test_quote_decision.py`.
- [x] 7. `issue`, purchase order template and `quotes approve` with and without `--supplier` (C6, C7) - check: tests compare the `purchase_orders` row and the outbox `.eml` with expected values, including the expected delivery over a weekend; stock and customer orders unchanged; refused choices (no offer, unusable offer, approve on a request with no usable offer) change nothing.
  Evidence: same test file: order row, outbox `.eml` (subject, body fields) and expected delivery 2026-10-20 for 3 working days from Thursday compared with expected values, stock and customer orders unchanged, `--supplier` choosing a partial offer orders the offered quantity, refused choices (no offer, unusable, no usable offer) change nothing and keep the thread paused.
- [x] 8. `close` and `quotes reject`, status guards and idempotence (C8) - check: reject gives `rejected` with no order and no email; approve twice, approve after reject and reject after approve fail with a message and leave one order and one email at most; re-running `issue` on resumption writes no second row or file.
  Evidence: same test file: reject gives `rejected` with no order or email; approve twice, approve after reject and reject after approve fail with a message and leave at most one order and one email; `issue_order` re-run writes no second row, rewrites a lost email only, keeps the bytes. Increments 6 to 8 were written and committed together because they share the graph and test file.
- [x] 9. Cross-process decision and `quotes list` (C9, C10) - check: decide and approve each in a subprocess on the same files compared with an in-process run on rows, outbox bytes and recorded calls; `quotes list` shows every status, the recommended supplier and the order number.
  Evidence: new tests in `tests/test_quote_decision_graph.py`: decide and approve each in a subprocess on the same files give the same rows (quote_requests, purchase_orders, quote_offers without the temp reply path) and outbox bytes as one process, and approve runs with the recordings file deleted (no model read); `quotes list` shows awaiting_approval, no_offers, ordered and rejected with `recommended=` and `order=` columns, added only once a request has offers so the phase 06 list test passes unchanged; 50 passed with `test_quote_request.py`.

### Batch C - Evaluation `offer_extraction`
- [x] 10. Seeded planning script and at least 100 replies in the ten categories with labels, fixed stratified split, automatic validation and a clean-context second-pass review (C11) - check: tests over the dataset and plan files count categories and minimums and re-run the plan with the same seed; generator output saved as `.evidence/fase-07/offer-dataset.txt`.
  Evidence: 120 replies, 10 categories x 12, dev 30 / test 90, `.evidence/fase-07/offer-dataset.txt`; `tests/test_offer_dataset.py` 11 passed; clean-context second pass (sonnet subagent, blind export only): 2 disagreements (OFF-021, OFF-024, case size in the text did not match the plan divisor 12), both `label_fixed` by correcting the text, 118 agree, 0 pending.
- [ ] 11. Runner, graders, metrics, critical errors, report and gates, registered in `harness` (C12) - check: tests that force each gate to fail prove a non-zero exit of `purchase-cycle eval --suite offer_extraction`.
- [ ] 12. Instruction tuning on the development split (at most three rounds), then the live test split record and baseline with Claude Haiku 4.5, with the owner's key (C13, D6) - check: stored baseline with per-reply results and the live report saved as `.evidence/fase-07/offer-baseline-live.txt`, cost noted; a gate miss stops execution for the owner.

### Batch D - Evaluation `quote_decision`, demo and documentation
- [ ] 13. At least 24 scenarios in `evals/datasets/quote_decision/` with the spec coverage, runner, deterministic grader, critical-error counter and gates (C14) - check: a test counts the coverage tags; one injection per critical error and a wrong expectation each prove a non-zero exit; replay report saved as `.evidence/fase-07/eval-quote-decision.txt`.
- [ ] 14. Sample replies and the extended `quotes-demo`, recorded once live and replayed (C15) - check: a pytest inside `check` runs the demo with the network blocked and asserts one `ordered` request with its order and one `no_offers` listed, and that the phase 06 steps print as in increment 2; output saved as `.evidence/fase-07/quotes-demo.txt`.
- [ ] 15. README sections in English for the decision step, the comparison rule, the commands, the purchase order, the demo in replay and live mode, both evaluations and the known limitations (C19) - check: followed step by step in the clean clone of increment 17.
- [ ] 16. Secret scan of the new recordings, sample replies, outbox examples, dataset and scenario files, asserting they are tracked (C18) - check: `tests/test_secrets.py` gains a phase 07 test.

### Batch E - Validation and closing
- [ ] 17. `npm run check`, unchanged-behaviour evidence and fresh-clone run with the Anthropic and LangSmith variables unset and no `.env` (C1, C17, C19) - check: twelve evaluations in replay; phase 01 to 06 tests passing apart from the named demo and suite-order tests; the ten earlier evaluations with every gate PASS; `git diff --stat 723109c -- evals/recordings evals/datasets evals/baselines` lists only the new files; `orders-demo` equal to increment 2 once run ids are masked.
- [ ] 18. LangSmith trace and experiment (C16) - check: blocked by the trace quota until about 2026-11-05; recorded as pending and delivered with the catch-up change of phases 02 to 05 (D8).
- [ ] 19. Adversarial review with `sdd-review` through `sdd-delivery` (at most two rounds), findings and fixes recorded below with regression tests; re-run `npm run check` after the fixes.
- [ ] 20. Results per criterion in the table below, master plan row 07 status, open items and limitations, candidate learnings; delivery on the branch and a PR to `main`, never a merge.

## Deviations
| ID | Summary | Affects criteria | Status |
|---|---|---|---|

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Results
Per criterion: command or path run, observed result and evidence reference.
Pending items, limitations and what could not be checked, stated plainly.

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.
