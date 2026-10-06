# Phase 02 - Web form orders: plan and results

Status: ready locally; C13 pending (LangSmith trace quota exhausted), so the phase does not close yet
Spec: `spec.md` (frozen)
Base: branch `fase-02-formulario-web` from `origin/main` at commit `f148c70`; the spec was frozen in `953e618`.

## Design notes
- The model client in `purchase_cycle/llm.py` gains a task description (instructions, tool and answer schema, recordings file).
  The phase 01 extraction task keeps its exact prompt, tool and recording keys, so its recordings and baseline stay valid.
- The matching task has its own prompt, a `record_catalog_match` tool returning `{"sku": str | null}` and its own recordings file `evals/recordings/web_form_matching.jsonl`.
- The subgraph lives in `purchase_cycle/web_form.py` with the nodes `validate`, `match`, `store` and `reply`.
  A rejected submission ends after `validate` with the errors in the state; nothing else runs.
- Deterministic matching compares a key built by lowercasing, dropping accents, turning every non-alphanumeric character into a space and collapsing spaces.
  No two catalog names or SKUs share a key, which a test checks.
- The reply is a fixed template filled from the stored rows joined with `products`, so every price comes from the database.
- The sample submission is `examples/web_form_submission.json`; its model-matched lines reuse texts of the evaluation dataset, so replay needs no extra recordings.
- The `web_form_matching` dataset is planned per line: each category is split 25% dev and 75% test first, then lines are grouped into submissions inside each split, so the split is by submission and exactly stratified.
- `npm run eval` runs both evaluations; `purchase-cycle eval --suite` picks one of them.
- Live runs of this phase set every LangSmith and LangChain tracing variable to `false` and `LANGSMITH_API_KEY` to empty, because the trace quota is exhausted.

## Increments
Each increment leaves the product working and covers concrete criteria.
This file is the durable state: a new session resumes from here and from Git.

- [x] 1. Model client serves a second task with its own prompt, schema and recordings file; phase 01 replay unchanged (C4) - evidence: `npm run check:python` green (50 passed); `uv run purchase-cycle eval --mode replay --split test` exits 0 with the phase 01 recordings untouched.
- [x] 2. Web form subgraph: schema, validation, deterministic and model matching, single-transaction store, template reply, checkpoints (C2, C3, C4, C5, C6, C8) - evidence: `tests/test_web_form.py` (rejections with zero model calls and zero rows, deterministic matching, store, single transaction, exact reply, resumption in a second process with 0 calls and 1 order) and `tests/test_llm.py::test_invalid_matching_answer_is_rejected_before_any_write`; 74 pytest tests pass.
- [x] 3. Demo command and sample submission file (C7) - evidence: `purchase-cycle web-form-demo` on `examples/web_form_submission.json` (2 deterministic lines, 4 model lines, 1 not in catalog); replay output in `.evidence/fase-02/demo-replay.txt`; live run against Claude Haiku 4.5 with tracing off in `.evidence/fase-02/demo-live.txt` (cache_read 8326 tokens per call); `tests/test_web_form.py::test_demo_command_prints_matching_order_and_reply` and `::test_demo_command_reports_a_rejected_submission`.
- [x] 4. Seeded planner of the `web_form_matching` dataset, automatic validation and dataset commands (C9) - evidence: `uv run purchase-cycle web-form-dataset plan` writes 624 lines in 225 submissions (104 per category, 26 dev and 78 test each), every product appears once or twice and all 32 families appear; plan and validation tests in `tests/test_web_form_dataset.py` pass.
- [x] 5. Agent-written texts, dataset build and second-pass label review (C9) - evidence: 4 writer agents (one per written category) wrote 104 texts each in `texts/`, every batch passed `web-form-dataset check` (1 near_miss line rewritten after the check); `web-form-dataset build` wrote 624 lines in 225 submissions (104 per category); blind relabelling of the 416 written lines by 4 clean-context agents that only saw the catalog and the texts agreed with the plan on every line (`second_pass_review.jsonl`); `tests/test_web_form_dataset.py` 7 passed.
- [x] 6. Owner audit sample of 60 agent-written lines and audit report command (C10) - evidence: `web-form-audit create` wrote `evals/audit/web_form_matching-audit-v1.0.csv`; the owner answered all 60 rows in the Lavish page `.lavish/auditoria-c10.html` (60 yes, 0 no) and the verdicts were written into the CSV; `web-form-audit report`: n=60, 0 wrong labels, error rate 0.0%, 95% CI [0.0%, 6.0%] (`.evidence/fase-02/audit-report.txt`).
- [x] 7. Evaluation harness for `web_form_matching`: graders, model calls per category, gates, report, both suites inside `check` (C3, C11) - evidence: `src/purchase_cycle/evaluation/web_form_eval.py`, `eval --suite` (default all); `tests/test_web_form_eval.py` forces the absolute, model-call and regression gates to fail; `npm run check` exit 0 with both reports (`.evidence/fase-02/check.txt`).
- [x] 8. Recordings on dev, Haiku baseline on test and threshold rule (C12) - evidence: dev recorded with no prompt change: 100.0% on 156 lines (`.evidence/fase-02/eval-dev-replay.txt`); test recorded and stored as baseline in `evals/baselines/web_form_matching.json`: line product accuracy 99.6% [98.5, 99.9] on 468 lines, 2 failures (WFM-0614, WFM-0427), so the threshold is 95% (`.evidence/fase-02/eval-test-baseline.txt`).
- [x] 9. README sections, secret scan of the new files, adversarial review, fixes and fresh-clone run (C1, C14, C15) - evidence: `tests/test_secrets.py::test_phase_02_data_files_are_tracked_and_scanned` passes; README sections in `7f0c25f`; review round 1 fixes in `0f6619e` and round 2 fixes in `4beefd9`.
  `npm run check` on `4beefd9`: exit 0, 105 passed, both replay evaluations PASS (`.evidence/fase-02/check.txt`).
  Fresh clone run: `.evidence/fase-02/fresh-clone.txt`.
