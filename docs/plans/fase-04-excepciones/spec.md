# Phase 04 - Exceptions: specification

Status: draft
Approved by the owner: -
Master plan: `../0_plan_maestro.md`

## Goal
When an order from the web form or from an email holds an ambiguous product, an unknown product or a doubtful quantity, the system asks the customer a question, pauses with its state saved and resumes when the answer arrives, even after the process was stopped and started again.
It is built as one shared LangGraph subgraph, `clarification`, inserted in both channels between understanding the order and storing it, so phase 05 and phase 10 compose it once.
It is needed now because phases 02 and 03 silently drop every line they cannot match, and because this is the project's first human-in-the-loop pause with durable state, a capability the master plan must make visible.

## Design direction
Proposed by the coordinator; the owner can override it through the open decisions.
- Both parent graphs keep their first steps (web form: `validate`, `match`; email: `intake`, `extract`) and gain a `clarify` step, the shared subgraph, before `store` and `reply`.
- Inside `clarify`: `detect` (deterministic rules) -> `ask` (the model drafts the question) -> `wait` (LangGraph `interrupt`, state in the SQLite checkpointer) -> `interpret` (the model reads the answer) -> back to `detect` for a new round or out to `store`.
- An order with no doubt goes straight through `clarify` with no model call and no pause, so phase 02 and phase 03 behaviour is unchanged for it.
- The phase 02 matcher and the phase 03 extractor keep their prompts, recordings and baselines (open decision 1).

## Scope
In:
- Doubt detection by deterministic rules over the lines the channel already produced, with three doubt types:
  - Ambiguous product: the line has no SKU and its text matches two or more catalog products by a deterministic candidate search over catalog names (for example "nitrile gloves" matches the five sizes of `GLV-NIT`); the candidates, at most 6, travel with the doubt.
  - Unknown product: the line has no SKU and the candidate search finds nothing.
  - Doubtful quantity: the quantity is above a fixed ceiling per line, or, for email lines, no number in the source text (digits, number words or dozens) supports the quantity, either as written or converted by the pack size.
- A question drafted by Claude Haiku 4.5 from the doubtful lines and their candidates, addressed to the customer, naming every doubtful line with the text the customer wrote; a deterministic check rejects a question that misses a doubtful line.
- A pause through LangGraph `interrupt` with the SQLite checkpointer already used by phases 01 to 03; the pending question is also recorded in a new `clarifications` table (thread id, channel, customer code, question, round, status, timestamps) so it can be listed without reading checkpoints.
- Resumption with the customer answer through a new CLI command that delivers a text answer to a thread id, from a new process, with `Command(resume=...)` (open decision 4).
- Answer interpretation by Claude Haiku 4.5: for every doubtful line it returns a catalog SKU and a quantity, a removal ("not needed"), or "still unclear"; deterministic checks require every doubtful line to be answered exactly once, every SKU to exist in the catalog and every quantity to be a positive whole number, and stop the run otherwise.
- Whole-order hold: nothing is stored while any doubt is open; after the last round the clear and resolved lines are stored as one order and the reply lists removed and unresolved lines (open decisions 2 and 3).
- A maximum of 2 question rounds per order; lines still unclear after the last round are left out of the order and listed in the reply (open decision 3).
- A CLI listing of pending clarifications and a CLI command that closes one without an answer, storing the clear lines and listing the rest as unanswered (open decision 5).
- All model calls through the existing client with live, record and replay modes and Pydantic validation; the question and interpretation prompts have their own recordings files.
- A demo that runs one web form submission and one email with doubts, prints the question and thread id and stops; a second command, run as a separate process, delivers a sample answer and prints the stored order and reply.
- Two new evaluations run in replay mode inside `npm run check` next to the three existing ones: `clarification_detection` and `clarification_answers` (see "Evaluation design").
- README sections for the exceptions demo, the new datasets and evaluations, and the known limitations.

