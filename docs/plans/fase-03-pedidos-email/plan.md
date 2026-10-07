# Phase 03 - Email orders: plan and results

Status: in execution; batches A, B, C and D done (owner audit of increment 9 done in `5720389`), batch E in progress
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
- [x] 1. Dependencies `pypdf` and `openpyxl`; email parsing, attachment text extraction and the ignored list (C3) - check: fixture emails built in tests (plain body, HTML body, `.txt`, text `.pdf`, `.xlsx` with two sheets, an image and a `.docx`) compare the extracted texts and the ignored list with expected values; `npm run check:python` green.
  Evidence (2026-10-07): `uv add pypdf openpyxl` (pypdf 6.19.0, openpyxl 3.1.5); `uv run pytest -q tests/test_email_intake.py` 7 passed; `npm run check:python` 112 passed, ruff clean.
- [x] 2. Customer lookup by sender ignoring case, rejection of unparseable, unknown-sender and over-long emails (C2) - check: pytest per case counting model calls (0) and rows in `orders` and `order_lines` (0), and asserting the message names the reason.
  Evidence (2026-10-07): `uv run pytest -q tests/test_email_intake.py` 17 passed (lookup ignoring case, unique lowercased customer emails, unparseable, unreadable file, broken attachment, unknown sender, over-long); graph level in `tests/test_email_order.py::test_rejected_email_makes_no_model_call_and_writes_nothing` (unknown sender, unparseable, over-long) asserts 0 intake and 0 extractor calls, 0 rows in `orders` and `order_lines`, and the reason in `errors`.
- [x] 3. Model client tasks `EMAIL_INTAKE` and `EMAIL_EXTRACTION` with their schemas, tools and recordings files; phase 01 and phase 02 replay unchanged (C5) - check: `tests/test_llm.py` cases for both tasks in replay with hand-written recordings in a temporary file, including a schema-invalid answer; `uv run purchase-cycle eval --mode replay --split test` still passes both existing suites with their recordings untouched (`git diff --stat evals/recordings` empty).
  Evidence (2026-10-07): `uv run pytest -q tests/test_llm.py` 22 passed (12 new email task cases, schema-invalid answers included); `uv run purchase-cycle eval --mode replay --split test` exit 0 with both suites; `git diff --stat evals/recordings` empty.
- [x] 4. Shared store and reply functions extracted from `web_form.py`, then the `email_order` subgraph with `intake`, `extract`, `store`, `reply` and SQLite checkpoints (C4, C5, C6, C7) - check: `tests/test_web_form.py` passes unchanged; new `tests/test_email_order.py` with recorded answers in temporary files covers: not-an-order email stores nothing and makes no extractor call, with decision and reason in the state (C4); foreign SKU, non-positive quantity, unknown source and schema-invalid answer stop the run with 0 rows (C5); one order with channel `email`, status `received`, the sender's customer code and one line per matched line, all-unmatched email stores no order, single transaction (C6); exact reply text for a fixed email, addressed to the sender, quoting the subject and listing unmatched lines with the customer's text (C7).
  Evidence (2026-10-07): store and reply moved to `db.insert_order` and `db.order_line_details`, `build_reply` takes the reference; `git diff tests/test_web_form.py` empty and its 31 tests pass; `uv run pytest -q tests/test_email_order.py` 14 passed; `npm run check` exit 0 (147 passed, both evaluations in replay pass).

### Batch B - Dataset planning, validation and rendering
- [x] 5. Seeded planner of `email_order_extraction`: categories, senders, order or not, lines with expected SKU or out-of-catalog item, expected quantity in sale units, unit expression, location (body or attachment format and layout), out-of-catalog, typo and near-miss shares, stratified 25/75 split by email (C9) - check: `uv run purchase-cycle email-dataset plan` writes `plan.jsonl`; tests assert at least 300 emails, at least 50 per category, at least 800 expected lines, every catalog family present, no product above a fixed share, no repeated SKU per email, exact stratified split, and the same plan for the same seed.
  Evidence (2026-10-07): `uv run purchase-cycle email-dataset plan` wrote 312 emails (52 per category, 13 dev and 39 test each) with 923 expected lines, seed 20261008; out-of-catalog, typo and near-miss lines are 10% each per order category; every SKU used at most 3 times (0.36% of 831 catalog lines); `uv run pytest -q tests/test_email_dataset.py` covers sizes, families, product share, no repeated SKU, trap shares, split and same plan for the same seed.
