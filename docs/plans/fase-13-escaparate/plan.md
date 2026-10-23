# Phase 13 - Portfolio showcase: plan and results

Status: pending (spec approved, execution on hold by owner)
Spec: `spec.md` (in review; frozen on approval)
Base: branch `fase-13-escaparate` from `main` at `2895902` (phase 12 merged through PR #30, visual guides through PR #31); the unchanged-behaviour reference of C9 is the code at `2895902`.

## Design notes
Written for the owner decisions of 2026-10-20: option A for D1 to D6, with LLM evaluation as the headline of the README.
- New module `purchase_cycle/showcase.py` with three generators, registered as commands from `cli.main` with one line each:
  - `diagrams`: builds every graph of the spec list with in-memory SQLite, temporary folders and a replay client, and writes `docs/graphs.md` from `get_graph().draw_mermaid()`; no model call.
  - `results-table [--check FILE]`: reads `data/eval_summaries/*.json` and prints the Markdown table between the README markers `<!-- results-table:start -->` and `<!-- results-table:end -->`; `--check` compares and exits 1 on a difference.
  - `compare-baselines OLD_REV`: reads each `evals/baselines/*.json` at `OLD_REV` with `git show` and in the working tree, pairs cases by id and reuses `evaluation/stats.py` for the exact McNemar test; output between `<!-- before-after:start -->` and `<!-- before-after:end -->`.
- `package.json` `check` gains `&& uv run --quiet purchase-cycle results-table --check README.md` after `npm run --silent eval`; nothing else in `check` changes.
- The evaluation loop diagram is hand-written Mermaid in the README (it shows a process, not a compiled graph); the graph diagrams are generated.
- Screenshots are taken with the built-in browser from the local Phoenix (`npm run phoenix`) and the local dashboard; the model-call span comes from a live trace already stored in Phoenix (16 October).
- Cost: none; no `--mode live` or record run in this phase.

## Rules for executors
- Execution starts from the approved frozen spec; changing a criterion needs a deviation approved by the owner.
- Each increment is sized for one Sonnet writer with a context ceiling of about 100k tokens; it starts from this file and Git, marks its increment `[x]` with its evidence and commit.
- Run git from the repository root, one command per call, no `cd` prefix; never push, merge or write to `main` outside the delivery increment.
- Failing tests first where a test is named; `unset ANTHROPIC_API_KEY LANGSMITH_API_KEY` before any offline command; never open `.env`.
- `npm run check` takes 15 to 25 minutes: run it in the background with output to `.evidence/fase-13/`, read only the tail, never two at once.

## Increments
Each increment leaves the product working and covers concrete criteria.
This file is the durable state: a new session resumes from here and from Git.

- [x] 1. Risky assumptions first (C1, C3, C4) - files: `.github/workflows/check.yml`, `tests/test_ci_workflow.py`; check: `draw_mermaid()` on every graph runs with the network blocked (one-off script, output in `.evidence/fase-13/mermaid-offline.txt`); the workflow pushed on the work branch runs the full `npm run check` on `ubuntu-latest`; every Linux failure is listed in this plan with its cause. If `draw_mermaid()` needs the network, stop for an owner decision.
  - Evidence: `draw_mermaid()` ran offline for all 13 graphs with sockets blocked (`.evidence/fase-13/mermaid-offline.txt`, local). `.github/workflows/check.yml` (pinned SHAs, `contents: read`, temporary push trigger on the work branch) ran the full `npm run check` on ubuntu-latest: run 39917315363 success in 6m49s, all suites replay PASS (log tail in `.evidence/fase-13/ci-run1-log-tail.txt`). Linux failures found: none. The push trigger on `fase-13-escaparate` and `fetch-depth: 0` on checkout (needed by increment 6 and the README test, which read `636711e`) are still to be set in increment 3.
- [ ] 2. Linux portability fixes (C3, C9) - files: only those the increment 1 failures name; check: CI green on the branch, local `npm run check` green on Windows, demo outputs equal to the base.
- [x] 3. CI hardening (C1, C2, C10) - files: `.github/workflows/check.yml`, `tests/test_ci_workflow.py`, `tests/test_secrets.py`; check: read-only permissions, actions pinned by SHA, no `secrets.`, replay mode; the workflow test fails on a workflow that breaks each rule. Evidence: `tests/test_ci_workflow.py` (2 tests, passing) checks each rule and fails on a workflow breaking it; the branch push trigger was removed (triggers: pull_request and push to main) and checkout has `fetch-depth: 0` for revision `636711e`; the secret scan of `test_secrets.py` already covers every tracked text file. The green CI run is evidenced on the phase PR (increment 10).
- [x] 4. Graph diagrams (C4) - files: `src/purchase_cycle/showcase.py`, `cli.py` registration line, `docs/graphs.md` (generated), `tests/test_showcase.py`; check: drift test fails after a hand edit of `docs/graphs.md`, passes after regeneration.
  - Evidence: `uv run purchase-cycle diagrams` wrote `docs/graphs.md` (13 Mermaid diagrams, offline, no model call); `uv run pytest -q tests/test_showcase.py` 3 passed (drift test, 13 diagrams, hand-edit detected); ruff clean. Files: `src/purchase_cycle/showcase.py`, `cli.py` (3 registration lines), `docs/graphs.md`, `tests/test_showcase.py`.
- [x] 5. Results table (C7) - files: `showcase.py`, `package.json` `check` script, `tests/test_showcase.py`; check: tests over sample summaries, a missing suite and a changed README value with exit 1.
  - Evidence: `uv run purchase-cycle results-table` prints the 21-suite table; `--check README.md` exits 0 on the current README and 1 on a changed value or missing markers (`tests/test_showcase.py`, 5 passed); `package.json` check ends with `results-table --check README.md`; a provisional marker block "Evaluation results" was added to the current README (the new README of increment 9 carries the final one). Files: `showcase.py`, `package.json`, `README.md`, `tests/test_showcase.py`.
- [x] 6. Before/after comparison (C13) - files: `showcase.py`, `tests/test_showcase.py` with two small baseline fixtures; check: known lost and gained counts and p values; output for `636711e` in `.evidence/fase-13/compare-018.txt` equals the change 018 figures.
  - Evidence: `uv run purchase-cycle compare-baselines 636711e` (saved in `.evidence/fase-13/compare-018.txt`, local) prints claims 0 lost/3 gained p=0.25; detection recall 0/12 p<0.01; documents exact match 0/2 p=0.50, unit price 0/1 p=1.00; email intake 0/1 p=1.00, line recall 2 lost/8 gained p=0.11, equal to the change 018 figures. `tests/test_showcase.py` 7 passed (two fixture baselines with 0 lost/4 gained, pair counts). Note: the README test of increment 9 needs git history that holds 636711e, so the CI checkout must use `fetch-depth: 0`. Files: `showcase.py`, `tests/test_showcase.py`.
- [x] 7. Guide move (C6) - files: `docs/guide.md`, `README.md`; check: comparison script output in `.evidence/fase-13/guide-move.txt` shows every former heading and paragraph present.
  - Evidence: `docs/guide.md` is the README of base 2895902 with only link targets rewritten (docs/ prefix dropped, `../LICENSE`); comparison script output in `.evidence/fase-13/guide-move.txt`: 75 of 75 headings and 293 of 293 paragraph blocks present, 0 missing. `README.md` is an interim stub (link to the guide, results block, license) until increment 9. Files: `docs/guide.md`, `README.md`.
- [x] 8. Screenshots (C8) - files: `docs/assets/dashboard.png`, `docs/assets/trace-*.png`; check: Phoenix spans of the replay `cycle-demo` read back from its API into `.evidence/fase-13/phoenix-spans.json`; each image inspected and noted here (no key, header, email address or user path). Evidence: spans of the Phoenix project read back from its REST API into `.evidence/fase-13/phoenix-spans.json` (502 spans, not versioned; trace d979d5ec... has 70 spans, the model-call trace edb77406... has 4). Images inspected one by one with no key, header, email address or user path: `dashboard.png` (summary page of 21 suites), `trace-orchestrator.png` (replay cycle-demo root span, output counts and the span tree), `trace-module-graph.png` (the inner sales graph under its stage), `trace-model-call.png` (ChatAnthropic span of the stored live run: system and user prompt, tool call, 8,910 tokens). The three trace images are cropped to the detail panel because the full page showed a truncated local path in the traces table.
- [ ] 9. Short README (C5, C14) - files: `README.md`, `tests/test_readme.py`; check: section order, at most 250 lines, every relative link resolves, the Stack section symbols exist in the named files, generated tables equal the command outputs; quickstart followed in a clean clone, output in `.evidence/fase-13/quickstart.txt`.
- [ ] 10. Verification and delivery (C1 to C14) - files: this plan, master plan row 13; check: full local `npm run check` with keys unset, CI green on the PR, unchanged-behaviour comparison (C9), cost 0 USD (C11), adversarial review per `sdd-delivery`, push and PR to `main`.

## Deviations
| ID | Summary | Affects criteria | Status |
|---|---|---|---|

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Results
| ID | Command or path run | Observed result | Evidence |
|---|---|---|---|
| C1 | | | |
| C2 | | | |
| C3 | | | |
| C4 | | | |
| C5 | | | |
| C6 | | | |
| C7 | | | |
| C8 | | | |
| C9 | | | |
| C10 | | | |
| C11 | | | |
| C12 | | | |
| C13 | | | |
| C14 | | | |

## Candidate learnings
Only reusable lessons with a verbatim quote from the session; consolidated when the phase closes.
