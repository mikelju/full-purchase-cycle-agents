# Phase 01 - Foundations: specification

Status: draft
Approved by the owner: pending
Master plan: `../0_plan_maestro.md`

## Goal
Leave a runnable Python project where the cross-cutting mechanisms every later phase relies on already work end to end: shared database, LLM client, persistent graph state, tracing and a professional evaluation harness.
A minimal graph proves them together, so phases 02 onwards only add business logic.
The evaluation harness and the synthetic golden dataset generator built here are reused by every later module.

## Scope
In:
- Python 3.13 project managed with uv (`pyproject.toml`, `uv.lock`), package under `src/`, tests under `tests/`, evaluation assets under `evals/`.
- Shared SQLite database with the tables the orders module needs: customers, products (catalog), stock, orders and order lines.
- Seed command with fictional data: at least 30 products and 10 customers, idempotent on re-run.
- One configuration point for the model: Claude Haiku 4.5 through `langchain-anthropic`, with structured output validated by Pydantic.
- Record and replay of model calls: `live` calls the real model, `record` calls it and stores the answer, `replay` answers only from stored recordings and never touches the network.
- Minimal graph "order line extraction": free text in, structured line (product and quantity) out, matched against the catalog in the database.
- LangGraph checkpoints in SQLite (`langgraph-checkpoint-sqlite`), so a run identified by `thread_id` can be inspected and resumed.
- LangSmith tracing enabled by environment variables; without them everything works and nothing is sent.
- Synthetic golden dataset of about 2,000 cases with labels fixed by construction: a script plans every case and validates it, and the sentences are written by the coding agent in Claude Code sessions under the owner's subscription; audited by the owner on a random sample.
- Evaluation harness: deterministic graders, Wilson confidence intervals, per-category results, an absolute threshold gate and a regression gate against a stored baseline; replay mode inside `npm run check`, live mode on demand.
- LangSmith Datasets and Experiments: the dataset splits are uploaded and every live evaluation of the test split is logged as an experiment.
- `.env.example` documenting every variable, and README sections for setup, demo, tests, dataset generation and evaluation.
- Python tooling: pytest for tests and ruff for lint and format, wired into `npm run check` and the `format` script.

Out:
- Real input channels (web form, email, WhatsApp) and replies to customers: phases 02 to 05.
- Questions to the customer, human approvals and interrupts: phase 04 onwards.
- Retry and failure-recovery policies beyond checkpoint resumption: phase 05.
- LLM-as-judge graders: they come with free-text outputs in later phases.
- Graphical interface and CI on GitHub Actions.

## Evaluation design

### Task under evaluation
An `extract` node sends the customer sentence and the catalog (SKU, name and sale unit of every product) to the model, which returns `sku` (or null when nothing in the catalog fits) and `quantity` in catalog sale units.
A `match` node checks that the SKU exists in the database and loads the catalog item.

### Golden dataset
- Size: at least 2,000 cases, in eight categories of at least 200 cases each.
- Split: a fixed, stratified split of 500 development cases and 1,500 test cases.
  Development cases may be inspected while tuning the prompt; test cases are not inspected case by case during tuning.
  Published figures always come from the test split.
- Labels by construction: a seeded planning script first picks, for every case, the category, the expected SKU (or an out-of-catalog item), the quantity and the trap; the coding agent then writes one sentence per planned case in Claude Code sessions, under the owner's subscription and not through the API.
  The expected answer is therefore decided before the sentence exists.
- Before the owner audit, the agent runs an automated second-pass review of every label in clean-context subagents and fixes or regenerates flagged cases.
- Every case records its id, category, split, sentence, expected SKU, expected quantity, trap and the dataset version.
- Automatic validation rejects and regenerates a case when the expected SKU does not exist, the quantity is not a positive integer, the sentence duplicates another one after normalisation, or a category rule fails (for example, a digit in a "quantity in words" case).
- Owner audit: 150 cases drawn at random are reviewed by the owner in a review file; the observed label error rate and its Wilson interval are reported.
- Contrast set: 60 hand-curated sentences written outside the generator pipeline and reviewed by the owner in the same audit; reported separately, with no threshold, to show the gap between synthetic and hand-written cases.
- The README states that the dataset is synthetic and how it was built.

