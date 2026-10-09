# Phase 05 - Orders module wrap-up: specification

Status: approved
Approved by the owner: 2026-10-09 (via Lavish)
Amended by deviation 05.1, approved by the owner on 2026-10-09 (via Lavish): `05.1-audit-evaluation-fixes.md`
Master plan: `../0_plan_maestro.md`

## Goal
The orders module receives orders from three channels (web form, email and a simulated WhatsApp), sends every incoming message to the right subgraph through a channel router, and survives the failures a real deployment meets: transient model errors, invalid model output and a process that dies in the middle of an order.
One command runs the whole module end to end, replayable with no key, so the module is finished and ready to be composed by the phase 10 orchestrator.
It is needed now because phases 02 to 04 handle each channel by its own CLI command, any model failure stops the run with nothing recorded, and a crash between storing an order and writing the checkpoint would store the order twice on resumption.
It makes visible the master plan capability "failure recovery", the only one of the five not yet shown.

## Design direction
Proposed by the coordinator and confirmed by the owner decisions below; each decision id points to "Owner decisions (2026-10-09)".
- WhatsApp is simulated by JSON message files shaped like a small subset of the WhatsApp Cloud API webhook payload, read from an inbox folder; replies are written as JSON files to an outbox folder (D1).
- A new parent graph `whatsapp_order` runs two new model tasks, WhatsApp intake and WhatsApp extraction, with their own instructions tuned on the development split of the WhatsApp dataset and their own recordings files, then the phase 04 `clarify` subgraph, `store` and `reply` (D3).
  The phase 03 email tasks, prompts, recordings and baselines stay untouched.
- A deterministic channel router reads each inbox item's envelope and starts the web form, email or WhatsApp graph, or resumes a paused WhatsApp clarification when the message comes from a customer with a pending WhatsApp question (D2).
- Every model-calling node gets a LangGraph `RetryPolicy` for transient errors, so retries are visible in the graph and in traces (D4).
- An invalid model answer is re-asked once with the validation error; a second invalid answer parks the order as `needs_review` in a new `failures` table, with nothing stored (D5).
- Each incoming message gets a source reference (channel and message id) recorded in a new `order_sources` table in the same transaction as the order, so a re-delivered message or a re-run `store` never stores a second order (D6).
- A test-only fault injection point kills the process at a named step; a resume command continues the thread from the SQLite checkpoint in a new process (D6).
- One command, `orders-demo`, runs a mixed sample inbox through the router, including the recovery scenes, in replay by default (D8).
- The phase 03 line graders are fixed to match lines one to one with SKU, quantity and source, a line citing a source outside its message is an invalid extraction, and a new `order_scenarios` suite grades full conversations by the final database state with a critical-error gate (deviation 05.1).

## Scope
In:
- Simulated WhatsApp channel (D1):
  - Inbox message files with `message_id`, `from` (phone number), `timestamp`, `type` and, for `type` `text`, `text.body`; one file per message.
  - The sender phone number identifies the customer by the `customers.phone` column after keeping digits only; an unknown number is rejected with a reason and nothing stored.
  - Only text messages are read; a message of any other type (image, audio, document) is rejected with a reply asking the customer to send the order as text (D1).
  - One message is one order request; several messages in a row are not grouped (D1).
  - The reply to the customer is written to an outbox folder as a JSON file with the recipient number, the reply text and the message id it answers, and printed.
- WhatsApp order graph: intake (is this an order), extraction, `clarify`, `store`, `reply` (D3):
  - New WhatsApp intake and WhatsApp extraction tasks with their own instructions, written for short and chatty chat messages, and their own recordings files.
  - The instructions are tuned only on the development split of `whatsapp_order_extraction`; the test split is never used for tuning and gives the gate figures.
  - The answer schemas and the semantic validation (an extracted SKU must exist in the catalog) follow the phase 03 extraction, so the phase 03 graders apply.
  - The phase 03 email tasks and the phase 04 subgraph are reused without changing their prompts, recordings or baselines.
- Channel router (D2):
  - Deterministic, no model call: the item type (web form JSON, `.eml` file, WhatsApp JSON) picks the graph; an item that fits none is rejected with a reason.
  - A WhatsApp text from a customer whose most recent pending clarification is a WhatsApp thread is delivered to that thread as the answer, through the phase 04 resume path; otherwise it starts a new order.