- [x] 6. Automatic validation and deterministic renderer: `fpdf2` as a dev dependency; `.eml` files with the four attachment layouts (order form PDF, delivery-note-like PDF, spreadsheet with header row, spreadsheet with extra columns) built from the plan and the written texts (C9) - check: tests with placeholder texts in a temporary folder prove that validation rejects a missing SKU, a non-positive quantity, a duplicate text after normalisation, a planned line missing from the rendered text and a category rule failure; two renders with the same seed give identical bytes and identical extracted text through the batch A parser.
  Evidence (2026-10-07): `uv add --dev fpdf2` (fpdf2 2.8.9); `email-dataset check` and `build` commands added; `uv run pytest -q tests/test_email_dataset.py` 28 passed (rejections of missing SKU, non-positive and fractional quantity, duplicate after normalisation, line missing from body and from the rendered PDF, category rules, quantity expression rules; identical bytes and parsed text over two renders of every layout); a one-off run of `build_dataset` over the whole plan with placeholder texts rendered all 312 emails with 0 rejections in 4.4 s; `npm run check` exit 0 (175 passed).

### Batch C - Written content, build, second pass and audit file
- [x] 7. Agent-written email bodies and line texts for every planned email, then `email-dataset check` and `email-dataset build` (C9) - check: writer agents (at most four at a time, each owning the text files of its categories) write in `texts/`; every batch passes `email-dataset check`, failing items are rewritten; `build` renders all `.eml` files and writes `dataset.jsonl`; dataset tests over the versioned files pass; generator output saved in `.evidence/fase-03/dataset-build.txt`.
  Evidence (2026-10-07): six writer files in `texts/` (52 emails each), each `uv run purchase-cycle email-dataset check <file>` 52 checked, 0 rejected; `uv run purchase-cycle email-dataset build` exit 0, output in `.evidence/fase-03/dataset-build.txt`: 312 `.eml` files in `emails/` and `dataset.jsonl` with 312 emails and 923 expected lines, 52 per category, 13 dev and 39 test per category (78 dev and 234 test emails, 273 dev and 650 test lines).
  The first build showed that openpyxl stamps `dcterms:modified` with the save time, so `.xlsx` bytes changed between runs; `_fixed_zip` now resets it to the creation date.
  New test `test_versioned_dataset_is_the_build_of_the_versioned_texts` rebuilds from the versioned texts and compares `dataset.jsonl`, every `.eml` byte for byte and the split; `uv run pytest -q tests/test_email_dataset.py` 29 passed.
- [x] 8. Second-pass review of every written email by clean-context subagents that see only the catalog and the rendered text, compared with the plan (C9) - check: `second_pass_review.jsonl` versioned, disagreements fixed or justified, counts recorded here.
  Evidence (2026-10-07): four clean-context annotators read the catalog and `model_text(parse_email(...))` of 78 emails each, never the labels; `uv run purchase-cycle email-dataset review <four annotation files>` pairs each labelled line with the read line of the same SKU, else the most similar text, and writes `second_pass_review.jsonl` (one record per email with the reading, its disagreements, the decision and the justification; earlier decisions are kept on a re-run).
  Counts: 312 emails, 310 agree and 2 disagree (EML-0031 SKU of one line; EML-0118 order or not and its line); 923 labelled lines, 921 agree on SKU and quantity; 2 fixed by text, 0 justified as annotator wrong.
  EML-0031: "alcohol prep pads" did not say box of 100 or 200, the line now says "boxes of 100"; EML-0118: subject and body asked for a quote, they now place an order; labels unchanged, `email-dataset check` 0 rejected and `email-dataset build` changed only those two `.eml` files and their dataset rows.
  13 agreeing emails carry the annotators' uncertainty notes (unit readings such as "75 underpads" as packs), kept in the file; the readings match the labels, so no change.
  New test `test_versioned_second_pass_review_covers_every_email_and_decides_every_disagreement` recomputes the disagreements and requires a decision for each.
