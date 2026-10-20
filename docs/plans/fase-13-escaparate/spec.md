# Phase 13 - Portfolio showcase: specification

Status: draft (spec in review)
Approved by the owner: pending
Master plan: `../0_plan_maestro.md`

## Goal
A visitor who opens the GitHub repository understands in two minutes what the system does, how it is built and how well it works, and can check it without running anything.
Today the README is a 1,242-line operating manual whose top lists eleven per-phase status lines (some stale, phase 11 still "awaiting review"); there is no architecture diagram, no visible evaluation result, no trace, and no continuous integration, so every "tests pass" claim rests on the owner's machine.
The phase delivers a CV-oriented README with diagrams generated from the code, an evaluation results table generated from the evaluation summaries, sample trace screenshots, and a GitHub Actions workflow that runs `npm run check` offline with no secret and no cost on every pull request.
It is needed now because phase 12 closed the functional scope; this is the last phase of the master plan and the one the owner shows to employers.
It shows the master plan capabilities "observability" and "structured evaluation" to an outside reader, and adds independent, public evidence that the checks pass.
The options below that are marked "recommended" are the ones the scope and criteria assume; each open decision says which parts change with another option.

## What exists today
- Twelve LangGraph graphs built in `src/purchase_cycle/` (`build_*_graph` in `web_form.py`, `email_order.py`, `whatsapp_order.py`, `clarification.py`, `graph.py`, `quotes.py`, `quote_decision.py`, `reconciliation.py`, `claims.py`, `receipts.py`, `sales.py`, `customer_responses.py`) and the orchestrator `build_cycle_graph` in `cycle.py`; `langgraph.json` registers the three channel graphs for LangGraph Studio.
- `npm run check` runs `npm ls`, the hook tests, `ruff check`, `ruff format --check`, `pytest -q` and the twenty-one evaluations in replay; it takes about 15 to 25 minutes on the owner's Windows machine and has never run in full on Linux (phase 05 ran one byte test on WSL Ubuntu).
- `npm run eval` writes one JSON summary per suite in `data/eval_summaries/` (git-ignored); `uv run purchase-cycle dashboard` builds `data/dashboard.html` from them.
- Phoenix local tracing (change 013); the README notes that a replay run has no model spans.
- English flow animations (`docs/assets/*-flow.gif` and `.mp4`) shown in the README, Spanish copies (`*-es.gif`, `.mp4`) used by the LinkedIn series, and two Spanish HTML guides added by PR #31 (`docs/assets/cycle-demo-visual-es.html`, `docs/assets/trace-dashboard-guide-es.html`); GitHub shows HTML files as source, not as pages.
- No `.github/` folder; the repository is public, so GitHub-hosted standard runners cost nothing.
- The LangSmith trace quota is exhausted until about 2026-11-05; the pending LangSmith traces of phases 02 to 10 belong to a separate catch-up change (master plan decision of 2026-10-15).

## Scope
In:
- README (D1, D6): a new English README for an outside reader, in this order: title and one-paragraph pitch; CI badge; the purchase cycle animation; architecture with the orchestrator diagram; a capability table that links each capability to the code and test that shows it; evaluation results; sample traces; how it is built (SDD Lite, guardrails, CI); quickstart (clone, `uv sync`, `cycle-demo` in replay, `npm run check`); limits; license.
  The current manual moves unchanged in content to `docs/guide.md`, which the README links; per-phase status lines leave the README (the master plan keeps them).
- Diagrams (D3): a command `uv run purchase-cycle diagrams` writes `docs/graphs.md` with one Mermaid diagram per graph, produced by `get_graph().draw_mermaid()` on the compiled graphs with no model call; the README embeds the orchestrator diagram and links the rest.
- Evaluation results (D5): a command `uv run purchase-cycle results-table` prints from `data/eval_summaries/` one Markdown table with, per suite, the case or scenario count, its main metric with the 95% Wilson interval and the gate verdict; with `--check README.md` it exits non-zero when the table between the README markers differs; it runs as the last step of `npm run check`.
  A screenshot of the dashboard, `docs/assets/dashboard.png`, from a replay run.
- Sample traces (D4): screenshots of the local Phoenix trace of one replay `cycle-demo` run (the orchestrator tree with its five stages, and one module graph under its stage) in `docs/assets/`, with captions saying what they show and that they come from a local Phoenix.
- Continuous integration (D2): `.github/workflows/check.yml` on `pull_request` and on `push` to `main`, on `ubuntu-latest`, with Python 3.13 through uv, Node 22, `npm ci`, `uv sync --locked` and `npm run check`; read-only permissions, no secret, actions pinned to a full commit SHA.
- Linux portability fixes that `npm run check` needs on the CI runner, with no behaviour change.
- Master plan row 13 status.

