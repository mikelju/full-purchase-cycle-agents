# Phase 03 - Email orders: plan and results

Status: planned; execution not started
Spec: `spec.md` (frozen)
Base: branch `fase-03-pedidos-email` from `main` at commit `d335905`; the spec was frozen in `d283f99`.

## Design notes
- New module `purchase_cycle/email_order.py` holds the email parsing, the customer lookup and the subgraph `email_order` with the nodes `intake`, `extract`, `store` and `reply`.
- Parsing uses `email` from the standard library with `policy.default`; an HTML body is reduced to text with `html.parser` from the standard library, no new dependency.
- Attachment text: `.txt` decoded with the declared charset (UTF-8 fallback), `.pdf` with `pypdf` page by page, `.xlsx` with `openpyxl` first sheet only, each row as tab-separated cell values; any other extension goes to the ignored list.
- The intake runs its deterministic part first: unparseable file or unknown sender ends the run with `errors` in the state, no model call and no row (C2).
- Assumption, recorded here and not in the spec: the text sent to the model is capped at a fixed number of characters (about 50,000), and a longer email is rejected before any model call with a message naming the reason, as in the phase 02 bounds.
- Customer lookup compares `lower(email)` in SQL with the lowercased sender address; a test checks that no two seeded customers share an address ignoring case.
- The model client in `purchase_cycle/llm.py` gains two tasks, without touching `EXTRACTION` or `MATCHING`, so the phase 01 and phase 02 recordings and baselines stay valid:
  - `EMAIL_INTAKE`: tool `record_email_intake`, answer `{"is_order": bool, "reason": str}`, recordings `evals/recordings/email_intake.jsonl`.
  - `EMAIL_EXTRACTION`: tool `record_email_order_lines`, answer `{"lines": [{"source": str, "source_text": str, "sku": str | null, "quantity": int}]}`, recordings `evals/recordings/email_order_extraction.jsonl`.
  - Both keep the catalog in the cached system prompt, because `build_system_prompt` always appends it; this keeps the client unchanged and the prompt above the cache minimum.
- The user message of both calls is built only from extracted text: subject, body text and, per supported attachment, its file name and text, in a fixed layout.
  The recording key is computed from that message, never from file bytes, so a re-render with the same content keeps the recordings valid.
- Extractor output checks after Pydantic validation, in the `extract` node: every non-null SKU exists in the catalog, every quantity is a positive whole number and every `source` is `body` or the name of a supported attachment; any failure raises before `store`, so nothing is written (C5).
  Unlike phase 02, a foreign SKU is not coerced to no match; it stops the run, as the spec requires.
- Storing and reply reuse phase 02: the insert of one order and its lines in one transaction and the read of stored rows joined with `products` move from `web_form.py` into shared functions that both subgraphs call; `build_reply` gains the order reference as a parameter so the phase 02 reply text stays byte-identical (its exact-reply test must pass unchanged).
- The email reply is the phase 02 reply body preceded by `To: <sender>` and `Subject: Re: <original subject>`; unmatched lines are listed with the `source_text` the customer wrote.
- Checkpoints in SQLite as in phase 02, one thread per email (default thread id from the file name).
- Dataset folder `evals/datasets/email_order_extraction/` with `plan.jsonl`, `texts/` (agent-written content), `emails/` (rendered `.eml` files with embedded attachments), `dataset.jsonl` (labels and split per email) and `second_pass_review.jsonl`.
- Dataset module `purchase_cycle/evaluation/email_dataset.py` (planning, validation, rendering, commands) and evaluation module `purchase_cycle/evaluation/email_eval.py`, registered in `SUITES` of `harness.py` so `npm run eval` runs three suites and `--suite` picks one.
- Suggested sizes: 52 emails per category (312 in total), 13 dev and 39 test per category, line counts per order email drawn so the total is at least 800; no repeated SKU inside one email.
- Rendering is deterministic: fixed seed, fixed PDF creation date in `fpdf2` and fixed document properties in `openpyxl`, fixed MIME boundaries and dates in the `.eml`, so a re-run gives identical bytes and identical extracted text.
- Live and record runs set every LangSmith and LangChain tracing variable to `false` and `LANGSMITH_API_KEY` to empty in the command environment until the quota resets (C13), as in phase 02.