- [x] 9. Audit sample of 40 random emails and audit report command with Wilson interval (C10) - check: `email-audit create` writes `evals/audit/email_order_extraction-audit-v1.0.csv`; tests for create and report on a temporary file.
  Evidence (2026-10-07): the owner reviewed the 40 sampled emails and marked all 40 `ok`, no comments; `uv run purchase-cycle email-audit report` gives n=40, 0 wrong labels, error rate 0.0%, 95% Wilson CI [0.0%, 8.8%], exit 0; output in `.evidence/fase-03/audit-report.txt`.
  Tests `test_audit_create_samples_forty_seeded_emails_and_never_overwrites`, `test_audit_report_passes_with_at_most_one_wrong_label` and `test_audit_report_refuses_rows_without_a_verdict` cover create and report on temporary files.
  Owner action: the owner reviews the 40 emails (about 20 minutes, Lavish page as in phase 02); the executor stops this increment after creating the file and leaves it pending until the verdicts are in, then runs `email-audit report` and saves `.evidence/fase-03/audit-report.txt`.
  Pass: at most 1 wrong label; more than 1 stops the phase for an owner decision.
  State (2026-10-07): audit file created, waiting for owner.
  `uv run purchase-cycle email-audit create` wrote 40 emails (seed 40, sorted by id) to `evals/audit/email_order_extraction-audit-v1.0.csv`, semicolon-separated with a BOM; columns `id`, `category`, `file`, `email_text` (the text the model sees), `is_order`, `expected_lines` (one per line: text, SKU and product or NOT IN CATALOG, quantity and sale unit), `verdict` (`ok` or `wrong`) and `comment`.
  Tests on temporary files: create (40 seeded rows, same bytes on a second run, never overwrites) and report (0 and 1 wrong pass, 2 fail, a missing verdict exits 2, Wilson interval printed); `uv run pytest -q tests/test_email_dataset.py` 38 passed.

### Batch D - Evaluation, recordings and baseline
- [x] 10. Evaluation `email_order_extraction`: deterministic graders, intake accuracy, line recall, line precision, field accuracy, out-of-catalog detection, email exact match, Wilson intervals globally, per category and per source, failures by category, absolute and McNemar regression gates, third suite in `npm run eval` and `eval-upload` (C11) - check: `tests/test_email_eval.py` forces each new gate (intake accuracy, line recall, line precision, both regression tests) to fail and proves a non-zero exit; the two existing suites still pass with their gates.
  Evidence: `src/purchase_cycle/evaluation/email_eval.py` registered as the third entry of `SUITES`, so `eval --suite email_order_extraction` and `eval-upload --suite email_order_extraction` work.
  `tests/test_email_eval.py` (11 tests) runs a 16-email test subset through the subgraph with stub recorded answers and proves exit 1 for the intake accuracy, line recall and line precision gates and both McNemar gates, exit 2 for missing recordings and exit 3 for a baseline under 95%.
  `npm run check` exit 0 with 196 tests; output in `.evidence/fase-03/check-increment-10.txt`.
  Until the recordings and baseline of increments 11 and 12 exist, `npm run eval` names the two existing suites with `--suite`; increment 12 restores the plain command so it replays the three suites.
- [x] 11. Recordings on the dev split and prompt tuning on dev only (C12) - needs the owner's key: `uv run purchase-cycle eval --suite email_order_extraction --mode record --split dev` with tracing off; output in `.evidence/fase-03/eval-dev.txt`.
  Evidence: three record runs on the dev split (78 emails, 245 expected catalog lines) with Haiku 4.5 and tracing off; two tuning rounds, only on `EMAIL_INTAKE_INSTRUCTIONS` and `EMAIL_EXTRACTION_INSTRUCTIONS` in `llm.py`.
  Round 0 (initial prompts): intake accuracy 97.4% [91.1, 99.3], line recall 94.7% [91.1, 96.9], line precision 95.5% [92.1, 97.5]; failures were out-of-catalog orders taken as non-orders, dropped out-of-catalog lines, "dozen" not multiplied, and quantities already in sale units divided by the pack size.
  Round 1 added the intake rule for out-of-catalog products and quantity rules; round 2 (final) made the table unit column and "N x" rules explicit and the intake rule for side questions.
  Final dev: intake accuracy 100.0% [95.3, 100.0] (n=78), line recall 98.8% [96.5, 99.6] (n=245), line precision 98.8% [96.5, 99.6] (n=245), email exact match 95.4%.
  Recordings `evals/recordings/email_intake.jsonl` (78) and `evals/recordings/email_order_extraction.jsonl` (65) hold only the final-prompt answers; no key strings found; phase 01 and 02 recordings untouched.
  Output in `.evidence/fase-03/eval-dev.txt`; `npm run check` exit 0 with 196 tests in `.evidence/fase-03/check-increment-11.txt`.
