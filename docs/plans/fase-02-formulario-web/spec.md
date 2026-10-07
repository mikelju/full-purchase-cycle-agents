# Phase 02 - Web form orders: specification

Status: approved
Approved by the owner: 2026-10-06
Master plan: `../0_plan_maestro.md`

## Goal
Leave the first real order channel working end to end: a web form submission becomes a validated order stored in the shared database and a reply to the customer.
It is built as a self-contained LangGraph subgraph, so phase 10 can compose it and phases 03 and 05 can reuse its matching, storing and reply steps for email and WhatsApp.
It is needed now because every later orders phase adds a channel or an exception on top of this path.

## Scope
In:
- A web form submission schema validated by Pydantic: submission id, customer code and 1 to 20 order lines, each with a product text field and a quantity in catalog sale units.
- A subgraph `web_form_order` with four nodes: `validate`, `match`, `store` and `reply`, checkpointed in SQLite like the phase 01 graph.
- Deterministic catalog matching first: a line whose product text is a catalog SKU or a catalog product name (ignoring case, extra spaces and punctuation) is resolved with no model call.
- Model matching for the remaining lines: Claude Haiku 4.5 receives the product text and the cached catalog and returns a SKU or null, through the existing client with live, record and replay modes and Pydantic validation.
- Storing the order in one transaction in the existing `orders` and `order_lines` tables, with channel `web_form` and status `received`.
- A reply to the customer built from a fixed template with the stored data: order number, each line with product name, quantity, sale unit, unit price and line total, the order total and the lines that could not be matched.
- A demo command that runs the subgraph on a sample submission file and prints the matching of each line, the stored order and the reply.
- A second evaluation, `web_form_matching`, with its own synthetic golden dataset built with the phase 01 method, run in replay mode inside `npm run check` next to the phase 01 evaluation.
- The pending phase 01 item: the "nothing is written" assertion in `tests/test_llm.py` now checks the `orders` and `order_lines` tables.
- README sections for the web form demo and the new evaluation.

Out:
- A real web page or web server; the form is simulated as a JSON submission file (see open decision 2).
- Sending the reply by email or any other channel; the reply is returned in the graph state and printed.
- Questions to the customer, pauses and resumption when a product is ambiguous or unknown: phase 04.
- Stock checks, stock reservation and out-of-stock handling: phase 06.
- Unit conversions (dozens, individual items against boxes): the form asks for sale units; conversions belong to free-text channels from phase 03.
- Protection against duplicate submissions and retry policies: phase 05.
- Customer authentication; the customer code in the submission is trusted.
- Changes to the catalog, the customers or the existing tables.

## Evaluation design

### Task under evaluation
The `match` node resolves the product text of every form line to a catalog SKU or to null when the product is not in the catalog.
Deterministic matching runs first and the model only sees the lines it does not resolve.
Quantities are not evaluated here: the form schema guarantees a positive whole number, and pytest covers validation.

### Golden dataset
- Size: at least 600 lines in six categories of at least 100 lines each, grouped into at least 200 submissions of 1 to 5 lines; every catalog family appears and no product dominates.
- Split: a fixed, stratified split by submission of about 25% development and 75% test; published figures come from the test split.
- Labels by construction: the seeded planning script fixes the category, the expected SKU (or an out-of-catalog item) and the trap of every line before any text exists.
- Lines of the exact name and SKU categories are produced by the script itself; the other lines are written by the coding agent in Claude Code sessions under the owner's subscription, as in phase 01.
- Automatic validation rejects and regenerates a line when the expected SKU does not exist, the text duplicates another line after normalisation, or a category rule fails (for example, a typo line that equals the catalog name).
- An automated second-pass review of every written label in clean-context subagents runs before the owner audit.
- Owner audit: 60 lines drawn at random from the agent-written categories, reviewed in a review file (see open decision 4).

Categories, with examples:

| Category | Product text in the form | Expected SKU | Path |
|---|---|---|---|
| Exact name | "Powder-free nitrile gloves, size M" | GLV-NIT-M | deterministic |
| SKU typed | "glv-nit-m" | GLV-NIT-M | deterministic |
| Short or informal name | "nitrile gloves M" | GLV-NIT-M | model |
| Typo | "nitril glovs medium" | GLV-NIT-M | model |
| Near-miss variant | "70% alcohol 250ml" (catalog has 250 ml and 500 ml) | ALC70-250 | model |
| Not in catalog | "hospital bed" | null | model |

### Metrics and graders
- Line product accuracy: share of lines whose resolved SKU equals the expected one, null included; deterministic exact match, 95% Wilson interval, globally and per category, plus failures grouped by category.
- Model calls per category: the exact name and SKU categories must make zero model calls.
- Submission accuracy: share of submissions with every line right; reported, not gated.