Out:
- Sending the question or receiving the answer through a real channel (email server, web page, WhatsApp); the answer arrives by CLI.
- Parsing an answer email (`.eml` reply threads); the answer is plain text (open decision 4).
- Automatic timeouts and reminders; the simulated clock arrives in phase 06 (open decision 5).
- Unknown senders and unknown customer codes: they stay rejected as in phases 02 and 03.
- New products the customer adds in the answer; they are ignored and the reply says so.
- Detecting a wrong variant the matcher or extractor picked with confidence (a SKU was returned); only lines with no SKU or a doubtful quantity raise doubts (open decision 1).
- Changes to the phase 01 to 03 prompts, recordings, datasets and gates.
- Stock checks, duplicate protection and retries: phases 05 and 06.

## Evaluation design

### Tasks under evaluation
- `clarification_detection`: given an order from either channel, the system must raise exactly the expected doubts, by line and type.
  It runs end to end through the channel steps in replay mode, so it measures the rules on real matcher and extractor output.
- `clarification_answers`: given the doubtful lines, their candidates, the question and a customer answer, the interpreter must return the expected resolution per doubtful line (SKU and quantity, removal, or still unclear).

### Golden datasets
Built with the phase 01 method: a seeded planning script fixes the labels before any text exists, the coding agent writes the texts in Claude Code sessions under the owner's subscription, automatic validation and a clean-context second-pass review run before the owner audit.
- `clarification_detection`: at least 200 orders, half web form submissions and half `.eml` emails, with at least 500 lines; at least 60 lines per doubt type and at least 60 orders with no doubt at all; fixed stratified split of about 25% development and 75% test (open decision 6).
- `clarification_answers`: at least 200 cases in at least six answer categories of at least 25 cases each: pick a candidate by size or variant, pick by description, give a quantity, remove a line, several lines answered at once, and an answer that is still unclear or off topic; same split rule (open decision 6).
- Owner audit: 40 items drawn at random across both datasets, reviewed in a review file.

### Metrics and graders
- Detection, per line and per doubt type: precision and recall of raised doubts.
- Detection, per order: false question rate, the share of orders with no expected doubt that receive a question.
- Answers, per doubtful line: resolution accuracy (right SKU and quantity, right removal or right "still unclear").
- Answers, per case: exact match, every doubtful line resolved right; reported, not gated.
- All graders are deterministic; every metric is reported with a 95% Wilson interval on the test split, globally, per channel and per category, plus failures grouped by category.

