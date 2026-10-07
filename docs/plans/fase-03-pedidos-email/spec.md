# Phase 03 - Email orders: specification

Status: approved
Approved by the owner: 2026-10-07
Master plan: `../0_plan_maestro.md`

## Goal
Add the second order channel: a customer email, with the order in the body or in a plain text, PDF or Excel attachment, becomes a validated order stored in the shared database and a reply to the customer.
It is built as a self-contained LangGraph subgraph with two agents, an intake agent that decides whether the email is an order and from which customer, and an extractor agent that turns its text into order lines.
It reuses the phase 02 storing and reply steps, so phase 10 can compose it and phase 05 can route to it.
It is needed now because free text with several lines, unit expressions and attachments is the first input where extraction can fail field by field, and phase 04 builds its questions to the customer on top of it.

## Scope
In:
- Email input as local `.eml` files (RFC 822), parsed with the Python standard library (owner decision 1).
- A subgraph `email_order` with the nodes `intake`, `extract`, `store` and `reply`, checkpointed in SQLite like the phase 02 subgraph.
- Intake agent, deterministic part: the sender address identifies the customer through the `email` column of `customers`, matched ignoring case; the body is taken as plain text (HTML bodies are reduced to text); supported attachments (`.txt`, `.pdf`, `.xlsx`) are turned into text and other attachments are listed as ignored.
- Intake agent, model part: Claude Haiku 4.5 classifies the email as an order or not an order (for example a question, a complaint or a newsletter) and returns a short reason (owner decision 5).
- Attachment text extraction: `.txt` decoded as text, text-based `.pdf` read page by page, `.xlsx` read row by row from the first sheet into a tab-separated text with `pypdf` and `openpyxl` (owner decision 2).
- Extractor agent: one Claude Haiku 4.5 call per order email receives the body and attachment texts together with the cached catalog and returns a list of lines, each with the source text, a SKU or null, and a quantity converted to catalog sale units.
- Deterministic checks of the extractor output: every SKU must exist in the catalog and every quantity must be a positive whole number; output that does not fit the schema stops the run before anything is written.
- Storing and reply through the phase 02 steps, with channel `email` and status `received`; lines with a null SKU are not stored and the reply lists them with the text the customer wrote.
- The reply is returned in the graph state and printed; it is addressed to the sender and quotes the original subject.
- All model calls through the existing client with live, record and replay modes and Pydantic validation; the intake and extractor prompts have their own recordings files.
- A demo command that runs the subgraph on a folder of sample `.eml` files (one body order, one PDF order, one Excel order, one non-order email) and prints, per email, the intake decision, the extracted lines with their source and the stored order and reply.
- A third evaluation, `email_order_extraction`, with field-level metrics and its own synthetic golden dataset built with the phase 01 method, run in replay mode inside `npm run check` next to the two existing evaluations.
- README sections for the email demo, the new dataset, the new evaluation and the catalog-in-prompt limitation.

