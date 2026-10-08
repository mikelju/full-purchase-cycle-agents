# Phase 05 - Orders module wrap-up: specification

Status: spec in review
Approved by the owner:
Master plan: `../0_plan_maestro.md`

## Goal
The orders module receives orders from three channels (web form, email and a simulated WhatsApp), sends every incoming message to the right subgraph through a channel router, and survives the failures a real deployment meets: transient model errors, invalid model output and a process that dies in the middle of an order.
One command runs the whole module end to end, replayable with no key, so the module is finished and ready to be composed by the phase 10 orchestrator.
It is needed now because phases 02 to 04 handle each channel by its own CLI command, any model failure stops the run with nothing recorded, and a crash between storing an order and writing the checkpoint would store the order twice on resumption.
It makes visible the master plan capability "failure recovery", the only one of the five not yet shown.

## Design direction
Proposed by the coordinator; every point marked with a decision id depends on the owner's choice in "Open decisions" and is written for the recommended option.
- WhatsApp is simulated by JSON message files shaped like a small subset of the WhatsApp Cloud API webhook payload, read from an inbox folder; replies are written as JSON files to an outbox folder (D1).
- A new parent graph `whatsapp_order` reuses the phase 03 intake and extraction tasks unchanged, with its own recordings files, then the phase 04 `clarify` subgraph, `store` and `reply` (D3).
- A deterministic channel router reads each inbox item's envelope and starts the web form, email or WhatsApp graph, or resumes a paused WhatsApp clarification when the message comes from a customer with a pending WhatsApp question (D2).
- Every model-calling node gets a LangGraph `RetryPolicy` for transient errors, so retries are visible in the graph and in traces (D4).
- An invalid model answer is re-asked once with the validation error; a second invalid answer parks the order as `needs_review` in a new `failures` table, with nothing stored (D5).
- Each incoming message gets a source reference (channel and message id) recorded in a new `order_sources` table in the same transaction as the order, so a re-delivered message or a re-run `store` never stores a second order (D6).
- A test-only fault injection point kills the process at a named step; a resume command continues the thread from the SQLite checkpoint in a new process (D6).
- One command, `orders-demo`, runs a mixed sample inbox through the router, including the recovery scenes, in replay by default (D8).

## Scope
In:
- Simulated WhatsApp channel (D1):
  - Inbox message files with `message_id`, `from` (phone number), `timestamp`, `type` and, for `type` `text`, `text.body`; one file per message.
  - The sender phone number identifies the customer by the `customers.phone` column after keeping digits only; an unknown number is rejected with a reason and nothing stored.
  - Only text messages are read; a message of any other type (image, audio, document) is rejected with a reply asking the customer to send the order as text (D1).
  - One message is one order request; several messages in a row are not grouped (D1).
  - The reply to the customer is written to an outbox folder as a JSON file with the recipient number, the reply text and the message id it answers, and printed.
- WhatsApp order graph: intake (is this an order), extraction, `clarify`, `store`, `reply`, reusing the phase 03 intake and extraction tasks and the phase 04 subgraph without changing their prompts, recordings or baselines (D3).
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
- `whatsapp_order_extraction`: given one WhatsApp message from a known customer, the system must decide whether it is an order and extract the expected catalog lines (SKU and quantity), end to end through the WhatsApp graph without clarification, in replay mode (D3, D7).
- `channel_routing`: given a mixed inbox with pending clarifications already in the database, every item must reach the expected route (web form, email, WhatsApp new order, WhatsApp answer to a named thread, or rejected with the expected reason) (D7).
- `failure_recovery`: given a scripted scenario (transient errors before success, transient errors beyond the budget, an invalid then valid answer, two invalid answers, a crash at each injection point followed by resumption, a re-delivered message), the module must end in the expected outcome with the expected number of orders and failures rows (D7).

