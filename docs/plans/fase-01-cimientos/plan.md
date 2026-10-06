# Phase 01 - Foundations: plan and results

Status: in progress
Spec: `spec.md` (frozen)
Base: branch `fase-01-cimientos`, commit `ac3c83a` plus the merge of `main` with the English `AGENTS.md`.

## Design notes
- Package `purchase_cycle` under `src/`, run with `uv run`.
- Model calls go through one client in `purchase_cycle/llm.py` that holds the three modes; the graph never builds the Anthropic client itself.
- The model answers through one forced tool call whose arguments are validated by Pydantic, in every mode, so live and replay share the same validation path.
- Recordings are keyed by a hash of model id, system prompt, tool schema and sentence; any change to the prompt or the catalog misses the recording and replay fails loudly.
- The cached prefix is the tool definition plus the system prompt with the full catalog; Haiku 4.5 only caches prefixes of at least 4,096 tokens, so the live run checks `cache_read_input_tokens`.
- Tracing is only enabled for live and record runs; replay forces it off so `npm run check` never sends anything, even with `.env` present.
- The evaluation runs every case through the real graph (without checkpointer) against a freshly seeded temporary database.

## Increments
Each increment leaves the product working and covers concrete criteria.
This file is the durable state: a new session resumes from here and from Git.

- [x] 1. uv project, ruff, pytest and `npm run check` wiring; `.env.example` (C1, C14) - evidence: `npm run check:python` green; `tests/test_secrets.py`.
- [x] 2. SQLite schema and idempotent seed with 300+ products in about 30 families and 50+ customers (C2) - evidence: `tests/test_seed.py`; two seed runs print 303 products, 50 customers, 303 stock rows (`.evidence/fase-01/seed.txt`).
- [x] 3. Model client: schema, cached catalog prompt, live, record and replay modes (C4, C5) - evidence: `tests/test_llm.py`; live calls read 8,402 cached tokens per call (raw API usage).
- [x] 4. Extraction graph with SQLite checkpoints, demo command and tracing switch (C3, C6, C7) - evidence: `tests/test_graph.py`; replay demo in `.evidence/fase-01/demo-replay.txt`; live demo extracted GLV-NIT-M x 40. LangSmith trace pending (EU endpoint).
- [x] 5. Seeded dataset planner and automatic validation (C8, C9) - evidence: `tests/test_dataset.py`.
- [x] 6. Sentences written in batches, dataset build and second-pass label review (C8) - evidence: 4 writer batches of 500 passed `dataset check`; build rejected 1 duplicate (OLX-1996), rewritten; blind relabelling of all 2,000 cases by 4 clean-context agents agreed on every case (`second_pass_review.jsonl`), and their transcripts show no access to labelled files.
- [x] 7. Contrast set, owner audit file and audit report (C10) - evidence: contrast set of 60 written by hand; the owner audited all 210 rows of `evals/audit/audit-v1.0.csv` (150 sample, 60 contrast) and marked every label correct; `uv run purchase-cycle audit report` gives 0 wrong labels in both sets (`.evidence/fase-01/audit-report.txt`).
- [x] 8. Evaluation harness: graders, Wilson, McNemar, gates, report, replay inside `check` (C11) - evidence: `tests/test_stats.py`, `tests/test_eval.py` (each gate forced to fail exits non-zero); dev split recorded: product 99.6%, quantity 99.8% (`.evidence/fase-01/eval-dev-record.txt`); replay report in `.evidence/fase-01/eval-replay.txt`.
- [x] 9. Prompt tuning on dev, recordings, Haiku baseline on test and threshold rule (C12) - evidence: test split recorded and stored as baseline in `evals/baselines/order_line_extraction.json`; product 99.5%, quantity 99.5% on 1,500 cases, so the threshold is 95% (`.evidence/fase-01/eval-test-baseline.txt`).
- [x] 10. LangSmith datasets and experiments, `eval:live` (C13, C7) - evidence: splits uploaded (`.evidence/fase-01/eval-upload.txt`); experiments `order-line-extraction-claude-haiku-4-5-bedb57ca` (baseline) and `order-line-extraction-claude-haiku-4-5-c58f4350` (`npm run eval:live`, `.evidence/fase-01/eval-live.txt`); demo trace URL in `.evidence/fase-01/demo-live-langsmith.txt`.
- [x] 11. README, secret scan and fresh-clone run (C1, C14, C15) - evidence: `tests/test_secrets.py`; clean clone at `201eecf` with no `.env` and no keys: `uv sync` and `npm run check` exit 0, README offline path followed (`.evidence/fase-01/clean-clone-check.txt`).

