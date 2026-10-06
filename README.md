# Full Purchase Cycle Agents

Portfolio project that demonstrates multi-agent orchestration with [LangGraph](https://github.com/langchain-ai/langgraph) (Python) over the full purchase cycle of a fictional company.
Status: phase 01 (foundations) built: shared database, model client, persistent graph state, tracing and the evaluation harness.
Phase 02 (web form orders) built: the first order channel, from a form submission to a stored order and a reply.

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
Every run is checkpointed in `data/checkpoints.db` under a `thread_id`; `--thread-id <id> --resume` continues a stopped run from its last checkpoint without calling the model again.
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
Every run is checkpointed in `data/checkpoints.db` under a `thread_id`; `--thread-id <id> --resume` continues a stopped run from its last checkpoint, so the order is stored once and the lines already matched are not sent to the model again.

## Run the tests

```
npm run check
```

Runs the method's hook tests, ruff, pytest and both evaluations in replay mode.

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

## Run the evaluation

```
npm run eval
npm run eval:live
```

`npm run eval` replays the test split of both evaluations and exits non-zero when a gate of either fails.
For `order_line_extraction` it prints product and quantity accuracy with 95% Wilson intervals, globally and per category, the failures and the contrast set.
For `web_form_matching` it prints line product accuracy with 95% Wilson intervals, globally and per category, the model calls per category (zero for exact name and SKU typed), submission accuracy (reported, not gated) and the failures.
The gates are:

- absolute gate: each gated metric must reach the threshold stored with its baseline;
- regression gate: an exact McNemar test against the stored per-case baseline fails on a significant drop (p < 0.05).

`uv run purchase-cycle eval --suite web_form_matching` runs only the new evaluation (`--suite order_line_extraction` only the first one; the default is `all`).
The Claude Haiku 4.5 baseline on the web form test split is 99.6% line product accuracy (95% interval 98.5% to 99.9%, 2 failures in 468 lines), so its threshold is 95%.

`npm run eval:live` runs the same cases against the real model and logs a LangSmith experiment against the uploaded dataset splits (`uv run purchase-cycle eval-upload` uploads the splits of both evaluations once; `--suite <name>` uploads one).
Other useful forms: `uv run purchase-cycle eval --suite <name> --split dev --mode live` for prompt tuning without traces, and `uv run purchase-cycle eval --suite <name> --mode record --split test --set-baseline` to re-record the test split of one evaluation and store a new baseline in `evals/baselines/`.
Recordings in `evals/recordings/` are tied to the exact prompt and model: changing either needs a new recording and a new comparison against the baseline.

## Limits

- Data, customers, suppliers and channels are fictional or simulated; there is no integration with real email, WhatsApp or ERP systems.
- The dataset is synthetic and written by a model of the same family as the one evaluated; the contrast set shows the gap with hand-written messages.
- Phase 01 extracts a single order line from free text; phase 02 adds the web form channel with multi-line orders and a template reply.
- The web form is a JSON file, not a web page; the reply is printed, not sent; ambiguous products, stock checks and duplicate submissions come in later phases.

## License

[MIT](LICENSE)