- Retries (D4): a LangGraph `RetryPolicy` on every node that calls the model, for transient errors only (connection errors, timeouts, rate limits and server errors of the Anthropic API), at most 3 attempts with exponential backoff; authentication, bad request and schema failures are not retried.
  The Anthropic SDK retries inside `ModelClient` are lowered to 0 so each retry is one graph attempt.
- Invalid model output (D5):
  - A schema-invalid answer (`InvalidModelOutput`) or a semantically invalid one (an extracted SKU not in the catalog, `InvalidExtraction`) is re-asked once with the validation error appended to the user message, through the same client and modes, with its own recording key.
  - A second invalid answer, or a retry budget exhausted, parks the thread: one `failures` row (thread id, channel, source reference, step, error, status `needs_review`, timestamps), nothing stored in `orders`, and a reply only when the channel can receive one (D5).
  - A CLI listing of failures and a command that resumes a failed thread from its last checkpoint, for example after the owner fixed a recording or the API recovered.
- Crash mid-flow (D6):
  - Idempotent store: a new `order_sources` table (channel, message id, thread id, order id, unique on channel and message id) written in the same transaction as the order; a repeated source returns the stored order number instead of inserting.
  - A fault injection setting, read only from an environment variable meant for tests and the demo, that ends the process with `os._exit` at a named point: after the channel steps, inside `store` after the commit and before the checkpoint, and in `reply`.
  - A CLI command that resumes any interrupted thread from the SQLite checkpoint in a new process.
- Full module demo `purchase-cycle orders-demo` over a sample mixed inbox in `examples/orders/` (D8), in replay with no key and in live mode.
- New evaluations in replay inside `npm run check` next to the five existing ones (D7): `whatsapp_order_extraction`, `channel_routing` and `failure_recovery` (see "Evaluation design").
- Deviation 05.1:
  - One-to-one email and WhatsApp line graders over (SKU, quantity, source), and (quantity, source) for unknown lines, with grader tests; stored baselines re-scored from their stored outputs (E1).
  - A line whose source is not the body or an attachment of its message, or `message` for WhatsApp, is an invalid extraction and takes the D5 re-ask path; README, code and tests agree (E2).
  - XLSX members written with `create_system = 0`, so dataset bytes are the same on Windows and Linux (E3).
  - A new `order_scenarios` evaluation of 30 full conversations through the router, recorded live once after the WhatsApp instructions are frozen, graded by the final database and outbox state, with a critical-error gate shared with `failure_recovery` (E4).
  - Every evaluation report separates the target, met or not, from the regression gate, names the counting unit of each interval and shows the Wilson upper bound of a zero-event rate (E5).
- README sections for the WhatsApp channel, the router, the recovery behaviour, the full demo, the new evaluations and the known limitations.

Out:
- A real WhatsApp, email or web integration, a webhook server or any network listener (D1).
- Voice notes, images and documents sent by WhatsApp; they are rejected with a reply (D1).
- Grouping several WhatsApp messages into one order (D1).
- Model-based channel or intent classification (D2).
- Changes to the phase 01 to 04 prompts, recordings, datasets and gates.
- Stock checks: phase 06 detects out-of-stock products.
- Automatic timeouts, reminders and a simulated clock: phase 06.
- Retrying a failed thread automatically on a schedule; the operator resumes it by CLI.
- Concurrency across processes beyond what phase 04 already guards (the pending-status guards stay as they are).

## Evaluation design

### Tasks under evaluation
- `whatsapp_order_extraction`: given one WhatsApp message from a known customer, the system must decide whether it is an order and extract the expected catalog lines (SKU and quantity), end to end through the WhatsApp graph without clarification, in replay mode; the model tasks under evaluation are the new WhatsApp intake and WhatsApp extraction tasks with their final tuned instructions (D3, D7).
- `channel_routing`: given a mixed inbox with pending clarifications already in the database, every item must reach the expected route (web form, email, WhatsApp new order, WhatsApp answer to a named thread, or rejected with the expected reason) (D7).
- `failure_recovery`: given a scripted scenario (transient errors before success, transient errors beyond the budget, an invalid then valid answer, two invalid answers, a crash at each injection point followed by resumption, a re-delivered message), the module must end in the expected outcome with the expected number of orders and failures rows (D7).
- `order_scenarios`: given an initial database state, inbox messages on any channel and scripted customer answers written from the customer's intent before seeing any question, the module must end in the expected final state (orders, lines with catalog SKU, quantity and price, sources, clarifications resolved or escalated, failures) with no critical error; the grader reads the database and the outbox, not the node sequence (deviation 05.1, E4).