## Deviations
| ID | Summary | Affects criteria | Status |
|---|---|---|---|

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | clean-context subagent | `main..085cb68` | correctness, tests, security, documentation | 16: 12 fixed, 4 deferred | closed |
| 2 | clean-context subagent | `085cb68..5e35f30` | correctness, tests, security, documentation | 5 minor: 4 fixed, 1 accepted as a limitation | closed |

Severities follow the sdd-review scale: blocking, important, minor; security findings map Critical and High to blocking, Medium to important and Low to minor.

Round 1 findings:

| # | Severity | Finding | Decision | Where |
|---|---|---|---|---|
| 1 | blocking | README claimed the owner audit was already done | fixed | `README.md` |
| 2 | blocking | The regression gate always failed with `--split dev` | fixed: skipped when the split differs from the baseline split | `evaluation/harness.py`, `tests/test_eval.py` |
| 3 | important | Empty `PURCHASE_CYCLE_DB` or `PURCHASE_CYCLE_CHECKPOINTS` broke sqlite | fixed: falls back to the default path | `config.py`, `tests/test_config.py` |
| 4 | blocking | `LANGSMITH_TRACING=false` had no effect because of `lru_cache`, and the contrast set was traced in live mode | fixed: `tracing_context` and cache clear | `config.py`, `evaluation/harness.py`, `tests/test_eval.py` |
| 5 | blocking | C13 marked met despite the 429 answers | fixed: met with limitation | this plan, Results |
| 6 | minor | `demo --resume` without `--thread-id` ended in a traceback | fixed: argument error and missing checkpoint message | `cli.py` |
| 7 | blocking | `--set-baseline` accepted live mode and overwrote the baseline when the threshold failed | fixed: record mode only, baseline kept on failure | `evaluation/harness.py`, `tests/test_eval.py` |
| 8 | important | `evaluate` swallowed target exceptions behind a misleading message | fixed: errors reported apart from missing cases | `evaluation/harness.py`, `tests/test_eval.py` |
| 9 | important | `eval-upload` did not compare the remote content | fixed: compares case ids | `evaluation/harness.py`, `tests/test_eval.py` |
| 10 | minor | Placeholder description in `pyproject.toml` | fixed | `pyproject.toml` |
| 11 | minor | `.claude/relevo/` was not ignored | fixed | `.gitignore` |
| 12 | important | No tests of `report_audit` or of dataset and sentence batch coherence | fixed | `tests/test_audit.py`, `tests/test_dataset.py` |
| 13 | minor | Synthetic phone numbers use a realistic Spanish format | deferred (SEC-001) | `docs/security.md` |
| 14 | minor | The owner's personal email appears in `pyproject.toml` authors | deferred (SEC-002) | `docs/security.md` |
| 15 | minor | Recordings are never pruned | deferred | `evals/recordings/` |
| 16 | minor | Empty "no write" assertion in `tests/test_llm.py` until phase 02 adds writes | deferred to phase 02 | `tests/test_llm.py` |

Round 2 findings (0 blocking, 0 important):

| # | Severity | Finding | Decision | Where |
|---|---|---|---|---|
| 1 | minor | The round 1 table used High, Medium and Low instead of the review scale | fixed | this plan |
| 2 | minor | `docs/security.md` did not use the required table | fixed | `docs/security.md` |
| 3 | minor | `save_baseline` kept a dead null-threshold branch after the caller started checking the threshold | fixed | `evaluation/harness.py` |
| 4 | minor | `test_resume_needs_a_known_thread` leaked the tracing variables written by `configure_tracing` | fixed: variables restored by `monkeypatch` and LangSmith cache cleared | `tests/test_graph.py` |
| 5 | minor | `eval-upload` compares only case ids, so a changed sentence without a `DATASET_VERSION` bump leaves stale remote content | accepted as a documented limitation | `evaluation/harness.py` |

Not confirmed at runtime: that `tracing_context(enabled=False)` silences LangChain tracers inside graph nodes; it was verified by reading code only.

## Results
Per criterion: command or path run, observed result and pointer to the evidence.
Pending items, limitations and what could not be checked, stated plainly.

