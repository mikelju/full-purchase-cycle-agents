# Phase 02 - Web form orders: plan and results

Status: in progress
Spec: `spec.md` (frozen)
Base: branch `fase-02-formulario-web`, commit `953e618`.

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
- [ ] 3. Demo command and sample submission file (C7) - evidence:
- [x] 4. Seeded planner of the `web_form_matching` dataset, automatic validation and dataset commands (C9) - evidence: `uv run purchase-cycle web-form-dataset plan` writes 624 lines in 225 submissions (104 per category, 26 dev and 78 test each), every product appears once or twice and all 32 families appear; plan and validation tests in `tests/test_web_form_dataset.py` pass.
- [x] 5. Agent-written texts, dataset build and second-pass label review (C9) - evidence: 4 writer agents (one per written category) wrote 104 texts each in `texts/`, every batch passed `web-form-dataset check` (1 near_miss line rewritten after the check); `web-form-dataset build` wrote 624 lines in 225 submissions (104 per category); blind relabelling of the 416 written lines by 4 clean-context agents that only saw the catalog and the texts agreed with the plan on every line (`second_pass_review.jsonl`); `tests/test_web_form_dataset.py` 7 passed.
- [ ] 6. Owner audit sample of 60 agent-written lines and audit report command (C10) - evidence:
- [ ] 7. Evaluation harness for `web_form_matching`: graders, model calls per category, gates, report, both suites inside `check` (C3, C11) - evidence:
- [ ] 8. Recordings on dev, Haiku baseline on test and threshold rule (C12) - evidence:
- [ ] 9. README sections, secret scan of the new files and fresh-clone run (C1, C14, C15) - evidence:
- [ ] 10. LangSmith trace of the demo and experiment of the new evaluation (C13, C7 live) - pending: the LangSmith monthly trace quota is exhausted (owner decision of 2026-10-06).

## Deviations
| ID | Summary | Affects criteria | Status |
|---|---|---|---|

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Results
Per criterion: command or path run, observed result and pointer to the evidence.
Pending items, limitations and what could not be checked, stated plainly.

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.