### Golden datasets
- `whatsapp_order_extraction`, built with the phase 01 method (seeded planning script, agent-written texts, automatic validation, clean-context second-pass review, owner audit) (D7):
  - At least 150 messages in at least five categories of at least 25: short list, chatty single line, several lines in one sentence, quantities in words or dozens, and not an order (question, greeting, complaint).
  - At least 300 catalog lines across the order messages.
  - ASCII English text, no emoji, consistent with the rest of the data.
  - Fixed stratified split of about 25% development and 75% test, as in phase 03: the development split is the only data used to tune the WhatsApp instructions, and published figures and gates come from the held-out test split (D3).
  - Owner audit: 30 items drawn at random, reviewed in a review file.
- `channel_routing`: at least 40 hand-built items covering every route and rejection reason, versioned with expected routes; no audit, since every label follows from the envelope rules.
- `failure_recovery`: at least 20 scripted scenarios, versioned with expected outcomes, using a fault-injecting test double around the recorded client so no network is used.
- `order_scenarios`: at least 30 hand-built scenarios, at least 8 per channel, 10 with a clarification, 5 with two answers, 3 with an unknown product, 3 with a crash and resume and 3 with a re-delivered message, versioned with expected final states; model answers recorded live once (deviation 05.1, E4).

### Metrics and graders
- WhatsApp extraction: the phase 03 email extraction graders and metrics, applied to WhatsApp messages (intake accuracy, line recall and precision, SKU and quantity accuracy per line), each with a 95% Wilson interval on the test split, globally and per category, plus failures grouped by category.
- Routing: exact route accuracy per item.
- Recovery: scenario success rate, where success means the expected final outcome, the expected count of `orders`, `order_lines`, `order_sources` and `failures` rows, and no duplicate order.
- Scenarios: scenario success rate with its 95% Wilson interval at scenario level, and critical errors per scenario: a duplicate order, a line price not equal to the catalog price at store time, an expected line neither stored nor asked about nor escalated, an order stored with an open doubt, a reply confirming an order that is not stored or naming another number, a line with a source outside its message (deviation 05.1, E4).
- Line graders match one to one: a produced line counts for at most one expected line, over (SKU, quantity, source) for catalog lines and (quantity, source) for unknown lines (deviation 05.1, E1).
- Every report shows the counting unit of each interval (line, message, doubt, answer, scenario) and, for a zero-event rate, its Wilson upper bound (deviation 05.1, E5).
- All graders are deterministic.