### Golden datasets
- `whatsapp_order_extraction`, built with the phase 01 method (seeded planning script, agent-written texts, automatic validation, clean-context second-pass review, owner audit) (D7):
  - At least 150 messages in at least five categories of at least 25: short list, chatty single line, several lines in one sentence, quantities in words or dozens, and not an order (question, greeting, complaint).
  - At least 300 catalog lines across the order messages.
  - ASCII English text, no emoji, consistent with the rest of the data.
  - Fixed stratified split of about 25% development and 75% test.
  - Owner audit: 30 items drawn at random, reviewed in a review file.
- `channel_routing`: at least 40 hand-built items covering every route and rejection reason, versioned with expected routes; no audit, since every label follows from the envelope rules.
- `failure_recovery`: at least 20 scripted scenarios, versioned with expected outcomes, using a fault-injecting test double around the recorded client so no network is used.

### Metrics and graders
- WhatsApp extraction: the phase 03 email extraction graders and metrics, applied to WhatsApp messages (intake accuracy, line recall and precision, SKU and quantity accuracy per line), each with a 95% Wilson interval on the test split, globally and per category, plus failures grouped by category.
- Routing: exact route accuracy per item.
- Recovery: scenario success rate, where success means the expected final outcome, the expected count of `orders`, `order_lines`, `order_sources` and `failures` rows, and no duplicate order.
- All graders are deterministic.