Categories, with examples from a fictional catalog:

| Category | Input sentence | Expected SKU | Expected quantity |
|---|---|---|---|
| Exact name | "Please send 40 units of M8 hex bolt zinc plated" | BOLT-M8-ZN | 40 |
| Synonym or abbreviation | "I need 12 pairs of nitrile gloves size L" | GLOVE-NIT-L | 12 |
| Quantity in words | "Could you ship two hundred cable ties, 300 mm?" | TIE-300 | 200 |
| Unit expressions | "Two dozen safety glasses, clear lens" | GLASS-CLR | 24 |
| Noise around the order | "Hi Laura, hope all is well. For the Bilbao site we'd need 5 safety helmets, white. Thanks!" | HELMET-WH | 5 |
| Typo | "6 rols of duct tape grey" | TAPE-DUCT-GR | 6 |
| Near-miss product | "30 hex bolts M10, zinc" (catalog has M8 and M10) | BOLT-M10-ZN | 30 |
| Not in catalog | "Do you have 3 hydraulic excavators?" | null | 3 |

### Metrics and graders
- Product accuracy: share of cases where the returned SKU equals the expected one, null included.
- Quantity accuracy: share of cases where the returned quantity equals the expected one exactly.
- Both are graded deterministically by exact match and reported with a 95% Wilson interval, globally and per category, plus the list of failures grouped by category.

### Gates
- Absolute gate: each metric on the test split must reach the threshold.
  The threshold is set from the baseline measured in this phase: 95% if both metrics reach at least 95% on the test split; otherwise execution stops and the owner chooses between a 90% threshold or another model, recorded as a deviation.
- Regression gate: per-case results are compared with the stored baseline using an exact McNemar test; a statistically significant drop (p < 0.05) on either metric fails.
- In replay mode both gates give the same result on every run; they break when code changes how answers are parsed or matched, or when new recordings are worse than the baseline.

### Report
Printed by `npm run eval` (illustrative numbers):