### Gates
- WhatsApp extraction absolute gates in the test split: intake accuracy and line recall at least 95%, if the baseline measured in this phase with the final tuned instructions reaches them; otherwise execution stops and the owner decides, recorded as a deviation.
- Tuning ends before the test split is recorded; the instructions are not changed after the test baseline is measured.
- WhatsApp extraction regression gate: exact McNemar test per line against the stored baseline, failing on a significant drop (p < 0.05).
- Routing and recovery gates: 100%, since both are deterministic.
- Scenario gates: critical errors 0 in `order_scenarios` and `failure_recovery`, with no averaging; a scenario success target of 90% fixed before measuring, reported as met or not; an exact McNemar regression gate per scenario against the stored baseline (deviation 05.1, E4).
- Every report shows, per gated metric, the target fixed before measuring and whether it is met, apart from the gate result; no existing threshold changes (deviation 05.1, E5).
- The five existing evaluations keep running unchanged with their own gates.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the nine evaluations in replay mode; the dataset byte tests also pass on Linux (deviation 05.1) | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; the dataset byte tests run once on Linux (WSL or a container); output saved as evidence |
| C2 | A WhatsApp inbox file is validated against the message format; a known phone number identifies the customer; an unknown number, a malformed file and a non-text message are rejected with a reason, write no order and, for the non-text message from a known customer, write a reply asking for text | Pytest tests per case on a temporary database and outbox |
| C3 | A clear WhatsApp text order goes through the WhatsApp intake and WhatsApp extraction tasks (new tasks with their own instructions and recordings files, distinct from the phase 03 email tasks), `clarify`, `store` and `reply`, stores one order with channel `whatsapp`, and writes the reply to the outbox with the recipient number and the answered message id; a WhatsApp message that is not an order stores nothing and gets a polite reply | Pytest tests in replay with hand-written recordings in temporary files |
| C4 | The router sends each web form JSON, `.eml` file and WhatsApp JSON to its graph with no model call, and rejects an item that fits no channel with a reason | Pytest tests counting model calls over a mixed temporary inbox |
| C5 | A WhatsApp text from a customer whose most recent pending clarification is a WhatsApp thread resumes that thread as the answer and ends with the order stored and the reply in the outbox; a customer with no pending WhatsApp thread starts a new order | Pytest tests on a temporary database with a paused thread |
| C6 | A message delivered twice, on any channel, stores one order: the second run writes no row to `orders`, `order_lines` or `order_sources` and its reply names the original order number; the order, its lines and its source row are written in one transaction | Pytest tests per channel counting rows, plus a test that a failure inside the transaction leaves no partial row |
| C7 | A model-calling node that meets transient errors is retried by its `RetryPolicy` and succeeds when the error stops within 3 attempts; after 3 failed attempts the thread is parked in `failures` with nothing stored; an authentication or bad request error is not retried | Pytest tests with a fault-injecting test double that counts attempts, with backoff set to zero in tests |
| C8 | An invalid model answer (schema-invalid, or an extracted SKU not in the catalog, or a line source outside its message (deviation 05.1)) is re-asked once with the validation error; a valid second answer completes the order as if the first had been valid; a second invalid answer parks the thread as `needs_review` in `failures`, stores nothing and leaves the thread resumable | Pytest tests with hand-written valid and invalid recordings in temporary files, counting model calls and rows |
| C9 | `failures list` shows every parked thread with channel, source reference, step, error and age; `failures resume <thread_id>` resumes it from its last checkpoint in a new process, completes it and marks the row resolved; resuming a thread that is not parked fails with a message and changes nothing | Pytest tests per command on a temporary database, one of them through a subprocess |
| C10 | With the fault injection set to each of the three points (after the channel steps, inside `store` after the commit and before the checkpoint, in `reply`), the process exits before finishing; `resume <thread_id>` in a second process continues from the SQLite checkpoint and ends with exactly one order and its reply, for the web form, email and WhatsApp graphs | Pytest test that runs the first process as a subprocess, checks its exit code, resumes in a second subprocess and counts rows |
| C11 | Phase 02 to 04 behaviour is unchanged: their tests pass unchanged, except the grader, `unmatched-kept` source and report-format tests named in deviation 05.1, the five existing evaluations replay with every gate PASS, `git diff --stat` on their recordings is empty, their baselines change only by the deviation 05.1 re-score with no stored figure lower, and `web-form-demo`, `email-demo` and `exceptions-demo` print the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C12 | `orders-demo` runs the sample mixed inbox through the router and prints, per item, the channel, the route, the outcome, the order number and the reply; it includes a WhatsApp order with a doubt answered by a later WhatsApp message, a retried transient error, a re-asked invalid answer, and a crash resumed in a second process; it ends with a summary of stored orders and parked failures; it works in replay with no key and in live mode against Claude Haiku 4.5 | Run in replay (evidence in `check` through a pytest with the network blocked) and once in live mode with the owner's key (output saved) |
| C13 | The versioned `whatsapp_order_extraction` dataset meets the sizes and category minimums in "Golden datasets", with a fixed stratified split, every item passes the automatic validation, and re-running the planning script with the same seed gives the same plan; the `channel_routing` and `failure_recovery` sets meet their minimums | Pytest tests over the dataset, plan and scenario files; generator output saved as evidence |
| C14 | The owner audit of 30 random WhatsApp items finds at most 1 item with a wrong label, and the report shows the observed error rate with its Wilson interval | Review file completed by the owner and the computed rate, saved as evidence |
| C15 | `npm run eval` in replay reports the nine evaluations; the new ones show the metrics in "Metrics and graders" with 95% Wilson intervals where defined, and the command exits non-zero when any gate of any evaluation fails | Run the command; pytest tests that force each new gate to fail prove the non-zero exit |
| C16 | The WhatsApp intake and extraction instructions are tuned on the development split only, with the dev metrics of every tuning round recorded; the `whatsapp_order_extraction` baseline on its test split is then measured with the final instructions, stored with per-item results, and its thresholds are set by the rule in "Gates" | Dev record run reports per tuning round, the stored baseline file and the test record run report, saved as evidence; `git log` shows no instruction change after the test baseline |
| C17 | With the LangSmith variables set, a live `orders-demo` run appears in LangSmith with one trace per thread, showing the retried attempts, the re-ask and the resumed crash as separate runs of the same thread, and a live run of `whatsapp_order_extraction` is logged as an experiment against its uploaded dataset splits | Trace and experiment links, and the owner's screenshot, as evidence |
| C18 | No secret is versioned: the new recordings, sample inbox files, outbox examples, dataset and scenario files contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C20 | The email and WhatsApp line graders match expected and produced lines one to one: a produced line counts for at most one expected line; an unknown line passes only with the expected quantity and source; a catalog line passes only with the expected SKU, quantity and source; the phase 03 baseline re-scored with them keeps its stored figures or the change is reported (deviation 05.1) | Grader tests for omission, duplicate, invention, wrong quantity and false source; re-score output saved as evidence |
| C21 | `order_scenarios` holds at least 30 scenarios with at least 8 per channel, 10 with a clarification, 5 with two answers, 3 with an unknown product, 3 with a crash and resume and 3 with a re-delivered message; each is graded by the final database and outbox state; the report shows scenario success with its Wilson interval, the 90% target met or not, and the McNemar regression gate (deviation 05.1) | Pytest tests over the scenario files; stored baseline and replay run report as evidence |
| C22 | Critical errors are counted per scenario in `order_scenarios` and `failure_recovery` (duplicate order, line price not equal to the catalog price at store time, expected line neither stored nor asked about nor escalated, order stored with an open doubt, reply confirming an order that is not stored or naming another number, line with a source outside its message) and any count above 0 fails the evaluation (deviation 05.1) | Pytest tests that inject each critical error and prove a non-zero exit |
| C23 | Every evaluation report shows, per gated metric, the target and whether it is met apart from the gate result, the counting unit of each interval and, for a zero-event rate, its Wilson upper bound (deviation 05.1) | Replay run of `npm run eval` saved as evidence; report tests |
| C19 | The README explains in English how to run the WhatsApp channel, the router, the failures and resume commands and `orders-demo` in replay and live mode, how the new datasets were built, how to run the new evaluations, and states the known limitations below | Follow the new README sections in a clean clone |