- [ ] 10. LangSmith trace of the demo and experiment of the new evaluation (C13) - pending: the LangSmith monthly trace quota is exhausted (owner decision of 2026-10-06).
  The C7 live run without tracing is already done in increment 3.

## Deviations
| ID | Summary | Affects criteria | Status |
|---|---|---|---|

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | local | `f148c70..7f0c25f` | correctness, security, evidence, scope | Fixed in `0f6619e`: `web-form-demo --resume` with `--thread-id`; clean `Error:` for an unreadable submission file and a missing checkpoint; recordings saved in `finally`; bounds `MAX_PRODUCT_TEXT=200` and `MAX_QUANTITY=2**63-1` (security Medium: model cost per line); `eval-upload --suite` default `all`; shared `ALPHA` and `DEFAULT_THRESHOLD`; new gate when report model calls differ from client calls; a test for each, including two mutants (model-call counter set to 0; foreign SKU not filtered) that now fail. SEC-003 to SEC-006 logged as Low, open in `docs/security.md`. | Fixed; pending items below |
| 2 | local, two independent reviewers | `7f0c25f..0f6619e` | correctness, evidence | Fixed in `4beefd9`: the call gate fired falsely when a model answer was schema-invalid, now invalid-answer calls are counted per submission and attributed, with a report line when > 0; a test forces the new gate (mutation `if False:` fails it) and a second mutation removing invalid-call attribution fails the invalid-answer test; README resume claims narrowed (resume only after a node completed; a run stopped inside `match` re-sends its model lines; C8 covers interruption after `match`); submission file read as utf-8-sig with `UnicodeDecodeError` handled cleanly; `--resume` with a submission path is a parser error; tests for missing, UTF-16 and BOM files, recordings saved on failure and `eval-upload` calling both suites. | Fixed; pending items below |

Maximum of two rounds reached; the remaining items go to the owner as pending.

Pending from round 1 (not fixed):
- Audit and evaluation code duplicated between the phase 01 and web form modules.
- `_suite()` returns `sys.modules[__name__]`.
- Two normalisers with different purposes: `match_key` and the dataset `normalise`.
- `store` runs again if a process dies after the commit but before the checkpoint (close to phase 05).
- No test changes a database price before the reply.
- No test for "evaluated lines differ from baseline".
- No tests for web form `audit create` and `audit report`.
- The silent coercion of a foreign SKU to no match is not logged.