```
order_line_extraction  mode=replay  split=test  cases=1500
metric              value   95% CI          threshold  result
product_accuracy    96.1%   [95.0, 97.0]    95.0%      PASS
quantity_accuracy   98.4%   [97.6, 98.9]    95.0%      PASS
regression vs baseline 2026-10-xx: no significant change (McNemar p=0.62)
per category (product_accuracy):
  near_miss          91.2%  [86.9, 94.2]
  typo               95.6%  [92.0, 97.6]
  ...
contrast set (hand-curated, n=60): product 93.3%, quantity 96.7%
```

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | The seed command creates the SQLite database with the five tables, at least 30 products and 10 customers, and a second run leaves the same row counts | Pytest test on a temporary database plus a manual run with counts printed |
| C3 | The demo command runs the minimal graph on a sample sentence and prints the extracted product, quantity and matched catalog item; it works in `replay` mode with no key and in `live` mode against Claude Haiku 4.5 | Run in replay mode (evidence in `check`) and once in live mode with the owner's key (output saved) |
| C4 | Model output that does not fit the Pydantic schema is rejected before anything is written to the database, with an error that names the failing field | Pytest test with a recorded invalid answer |
| C5 | In `replay` mode a call with no recording fails with an error that names the case and how to record it, and makes no network call | Pytest test with networking blocked |
| C6 | A run interrupted after the extraction node and restarted in a new process with the same `thread_id` continues from the checkpoint without calling the model again | Pytest test that runs the graph in two separate processes and counts model calls |
| C7 | With the LangSmith variables set, a live run appears in the configured LangSmith project with one span per graph node; with them unset, no tracing request is made and no warning is printed | Owner checks the trace in LangSmith (link or screenshot as evidence); pytest test for the unset case |
| C8 | The versioned golden dataset holds at least 2,000 cases in the eight categories, at least 200 per category, with a fixed stratified split of 500 development and 1,500 test cases, and every case passes the automatic validation | Pytest test over the dataset file; generator command output saved as evidence |
| C9 | The planning script fixes the category, expected SKU, quantity and trap of every case before any sentence is written, and re-running it with the same seed produces the same plan; the final dataset is versioned and only changes through the documented generation procedure | Pytest test on the planning script; the plan file and the dataset file are compared in the test |
| C10 | The owner audit of 150 random cases finds at most 2 wrong labels, and the report shows the observed label error rate with its Wilson interval | Review file completed by the owner and the computed rate, saved as evidence |
| C11 | `npm run eval` in replay mode reports both metrics with 95% Wilson intervals on the test split, globally and per category, plus the contrast set, and exits non-zero when the absolute gate or the regression gate fails; it is part of `npm run check` | Run the command; pytest tests that force each gate to fail prove the non-zero exit |
| C12 | The baseline of Claude Haiku 4.5 on the test split is measured, stored with its per-case results, and the threshold is set by the rule in "Gates" | Stored baseline file and the report of the live run, saved as evidence |
| C13 | `npm run eval:live` runs the cases against the real model, prints the same report and logs an experiment in LangSmith against the uploaded dataset splits | One run with the owner's keys; LangSmith dataset and experiment links as evidence |
| C14 | No secret is versioned: `.env` is ignored, `.env.example` lists every variable with no values, and recordings contain no keys or auth headers | `git ls-files` review and a pytest test that scans the recordings |
| C15 | The README explains in English how to set up, seed, run the demo, run tests, generate the dataset and run the evaluation in replay and live mode, and states that the dataset is synthetic | Follow the README step by step in a clean clone |

## Constraints and risks
- New dependencies, approved with this spec: `langgraph`, `langchain-anthropic`, `langgraph-checkpoint-sqlite`, `pydantic`, `langsmith`, `python-dotenv`, `pytest` and `ruff`; Wilson intervals and the McNemar test are computed without extra libraries; any other dependency is asked first.
- Recording and live runs need the owner's Anthropic and LangSmith API keys in `.env`; the agent never reads that file.
- Dataset generation runs in Claude Code under the owner's subscription; the application itself calls the model only through the API key, as the subscription cannot be used as an application credential.
- Expected API cost of the phase: 3 to 8 USD (recording 2,000 Haiku answers, prompt tuning on the development split, a few live runs); prompt caching of the catalog lowers it. This is an estimate, not a limit.
- LangSmith free plan has a monthly trace allowance; to stay within it, only test-split evaluations are logged as experiments, and development-split tuning runs without tracing.
- The owner's audit takes about 45 minutes and blocks C10.
- The sentence writer (the coding agent) and the evaluated model belong to the same family; categories and traps are defined by us, not by the generator, and the contrast set shows the gap.
- Replay recordings are tied to the exact prompt and model; changing either requires re-recording and a new comparison against the baseline.
- If Haiku does not reach 95% on the test split, execution stops for the owner's decision; thresholds are never lowered silently.
- Windows is the reference environment; paths and process handling must work there.

## Assumptions
- The model id `claude-haiku-4-5` is kept in one configuration module.
- Catalog and customers belong to a fictional Spanish industrial supplies company; all data, product names and sentences are in English.
- `npm run check` keeps the existing hooks suite and adds ruff, pytest and the replay evaluation; replaying 2,000 cases takes seconds.
- Recordings of about 2,000 answers (a few megabytes) are versioned in the repository.

## Open decisions
None blocking. The owner provides the two API keys before execution and completes the audit when the dataset is ready.