| ID | Status | Evidence |
|---|---|---|
| C1 | met | Clean clone at `201eecf` with Anthropic and LangSmith variables stripped and no `.env`: `uv sync` and `npm run check` exit 0 (`.evidence/fase-01/clean-clone-check.txt`, step 3). |
| C2 | met | `tests/test_seed.py`; two `uv run purchase-cycle seed` runs print 50 customers, 303 products, 303 stock rows, 0 orders, 0 order lines (`.evidence/fase-01/seed.txt`). |
| C3 | met | Replay demo extracts GLV-NIT-M x 40 and prints the catalog item (`.evidence/fase-01/demo-replay.txt`); live demo against Claude Haiku 4.5 gives the same result with 8,402 cached tokens (`.evidence/fase-01/demo-live-langsmith.txt`). |
| C4 | met | `tests/test_llm.py::test_invalid_answer_is_rejected_before_any_write`; 50 pytest tests pass (`.evidence/fase-01/pytest.txt`). |
| C5 | met | `tests/test_llm.py::test_replay_without_recording_fails_offline` with networking blocked; the clean-clone demo shows the error naming the sentence and the `--mode record` command. |
| C6 | met | `tests/test_graph.py::test_resume_in_new_process_skips_the_model`. |
| C7 | pending owner | Unset case: `tests/test_graph.py::test_no_tracing_without_langsmith_variables`. Set case: the live demo trace was verified through the LangSmith API and its URL is recorded in `.evidence/fase-01/demo-live-langsmith.txt`; the owner's own link or screenshot is still missing. |
| C8 | met | `tests/test_dataset.py` (size, eight categories, per-product coverage, 500/1,500 split, validation); blind second-pass review of the 2,000 labels found 0 discrepancies (`second_pass_review.jsonl`). |
| C9 | met | `tests/test_dataset.py::test_plan_is_reproducible_from_the_seed` and `test_dataset_labels_come_from_the_plan`. |
| C10 | met | The owner reviewed all 150 sample cases and the 60 contrast sentences in `evals/audit/audit-v1.0.csv` and found 0 wrong labels: sample error rate 0.0%, 95% Wilson interval [0.0%, 2.5%]; contrast 0.0% [0.0%, 6.0%] (`.evidence/fase-01/audit-report.txt`). |
| C11 | met | `npm run eval` reports both metrics with Wilson intervals globally, per category and for the contrast set, exit 0 (`.evidence/fase-01/eval-replay.txt`); `tests/test_eval.py` forces each gate to fail and checks the non-zero exit and the wiring into `check`. |
| C12 | met | Baseline in `evals/baselines/order_line_extraction.json`: product 99.5% [99.0, 99.7], quantity 99.5% [99.0, 99.7] on 1,500 test cases, both at least 95%, so the threshold is 95%; contrast set product 98.3%, quantity 100.0%; dev split 99.6% and 99.8% (`.evidence/fase-01/eval-test-baseline.txt`, `eval-dev-record.txt`). |
| C13 | met with limitation | Splits `order-line-extraction-v1.0-dev` (500) and `-test` (1,500) uploaded (`.evidence/fase-01/eval-upload.txt`); the test baseline experiment `order-line-extraction-claude-haiku-4-5-bedb57ca` is complete in LangSmith. `npm run eval:live` measured product 99.5%, quantity 99.5%, no significant regression vs that baseline (McNemar p=1.00 on both) and logged experiment `order-line-extraction-claude-haiku-4-5-c58f4350` (`.evidence/fase-01/eval-live.txt`). Limitation: during that run LangSmith answered 429 "Monthly unique traces usage limit exceeded" 199 times, so the live experiment is incomplete in LangSmith; the local report covers all 1,500 cases. |
| C14 | met | `.env` is ignored by `.gitignore` and not tracked; `tests/test_secrets.py` checks `.env.example` and scans tracked files and recordings for keys. |
| C15 | met | README sections for setup, seed, demo, tests, dataset and evaluation; it states the dataset is synthetic. The offline path was followed in the clean clone (`.evidence/fase-01/clean-clone-check.txt`, step 4). Live steps were run in the working copy, not the clean clone, and dataset generation was not re-run. Without a key, `demo --mode live` exits 1 with a raw traceback instead of a short message. |

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.
- The synthetic dataset is easy for Haiku, probably because the same model family wrote the sentences; the contrast set (98.3% product) does not show a large gap yet.
- The remaining failures cluster on sale-unit conversions (dozens, boxes against rolls) and on out-of-catalog items mapped to a near product.
- LangSmith EU accounts need `LANGSMITH_ENDPOINT`; without it the API answers 403.
- LangChain `usage_metadata` hid cache writes, so the raw `response_metadata` usage is read instead.