## Rules for executors
- Each execution batch starts in a fresh session from this file and Git; it implements its increments in order, marks each one `[x]` with its evidence and commits.
- Before closing a batch, `npm run check` passes and the batch commit is made; the next batch only starts from a green check.
- Dependencies are added only where an increment says so: `pypdf` and `openpyxl` at runtime and `fpdf2` in the `dev` group (spec, owner decisions 2 and 3), with `uv add`.
- Live API runs (`--mode live` or `--mode record`) use the owner's Anthropic key, which the CLI loads from `.env` through `load_dotenv`.
  The agent never opens, prints or copies `.env`; it only runs the command.
  If the command fails for a missing key, the agent stops that increment, marks it pending with the error text and continues with the work that does not need the key.
- Owner actions stop the increment that needs them: the audit of 40 emails (C10), a baseline under the 95% rule (deviation), and C13 until the LangSmith quota resets around 2026-11-05.
- Evidence files go to `.evidence/fase-03/`.

## Increments
Each increment leaves the product working and covers concrete criteria.
This file is the durable state: a new session resumes from here and from Git.

### Batch A - Email intake, model tasks and subgraph
- [ ] 1. Dependencies `pypdf` and `openpyxl`; email parsing, attachment text extraction and the ignored list (C3) - check: fixture emails built in tests (plain body, HTML body, `.txt`, text `.pdf`, `.xlsx` with two sheets, an image and a `.docx`) compare the extracted texts and the ignored list with expected values; `npm run check:python` green.
- [ ] 2. Customer lookup by sender ignoring case, rejection of unparseable, unknown-sender and over-long emails (C2) - check: pytest per case counting model calls (0) and rows in `orders` and `order_lines` (0), and asserting the message names the reason.
- [ ] 3. Model client tasks `EMAIL_INTAKE` and `EMAIL_EXTRACTION` with their schemas, tools and recordings files; phase 01 and phase 02 replay unchanged (C5) - check: `tests/test_llm.py` cases for both tasks in replay with hand-written recordings in a temporary file, including a schema-invalid answer; `uv run purchase-cycle eval --mode replay --split test` still passes both existing suites with their recordings untouched (`git diff --stat evals/recordings` empty).
- [ ] 4. Shared store and reply functions extracted from `web_form.py`, then the `email_order` subgraph with `intake`, `extract`, `store`, `reply` and SQLite checkpoints (C4, C5, C6, C7) - check: `tests/test_web_form.py` passes unchanged; new `tests/test_email_order.py` with recorded answers in temporary files covers: not-an-order email stores nothing and makes no extractor call, with decision and reason in the state (C4); foreign SKU, non-positive quantity, unknown source and schema-invalid answer stop the run with 0 rows (C5); one order with channel `email`, status `received`, the sender's customer code and one line per matched line, all-unmatched email stores no order, single transaction (C6); exact reply text for a fixed email, addressed to the sender, quoting the subject and listing unmatched lines with the customer's text (C7).

### Batch B - Dataset planning, validation and rendering
- [ ] 5. Seeded planner of `email_order_extraction`: categories, senders, order or not, lines with expected SKU or out-of-catalog item, expected quantity in sale units, unit expression, location (body or attachment format and layout), out-of-catalog, typo and near-miss shares, stratified 25/75 split by email (C9) - check: `uv run purchase-cycle email-dataset plan` writes `plan.jsonl`; tests assert at least 300 emails, at least 50 per category, at least 800 expected lines, every catalog family present, no product above a fixed share, no repeated SKU per email, exact stratified split, and the same plan for the same seed.
- [ ] 6. Automatic validation and deterministic renderer: `fpdf2` as a dev dependency; `.eml` files with the four attachment layouts (order form PDF, delivery-note-like PDF, spreadsheet with header row, spreadsheet with extra columns) built from the plan and the written texts (C9) - check: tests with placeholder texts in a temporary folder prove that validation rejects a missing SKU, a non-positive quantity, a duplicate text after normalisation, a planned line missing from the rendered text and a category rule failure; two renders with the same seed give identical bytes and identical extracted text through the batch A parser.