- [x] 12. Haiku 4.5 baseline on the test split, stored with per-email and per-line results, thresholds by the spec rule (C12) - needs the owner's key: `--mode record --split test --set-baseline`; baseline in `evals/baselines/email_order_extraction.json`; output in `.evidence/fase-03/eval-test-baseline.txt`; then `npm run check` replays the three suites.
  If intake accuracy, line recall or line precision is under 95%, the executor stops, writes deviation `03.1` with the measured figures and leaves the threshold to the owner.
  Evidence: one record run on the test split (234 emails, 586 expected catalog lines) with the final prompts of increment 11 and tracing off, exit 0.
  Test baseline: intake accuracy 98.7% [96.3, 99.6] (n=234), line recall 96.9% [95.2, 98.0] (n=586), line precision 99.0% [97.7, 99.5] (n=574); field SKU 97.3%, field quantity 99.6%, out-of-catalog detection 96.4%, email exact match 94.4%; 11 failing emails listed in the output.
  All three gated metrics reach 95%, so the threshold is 95% by the spec rule and no deviation is needed.
  `evals/baselines/email_order_extraction.json` stores the metrics, the threshold rule, 234 per-email and 586 per-line results; no key strings in it or in the recordings (312 intake and 257 extraction answers for dev and test).
  Output in `.evidence/fase-03/eval-test-baseline.txt`.
  `npm run eval` is the plain command again and replays the three suites with every gate passing in about 15 s (`.evidence/fase-03/eval-replay-increment-12.txt`); `npm run check` exit 0 with 196 tests (`.evidence/fase-03/check-increment-12.txt`).
  Live spend of increments 11 and 12: 851 model calls, about 2.35 USD at Haiku 4.5 prices from the printed token counts.

### Batch E - Demo, README, secrets and fresh clone
- [x] 13. Sample folder `examples/email_orders/` with one body order, one PDF order, one Excel order and one non-order email, copied from dataset emails so the replay recordings already cover them, and the command `purchase-cycle email-demo` (C8) - check: a pytest runs the command in replay and asserts the printed intake decision, each line with its source, the order number and the reply; replay output in `.evidence/fase-03/demo-replay.txt`.
  Needs the owner's key: one live run with tracing off, output with cached tokens in `.evidence/fase-03/demo-live.txt` (C5, C8).
  Evidence: `examples/email_orders/` holds four test-split emails whose recorded answers are fully correct in the baseline: `EML-0024.eml` (body, one out-of-catalog line), `EML-0001.eml` (PDF delivery note), `EML-0012.eml` (Excel) and `EML-0002.eml` (not an order).
  `purchase-cycle email-demo [folder] --mode --checkpoints` runs each email in its own checkpoint thread and prints the intake decision with its reason, each line with its source text, SKU and source, the stored order number and the reply.
  `test_email_demo_command_prints_intake_lines_order_and_reply` runs it in replay with the network blocked and asserts those outputs; replay output with no key in `.evidence/fase-03/demo-replay.txt` (orders 1 to 3, exit 0).
  `npm run check` exit 0 with 198 tests (`.evidence/fase-03/check-increment-13.txt`).
  Live run pending: it needs the owner's key.
- [x] 14. README sections for the email demo in replay and live mode, how the dataset was built, how to run the new evaluation, and the catalog-in-prompt limitation with no retrieval (C15) - check: follow the sections in the clean clone of increment 16.
  Evidence: `README.md` gains "Run the email demo" (replay and live commands, the four nodes, what is printed), "Email order extraction dataset" (seeded plan, agent-written texts, deterministic build, blind second pass with 2 of 312 fixed, owner audit 40/40), the email evaluation command with the Haiku 4.5 baseline table from `evals/baselines/email_order_extraction.json`, and two limits: `.eml` folder instead of a mailbox, and the whole catalog in the prompt with no retrieval.
  The commands named in the new sections exist (`--help` of `email-dataset`, `email-audit`, `eval`); `uv run purchase-cycle eval --suite email_order_extraction` exit 0 with the baseline figures (`.evidence/fase-03/readme-email-eval-replay.txt`); the walk-through in a clean clone stays with increment 16.
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