## Constraints and risks
- No new dependency is expected: `RetryPolicy`, the SQLite checkpointer and `Command` are already in `langgraph`; the WhatsApp simulation uses JSON files and the standard library (D1).
- New tables `order_sources` and `failures` in the shared schema; existing tables are not changed.
- `ModelClient` builds `ChatAnthropic` with `max_retries=6` today, so transient API errors are already retried inside the SDK, invisibly to the graph; lowering it to 0 changes live behaviour only, since recording keys hold model, system prompt, tool and message, not retry settings (D4).
- A re-ask message differs from the first message, so it has its own recording key; replay of a re-ask needs its own recording, and the existing recordings, all valid, never trigger one.
- LangGraph re-runs the interrupted node from its start on resumption; any node with a side effect (`store`, the outbox write, the `failures` write) must be idempotent, which `order_sources` and keyed outbox file names provide.
- `os._exit` skips cleanup; the SQLite files must stay consistent, which SQLite transactions guarantee; on Windows a subprocess with `os._exit` is reproducible, unlike sending signals.
- The checkpoint file and the business database are two SQLite files; the crash between the business commit and the checkpoint write is exactly the case C10 tests at the `store` injection point.
- New WhatsApp intake and extraction instructions need prompt tuning on the development split, which adds live calls, code and the risk of overfitting a small split (about 38 messages); the held-out test split shows it, and a gate miss goes to the owner as a deviation (D3).
- Live runs need the owner's Anthropic and LangSmith keys in `.env`; the agent never reads that file.
- Deviation 05.1 adds about 150 live calls for `order_scenarios`, about 0.45 USD, raising the phase estimate to about 1.75 USD, inside the D9 ceiling with a smaller margin; the executor stops before a run that would pass it.
- Expected API cost of the phase before deviation 05.1: about 1.3 USD, under the 2 USD ceiling of D9, with prompt caching: about 210 calls for up to three tuning rounds of intake and extraction on the development split (about 38 messages), about 200 calls for the test split record (about 112 messages) and about 50 calls for the demo, re-ask recordings and live runs, at the phase 03 measured rate of about 0.003 USD per call; a fourth tuning round or a full re-record would use most of the remaining margin.
- The LangSmith trace quota is expected to reset around 2026-11-05; until then C17 is blocked (D10).
- Like earlier phases, the text writer and the evaluated model belong to the same family, so WhatsApp results may be optimistic.
- The owner audit takes about 15 minutes and blocks C14.
- Thirty scenarios give a wide interval at scenario level; a scripted customer answer cannot react to an odd question, so a poor question shows only when the scripted answer no longer resolves the doubt.