Out:
- The LangSmith traces of phases 02 to 12 and public LangSmith links; they come with the catch-up change after about 2026-11-05.
- Any live model run or API cost.
- Changes to module behaviour, prompts, recordings, datasets, baselines and gates.
- New flow animations, LinkedIn posts, a hosted or interactive demo, GitHub Pages (unless D5 B), a Spanish README (unless D6 B).
- A Windows CI runner (unless D2 C), release automation, deployment, dependency bots.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | `.github/workflows/check.yml` runs `npm run check` on every pull request and every push to `main` on `ubuntu-latest` with Python 3.13 and Node 22, and the run is green on the phase PR | `gh pr checks` on the phase PR and the run link, saved as evidence |
| C2 | CI uses no secret and can incur no API cost: the workflow references no `secrets.` value and sets no Anthropic or LangSmith variable, the evaluations run in replay, `permissions` is `contents: read`, and every third-party action is pinned to a full commit SHA | A pytest over the workflow text inside `check`; the CI log shows the replay mode of every suite |
| C3 | `npm run check` passes both on the CI Linux runner and locally on Windows with no API key and no `.env` file, after any portability fix | CI run of C1 and a local run with the keys unset, output saved as evidence |
| C4 | `uv run purchase-cycle diagrams` writes `docs/graphs.md` with one Mermaid diagram per graph of "What exists today" (thirteen with the orchestrator), each generated from the compiled graph; a pytest regenerates the file in memory and fails if it differs from the committed one | Pytest inside `check`; the rendered `docs/graphs.md` on GitHub, screenshot saved as evidence |
| C5 | The README has the sections of "Scope" in that order, in English, with the CI badge, the orchestrator Mermaid diagram, the evaluation table, the dashboard screenshot and the trace screenshots; the quickstart takes at most four commands from a fresh clone to a replayed `cycle-demo`; every relative link and image path in `README.md`, `docs/guide.md` and `docs/graphs.md` resolves to a versioned file | A pytest that checks the section order and resolves every relative link; the quickstart followed in a clean clone, output saved as evidence |
| C6 | `docs/guide.md` holds every section of the former README; no heading or paragraph is lost in the move | A comparison script over the former README at the base commit and the new pair of files, output saved as evidence |
| C7 | `uv run purchase-cycle results-table` prints the table of "Scope" for the twenty-one suites from the summaries, a missing suite shows as missing, and `--check README.md` exits non-zero when the README table differs; `npm run check` ends with that check, so a stale table fails CI | Pytest tests over sample summaries and a README with a changed value; the CI run of C1 |
| C8 | `docs/assets/dashboard.png` and at least two trace screenshots from a local Phoenix replay `cycle-demo` run are versioned and referenced from the README with captions; they show no key, token, email address or local user path | File inspection and a note per image in `plan.md`; the Phoenix spans of that run read back from its API and saved as evidence, as change 013 did |
| C9 | Behaviour of phases 01 to 12 is unchanged: their tests pass, the twenty-one evaluations replay with every gate PASS, `git diff --stat` on recordings, datasets and baselines is empty, and every `*-demo` command (`orders-demo`, `quotes-demo`, `reconcile-demo`, `receipt-demo`, `sales-demo`, `cycle-demo`) prints the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C10 | No secret is versioned: the workflow, `docs/graphs.md` and the new README and guide contain no keys or auth headers | `tests/test_secrets.py` covers the new text files |
| C11 | The phase makes no live model call and spends 0 USD | No `--mode live` or `record` run in the evidence; `plan.md` states the cost |
| C12 | Master plan row 13 shows the phase status | Compare row 13 with the phase status |