### Batch C - Written content, build, second pass and audit file
- [ ] 7. Agent-written email bodies and line texts for every planned email, then `email-dataset check` and `email-dataset build` (C9) - check: writer agents (at most four at a time, each owning the text files of its categories) write in `texts/`; every batch passes `email-dataset check`, failing items are rewritten; `build` renders all `.eml` files and writes `dataset.jsonl`; dataset tests over the versioned files pass; generator output saved in `.evidence/fase-03/dataset-build.txt`.
- [ ] 8. Second-pass review of every written email by clean-context subagents that see only the catalog and the rendered text, compared with the plan (C9) - check: `second_pass_review.jsonl` versioned, disagreements fixed or justified, counts recorded here.
- [ ] 9. Audit sample of 40 random emails and audit report command with Wilson interval (C10) - check: `email-audit create` writes `evals/audit/email_order_extraction-audit-v1.0.csv`; tests for create and report on a temporary file.
  Owner action: the owner reviews the 40 emails (about 20 minutes, Lavish page as in phase 02); the executor stops this increment after creating the file and leaves it pending until the verdicts are in, then runs `email-audit report` and saves `.evidence/fase-03/audit-report.txt`.
  Pass: at most 1 wrong label; more than 1 stops the phase for an owner decision.

### Batch D - Evaluation, recordings and baseline
- [ ] 10. Evaluation `email_order_extraction`: deterministic graders, intake accuracy, line recall, line precision, field accuracy, out-of-catalog detection, email exact match, Wilson intervals globally, per category and per source, failures by category, absolute and McNemar regression gates, third suite in `npm run eval` and `eval-upload` (C11) - check: `tests/test_email_eval.py` forces each new gate (intake accuracy, line recall, line precision, both regression tests) to fail and proves a non-zero exit; the two existing suites still pass with their gates.
- [ ] 11. Recordings on the dev split and prompt tuning on dev only (C12) - needs the owner's key: `uv run purchase-cycle eval --suite email_order_extraction --mode record --split dev` with tracing off; output in `.evidence/fase-03/eval-dev.txt`.
- [ ] 12. Haiku 4.5 baseline on the test split, stored with per-email and per-line results, thresholds by the spec rule (C12) - needs the owner's key: `--mode record --split test --set-baseline`; baseline in `evals/baselines/email_order_extraction.json`; output in `.evidence/fase-03/eval-test-baseline.txt`; then `npm run check` replays the three suites.
  If intake accuracy, line recall or line precision is under 95%, the executor stops, writes deviation `03.1` with the measured figures and leaves the threshold to the owner.

### Batch E - Demo, README, secrets and fresh clone
- [ ] 13. Sample folder `examples/email_orders/` with one body order, one PDF order, one Excel order and one non-order email, copied from dataset emails so the replay recordings already cover them, and the command `purchase-cycle email-demo` (C8) - check: a pytest runs the command in replay and asserts the printed intake decision, each line with its source, the order number and the reply; replay output in `.evidence/fase-03/demo-replay.txt`.
  Needs the owner's key: one live run with tracing off, output with cached tokens in `.evidence/fase-03/demo-live.txt` (C5, C8).
- [ ] 14. README sections for the email demo in replay and live mode, how the dataset was built, how to run the new evaluation, and the catalog-in-prompt limitation with no retrieval (C15) - check: follow the sections in the clean clone of increment 16.
- [ ] 15. Secret scan covering the new recordings, sample emails, dataset and baseline files, including decoded attachment text (C14) - check: `tests/test_secrets.py` gains a phase 03 test that also asserts the files are tracked.
- [ ] 16. `npm run check` and fresh-clone run with the Anthropic and LangSmith variables unset (C1) - check: `uv sync` and `npm run check` in a clean clone, three evaluations in replay, output in `.evidence/fase-03/fresh-clone.txt`; record the replay time of the three suites.
- [ ] 17. LangSmith trace of a live demo run with one span per node and an experiment of the new evaluation against its uploaded splits (C13) - blocked: the trace quota is exhausted until about 2026-11-05 (owner decision 6); executors skip it and leave it pending.
  When unblocked: `eval-upload --suite email_order_extraction`, live demo and live evaluation with tracing on, links and the owner's screenshot as evidence.

After batch E, validation follows `sdd-delivery` with the adversarial review of `sdd-review` (at most two rounds), recorded below.
The phase can be ready locally with C10 or C13 pending, but it does not close until both are met.

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
