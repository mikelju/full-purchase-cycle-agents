# Phase 01 - Foundations: specification

Status: draft
Approved by the owner: pending
Master plan: `../0_plan_maestro.md`

## Goal
Leave a runnable Python project where the cross-cutting mechanisms every later phase relies on already work end to end: shared database, LLM client, persistent graph state, tracing and evaluation.
A minimal graph proves them together, so phases 02 onwards only add business logic.
It is needed now because every module depends on these pieces and they are cheaper to get right once, on a small graph.

## Scope
In:
- Python 3.13 project managed with uv (`pyproject.toml`, `uv.lock`), package under `src/`, tests under `tests/`, evaluation cases under `evals/`.
- Shared SQLite database with the tables the orders module needs: customers, products (catalog), stock, orders and order lines.
- Seed command with fictional data: at least 30 products and 10 customers, idempotent on re-run.
- One configuration point for the model: Claude Haiku 4.5 through `langchain-anthropic`, with structured output validated by Pydantic.
- Record and replay of model calls: `live` calls the real model, `record` calls it and stores the answer, `replay` answers only from stored recordings and never touches the network.
- Minimal graph "order line extraction": free text in, structured line (product and quantity) out, matched against the catalog in the database.
- LangGraph checkpoints in SQLite (`langgraph-checkpoint-sqlite`), so a run identified by `thread_id` can be inspected and resumed.
- LangSmith tracing enabled by environment variables; without them everything works and nothing is sent.
- Evaluation harness: cases in a versioned file, metrics, thresholds and a readable report; runs in replay mode inside `npm run check` and in live mode on demand.
- `.env.example` documenting every variable, and README sections for setup, demo, tests and evaluation.
- Python tooling: pytest for tests and ruff for lint and format, wired into `npm run check` and the `format` script.

Out:
- Real input channels (web form, email, WhatsApp) and replies to customers: phases 02 to 05.
- Questions to the customer, human approvals and interrupts: phase 04 onwards.
- Retry and failure-recovery policies beyond checkpoint resumption: phase 05.
- Graphical interface, CI on GitHub Actions and LangSmith hosted datasets or experiments.

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
| C8 | `npm run eval` runs at least 15 cases in replay mode, covering the six categories listed in "Evaluation design", prints per-metric results against thresholds and exits non-zero if any metric is below its threshold; it is part of `npm run check` | Run the command; a test with a deliberately wrong expected value proves the non-zero exit |
| C9 | Evaluation metrics and thresholds: product match accuracy at least 90% and exact quantity accuracy at least 90%, measured on the recorded answers of the real model | Report printed by `npm run eval`, saved as evidence |
| C10 | `npm run eval:live` runs the same cases against the real model and prints the same report | One run with the owner's key; output saved as evidence |
| C11 | No secret is versioned: `.env` is ignored, `.env.example` lists every variable with no values, and recordings contain no keys or auth headers | `git ls-files` review and a pytest test that scans the recordings |
| C12 | The README explains in English how to set up, seed, run the demo, run tests and run the evaluation in replay and live mode | Follow the README step by step in a clean clone |

## Evaluation design
What the minimal graph does: an `extract` node sends the customer sentence and the catalog (SKU and name of every product) to the model, which returns a structured answer with `sku` (or null when nothing in the catalog fits) and `quantity`; a `match` node checks that the SKU exists in the database and loads the catalog item.

Each case in `evals/order_line_extraction.jsonl` holds an id, a category, the input sentence and the expected result.
Example cases, with fictional catalog items:

| Category | Input sentence | Expected SKU | Expected quantity |
|---|---|---|---|
| Exact name | "Please send 40 units of M8 hex bolt zinc plated" | BOLT-M8-ZN | 40 |
| Synonym or abbreviation | "I need 12 pairs of nitrile gloves size L" | GLOVE-NIT-L | 12 |
| Quantity in words | "Could you ship two hundred cable ties, 300 mm?" | TIE-300 | 200 |
| Noise around the order | "Hi Laura, hope all is well. For the Bilbao site we'd need 5 safety helmets, white. Thanks!" | HELMET-WH | 5 |
| Typo | "6 rols of duct tape grey" | TAPE-DUCT-GR | 6 |
| Not in catalog | "Do you have 3 hydraulic excavators?" | null | 3 |

Metrics, computed over all cases:
- Product accuracy: share of cases where the returned SKU equals the expected one, null included; threshold 90%.
- Quantity accuracy: share of cases where the returned quantity equals the expected one exactly; threshold 90%.
With 15 cases, 90% means at most one miss per metric.

Report printed by `npm run eval` (illustrative numbers):

```
order_line_extraction  mode=replay  cases=15
metric               value   threshold  result
product_accuracy     93.3%   90.0%      PASS
quantity_accuracy   100.0%   90.0%      PASS
failures:
  case-05 typo: expected TAPE-DUCT-GR, got null
```

The process exits with code 1 if any metric fails, so `npm run check` fails too.
In replay mode the answers are the recorded real answers, so the result is the same on every run and costs nothing; it breaks when code changes how answers are parsed or matched, and a prompt change without re-recording fails with the missing-recording error of C5.
`npm run eval:live` measures the model again on the same cases; `record` mode refreshes the recordings after a deliberate prompt or model change.
Later phases grow this dataset and add their own ones.

## Constraints and risks
- New dependencies, approved with this spec: `langgraph`, `langchain-anthropic`, `langgraph-checkpoint-sqlite`, `pydantic`, `langsmith`, `python-dotenv`, `pytest` and `ruff`; any other one is asked first.
- Recording answers and C3, C7 and C10 need the owner's Anthropic and LangSmith API keys in `.env`; the agent never reads that file. Expected cost of recording and live runs: well under one euro.
- Replay recordings are tied to the exact prompt and model; changing either requires re-recording, and the evaluation then measures the new answers.
- Thresholds in C9 are set before seeing real results; if the real model falls below them, it is a deviation for the owner, not a silent change.
- Windows is the reference environment; paths and process handling must work there.

## Assumptions
- The model id used is the current Anthropic id for Claude Haiku 4.5, kept in one configuration constant.
- Catalog and customers belong to a fictional Spanish industrial supplies company; all data, product names and evaluation sentences are in English.
- `npm run check` keeps the existing hooks suite and adds ruff, pytest and the replay evaluation.

## Open decisions
None blocking. The owner provides the two API keys before execution.