### Gates
- WhatsApp extraction absolute gates in the test split: intake accuracy and line recall at least 95%, if the baseline measured in this phase reaches them; otherwise execution stops and the owner decides, recorded as a deviation.
- WhatsApp extraction regression gate: exact McNemar test per line against the stored baseline, failing on a significant drop (p < 0.05).
- Routing and recovery gates: 100%, since both are deterministic.
- The five existing evaluations keep running unchanged with their own gates.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the eight evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | A WhatsApp inbox file is validated against the message format; a known phone number identifies the customer; an unknown number, a malformed file and a non-text message are rejected with a reason, write no order and, for the non-text message from a known customer, write a reply asking for text | Pytest tests per case on a temporary database and outbox |
| C3 | A clear WhatsApp text order goes through intake, extraction, `clarify`, `store` and `reply`, stores one order with channel `whatsapp`, and writes the reply to the outbox with the recipient number and the answered message id; a WhatsApp message that is not an order stores nothing and gets a polite reply | Pytest tests in replay with hand-written recordings in temporary files |
| C4 | The router sends each web form JSON, `.eml` file and WhatsApp JSON to its graph with no model call, and rejects an item that fits no channel with a reason | Pytest tests counting model calls over a mixed temporary inbox |
| C5 | A WhatsApp text from a customer whose most recent pending clarification is a WhatsApp thread resumes that thread as the answer and ends with the order stored and the reply in the outbox; a customer with no pending WhatsApp thread starts a new order (depends on D2) | Pytest tests on a temporary database with a paused thread |
| C6 | A message delivered twice, on any channel, stores one order: the second run writes no row to `orders`, `order_lines` or `order_sources` and its reply names the original order number; the order, its lines and its source row are written in one transaction | Pytest tests per channel counting rows, plus a test that a failure inside the transaction leaves no partial row |
| C7 | A model-calling node that meets transient errors is retried by its `RetryPolicy` and succeeds when the error stops within 3 attempts; after 3 failed attempts the thread is parked in `failures` with nothing stored; an authentication or bad request error is not retried | Pytest tests with a fault-injecting test double that counts attempts, with backoff set to zero in tests |
| C8 | An invalid model answer (schema-invalid, or an extracted SKU not in the catalog) is re-asked once with the validation error; a valid second answer completes the order as if the first had been valid; a second invalid answer parks the thread as `needs_review` in `failures`, stores nothing and leaves the thread resumable (depends on D5) | Pytest tests with hand-written valid and invalid recordings in temporary files, counting model calls and rows |
| C9 | `failures list` shows every parked thread with channel, source reference, step, error and age; `failures resume <thread_id>` resumes it from its last checkpoint in a new process, completes it and marks the row resolved; resuming a thread that is not parked fails with a message and changes nothing | Pytest tests per command on a temporary database, one of them through a subprocess |
| C10 | With the fault injection set to each of the three points (after the channel steps, inside `store` after the commit and before the checkpoint, in `reply`), the process exits before finishing; `resume <thread_id>` in a second process continues from the SQLite checkpoint and ends with exactly one order and its reply, for the web form, email and WhatsApp graphs | Pytest test that runs the first process as a subprocess, checks its exit code, resumes in a second subprocess and counts rows |
| C11 | Phase 02 to 04 behaviour is unchanged: their tests pass unchanged, the five existing evaluations replay with every gate PASS, `git diff --stat` on their recordings and baselines is empty, and `web-form-demo`, `email-demo` and `exceptions-demo` print the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C12 | `orders-demo` runs the sample mixed inbox through the router and prints, per item, the channel, the route, the outcome, the order number and the reply; it includes a WhatsApp order with a doubt answered by a later WhatsApp message, a retried transient error, a re-asked invalid answer, and a crash resumed in a second process; it ends with a summary of stored orders and parked failures; it works in replay with no key and in live mode against Claude Haiku 4.5 (depends on D8) | Run in replay (evidence in `check` through a pytest with the network blocked) and once in live mode with the owner's key (output saved) |
| C13 | The versioned `whatsapp_order_extraction` dataset meets the sizes and category minimums in "Golden datasets", with a fixed stratified split, every item passes the automatic validation, and re-running the planning script with the same seed gives the same plan; the `channel_routing` and `failure_recovery` sets meet their minimums | Pytest tests over the dataset, plan and scenario files; generator output saved as evidence |
| C14 | The owner audit of 30 random WhatsApp items finds at most 1 item with a wrong label, and the report shows the observed error rate with its Wilson interval | Review file completed by the owner and the computed rate, saved as evidence |
| C15 | `npm run eval` in replay reports the eight evaluations; the new ones show the metrics in "Metrics and graders" with 95% Wilson intervals where defined, and the command exits non-zero when any gate of any evaluation fails | Run the command; pytest tests that force each new gate to fail prove the non-zero exit |
| C16 | The `whatsapp_order_extraction` baseline on its test split is measured, stored with per-item results, and its thresholds are set by the rule in "Gates" | Stored baseline file and the record run report, saved as evidence |
| C17 | With the LangSmith variables set, a live `orders-demo` run appears in LangSmith with one trace per thread, showing the retried attempts, the re-ask and the resumed crash as separate runs of the same thread, and a live run of `whatsapp_order_extraction` is logged as an experiment against its uploaded dataset splits (depends on D10) | Trace and experiment links, and the owner's screenshot, as evidence |
| C18 | No secret is versioned: the new recordings, sample inbox files, outbox examples, dataset and scenario files contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C19 | The README explains in English how to run the WhatsApp channel, the router, the failures and resume commands and `orders-demo` in replay and live mode, how the new datasets were built, how to run the new evaluations, and states the known limitations below | Follow the new README sections in a clean clone |

## Constraints and risks
- No new dependency is expected: `RetryPolicy`, the SQLite checkpointer and `Command` are already in `langgraph`; the WhatsApp simulation uses JSON files and the standard library (D1).
- New tables `order_sources` and `failures` in the shared schema; existing tables are not changed.
- `ModelClient` builds `ChatAnthropic` with `max_retries=6` today, so transient API errors are already retried inside the SDK, invisibly to the graph; lowering it to 0 changes live behaviour only, since recording keys hold model, system prompt, tool and message, not retry settings (D4).
- A re-ask message differs from the first message, so it has its own recording key; replay of a re-ask needs its own recording, and the existing recordings, all valid, never trigger one.
- LangGraph re-runs the interrupted node from its start on resumption; any node with a side effect (`store`, the outbox write, the `failures` write) must be idempotent, which `order_sources` and keyed outbox file names provide.
- `os._exit` skips cleanup; the SQLite files must stay consistent, which SQLite transactions guarantee; on Windows a subprocess with `os._exit` is reproducible, unlike sending signals.
- The checkpoint file and the business database are two SQLite files; the crash between the business commit and the checkpoint write is exactly the case C10 tests at the `store` injection point.
- Reusing the email intake and extraction instructions for WhatsApp texts may cost accuracy on very short or chatty messages; the WhatsApp baseline shows it, and a gate miss goes to the owner as a deviation (D3).
- Live runs need the owner's Anthropic and LangSmith keys in `.env`; the agent never reads that file.
- Expected API cost of the phase: under 1 USD (recording intake and extraction for about 150 WhatsApp messages on both splits, the demo recordings and a few live runs) with prompt caching; the ceiling is set by D9.
- The LangSmith trace quota is expected to reset around 2026-11-05; until then C17 is blocked (D10).
- Like earlier phases, the text writer and the evaluated model belong to the same family, so WhatsApp results may be optimistic.
- The owner audit takes about 15 minutes and blocks C14.