Pending from round 2 (minor):
- If `save_recordings` raises in `finally`, it masks the original error.
- On the error path the count of saved recordings is not printed.
- Resuming a finished or rejected thread reprints the outcome as "resumed".

## Results
Per criterion: command or path run, observed result and pointer to the evidence.
Pending items, limitations and what could not be checked, stated plainly.

| Criterion | Result | Evidence |
|---|---|---|
| C1 | Pass: fresh clone at `4beefd9`, no keys set and no `.env`; `uv sync`, `npm ci` and `npm run check` exit 0, 105 passed | `.evidence/fase-02/fresh-clone.txt` |
| C2 | Pass: rejections with zero model calls and zero rows | `tests/test_web_form.py` (increment 2) |
| C3 | Pass: SKU and name matching with no model call; zero model calls in the exact categories of the report | `tests/test_web_form.py`; `.evidence/fase-02/check.txt` |
| C4 | Pass: model matching in live, record and replay; invalid answer stops before any write; cached tokens in the live demo | `tests/test_llm.py::test_invalid_matching_answer_is_rejected_before_any_write`; `.evidence/fase-02/demo-live.txt` (cache_read 8326 tokens per call) |
| C5 | Pass: one order and its lines in a single transaction | `tests/test_web_form.py` (increment 2) |
| C6 | Pass: reply equals the expected text, prices from the database | `tests/test_web_form.py` (increment 2) |
| C7 | Pass: replay and live demo (live run with tracing off) | `.evidence/fase-02/demo-replay.txt`; `.evidence/fase-02/demo-live.txt`; demo command tests in `tests/test_web_form.py` |
| C8 | Pass: resumption in a second process with 0 model calls and 1 order | `tests/test_web_form.py` (increment 2) |
| C9 | Pass: 624 lines, 225 submissions, 104 per category, stratified split by submission; regeneration gives identical files with 0 rejected batches | `tests/test_web_form_dataset.py`; `.evidence/fase-02/dataset-generation.txt` |
| C10 | Pass: n=60, 0 wrong labels, error rate 0.0%, 95% CI [0.0%, 6.0%] | `.evidence/fase-02/audit-report.txt` |
| C11 | Pass: both evaluations reported with Wilson intervals; forced gate failures exit non-zero; web form test split product_accuracy 99.5% [99.0, 99.7], quantity_accuracy 99.5%, line_product_accuracy 99.6% [98.5, 99.9], threshold 95% | `tests/test_web_form_eval.py`; `.evidence/fase-02/check.txt` |
| C12 | Pass: Haiku baseline 99.6% [98.5, 99.9] on 468 test lines, threshold 95% | `evals/baselines/web_form_matching.json`; `.evidence/fase-02/eval-test-baseline.txt` |
| C13 | PENDING: blocked by the exhausted monthly LangSmith trace quota (owner decision of 2026-10-06); the phase does not close until the trace and the experiment are seen | - |
| C14 | Pass: new recordings and dataset files tracked and scanned | `tests/test_secrets.py::test_phase_02_data_files_are_tracked_and_scanned` |
| C15 | Pass: README sections followed in a clean clone at `4beefd9`; `web-form-demo` stores order 1 and `eval --suite web_form_matching` passes (99.6%), all exit 0 | README sections in `7f0c25f` and `4beefd9`; `.evidence/fase-02/fresh-clone.txt` |

`npm run check` on `4beefd9`: exit 0, 105 passed, both replay evaluations PASS (`.evidence/fase-02/check.txt`).
Limitations: the review items listed as pending above are open for the owner; SEC-003 to SEC-006 stay open as Low in `docs/security.md`.

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.

- Candidate, not applied: a new consistency gate must account for every error path that consumes a model call.
  Quote from review round 2: "A single invalid Haiku answer in live or record mode fails the evaluation for a reason that is not true".
- Candidate, not applied: a resume claim must be tested on a stop the user path can actually produce.
  Quote from review round 2: "the CLI graph has no `interrupt_after`, so a run stops only on an exception inside `match`".