### Gates
- Absolute gates in the test split: detection recall per doubt type at least 95%, false question rate at most 5%, and answer resolution accuracy at least 95%, if the baseline measured in this phase reaches them; otherwise execution stops and the owner decides, recorded as a deviation.
- Regression gate: exact McNemar test per line for detection recall and per doubtful line for resolution accuracy against the stored baseline, failing on a significant drop (p < 0.05).
- The three existing evaluations keep running unchanged with their own gates.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.
Written for the recommended options of the open decisions; adjusted before approval if the owner chooses otherwise.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the five evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | A web form submission and an email with no doubt go through `clarify` with no extra model call and no pause, and store the same rows and reply as in phases 02 and 03 | Pytest tests counting model calls and comparing rows and reply with the phase 02 and 03 expected values |
| C3 | The detection rules raise an ambiguous doubt with its candidates, an unknown doubt, and a doubtful quantity doubt for the over-ceiling and unsupported-number cases, and nothing for a clear line | Pytest tests per rule over fixture lines of both channels |
| C4 | An order with at least one doubt ends its run paused: the state holds the drafted question, the `clarifications` table holds one pending row for the thread, and no row is written to `orders` or `order_lines` | Pytest test on a temporary database with recorded answers, counting rows |
| C5 | The drafted question names every doubtful line with the text the customer wrote and, for an ambiguous line, its candidate names; a recorded question that misses a doubtful line stops the run before pausing | Pytest tests with recorded valid and invalid questions |
| C6 | A paused thread resumes with the customer answer from a new operating system process, after the process that paused it has exited, and ends with the order stored and the reply built | Pytest test that pauses in one subprocess, checks it has exited, resumes in a second subprocess and compares the stored rows and reply with expected values |
| C7 | The interpreter returns, per doubtful line, a SKU and quantity, a removal or "still unclear" through the existing client in live, record and replay modes; a missing or repeated line, a SKU not in the catalog or a non-positive quantity stops the run before anything is written | Pytest tests with recorded valid and invalid answers that count rows |
| C8 | After an answer, lines still unclear trigger a second question; after the second round, the order stores the clear and resolved lines in one transaction and the reply lists removed and unresolved lines with the text the customer wrote | Pytest tests over a two-round fixture comparing rows and reply with expected values |
| C9 | The pending listing shows every paused thread with channel, customer, round and age; closing a thread without an answer stores the clear lines, lists the rest as unanswered, and marks the row closed; answering or closing a thread that is not pending fails with a message and changes nothing | Pytest tests per command on a temporary database |
| C10 | The demo prints, for one web form submission and one email with doubts, the doubts found, the question and the thread id, and stops; the answer command, run afterwards as a separate process, prints the interpretation, the stored order number and the reply; both work in replay mode with no key and in live mode against Claude Haiku 4.5 | Run in replay mode (evidence in `check`) and once in live mode with the owner's key (output saved) |
| C11 | The versioned `clarification_detection` and `clarification_answers` datasets meet the sizes and category minimums in "Golden datasets", with fixed stratified splits, and every item passes the automatic validation; re-running the planning scripts with the same seed gives the same plans | Pytest tests over the dataset and plan files; generator output saved as evidence |
| C12 | The owner audit of 40 random items finds at most 1 item with a wrong label, and the report shows the observed error rate with its Wilson interval | Review file completed by the owner and the computed rate, saved as evidence |
| C13 | `npm run eval` in replay mode reports the five evaluations; the new ones show the metrics in "Metrics and graders" with 95% Wilson intervals on the test split, globally, per channel and per category, and the command exits non-zero when any absolute or regression gate of any evaluation fails | Run the command; pytest tests that force each new gate to fail prove the non-zero exit |
| C14 | The baselines of the new evaluations on their test splits are measured, stored with their per-item results, and the thresholds are set by the rule in "Gates" | Stored baseline files and the live run report, saved as evidence |
| C15 | With the LangSmith variables set, a live demo run appears in LangSmith as one thread with the pause and the resumption, with one span per node of the parent graph and of `clarify`, and a live run of each new evaluation is logged as an experiment against its uploaded dataset splits | Trace and experiment links, and the owner's screenshot of the trace, as evidence |
| C16 | No secret is versioned: the new recordings, sample inputs and dataset files contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C17 | The README explains in English how to run the exceptions demo and the answer command in replay and live mode, how the new datasets were built and how to run the new evaluations, and states the known limitations below | Follow the new README sections in a clean clone |

## Constraints and risks
- No new dependency is expected: `langgraph` already provides `interrupt`, `Command` and the SQLite checkpointer.
- New table `clarifications` in the shared database schema; existing tables are not changed.
- Live runs need the owner's Anthropic and LangSmith keys in `.env`; the agent never reads that file.
- Expected API cost of the phase: 1 to 4 USD (recording the matcher and extractor for the new detection orders, about 300 question drafts and 250 interpretations per recorded split, prompt tuning on the development split and a few live runs) with prompt caching; an estimate, not a limit.
- The LangSmith trace quota is expected to reset around 2026-11-05; until then C15, like phase 03 C13, stays blocked and the phase can be ready locally but not closed.
- `interrupt` re-runs the node that paused from its start on resumption; the question must be drafted in a node before the pause, or the resumption pays and records a second model call.
- An interrupt inside a subgraph needs the parent graph compiled with the checkpointer and resumed with the same thread id; both parents already take a checkpointer.
- The checkpoint file and the business database are two SQLite files; a crash between storing the order and marking the clarification closed could leave them out of step, so `store` and the status change share one business transaction and the checkpoint is written after it.
- Candidate search by catalog names may miss informal names the model would understand; detection recall per type shows it.
- Like earlier phases, the text writer and the evaluated model belong to the same family, so results may be optimistic.
- The owner audit takes about 20 minutes and blocks C12.
- Windows is the reference environment; the restart test uses subprocesses, not signals.