## Assumptions
- WhatsApp message ids are unique per message; the email source reference is the `Message-ID` header, or the file name when the header is missing; the web form source reference is the `submission_id`.
- Phone numbers in `customers.phone` and in WhatsApp files are compared after keeping digits only (for example `+34 600 101 201` and `34600101201` match).
- The WhatsApp reply text reuses the email reply wording, since both are free text to the customer.
- When a customer has more than one pending WhatsApp thread, a new WhatsApp text answers the most recent one; the `clarify list` command still shows all of them.
- The web form and email channels do not resume threads from new inbox items; their answers keep arriving through `clarify answer`, as in phase 04.
- The fault injection setting is ignored unless its environment variable is set, and the demo sets it only for its crash scene.
- `npm run check` stays fast: replaying the eight evaluations takes seconds.

## Known limitations
- WhatsApp is simulated by files; there is no webhook, no delivery receipt and no media handling.
- Several WhatsApp messages that together form one order are processed as separate requests.
- A failed thread is resumed by an operator, never automatically.
- The idempotency key is the message id the channel gives; the same order sent twice with two different message ids is stored twice.
- The candidate search, the extractor and the security limitations of phases 03 and 04 (SEC-003, SEC-004, SEC-007 of `docs/security.md`) apply to the WhatsApp channel too; the sender is identified by the phone number in the file only.

## Open decisions
Questions the owner must resolve before approval; empty on approval.
The recommended option is marked; the design direction, scope and criteria above are written for it.

### D1 - What "simulated WhatsApp" means
- A (recommended): JSON message files in an inbox folder, shaped like a subset of the WhatsApp Cloud API webhook payload, replies as JSON files in an outbox; text only, other types rejected with a reply. Trade-off: no dependency and fully reproducible, but no live interaction feel.
- B: a local HTTP endpoint with the standard library that accepts the same payload, plus a small sender script. Trade-off: closer to a real webhook, but adds a server process to tests and Windows port handling.
- C: option A plus voice notes and images, transcribed or read by a model. Trade-off: richer demo, but new dependency or model task, new cost and a new evaluation.

### D2 - Channel router
- A: deterministic by item type only; WhatsApp answers keep using `clarify answer`. Trade-off: smallest change, but WhatsApp is not conversational and the router is trivial.
- B (recommended): deterministic by item type, plus a deterministic rule that a WhatsApp text from a customer with a pending WhatsApp clarification is that thread's answer. Trade-off: shows a real conversation over a channel at small cost, with the "most recent pending thread" assumption.
- C: Claude Haiku 4.5 classifies each message (new order, answer, other). Trade-off: handles mixed intents, but adds a model task, cost, a dataset and a non-deterministic router.

### D3 - How WhatsApp messages are understood
- A (recommended): reuse the phase 03 intake and extraction tasks unchanged, with a WhatsApp layout of the user message and new recordings files. Trade-off: no prompt work and no change to phase 03, but possibly lower accuracy on chatty texts.
- B: new WhatsApp intake and extraction tasks with their own instructions, tuned on the dev split. Trade-off: better fit to the channel, but prompt tuning, more cost and more code.