### Gates
- Absolute gate on line product accuracy in the test split: 95% if the baseline measured in this phase reaches it; otherwise execution stops and the owner decides, recorded as a deviation.
- Regression gate: exact McNemar test per line against the stored baseline, failing on a significant drop (p < 0.05).
- The phase 01 evaluation keeps running unchanged with its own gates.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs both evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | A submission with an unknown customer code, no lines, more than 20 lines, an empty product text or a quantity that is not a positive whole number is rejected with a message naming the failing field, with no model call and no row written | Pytest tests per case, counting model calls and rows in `orders` and `order_lines` |
| C3 | A line whose product text is a catalog SKU or a catalog name, ignoring case, extra spaces and punctuation, is matched with no model call | Pytest tests; zero model calls in the exact name and SKU categories of the evaluation report |
| C4 | Lines not matched deterministically are resolved by Claude Haiku 4.5 through the existing client in live, record and replay modes, with the catalog prompt cached; model output that does not fit the schema stops the run before anything is written to `orders` or `order_lines` | Pytest test with a recorded invalid answer that counts rows; the pending phase 01 assertion in `tests/test_llm.py` is filled; cached tokens shown in the live demo output |
| C5 | A valid submission stores one order with channel `web_form`, status `received` and the customer code, and one order line per matched line with its SKU and quantity, in a single transaction; unmatched lines are not stored, and a submission with no matched line stores no order | Pytest tests on a temporary database comparing the stored rows with the submission |
| C6 | The reply shows the order number, each stored line with product name, quantity, sale unit, unit price and line total, the order total, and each unmatched line with the text the customer wrote; prices come from the database | Pytest test comparing the reply with an expected text for a fixed submission |
| C7 | The demo command runs the subgraph on a sample submission file and prints, per line, the matched SKU and whether it came from deterministic matching or the model, then the stored order number and the reply; it works in replay mode with no key and in live mode against Claude Haiku 4.5 | Run in replay mode (evidence in `check`) and once in live mode with the owner's key (output saved) |
| C8 | A run interrupted after the `match` node and restarted in a new process with the same `thread_id` stores the order once and makes no new model call | Pytest test running the subgraph in two separate processes, counting model calls and stored orders |
| C9 | The versioned `web_form_matching` dataset holds at least 600 lines in the six categories, at least 100 per category, grouped into at least 200 submissions with a fixed stratified split by submission, and every line passes the automatic validation; re-running the planning script with the same seed gives the same plan | Pytest tests over the dataset and plan files; generator output saved as evidence |
| C10 | The owner audit of 60 random agent-written lines finds at most 1 wrong label, and the report shows the observed error rate with its Wilson interval | Review file completed by the owner and the computed rate, saved as evidence |
| C11 | `npm run eval` in replay mode reports both evaluations with 95% Wilson intervals on the test split, globally and per category, and exits non-zero when any absolute or regression gate of either evaluation fails | Run the command; pytest tests that force each new gate to fail prove the non-zero exit |
| C12 | The baseline of Claude Haiku 4.5 on the `web_form_matching` test split is measured, stored with its per-line results, and the threshold is set by the rule in "Gates" | Stored baseline file and the live run report, saved as evidence |
| C13 | With the LangSmith variables set, a live demo run appears in LangSmith with one span per subgraph node, and a live run of the new evaluation is logged as an experiment against its uploaded dataset splits | Trace and experiment links, and the owner's screenshot of the trace, as evidence |
| C14 | No secret is versioned: the new recordings and dataset files contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C15 | The README explains in English how to run the web form demo in replay and live mode, how the new dataset was built and how to run the new evaluation | Follow the new README sections in a clean clone |

## Constraints and risks
- No new dependencies are expected; the subgraph uses LangGraph, Pydantic, SQLite and the existing client.
- Live runs need the owner's Anthropic and LangSmith keys in `.env`; the agent never reads that file.
- Expected API cost of the phase: 1 to 3 USD (about 400 model-matched lines recorded, prompt tuning on the development split and a few live runs) with prompt caching; an estimate, not a limit.
- The LangSmith monthly trace allowance is exhausted; C13 cannot be checked until it renews (see open decision 5), and replay runs never trace.
- The existing client is written for phase 01 sentence extraction; it must serve a second prompt and recordings file without changing the phase 01 recordings, or the phase 01 regression suite breaks.
- The deterministic categories are easy by design; the honest figure is the per-category accuracy of the model categories, which the report shows apart.
- Like phase 01, the line writer and the evaluated model belong to the same family, so results may be optimistic.
- The owner audit takes about 15 minutes and blocks C10.
- Windows is the reference environment.

## Assumptions
- A web form gives structured input, so customer, line count and quantities need no model; the model is only needed to map free-text product names to the catalog, and only when deterministic matching fails.
- One model call per unresolved line, reusing the phase 01 pattern; the whole catalog still fits in the prompt, so no catalog search step is added.
- The reply is filled from a fixed template rather than written by the model: every figure must be exact, and it costs nothing and is fully testable.
- The quantity field is in catalog sale units and is stored as entered.
- Product texts and replies are in English, like the rest of the data.
- Recordings for the new evaluation go to their own file under `evals/recordings/`.
- `npm run check` stays fast: replaying both evaluations takes seconds.
- Owner decisions of 2026-10-06:
  - The customer types the product name by hand in the form, so the model only acts on names with no exact match.
  - The form is simulated as a file holding the submitted data; no web page or server is built.
  - When some lines are not recognised, the order is stored with the rest and the reply lists the missing lines.
  - The owner reviews a sample of 60 lines of the new evaluation set (C10).
  - The LangSmith check (C13) waits for the monthly trace quota to renew; the phase can be ready locally but does not close until the trace and the experiment are seen.

## Open decisions
None.