## Assumptions
- One question per round covers every open doubt of the order; questions are never sent line by line.
- The quantity ceiling is a single fixed number of sale units per line (proposed: 500), set as a constant in this phase.
- The thread id identifies the order across the pause; the `clarifications` row also stores which channel graph to resume.
- The customer answer is free text in English, like the rest of the data.
- The question and the reply after resumption are returned in the graph state and printed, as in phases 02 and 03.
- A removal is the customer's choice and is listed in the reply as removed, not as unmatched.
- An unknown product gets a question that names it and asks for another description or confirms removal; the interpreter can resolve it only to a catalog SKU.
- Recordings for the new prompts go to their own files under `evals/recordings/`.
- `npm run check` stays fast: replaying the five evaluations takes seconds.

## Known limitations
- Questions and answers do not travel through a real channel; the answer is delivered by a CLI command.
- There is no automatic timeout or reminder; an unanswered thread stays paused until an operator closes it.
- A wrong variant picked with confidence by the matcher or extractor raises no doubt.
- The candidate search and the extractor send or use the whole catalog, with the same context-window limitation stated in phase 03.

## Open decisions
Questions the owner must resolve before approval. Empty on approval.

1. How are doubts detected?
   - A (recommended): deterministic rules over the lines the channels already produce, plus a candidate search over catalog names. Keeps the phase 02 and 03 prompts, recordings and baselines intact, costs nothing per order and is fully testable; it cannot see a wrong variant picked with confidence.
   - B: extend the matcher and extractor answers with candidates and a quantity-evidence flag. Catches more cases, but re-records and re-baselines two existing evaluations and changes frozen phase 02 and 03 behaviour (deviation).
   - C: an extra model call that reviews every order for doubts. Most flexible, but every clear order pays a call and C2 no longer holds.
2. What is stored while a question is open?
   - A (recommended): hold the whole order; nothing is stored until the last round ends. One order per request, the checkpoint is the single source of the pending state, no status changes on stored orders.
   - B: store the clear lines now as an order with status `on_hold` and add the resolved lines on resumption. Visible in the database, but needs order updates and a new status.
   - C: store the clear lines now as a received order and the resolved lines later as a second order. The customer gets two orders and two replies.
3. How many question rounds, and what happens if the answer is still unclear?
   - A (recommended): up to 2 rounds; then the clear and resolved lines are stored and the rest are listed in the reply as unresolved. Gives the customer one retry without endless loops.
   - B: 1 round; anything still unclear is left out. Simplest, but one vague answer loses the line.
   - C: up to 2 rounds; then the order goes to a human operator with status `needs_review` instead of being stored. Safest for the business, adds an operator path this phase does not have.
4. How does the customer answer arrive in the demo?
   - A (recommended): one CLI command for both channels that takes a thread id and the answer as text (`--text` or `--file`). Same path for web and email, easy to test across processes.
   - B: for email, a reply `.eml` file matched to the thread by its subject or `In-Reply-To` header; for web form, text. More realistic, adds reply parsing and quoting rules.
5. What happens when the answer never arrives?
   - A (recommended): the thread stays paused; a CLI listing shows pending threads and a close command stores the clear lines and lists the rest as unanswered. Automatic timeouts wait for the phase 06 simulated clock.
   - B: add a timeout now with a simulated "now" argument on the close command that closes every thread older than N days. Earlier proof of time handling, partly duplicates phase 06.
   - C: no close path in this phase; threads stay paused forever. Least work, but the demo cannot show what happens to a silent customer.
6. Evaluation size and audit.
   - A (recommended): 200 orders (at least 500 lines) for detection and 200 answer cases, owner audit of 40 items. Enough for Wilson intervals of about 3 points on the main metrics at moderate cost.
   - B: 400 orders and 400 answer cases, audit of 60. Tighter intervals, about double the writing time and recording cost.
   - C: 100 orders and 100 answer cases, audit of 20. Cheaper, intervals too wide to gate at 95%.
7. Who writes the question to the customer?
   - A (recommended): Claude Haiku 4.5, from the doubtful lines and candidates, with a deterministic check that every doubtful line is named. Natural questions that fit any mix of doubts; shows a second agent in the subgraph.
   - B: a fixed template filled from the doubts. No cost and fully deterministic, but stiff and less of an agent showcase.
