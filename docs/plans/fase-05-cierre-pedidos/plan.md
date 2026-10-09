# Phase 05 - Orders module wrap-up: plan and results

Status: ready to execute
Spec: `spec.md` (frozen, approved by the owner on 2026-10-09)
Base: branch `fase-05-cierre-pedidos` from `main` at commit `f1a1e4f`.

## Design notes
Written for the owner decisions of 2026-10-09 recorded in the spec (D3 option B, the recommended option elsewhere).
- New module `purchase_cycle/whatsapp_order.py`: message model (Pydantic), inbox reader, phone lookup by digits, outbox writer and `build_whatsapp_order_graph` with the nodes `intake`, `extract`, optional `clarify`, `store` and `reply`, mirroring `build_email_order_graph` (D1, D3).
- New tasks `WHATSAPP_INTAKE` and `WHATSAPP_EXTRACTION` in `llm.py`, with their own constants `WHATSAPP_INTAKE_INSTRUCTIONS` and `WHATSAPP_EXTRACTION_INSTRUCTIONS` written for short and chatty chat messages, the phase 03 answer schemas (`IntakeDecision`, `EmailLines`) and the phase 03 catalog validation, and new recordings files `evals/recordings/whatsapp_intake.jsonl` and `whatsapp_order_extraction.jsonl`; `EMAIL_INTAKE`, `EMAIL_EXTRACTION` and their instructions stay untouched (D3).
- The WhatsApp graph builds the user message for the new tasks in a fixed layout from the message text, so recording keys do not depend on ids or timestamps (D3).
- The first instructions are a draft; they are tuned only on the dev split of `whatsapp_order_extraction` (increment 15) and frozen before the test split is recorded (increment 16) (D3).
- New module `purchase_cycle/router.py`: `route(item, conn)` returns the route (`web_form`, `email`, `whatsapp_new`, `whatsapp_answer` with the thread id, or `rejected` with a reason) as a pure function over the item and the `clarifications` table; `run_inbox` runs each routed item through its graph (D2).
- New tables in `db.SCHEMA`: `order_sources` (channel, message id, thread id, order id, unique on channel and message id) and `failures` (thread id primary key, channel, source reference, step, error, status `needs_review` or `resolved`, timestamps); existing tables unchanged (D5, D6).
- `db.insert_order` gains an optional source reference written in the same transaction; a repeated source returns the stored order id; the three channel `store` nodes pass it (D6).
- Retries: a shared `MODEL_RETRY` `RetryPolicy` (3 attempts, exponential backoff, `retry_on` a predicate that accepts only `anthropic` connection, timeout, rate limit and 5xx errors) on `match`, `intake`, `extract`, `ask` and `interpret`; `ModelClient` builds `ChatAnthropic` with `max_retries=0`; backoff parameters are module constants that tests set to zero (D4).
- Re-ask: `ModelClient.extract` gains an optional `correction` text; the channel nodes catch `InvalidModelOutput` and `InvalidExtraction` once and call again with the validation error appended; a second failure raises `NeedsReview`, which a wrapper turns into a `failures` row and an end state with no store (D5).
  The `clarify` subgraph keeps its phase 04 rejection behaviour for answers; only the question draft gains the re-ask.
- Fault injection: `purchase_cycle/faults.py` reads `PURCHASE_CYCLE_CRASH_AT` (`after_channel_steps`, `after_store_commit`, `in_reply`) and calls `os._exit(70)` at that point; nothing happens when the variable is unset (D6).
- CLI: `purchase-cycle whatsapp-demo` (one inbox folder through the WhatsApp graph), `route` (one inbox folder through the router), `resume <thread_id>` (continue any interrupted thread from its checkpoint), `failures list` and `failures resume <thread_id>`, and `orders-demo` (D8).
- Thread ids carry the channel prefix so `resume` can rebuild the right parent graph without reading checkpoints; the `failures` row also stores the channel.
- Live and record runs set every LangSmith and LangChain tracing variable to `false` and `LANGSMITH_API_KEY` to empty until the quota resets (C17, D10), as in phases 03 and 04.

## Rules for executors
- Each batch is sized for one writer agent with about 90k tokens of context; it starts in a fresh session from this file and Git, implements its increments in order, marks each one `[x]` with its evidence and commits before the context runs out.
- Before closing a batch, `npm run check` passes (pytest quiet) and the batch commit is made; the next batch only starts from a green check.
- Search before reading; read large files by chunks; reuse the phase 02 to 04 functions named in the design notes.
- No new dependencies (spec constraint).
- Live API runs (`--mode live` or `--mode record`) use the owner's Anthropic key, which the CLI loads from `.env` through `load_dotenv`.
  The agent never opens, prints or copies `.env`; it only runs the command.
  If the command fails for a missing key, the agent stops that increment, marks it pending with the error text and continues with the work that does not need the key.
