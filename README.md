# Full Purchase Cycle Agents

Portfolio project that demonstrates multi-agent orchestration with [LangGraph](https://github.com/langchain-ai/langgraph) (Python) over the full purchase cycle of a fictional company.
Status: phase 01 (foundations) built: shared database, model client, persistent graph state, tracing and the evaluation harness.
Phase 02 (web form orders) built: the first order channel, from a form submission to a stored order and a reply.
Phase 03 (email orders) built: the email channel, from an `.eml` file with its body and PDF or Excel attachments to a stored order and a reply.
Phase 04 (exceptions) built and delivered for review: both channels ask the customer about ambiguous products, unknown products and doubtful quantities, pause with their state saved and resume when the answer arrives; its LangSmith trace evidence is pending.

## What it does

| Module | Flow | Capabilities shown |
|---|---|---|
| Customer orders | Email (text, PDF, Excel), web form and simulated WhatsApp; extraction, catalog matching, exceptions and reply | Per-channel subgraphs, pause until the customer answers, database |
| Supplier quotes | Out of stock, quote requests, multi-day wait with saved state, comparison and human approval | Persistent state, human-in-the-loop, resumption |
| Invoice reconciliation | Invoice against purchase order and delivery note, differences and a claim draft approved by a person | Structured validation, human-in-the-loop |
| Orchestrator | Top-level graph composing the three modules over the shared database | Subgraph composition, failure recovery |

Every module will have a runnable demo, tests, an evaluation with metrics over a set of cases, and traces in LangSmith.
The plan and its phases live in [docs/plans/0_plan_maestro.md](docs/plans/0_plan_maestro.md).

## How it is built

The project is developed with the SDD Lite method: a master plan, phases with frozen acceptance criteria, autonomous agent execution and adversarial review.
The method guide (in Spanish) is [docs/como-trabajar.md](docs/como-trabajar.md).
`npm run check` groups all checks; the method's guardrails live in `.claude/settings.json` and `.claude/hooks/`.

## Requirements

Windows, Python 3.13 with uv, Node.js 22 (method tooling), Git and GitHub CLI.

## Setup

```
uv sync
npm ci --ignore-scripts --no-audit --no-fund
```

Nothing else is needed to run the checks: `npm run check` replays stored model answers and needs no API key and no `.env` file.

To call the real model, copy `.env.example` to `.env` and fill it in:

- `ANTHROPIC_API_KEY`: an Anthropic API key that belongs to a workspace.
- `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true` and `LANGSMITH_PROJECT`: optional; with them, live runs are traced in LangSmith. `LANGSMITH_ENDPOINT` is only needed for EU accounts.

## Seed the database

```
uv run purchase-cycle seed
```

Creates `data/purchase_cycle.db` (SQLite) with the five shared tables and the permanent fictional catalog: 303 products in 32 families of a Spanish medical supplies distributor, and 50 customers (clinics, care homes and pharmacies).
Re-running it leaves the same rows.

## Run the demo

```
uv run purchase-cycle demo
uv run purchase-cycle demo "Could you send 6 bottles of 70% alcohol, 250 ml?" --mode live
```

The minimal graph has two nodes: `extract` asks Claude Haiku 4.5 for the product SKU and quantity, validated by a Pydantic schema, and `match` loads the product from the database.
The default `--mode replay` answers from stored recordings; `--mode live` calls the model and `--mode record` also stores the answer.
The catalog part of the prompt is cached, and the demo prints the cached tokens it read.
Every run is checkpointed in `data/checkpoints.db` under a `thread_id`; `--thread-id <id> --resume` continues a run stopped after a node completed, for example after `extract`, from that checkpoint without calling the model again; a run stopped inside `extract` calls the model again.
With the LangSmith variables set, live runs appear in LangSmith with one span per node; replay runs never send traces.

## Run the web form demo

```
uv run purchase-cycle web-form-demo
uv run purchase-cycle web-form-demo examples/web_form_submission.json --mode live
```

The web form is simulated as a JSON submission file: a submission id, a customer code and 1 to 20 lines, each with the product text the customer typed (up to 200 characters) and a quantity in catalog sale units.
The default file is `examples/web_form_submission.json`.
The `web_form_order` subgraph has four nodes:

- `validate` checks the submission with Pydantic and the customer code against the database; a rejected submission stops here with a message naming the failing field, with no model call and nothing stored.
- `match` resolves each line to a SKU: a catalog SKU or catalog name, ignoring case, accents, extra spaces and punctuation, is matched with no model call; any other text goes to Claude Haiku 4.5 with the cached catalog, which returns a SKU or null.
- `store` writes one order with channel `web_form` and status `received` and one order line per matched line, in a single transaction; a submission with no matched line stores no order.
- `reply` fills a fixed template from the stored rows, so every price comes from the database, and lists the lines that could not be matched with the text the customer wrote.

The demo prints, per line, the matched SKU and whether it came from deterministic matching or the model, then the stored order number and the reply.
`--mode replay` (the default) needs no key, because the sample file reuses texts of the evaluation dataset; `--mode live` calls the model and prints the cached tokens.
Every run is checkpointed in `data/checkpoints.db` under a `thread_id`; `--thread-id <id> --resume` continues a run stopped after a node completed, for example after `match`, from that checkpoint, so the order is stored once and the matched lines are not sent to the model again.
A run stopped inside `match` (an API error, a missing recording or Ctrl+C) re-runs `match` on resume and sends its model lines again.

## Run the email demo

```
uv run purchase-cycle email-demo
uv run purchase-cycle email-demo examples/email_orders --mode live
```

The demo reads every `.eml` file of a folder; the default folder `examples/email_orders/` holds four emails: an order in the body (`EML-0024.eml`), an order in a PDF attachment (`EML-0001.eml`), an order in an Excel attachment (`EML-0012.eml`) and an email that is not an order (`EML-0002.eml`).
The `email_order` subgraph has four nodes:

- `intake` parses the email with the standard library, finds the customer by the sender address and reads the body and the `.txt`, `.pdf` and `.xlsx` attachments; an unknown sender, an unreadable file or an over-long text stops here with no model call; then Claude Haiku 4.5 decides whether the email is an order and gives a reason.
- `extract` runs only for orders: the model returns every ordered line with the customer's text, a catalog SKU or null, the quantity in sale units and its source (`body` or the attachment name); a SKU outside the catalog, a non-positive quantity or an unknown source stops the run before anything is stored.
- `store` writes one order with channel `email` and status `received` and one order line per matched line, in a single transaction; an email with no matched line stores no order.
- `reply` fills the phase 02 template addressed to the sender, quoting the subject, with prices from the database and the lines that could not be matched.

The demo prints, per email, the intake decision and its reason, each line with its text, SKU and source, the stored order number and the reply.
`--mode replay` (the default) needs no key, because the sample emails are copied from the test split of the evaluation dataset and their recorded answers are reused; `--mode live` calls the model and prints the cached tokens of both calls.
Each email runs in its own checkpoint thread in `data/checkpoints.db`.

## Run the exceptions demo

```
uv run purchase-cycle exceptions-demo
uv run purchase-cycle clarify list
uv run purchase-cycle clarify answer <thread_id> --file examples/exceptions/web_form_answer.txt
uv run purchase-cycle clarify answer <thread_id> --text "The caps are the bouffant caps. Please drop the hospital beds." --mode live
uv run purchase-cycle clarify close <thread_id>
```

Every order channel (web form, email and WhatsApp) now has a `clarify` step, the shared `clarification` subgraph, between understanding the order (`match` or `extract`) and `store`:

- `detect` applies deterministic rules to every line: an ambiguous product when the line text fits two or more catalog products by a search over catalog names (up to 6 candidates), also when the matcher or extractor already gave it a SKU, an unknown product when the line has no SKU and the search finds nothing, and a doubtful quantity above 500 sale units or, for the free-text channels (email and WhatsApp), not supported by any number in the source text.
- `ask` has Claude Haiku 4.5 draft one question for all the doubtful lines; a deterministic check rejects a question that does not name every doubtful line with the text the customer wrote and every candidate name.
- `wait` pauses the run with LangGraph `interrupt`; the state stays in the SQLite checkpointer `data/checkpoints.db` and a `pending` row in the `clarifications` table, and nothing is written to `orders` or `order_lines`.
- `interpret` has the model read the customer answer into, per doubtful line, a SKU and quantity, a removal or "still unclear"; a missing or repeated line, a SKU outside the catalog or a non-positive quantity is rejected and the same question keeps waiting.
- Lines still unclear get a second question; after the second round the clear and resolved lines are stored in one transaction and the reply lists removed and unresolved lines with the text the customer wrote.

An order with no doubt goes straight through `clarify` with no model call and no pause, as in phases 02 and 03.

`exceptions-demo` runs the two samples of `examples/exceptions/`: a web form submission (`web_form_submission.json`, an ambiguous gel and a quantity of 1,200) and an email (`email_order.eml`, ambiguous caps and hospital beds not in the catalog).
For each one it prints the lines, the doubts found, the question and the thread id, and stops; the thread id starts with a new run id on every run, so copy it from the output, which also prints the full `clarify answer` command.
The other commands run as separate processes, so the pause survives the end of the demo process:

- `clarify list` shows every paused thread with its channel, customer, round and age.
- `clarify answer <thread_id> --text "..."` or `--file <path>` resumes the thread with the customer answer and prints the interpretation, the stored order number and the reply, or the second question; `examples/exceptions/web_form_answer.txt` and `email_answer.txt` are sample answers for the two samples.
- `clarify close <thread_id>` closes a thread with no answer: it stores the clear lines, lists the rest in the reply as unanswered and marks the row `closed`.

Answering or closing a thread that is not pending fails with a message and changes nothing.
`--mode replay` (the default) needs no key: the channel steps replay the detection evaluation recordings and the question and interpretation of the samples and their sample answers are recorded in `evals/recordings/`; any other answer text needs `--mode live`.
`--mode live` on `exceptions-demo` and on `clarify answer` calls Claude Haiku 4.5 and prints the cached tokens; with the LangSmith variables set the run is traced, and the pause and the resumption share the thread id (the phase 04 trace evidence is still pending, see Limits).

## Run the tests

```
npm run check
```

Runs the method's hook tests, ruff, pytest and the five evaluations in replay mode.

## The golden dataset

The evaluation dataset is synthetic.
`evals/datasets/order_line_extraction/` holds 2,000 single-line order requests in eight categories of 250 cases (exact name, synonym, quantity in words, unit expressions, noise around the order, typos, near-miss variants and products not in the catalog), split into 500 development and 1,500 test cases, stratified by category.
Every catalog product appears in five or six cases.

How it was built:

1. `uv run purchase-cycle dataset plan` writes `plan.jsonl` from a fixed seed: the category, expected SKU, quantity and trap of every case are decided before any sentence exists.
2. The sentences in `sentences/` were written by the coding agent (Claude Code under the owner's subscription, not the API), following the plan, and checked with `uv run purchase-cycle dataset check <file>`.
3. `uv run purchase-cycle dataset build` joins plan and sentences, rejects cases that break the automatic rules (unknown SKU, non-positive quantity, duplicate sentence, category rule) and writes `dataset.jsonl`.
4. A blind second pass by clean-context agents labelled every sentence again and compared its labels with the plan; any disagreement is fixed before the owner audit (version 1.0 had none).
5. The owner audits 150 random cases plus a hand-written contrast set of 60 sentences (`contrast.jsonl`): `uv run purchase-cycle audit create`, fill the `verdict` column of `evals/audit/`, then `uv run purchase-cycle audit report`.
   The audit passes with at most 2 wrong labels in the sample; its result is recorded in the phase plan (`docs/plans/fase-01-cimientos/plan.md`).

The dataset only changes through these commands; the labels in `dataset.jsonl` must equal the plan, and a test checks it.

### Web form matching dataset

`evals/datasets/web_form_matching/` holds 624 form lines grouped into 225 submissions of 1 to 5 lines, in six categories of 104 lines: exact name, SKU typed, short or informal name, typo, near-miss variant and product not in the catalog.
The split is by submission, stratified by category: 156 development and 468 test lines.
Every catalog family appears and every product appears once or twice.

It was built with the same method:

1. `uv run purchase-cycle web-form-dataset plan` writes `plan.jsonl` from a fixed seed: the category, expected SKU (or an out-of-catalog item) and trap of every line, and its submission and split.
2. The exact name and SKU lines are produced by the script; the other four categories were written by the coding agent into `texts/` and checked with `uv run purchase-cycle web-form-dataset check <file>`.
3. `uv run purchase-cycle web-form-dataset build` joins plan and texts, rejects lines that break the automatic rules (unknown SKU, duplicate text after normalisation, category rule) and writes `dataset.jsonl`.
4. A blind second pass by clean-context agents relabelled every written line (`second_pass_review.jsonl`); version 1.0 had no disagreement with the plan.
5. The owner audited 60 random written lines: `uv run purchase-cycle web-form-audit create`, fill the `verdict` column of `evals/audit/web_form_matching-audit-v1.0.csv`, then `uv run purchase-cycle web-form-audit report`.
   The audit passes with at most 1 wrong label; version 1.0 had none (error rate 0.0%, 95% Wilson interval 0.0% to 6.0%).

### Email order extraction dataset

`evals/datasets/email_order_extraction/` holds 312 `.eml` files in six categories of 52 emails: list in the body, conversational body, `.txt` attachment, PDF attachment, Excel attachment and not an order.
They carry 923 expected lines, 92 of them for products not in the catalog; the split is by email, stratified by category: 78 development and 234 test emails.
PDF attachments use two layouts (order form and delivery note) and Excel attachments two more (header row and extra columns).

It was built with the same method:

1. `uv run purchase-cycle email-dataset plan` writes `plan.jsonl` from a fixed seed: the category, sender, order or not, and every line with its expected SKU or out-of-catalog item, quantity in sale units, unit expression, location and trap (typo or near-miss variant), before any text exists.
2. The email bodies and line texts in `texts/` were written by the coding agent following the plan and checked with `uv run purchase-cycle email-dataset check <file>`.
3. `uv run purchase-cycle email-dataset build` validates the texts (unknown SKU, non-positive quantity, duplicate text, a planned line missing from the rendered text, category rules), renders the `.eml` files and attachments deterministically and writes `dataset.jsonl`.
4. A blind second pass by clean-context agents read only the catalog and the text the model sees and labelled every email again (`second_pass_review.jsonl`); 2 of 312 emails disagreed with the plan and their texts were fixed.
5. The owner audited 40 random emails: `uv run purchase-cycle email-audit create`, fill the `verdict` column of `evals/audit/email_order_extraction-audit-v1.0.csv`, then `uv run purchase-cycle email-audit report`.
   The audit passes with at most 1 wrong label; version 1.0 had none, 40 of 40 correct (error rate 0.0%, 95% Wilson interval 0.0% to 8.8%).

### Clarification datasets

Two datasets evaluate the exceptions step:

- `evals/datasets/clarification_detection/` holds 200 orders, 100 web form submissions and 100 emails (`emails/`), with 620 lines: 70 lines per doubt type (ambiguous product, unknown product, doubtful quantity) and 64 orders with no doubt; each line carries its expected doubts.
  Email quantity doubts are 15 lines over the 500 ceiling and 20 lines with no stated number.
- `evals/datasets/clarification_answers/` holds 210 answer cases in six categories of 35: pick by size or variant, pick by description, give a quantity, remove a line, several lines at once, and still unclear or off topic; each case has its doubtful lines, the question sent and the customer answer, with the expected resolution per line (a SKU and quantity, a removal or still unclear).

Both are split by order or case, stratified by channel and doubt type or by category: 50 development and 150 test orders, 54 development and 156 test cases.
They were built with the same method as the earlier datasets:

1. `uv run purchase-cycle clarification-dataset plan --dataset detection` (or `--dataset answers`) writes `plan.jsonl` from a fixed seed: every line with its product or out-of-catalog item, quantity, doubt type and, for an ambiguous line, its candidate set, before any text exists.
2. The texts in `texts/` were written by the coding agent following the plan and checked with `uv run purchase-cycle clarification-dataset check --dataset <name> <file>`.
3. `uv run purchase-cycle clarification-dataset build --dataset <name>` judges every line with the runtime detection rules, as an ideal matcher or extractor would return it, and rejects a text that does not raise exactly its planned doubt and candidates, a planned line missing from the text, a broken quantity rule, a duplicate text or an answer naming a SKU it may not name; emails are rendered with the phase 03 renderer and `dataset.jsonl` is written only when nothing is rejected.
4. A blind second pass by clean-context agents read only the catalog and the texts (`second_pass_judgments/`) and was compared with the plan in `second_pass_review.jsonl`: 27 of 620 detection lines and 23 of 264 doubtful answer lines disagreed, every one justified (mostly candidate sets and the boundary between picking by variant and by description, with the same resolution).
5. The owner audit of 40 random items, 20 from each dataset: `uv run purchase-cycle clarification-audit create`, fill the `verdict` column of `evals/audit/clarification-audit-v1.0.csv`, then `uv run purchase-cycle clarification-audit report`.
   The audit passes with at most 1 wrong label; the owner review (2026-10-08) found 1 wrong label in 40, an error rate of 2.5% with a 95% Wilson interval of [0.4%, 12.9%], so it passes.

## Run the evaluation

```
npm run eval
npm run eval:live
```

`npm run eval` replays the test split of the five evaluations and exits non-zero when a gate of any of them fails.
For `order_line_extraction` it prints product and quantity accuracy with 95% Wilson intervals, globally and per category, the failures and the contrast set.
For `web_form_matching` it prints line product accuracy with 95% Wilson intervals, globally and per category, the model calls per category (zero for exact name and SKU typed), submission accuracy (reported, not gated) and the failures.
The gates are:

- absolute gate: each gated metric must reach the threshold stored with its baseline;
- regression gate: an exact McNemar test against the stored per-case baseline fails on a significant drop (p < 0.05).

`uv run purchase-cycle eval --suite web_form_matching` runs only the new evaluation (`--suite order_line_extraction` only the first one; the default is `all`).
The Claude Haiku 4.5 baseline on the web form test split is 99.6% line product accuracy (95% interval 98.5% to 99.9%, 2 failures in 468 lines), so its threshold is 95%.

`uv run purchase-cycle eval --suite email_order_extraction` runs only the email evaluation through the `email_order` subgraph.
It prints intake accuracy, line recall and line precision (the gated metrics), field accuracy for SKU and quantity, out-of-catalog detection and email exact match, with 95% Wilson intervals globally, per category and per source, and the failing emails by category.
Lines are matched one to one, so an extracted line counts for at most one expected line.
Line recall counts expected catalog lines matched by an extracted line with the same SKU, quantity and source; line precision counts extracted catalog lines matched that way.
Out-of-catalog detection holds when an order email's unknown lines pair one to one with the expected ones on quantity, source and the requested text, which the line's citation must name as whole words (a citation naming several expected texts matches none); email exact match also needs the right intake, every catalog line matched and no extra line.
The Claude Haiku 4.5 baseline on the 234 test emails (`evals/baselines/email_order_extraction.json`):

| Metric | Value | 95% interval |
|---|---|---|
| Intake accuracy (234 emails) | 98.7% | 96.3% to 99.6% |
| Line recall (586 expected lines) | 96.9% | 95.2% to 98.0% |
| Line precision (574 extracted lines) | 99.0% | 97.7% to 99.5% |
| Field accuracy, SKU (586 expected lines) | 97.3% | 95.6% to 98.3% |
| Field accuracy, quantity (570 lines with the right SKU) | 99.6% | 98.7% to 99.9% |
| Out-of-catalog detection (195 order emails) | 96.4% | 92.8% to 98.3% |
| Email exact match (195 order emails) | 94.4% | 90.2% to 96.8% |

The three gated metrics reach 95%, so the threshold is 95%.

`uv run purchase-cycle eval --suite clarification_detection` runs the detection orders through both parent graphs up to `detect`, replaying the phase 02 matcher and phase 03 intake and extraction answers recorded for these orders, and compares the doubts raised per line with the expected ones.
It prints recall and precision per doubt type and the false question rate (the share of orders with no doubt that still get a question), with 95% Wilson intervals globally, per channel and per line kind, and the failing orders.
The Claude Haiku 4.5 baseline on the 150 test orders and 453 lines (`evals/baselines/clarification_detection.json`):

| Metric | Value | 95% interval | Threshold |
|---|---|---|---|
| Recall, ambiguous product (55 lines) | 89.1% | 78.2% to 94.9% | at least 89.1% |
| Recall, unknown product (53 lines) | 94.3% | 84.6% to 98.1% | at least 94.3% |
| Recall, doubtful quantity (46 lines) | 91.3% | 79.7% to 96.6% | at least 91.3% |
| False question rate (48 orders with no doubt) | 0.0% | 0.0% to 7.4% | at most 5% |
| Precision, ambiguous product (49 doubts raised) | 100.0% | 92.7% to 100.0% | reported |
| Precision, unknown product (51 doubts raised) | 98.0% | 89.7% to 99.7% | reported |
| Precision, doubtful quantity (43 doubts raised) | 97.7% | 87.9% to 99.6% | reported |

The spec gate was 95% recall per doubt type; the baseline missed it, and the owner set the three recall thresholds at the measured level (deviation 04.1 in [docs/plans/fase-04-excepciones/plan.md](docs/plans/fase-04-excepciones/plan.md)).
Every miss is an email line: the phase 03 extractor drops some lines, returns a non-integer quantity that the schema rejects, or copies a purpose clause such as "for the treatment room" into the line text so the candidate search finds one product or none; web form recall is 100% for the three types.
The extractor fix is a separate change, [docs/changes/001-email-extractor-dropped-lines.md](docs/changes/001-email-extractor-dropped-lines.md), not yet authorized.

`uv run purchase-cycle eval --suite clarification_answers` sends each case's doubts, question and answer to the interpreter and compares its reading with the expected resolution per line.
It prints resolution accuracy per doubtful line (gated) and case exact match (reported), with 95% Wilson intervals globally, per channel and per category, and the failures.
The Claude Haiku 4.5 baseline on the 156 test cases (`evals/baselines/clarification_answers.json`):

| Metric | Value | 95% interval | Threshold |
|---|---|---|---|
| Resolution accuracy (195 doubtful lines) | 99.5% | 97.2% to 99.9% | at least 95% |
| Case exact match (156 cases) | 99.4% | 96.5% to 99.9% | reported |

Both new evaluations also have the McNemar regression gate against their stored per-item baseline.

`npm run eval:live` runs the same cases against the real model and logs a LangSmith experiment against the uploaded dataset splits (`uv run purchase-cycle eval-upload` uploads the splits of the five evaluations once; `--suite <name>` uploads one).
Other useful forms: `uv run purchase-cycle eval --suite <name> --split dev --mode live` for prompt tuning without traces, and `uv run purchase-cycle eval --suite <name> --mode record --split test --set-baseline` to re-record the test split of one evaluation and store a new baseline in `evals/baselines/`.
Recordings in `evals/recordings/` are tied to the exact prompt and model: changing either needs a new recording and a new comparison against the baseline.

## Limits

- Data, customers, suppliers and channels are fictional or simulated; there is no integration with real email, WhatsApp or ERP systems.
- The dataset is synthetic and written by a model of the same family as the one evaluated; the contrast set shows the gap with hand-written messages.
- Phase 01 extracts a single order line from free text; phase 02 adds the web form channel with multi-line orders and a template reply.
- The web form is a JSON file, not a web page; the reply is printed, not sent; ambiguous products, stock checks and duplicate submissions come in later phases.
- The email channel reads `.eml` files from a folder, not a mailbox; scanned PDFs, images and other attachment types are not read.
- The email extractor sends the whole catalog in the prompt, so it only works while the catalog fits the model's context window; retrieval over the catalog is not implemented.
- Phase 04 questions and answers do not travel through a real channel: the question is printed and the answer is delivered by the `clarify answer` command.
- There is no automatic timeout or reminder; an unanswered thread stays paused until an operator runs `clarify close`.
- A wrong variant picked with confidence by the matcher or the extractor raises no product doubt when the line text fits a single catalog product or none.
- The candidate search and the extractor send or use the whole catalog, with the same context-window limitation as phase 03.
- Detection inherits the phase 03 extractor limitations: dropped email lines, non-integer quantities rejected by the schema and purpose clauses copied into the line text lose doubts on email orders, so the detection recall thresholds sit at the measured level until change 001 is done.
- Four detection lines with generic hints ("single", "free", "cm size", "litre") are labelled ambiguous by the runtime rule while a person may read them as unknown or as the default size; the owner accepted them as a recorded limitation of the second pass.
- The LangSmith trace of the phase 04 demo and the experiments of the two new evaluations are pending until the LangSmith trace quota resets, around 2026-11-05.

## License

[MIT](LICENSE)