## Constraints and risks
- New CI-only dependencies: the GitHub actions `actions/checkout`, `actions/setup-node` and `astral-sh/setup-uv`, pinned by SHA; no new Python or Node package.
- GitHub-hosted standard runners are free for public repositories, so CI adds no cost; a private repository would consume the free minutes quota.
- `npm run check` takes 15 to 25 minutes locally, so each PR waits about that long for its CI result (D2).
- The suite has never run in full on Linux: path separators, line endings, file encodings, fonts or byte-level outputs (the phase 05 XLSX case) may fail there; each fix must leave Windows behaviour unchanged, and C9 guards it.
- `npm ls` in `check` needs the Node dev dependencies, so CI runs `npm ci` first; the hook tests run with Node and must not depend on Windows paths.
- `results-table --check` reads the summaries the same `check` run just wrote, so it must run after `npm run eval`; a README edited by hand to a wrong value fails CI by design.
- Mermaid output of LangGraph may change with a LangGraph upgrade; then the C4 test fails and the file is regenerated, never hand-edited.
- A replay run has no model spans, so the trace screenshots show the graph and stage structure, not prompts and token counts; model spans come with the LangSmith catch-up change (D4).
- Phoenix tracing is never set up inside `npm run check`, pytest or CI (change 013 guard).

## Assumptions
- "Graph diagrams" means diagrams generated from the code, so they cannot drift from it.
- "Sample traces" can be local Phoenix screenshots now; public LangSmith links are added later by the catch-up change, which may edit the README traces section.
- "Evaluation results" means the replayed figures of the twenty-one suites that `npm run check` already produces, not a new live measurement.
- The reader is a technical recruiter or hiring engineer who reads English; the LinkedIn series in Spanish links to the repository.
- `get_graph().draw_mermaid()` needs no network and no extra package; this is verified in the first increment, and if false the phase stops for a decision.

## Known limitations
- The traces shown are local screenshots of a replay run, with no model spans, until the LangSmith catch-up change.
- The evaluation table shows the latest replay, not live model measurements; the live figures stay in the baselines and phase plans.
- CI runs on Linux only; Windows stays the owner's local platform.

## Open decisions
Each decision lists 2 to 4 options with trade-offs; the recommended option is the one the scope and criteria assume.
- D1 - README shape:
  - A (recommended): a short CV-oriented README (about 150 to 250 lines) and the current manual moved to `docs/guide.md`; first screen readable in two minutes, nothing lost; one more file to link.
  - B: a showcase section on top of the current 1,242-line README; no move, but the reader still meets a long manual and the stale status lines must be cleaned anyway.
- D2 - CI scope:
  - A (recommended): full `npm run check` on every PR and every push to `main`, Linux only; one workflow, CI proves exactly what the method calls "check"; about 15 to 25 minutes per run, free on a public repository.
  - B: ruff and pytest on every PR, full `check` with evaluations only on push to `main` and weekly; faster PR feedback, but a PR can be green with a failing evaluation.
  - C: A on a Linux and Windows matrix; covers the owner's platform too, but doubles the time and Windows runners are slower; adds little since Windows is checked locally.
- D3 - Diagrams:
  - A (recommended): Mermaid text generated from the compiled graphs into `docs/graphs.md`, rendered natively by GitHub, with a drift test; no dependency, always in sync.
  - B: PNG images through `draw_mermaid_png()`; looks the same everywhere, but needs the mermaid.ink web service or a browser dependency, and binary files cannot be compared in a test.
  - C: one hand-drawn architecture diagram only; nicest look, but it can drift from the code and shows no graph detail.
- D4 - Sample traces:
  - A (recommended): Phoenix screenshots of a replay `cycle-demo` now, at no cost; the LangSmith catch-up change adds public trace links with model spans after about 2026-11-05.
  - B: Phoenix screenshots of one live `cycle-demo` run that shows model spans with prompts and tokens; more telling, but a new live spend (about 0.15 USD, ceiling 1 USD) that C11 would have to allow.
  - C: wait for public LangSmith links; the native tool for LangGraph, but the phase stays open until the quota resets.
- D5 - Evaluation results and dashboard:
  - A (recommended): a Markdown table in the README generated from the summaries and checked in CI, plus a dashboard screenshot; readable on GitHub with no click.
  - B: A plus GitHub Pages publishing the dashboard and the two Spanish HTML guides as real pages; live pages, but it needs a repository setting the owner turns on and a deploy workflow with write permissions.
  - C: commit `data/dashboard.html` into `docs/`; no new mechanism, but GitHub shows it as source code, so a visitor must download it.
- D6 - Language and Spanish material:
  - A (recommended): English README only; the Spanish animations and HTML guides are linked in one "Material in Spanish" line; one text to maintain.
  - B: a full Spanish `README.es.md` too; helps Spanish recruiters, but two texts to keep in sync.
  - C: A with the Spanish animations featured in the README instead of the English ones; consistent with the LinkedIn series, but odd for an English README.