- Live spend stays under the D9 ceiling; the executor sums the recorded usage after each record run and stops before a run that would pass it.
- Owner actions stop the increment that needs them: the audit of 30 items (C14), a baseline under the 95% rule, and C17 until the LangSmith quota resets around 2026-11-05.
- Evidence files go to `.evidence/fase-05/`.

## Increments
Each increment leaves the product working and covers concrete criteria.
This file is the durable state: a new session resumes from here and from Git.

### Batch A - WhatsApp channel and idempotent store (no live calls)
- [ ] 1. WhatsApp message model, inbox reader, phone lookup by digits, rejections (unknown number, malformed file, non-text type) and outbox writer (C2) - check: new `tests/test_whatsapp_order.py` cases per rejection on a temporary database and outbox; a non-text message from a known customer writes the "send it as text" reply.
  Evidence:
- [ ] 2. `WHATSAPP_INTAKE` and `WHATSAPP_EXTRACTION` tasks with first-draft instructions and their recordings paths, and `build_whatsapp_order_graph` with `intake`, `extract`, optional `clarify`, `store` and `reply` using them with the WhatsApp message layout, and `purchase-cycle whatsapp-demo` (C3, C11) - check: replay tests with hand-written recordings in temporary files: a clear order stores one `whatsapp` order and writes the outbox reply; a non-order message stores nothing and gets a polite reply; a test asserts the WhatsApp tasks have their own names, instructions and recordings paths, distinct from the email tasks; `git diff` shows no change to `EMAIL_INTAKE_INSTRUCTIONS`, `EMAIL_EXTRACTION_INSTRUCTIONS` or any phase 02 to 04 recordings file.
  Evidence:
- [ ] 3. `order_sources` table and idempotent `insert_order` used by the web form, email and WhatsApp `store` nodes (C6, C11) - check: per-channel tests deliver the same message twice and count one order, one source row and a reply naming the original order number; a forced failure inside the transaction leaves no partial row; `tests/test_seed.py` sees the new empty table and unchanged counts; the five evaluations replay with every gate PASS.
  Evidence:

### Batch B - Router and recovery (no live calls)
- [ ] 4. `router.route`, `run_inbox` and `purchase-cycle route`, with the WhatsApp answer rule over pending WhatsApp clarifications (C4, C5) - check: tests over a mixed temporary inbox count zero model calls in routing and assert each route and rejection reason; a paused WhatsApp thread is resumed by a later WhatsApp text and ends with the order stored and the outbox reply; a customer with no pending WhatsApp thread starts a new order.
  Evidence:
- [ ] 5. `MODEL_RETRY` on every model-calling node, `max_retries=0` in `ModelClient`, and a fault-injecting test double that raises chosen `anthropic` errors a set number of times (C7, C11) - check: tests count attempts: 2 transient errors then success completes the order; 3 transient errors park the thread with nothing stored; an authentication error is attempted once; backoff set to zero; existing tests pass unchanged.
  Evidence:
- [ ] 6. Re-ask with the validation error and the `failures` table with `needs_review` (C8) - check: hand-written recordings in temporary files: invalid then valid answer completes the order with 2 calls; two invalid answers write one `failures` row, no order and leave the thread resumable; an extracted SKU not in the catalog takes the same path; the five evaluations replay unchanged.
  Evidence:
- [ ] 7. `failures list` and `failures resume <thread_id>` (C9) - check: tests per command on a temporary database; resume in a subprocess after a recording is added completes the order and marks the row `resolved`; resuming a thread that is not parked fails with a message and changes no row.
  Evidence:
- [ ] 8. Fault injection at three points and `purchase-cycle resume <thread_id>` for the three graphs (C10) - check: for each point and each graph, a subprocess with `PURCHASE_CYCLE_CRASH_AT` set exits with code 70, a second subprocess resumes the thread and the database holds exactly one order, its lines, one source row and the reply; network blocked, replay recordings in temporary files.
  Evidence:

### Batch C - WhatsApp golden dataset (no live calls)
- [ ] 9. Seeded planner of `whatsapp_order_extraction`: 150 messages or more in five categories of at least 25, 300 catalog lines or more, expected intake decision and lines, stratified 25/75 split (C13) - check: `uv run purchase-cycle whatsapp-dataset plan` writes `plan.jsonl`; tests assert the minimums, the split and the same plan for the same seed.
  Evidence:
- [ ] 10. Automatic validation and deterministic build: messages rendered as WhatsApp JSON files, labels in `dataset.jsonl` (C13) - check: tests with placeholder texts in a temporary folder prove rejections (planned line missing, quantity rule broken, duplicate text after normalisation, non-ASCII text) and identical bytes over two builds.
  Evidence:
- [ ] 11. Agent-written texts, then `check` and `build` (C13) - check: writer agents (at most four at a time, each owning its text files) write in `texts/`; every file passes `check` with 0 rejected; output in `.evidence/fase-05/dataset-build.txt`; a test rebuilds from the versioned texts byte for byte.
  Evidence:
