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
- [ ] 7. Contrast set, owner audit file and audit report (C10) - evidence: contrast set of 60 written by hand; `evals/audit/audit-v1.0.csv` created with 150 sample and 60 contrast rows; waiting for owner audit (verdict column still empty).
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

## Results
Per criterion: command or path run, observed result and pointer to the evidence.
Pending items, limitations and what could not be checked, stated plainly.

| ID | Status | Evidence |
|---|---|---|
| C1 | met | Clean clone at `201eecf` with Anthropic and LangSmith variables stripped and no `.env`: `uv sync` and `npm run check` exit 0 (`.evidence/fase-01/clean-clone-check.txt`, step 3). |
| C2 | met | `tests/test_seed.py`; two `uv run purchase-cycle seed` runs print 50 customers, 303 products, 303 stock rows, 0 orders, 0 order lines (`.evidence/fase-01/seed.txt`). |
| C3 | met | Replay demo extracts GLV-NIT-M x 40 and prints the catalog item (`.evidence/fase-01/demo-replay.txt`); live demo against Claude Haiku 4.5 gives the same result with 8,402 cached tokens (`.evidence/fase-01/demo-live-langsmith.txt`). |
| C4 | met | `tests/test_llm.py::test_invalid_answer_is_rejected_before_any_write`; 35 pytest tests pass (`.evidence/fase-01/pytest.txt`). |
| C5 | met | `tests/test_llm.py::test_replay_without_recording_fails_offline` with networking blocked; the clean-clone demo shows the error naming the sentence and the `--mode record` command. |
| C6 | met | `tests/test_graph.py::test_resume_in_new_process_skips_the_model`. |
| C7 | pending owner | Unset case: `tests/test_graph.py::test_no_tracing_without_langsmith_variables`. Set case: the live demo trace was verified through the LangSmith API and its URL is recorded in `.evidence/fase-01/demo-live-langsmith.txt`; the owner's own link or screenshot is still missing. |
| C8 | met | `tests/test_dataset.py` (size, eight categories, per-product coverage, 500/1,500 split, validation); blind second-pass review of the 2,000 labels found 0 discrepancies (`second_pass_review.jsonl`). |
| C9 | met | `tests/test_dataset.py::test_plan_is_reproducible_from_the_seed` and `test_dataset_labels_come_from_the_plan`. |
| C10 | pending owner | `evals/audit/audit-v1.0.csv` holds 150 random cases and the 60 contrast sentences; the verdict column is empty, so no label error rate exists yet. |
| C11 | met | `npm run eval` reports both metrics with Wilson intervals globally, per category and for the contrast set, exit 0 (`.evidence/fase-01/eval-replay.txt`); `tests/test_eval.py` forces each gate to fail and checks the non-zero exit and the wiring into `check`. |
| C12 | met | Baseline in `evals/baselines/order_line_extraction.json`: product 99.5% [99.0, 99.7], quantity 99.5% [99.0, 99.7] on 1,500 test cases, both at least 95%, so the threshold is 95%; contrast set product 98.3%, quantity 100.0%; dev split 99.6% and 99.8% (`.evidence/fase-01/eval-test-baseline.txt`, `eval-dev-record.txt`). |
| C13 | met | Splits `order-line-extraction-v1.0-dev` (500) and `-test` (1,500) uploaded (`.evidence/fase-01/eval-upload.txt`); `npm run eval:live` logged experiment `order-line-extraction-claude-haiku-4-5-c58f4350`, product 99.5%, quantity 99.5%, no significant regression vs the baseline experiment `order-line-extraction-claude-haiku-4-5-bedb57ca` (McNemar p=1.00 on both) (`.evidence/fase-01/eval-live.txt`). Limitation: during that run LangSmith returned 429 "Monthly unique traces usage limit exceeded", so some traces of the experiment were not ingested. |
| C14 | met | `.env` is ignored by `.gitignore` and not tracked; `tests/test_secrets.py` checks `.env.example` and scans tracked files and recordings for keys. |
| C15 | met | README sections for setup, seed, demo, tests, dataset and evaluation; it states the dataset is synthetic. The offline path was followed in the clean clone (`.evidence/fase-01/clean-clone-check.txt`, step 4). Live steps were run in the working copy, not the clean clone, and dataset generation was not re-run. Without a key, `demo --mode live` exits 1 with a raw traceback instead of a short message. |

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.
- The synthetic dataset is easy for Haiku, probably because the same model family wrote the sentences; the contrast set (98.3% product) does not show a large gap yet.
- The remaining failures cluster on sale-unit conversions (dozens, boxes against rolls) and on out-of-catalog items mapped to a near product.
- LangSmith EU accounts need `LANGSMITH_ENDPOINT`; without it the API answers 403.
- LangChain `usage_metadata` hid cache writes, so the raw `response_metadata` usage is read instead.
