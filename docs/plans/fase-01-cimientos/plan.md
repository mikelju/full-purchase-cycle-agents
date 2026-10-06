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

- [ ] 1. uv project, ruff, pytest and `npm run check` wiring; `.env.example` (C1, C14) - evidence:
- [ ] 2. SQLite schema and idempotent seed with 300+ products in about 30 families and 50+ customers (C2) - evidence:
- [ ] 3. Model client: schema, cached catalog prompt, live, record and replay modes (C4, C5) - evidence:
- [ ] 4. Extraction graph with SQLite checkpoints, demo command and tracing switch (C3, C6, C7) - evidence:
- [ ] 5. Seeded dataset planner and automatic validation (C8, C9) - evidence:
- [ ] 6. Sentences written in batches, dataset build and second-pass label review (C8) - evidence:
- [ ] 7. Contrast set, owner audit file and audit report (C10) - evidence:
- [ ] 8. Evaluation harness: graders, Wilson, McNemar, gates, report, replay inside `check` (C11) - evidence:
- [ ] 9. Prompt tuning on dev, recordings, Haiku baseline on test and threshold rule (C12) - evidence:
- [ ] 10. LangSmith datasets and experiments, `eval:live` (C13, C7) - evidence:
- [ ] 11. README, secret scan and fresh-clone run (C1, C14, C15) - evidence:

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