- [ ] 12. Second-pass review by clean-context subagents that see only the catalog and the texts, compared with the plan (C13) - check: `second_pass_review.jsonl` versioned, every disagreement fixed or justified, counts recorded here, a test requires a decision per disagreement.
  Evidence:
- [ ] 13. Audit sample of 30 random items and audit report with Wilson interval (C14) - check: `whatsapp-audit create` writes `evals/audit/whatsapp_order_extraction-audit-v1.0.csv`; tests for create and report; the owner completes the review file.
  Evidence:

### Batch D - Evaluations and baselines
- [ ] 14. Suites `whatsapp_order_extraction` (phase 03 graders, Wilson intervals globally and per category, absolute and McNemar gates), `channel_routing` (40 items or more, 100% gate) and `failure_recovery` (20 scenarios or more, 100% gate), registered in `SUITES` and, for the WhatsApp suite, in `eval-upload` (C13, C15) - check: tests force each new gate to fail and prove exit 1, exit 2 for missing recordings and exit 3 for a baseline under 95%; `npm run eval` names the five existing suites plus the two deterministic ones until increment 16.
  Evidence:
- [ ] 15. Prompt tuning of `WHATSAPP_INTAKE_INSTRUCTIONS` and `WHATSAPP_EXTRACTION_INSTRUCTIONS` on the dev split of `whatsapp_order_extraction` only (C16) - needs the owner's key: `eval --suite whatsapp_order_extraction --mode record --split dev`, tracing off, once per tuning round, at most three rounds unless the owner allows more within the D9 ceiling; each round records its dev metrics, its failures by category, the instruction change and its spend here; the test split is not run; the final recordings hold only the final-instruction answers; output and usage in `.evidence/fase-05/eval-dev.txt`.
  Evidence:
- [ ] 16. Haiku 4.5 baseline on the held-out test split with the final instructions of increment 15, per-item results and thresholds by the spec rule (C16) - needs the owner's key: `--mode record --split test --set-baseline`; the instructions are not changed after this run; baseline in `evals/baselines/whatsapp_order_extraction.json`; output in `.evidence/fase-05/eval-test-baseline.txt`; a gate miss stops here as a deviation for the owner; then `npm run eval` replays the eight suites; the total live spend so far is recorded here against the 2 USD ceiling.
  Evidence:

### Batch E - Full demo, documentation and closing
- [ ] 17. Sample mixed inbox `examples/orders/` (web form JSON, `.eml`, WhatsApp orders, one WhatsApp order with a doubt and its later answer, one non-text message, one re-delivered message) and `purchase-cycle orders-demo` with the retry, re-ask and crash scenes, plus the recordings it needs (C12) - check: a pytest runs the demo in replay with the network blocked and asserts channel, route, outcome, order number and reply per item and the final summary; replay output in `.evidence/fase-05/demo-replay.txt`; live run with the owner's key and tracing off in `.evidence/fase-05/demo-live.txt`.
  Evidence:
- [ ] 18. README sections for the WhatsApp channel, the router, the recovery behaviour, the failures and resume commands, `orders-demo`, the new datasets and evaluations, and the known limitations (C19) - check: follow them in the clean clone of increment 20.
  Evidence:
- [ ] 19. Secret scan of the new recordings, sample inbox and outbox files, dataset, scenario, audit and baseline files, asserting they are tracked (C18) - check: `tests/test_secrets.py` gains a phase 05 test.
  Evidence:
- [ ] 20. `npm run check`, unchanged-behaviour evidence and fresh-clone run with the Anthropic and LangSmith variables unset and no `.env` (C1, C11, C19) - check: `uv sync`, `npm ci` and `npm run check` in a clean clone, eight evaluations in replay; `web-form-demo`, `email-demo` and `exceptions-demo` replay output compared with the output saved before batch A; `git diff --stat` on phase 01 to 04 recordings and baselines empty; output in `.evidence/fase-05/fresh-clone.txt`.
  Evidence:
- [ ] 21. LangSmith trace of a live `orders-demo` run (retried attempts, re-ask, resumed crash) and an experiment of `whatsapp_order_extraction` against its uploaded splits (C17) - blocked: the trace quota is exhausted until about 2026-11-05; under D10 option B it is delivered by the catch-up change with phases 02 to 04; executors skip it and leave it pending.
  Evidence:
- [ ] 22. Adversarial review with `sdd-review` through `sdd-delivery` (at most two rounds), findings and fixes recorded below with regression tests; re-run `npm run check` and the eight replay evaluations after the fixes.
  Evidence:
- [ ] 23. Results per criterion in the table below, master plan row 05 status, open items and limitations, candidate learnings; delivery on the branch and a PR to `main`, never a merge.
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

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.