### D4 - Retry policy
- A (recommended): LangGraph `RetryPolicy` on model-calling nodes, transient errors only, 3 attempts with exponential backoff, SDK retries lowered to 0. Trade-off: retries visible in the graph and traces, with a small change to live behaviour.
- B: keep the SDK retries (`max_retries=6`) and only document and test them. Trade-off: no code change, but retries stay invisible and the capability is not shown by the graph.
- C: a custom retry loop inside `ModelClient`. Trade-off: full control, but duplicates what LangGraph provides and hides retries from traces.

### D5 - Invalid model output
- A (recommended): re-ask once with the validation error; a second failure parks the thread as `needs_review` in a `failures` table, resumable by CLI. Trade-off: recovers most glitches and never loses an order, with one extra recorded call per glitch.
- B: no re-ask; park the thread for human review at once. Trade-off: simpler and cheaper, but a one-off glitch always needs an operator.
- C: re-ask up to 2 times, then stop the run as today. Trade-off: more automatic recovery, but a persistent failure is still lost from any queue.

### D6 - Crash mid-flow and duplicate protection
- A (recommended): an environment-variable fault injection that ends the process with `os._exit` at three named points, a `resume` command from the SQLite checkpoint, and a new `order_sources` table that makes `store` idempotent. Trade-off: deterministic subprocess tests on Windows and no duplicate orders, with one new table.
- B: same injection and resume, idempotency by a new unique column on `orders`. Trade-off: one table fewer, but it changes an existing table and needs a migration for existing database files.
- C: crash shown only by raising an exception inside a node, without idempotency. Trade-off: simplest, but does not prove a real process death and leaves the duplicate order risk.

### D7 - Evaluation additions
- A (recommended): a new `whatsapp_order_extraction` golden dataset of 150 messages with a 30-item owner audit, plus deterministic `channel_routing` (40 items) and `failure_recovery` (20 scenarios) suites gated at 100%. Trade-off: every new capability appears in the eval report, at moderate dataset work.
- B: only the WhatsApp dataset; router and recovery covered by pytest alone. Trade-off: less work, but recovery is absent from the evaluation report.
- C: as A but 200 WhatsApp messages and a 40-item audit, like phase 04. Trade-off: tighter intervals, but more writing, cost and audit time.

### D8 - Shape of the full module demo
- A (recommended): one command, `orders-demo`, over a sample mixed inbox, running every channel, a WhatsApp clarification and its answer, and the three recovery scenes (the crash scene runs its first and second process as subprocesses), replay by default and live on request. Trade-off: one reproducible showcase, but a longer command to build and test.
- B: a script that chains the existing per-channel commands plus new recovery commands. Trade-off: less new code, but no router in the path and a weaker showcase.
- C: option A without the recovery scenes, which stay in tests and the eval. Trade-off: shorter demo, but recovery is not visible to someone running the demo.

### D9 - Live model cost
- A (recommended): accept live cost with a ceiling of 2 USD for the phase. Trade-off: covers recordings, baseline and live demos with margin.
- B: ceiling of 1 USD. Trade-off: enough for the expected spend, but no margin for a re-record after a fix.
- C: no live cost; the phase ships replay only with hand-written recordings. Trade-off: zero cost, but no real baseline for WhatsApp and no live demo evidence.

### D10 - LangSmith criterion while the trace quota is exhausted (until about 2026-11-05)
- A: keep C17 in this phase and leave it pending, as phases 02 to 04 did; the phase is ready locally but not closed. Trade-off: consistent with earlier phases, but a fourth phase stays open.
- B (recommended): keep C17 in this phase but deliver it, with phases 02 C13, 03 C13 and 04 C15, through one catch-up change after the reset, so all four are traced in one session. Trade-off: one focused session and one owner screenshot round, while the phases still close only when the change is done.
- C: replace C17 with local trace evidence (the graph stream events saved as a JSON file) and drop LangSmith from this phase. Trade-off: closable now, but weakens the observability goal set in the master plan.