Out:
- Real mailbox access (IMAP, Gmail API or any mail server) and sending the reply by email (owner decision 1).
- Scanned or image-only PDFs, OCR, images, `.xls`, `.csv`, `.docx` and other attachment types; they are listed as ignored.
  Candidate for a later phase (owner's suggestion, not yet scheduled).
- Questions to the customer, pauses and resumption when a product is ambiguous, a quantity is doubtful or the sender is unknown: phase 04.
- Email threads, forwarded chains and replies to a previous order; one email holds at most one order.
- Duplicate email protection and retry policies: phase 05.
- Stock checks and reservation: phase 06.
- Changes to the catalog, the customers or the existing tables.
- Changes to the phase 01 and phase 02 evaluations, prompts or recordings.

## Evaluation design

### Task under evaluation
Given one `.eml` file, the subgraph must decide whether it is an order and, for an order, produce the set of catalog lines (SKU and quantity in sale units) and the lines it could not match.
Graded fields per email: the intake decision; and per order line: the SKU and the quantity.
Customer identification is a deterministic lookup and is covered by pytest, not by the evaluation.

### Golden dataset
- Size: at least 300 emails in six categories of at least 50 emails each, with at least 800 expected order lines in total; every catalog family appears and no product dominates (owner decision 4).
- Split: a fixed, stratified split by email of about 25% development and 75% test; published figures come from the test split.
- Labels by construction: the seeded planning script fixes, before any text exists, the category, the sender (a seeded customer address), whether the email is an order, its lines with expected SKU (or an out-of-catalog item) and expected quantity in sale units, the unit expression and where each line lives (body or attachment).
- Email bodies and the line texts are written by the coding agent in Claude Code sessions under the owner's subscription, as in phases 01 and 02.
- A rendering script builds the `.eml` files and their attachments from the planned and written content with a few fixed layouts per format (order form PDF, delivery-note-like PDF, spreadsheet with header row, spreadsheet with extra columns), so attachment contents match the labels exactly.
- Automatic validation rejects and regenerates an email when an expected SKU does not exist, a quantity is not a positive whole number, the text duplicates another email after normalisation, a planned line is missing from the rendered text, or a category rule fails.
- An automated second-pass review of every written email in clean-context subagents runs before the owner audit.
- Owner audit: 40 emails drawn at random, reviewed in a review file (owner decision 4).

Categories, with examples:

| Category | Where the order is | Example | Expected |
|---|---|---|---|
| Body, plain list | Body | "Please send: 10 boxes nitrile gloves M, 5 bottles 70% alcohol 500 ml" | 2 lines |
| Body, conversational | Body, inside greetings and context | "Hi Laura, for the Bilbao home we'd need two dozen digital thermometers and some FFP2 masks, 6 boxes" | 2 lines, one unit conversion |
| Text attachment | `.txt` attachment, short body | "Order attached" plus a text list | 1 to 8 lines |
| PDF attachment | `.pdf` order form | Table with product, quantity and unit | 1 to 8 lines |
| Excel attachment | `.xlsx` sheet | Header row plus one row per line, sometimes extra columns | 1 to 8 lines |
| Not an order | Body | A question about delivery times, a complaint, a newsletter | not an order, no lines |

Every order category includes some out-of-catalog items, typos and near-miss variants, at a fixed share set by the planning script.

### Metrics and graders
- Intake accuracy: share of emails whose order or not-order decision is right; Wilson interval.
- Line recall: share of expected catalog lines found with the right SKU and the right quantity.
- Line precision: share of produced catalog lines that equal an expected line in SKU and quantity.
- Field accuracy: after pairing produced and expected lines by SKU, the share of expected lines with the right SKU and, among those, the share with the right quantity; reported to show which field fails.
- Out-of-catalog detection: share of emails whose number of unmatched lines equals the expected number; reported, not gated.
- Email exact match: share of order emails with every line right and nothing extra; reported, not gated.
- All graders are deterministic; every metric is reported with a 95% Wilson interval on the test split, globally, per category and per source (body, `.txt`, `.pdf`, `.xlsx`), plus failures grouped by category.

### Gates
- Absolute gates on intake accuracy, line recall and line precision in the test split: 95% each if the baseline measured in this phase reaches it; otherwise execution stops and the owner decides, recorded as a deviation.
- Regression gate: exact McNemar test per email for intake accuracy and per expected line for line recall against the stored baseline, failing on a significant drop (p < 0.05).
- The phase 01 and phase 02 evaluations keep running unchanged with their own gates.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the three evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | An `.eml` file whose sender address is not a customer's email, or that cannot be parsed, is rejected with a message naming the reason, with no model call and no row written | Pytest tests per case, counting model calls and rows in `orders` and `order_lines` |
| C3 | The intake turns the body (plain or HTML) and each `.txt`, text-based `.pdf` and `.xlsx` attachment into text, and lists any other attachment as ignored | Pytest tests over fixture emails comparing the extracted texts and the ignored list with expected values |
| C4 | An email classified as not an order stores nothing, makes no extractor call and ends with the intake decision and its reason in the state | Pytest test with recorded answers, counting extractor calls and stored rows |
| C5 | The extractor returns, per line, the source text, a SKU or null and a quantity in sale units through the existing client in live, record and replay modes with the catalog prompt cached; a SKU not in the catalog or output that does not fit the schema stops the run before anything is written | Pytest tests with recorded valid and invalid answers that count rows; cached tokens shown in the live demo output |
| C6 | An order email stores one order with channel `email`, status `received` and the sender's customer code, and one order line per matched line, in a single transaction; an email with no matched line stores no order | Pytest tests on a temporary database comparing the stored rows with the recorded extraction |
| C7 | The reply is addressed to the sender, quotes the original subject and shows the same order and line details as the phase 02 reply, including the unmatched lines with the text the customer wrote | Pytest test comparing the reply with an expected text for a fixed email |
| C8 | The demo command runs the subgraph on the sample `.eml` folder and prints, per email, the intake decision, each extracted line with its source (body or attachment name), the stored order number and the reply; it works in replay mode with no key and in live mode against Claude Haiku 4.5 | Run in replay mode (evidence in `check`) and once in live mode with the owner's key (output saved) |
| C9 | The versioned `email_order_extraction` dataset holds at least 300 emails in the six categories, at least 50 per category, with at least 800 expected lines, a fixed stratified split by email, and every email passes the automatic validation; re-running the planning and rendering scripts with the same seed gives the same plan and the same attachment contents | Pytest tests over the dataset, plan and rendered files; generator output saved as evidence |
| C10 | The owner audit of 40 random emails finds at most 1 email with a wrong label, and the report shows the observed error rate with its Wilson interval | Review file completed by the owner and the computed rate, saved as evidence |
| C11 | `npm run eval` in replay mode reports the three evaluations; the new one shows intake accuracy, line recall, line precision, field accuracy, out-of-catalog detection and email exact match with 95% Wilson intervals on the test split, globally, per category and per source, and the command exits non-zero when any absolute or regression gate of any evaluation fails | Run the command; pytest tests that force each new gate to fail prove the non-zero exit |
| C12 | The baseline of Claude Haiku 4.5 on the `email_order_extraction` test split is measured, stored with its per-email and per-line results, and the thresholds are set by the rule in "Gates" | Stored baseline file and the live run report, saved as evidence |
| C13 | With the LangSmith variables set, a live demo run appears in LangSmith with one span per subgraph node, and a live run of the new evaluation is logged as an experiment against its uploaded dataset splits | Trace and experiment links, and the owner's screenshot of the trace, as evidence |
| C14 | No secret is versioned: the new recordings, sample emails and dataset files contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C15 | The README explains in English how to run the email demo in replay and live mode, how the new dataset was built and how to run the new evaluation, and states explicitly the known limitation that the extractor sends the whole catalog in the prompt, so it only works while the catalog fits the model's context window, and that retrieval is not implemented | Follow the new README sections in a clean clone |

## Constraints and risks
- New dependencies: `pypdf` and `openpyxl` at runtime, `fpdf2` (LGPL-3.0) for development only (owner decisions 2 and 3).
- Live runs need the owner's Anthropic and LangSmith keys in `.env`; the agent never reads that file.
- Expected API cost of the phase: 2 to 5 USD (about 300 intake calls and 250 extractor calls per recorded split, prompt tuning on the development split and a few live runs) with prompt caching; an estimate, not a limit.
- The LangSmith free trace quota (5,000 traces) was exhausted right after it started on 2026-10-06; the owner expects it to reset around 2026-11-05, so C13 stays blocked until then (owner decision 6).
- The `.eml` files and attachments are binary or semi-binary; recording keys must come from the extracted text, not from file bytes, so a re-render with the same content keeps the recordings valid.
- Synthetic PDFs and spreadsheets are cleaner than real ones; the per-source report shows the gap only within synthetic data.
- Like phases 01 and 02, the text writer and the evaluated model belong to the same family, so results may be optimistic.
- Line pairing by SKU is ambiguous when an email repeats a SKU; the planning script forbids repeated SKUs within one email.
- The existing client must serve two more prompts and recordings files without changing the phase 01 and phase 02 recordings, or their regression suites break.
- The owner audit takes about 20 minutes and blocks C10.
- Windows is the reference environment.

## Assumptions
- One email holds at most one order, from the customer whose address is the sender.
- The intake classification and the extraction are separate model calls, so a non-order email never pays for extraction and each agent is evaluated on its own field.
- One extractor call per order email with all its texts, rather than one call per line, because the lines are not known before extraction; the catalog still fits in the cached prompt.
- Quantities are converted to sale units by the extractor, as in phase 01 (for example "two dozen" thermometers sold by unit is 24).
- When some lines are not matched, the order is stored with the rest and the reply lists the missing lines, as decided for phase 02.
- Only the first sheet of an `.xlsx` attachment is read.
- Product texts, emails and replies are in English, like the rest of the data.
- Recordings for the new prompts go to their own files under `evals/recordings/`.
- `npm run check` stays fast: replaying the three evaluations takes seconds.

## Known limitations
- The extractor sends the whole catalog in the prompt together with the email text.
  This only works while the catalog fits in the model's context window and stays affordable with prompt caching.
  Retrieval (RAG, embeddings) is not implemented in this project; the owner has built it in another project (Voice to Order).

## Owner decisions (2026-10-07)
1. Email source: local `.eml` files in a folder, parsed with the standard library; real mailbox ingestion can be a later change.
2. PDF and Excel attachments: local text extraction with `pypdf` and `openpyxl` as runtime dependencies; the extracted text goes to the model.
3. Dataset attachments: `fpdf2` (LGPL-3.0) as a development dependency for PDFs and `openpyxl` for spreadsheets, from a seeded script; the rendered files are versioned, so `check` never re-renders.
4. Dataset size and audit: 300 emails, at least 800 lines, owner audit of 40 emails.
5. Intake classification: Claude Haiku 4.5.
6. LangSmith check: C13 is kept.
   The free trace quota (5,000 traces) was exhausted right after it started; the owner expects it to reset about 30 days after 2026-10-06, around 2026-11-05.
   The phase can be ready locally but stays open until C13 is met.