## Assumptions
- WhatsApp message ids are unique per message; the email source reference is the `Message-ID` header, or the file name when the header is missing; the web form source reference is the `submission_id`.
- Phone numbers in `customers.phone` and in WhatsApp files are compared after keeping digits only (for example `+34 600 101 201` and `34600101201` match).
- The WhatsApp reply text reuses the email reply wording, since both are free text to the customer.
- When a customer has more than one pending WhatsApp thread, a new WhatsApp text answers the most recent one; the `clarify list` command still shows all of them.
- The web form and email channels do not resume threads from new inbox items; their answers keep arriving through `clarify answer`, as in phase 04.
- The fault injection setting is ignored unless its environment variable is set, and the demo sets it only for its crash scene.
- `npm run check` stays fast: replaying the nine evaluations takes seconds.
- The WhatsApp instructions live next to the phase 03 instructions in `llm.py` as new constants and tasks, following the existing style; at most three tuning rounds are expected on the development split.

## Known limitations
- WhatsApp is simulated by files; there is no webhook, no delivery receipt and no media handling.
- Several WhatsApp messages that together form one order are processed as separate requests.
- A failed thread is resumed by an operator, never automatically.
- The idempotency key is the message id the channel gives; the same order sent twice with two different message ids is stored twice.
- The WhatsApp instructions are tuned on a development split of about 38 messages; the test split measures how well they generalise, but chat styles absent from the dataset may score lower.
- The candidate search, the extractor and the security limitations of phases 03 and 04 (SEC-003, SEC-004, SEC-007 of `docs/security.md`) apply to the WhatsApp channel too; the sender is identified by the phone number in the file only.

## Owner decisions (2026-10-09)
The owner chose the recommended option in nine of the ten open decisions, chose option B in D3, and approved the spec via Lavish.
Each reason is the owner decision of 2026-10-09.
- D1 - What "simulated WhatsApp" means: A, JSON message files in an inbox folder shaped like a subset of the WhatsApp Cloud API webhook payload, replies as JSON files in an outbox, text only.
- D2 - Channel router: B, deterministic by item type, plus the rule that a WhatsApp text from a customer with a pending WhatsApp clarification answers that thread.
- D3 - How WhatsApp messages are understood: B, new WhatsApp intake and extraction tasks with their own instructions, tuned on the development split; the phase 03 prompts are not reused.
- D4 - Retry policy: A, LangGraph `RetryPolicy` on model-calling nodes, transient errors only, 3 attempts with exponential backoff, SDK retries lowered to 0.
- D5 - Invalid model output: A, re-ask once with the validation error; a second failure parks the thread as `needs_review` in a `failures` table, resumable by CLI.
- D6 - Crash mid-flow and duplicate protection: A, environment-variable fault injection with `os._exit` at three named points, a `resume` command and a new `order_sources` table for an idempotent `store`.
- D7 - Evaluation additions: A, a `whatsapp_order_extraction` dataset of 150 messages with a 30-item owner audit, plus deterministic `channel_routing` (40 items) and `failure_recovery` (20 scenarios) suites gated at 100%.
- D8 - Shape of the full module demo: A, one `orders-demo` command over a sample mixed inbox with every channel, a WhatsApp clarification and the three recovery scenes, replay by default and live on request.
- D9 - Live model cost: A, a ceiling of 2 USD for the phase.
- D10 - LangSmith criterion while the trace quota is exhausted: B, C17 stays in this phase and is delivered with phases 02 C13, 03 C13 and 04 C15 through one catch-up change after the reset.
