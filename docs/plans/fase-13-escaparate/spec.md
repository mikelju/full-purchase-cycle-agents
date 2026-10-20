# Phase 13 - Portfolio showcase: specification

Status: spec approved (2026-10-20, frozen)
Approved by the owner: pending
Master plan: `../0_plan_maestro.md`

## Goal
A visitor who opens the GitHub repository understands in two minutes what the system does, how it is built and how well it works, and can check it without running anything.
Today the README is a 1,242-line operating manual whose top lists eleven per-phase status lines (some stale, phase 11 still "awaiting review"); there is no architecture diagram, no visible evaluation result, no trace, and no continuous integration, so every "tests pass" claim rests on the owner's machine.
The phase delivers a CV-oriented README with diagrams generated from the code, an evaluation results table generated from the evaluation summaries, sample trace screenshots, and a GitHub Actions workflow that runs `npm run check` offline with no secret and no cost on every pull request.
It is needed now because phase 12 closed the functional scope; this is the last phase of the master plan and the one the owner shows to employers.
It shows the master plan capabilities "observability" and "structured evaluation" to an outside reader, and adds independent, public evidence that the checks pass.
Owner framing of 2026-10-20: the project is not a system to run a company's purchases and sales but a showcase of how the owner works on a real LLM project, above all LLM evaluation: evaluation suites, traces, LangChain, LangGraph and LangSmith, evaluation of model answers, and evaluation of how the agent behaves after its instructions change.
So the short README leads with LLM evaluation and makes it visual; the business process is the setting, not the headline; the longer guide keeps the same emphasis.
The scope and criteria follow the owner decisions of 2026-10-20 recorded under "Resolved decisions".

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
- README (D1, D6): a new short English README (at most 250 lines) for an outside reader that leads with LLM evaluation, in this order:
  - title, a pitch that names LLM evaluation first, and the CI badge;
  - "How the LLM work is evaluated": a Mermaid diagram of the evaluation loop (versioned golden datasets, live record with Claude Haiku 4.5, recordings, offline replay inside `npm run check` and CI, metrics with 95% Wilson intervals, absolute gates, McNemar regression gate against the stored baseline, dashboard);
  - the generated evaluation results table and the dashboard screenshot;
  - "Catching an instruction change": the before/after table of change 018 (old versus new baseline per suite, lost, gained, exact McNemar p), generated from versioned baselines;
  - sample traces: Phoenix screenshots including a model-call span with its prompt and token counts;
  - "Stack": LangChain (`ChatAnthropic` with tool-schema structured output in `llm.py`), LangGraph (subgraphs, `interrupt` pauses, SQLite checkpoints, the orchestrator), LangSmith (`evaluate` experiments and datasets in `evaluation/harness.py`, tracing), Phoenix (local OpenTelemetry traces), each with the file that uses it;
  - architecture with the orchestrator diagram and the purchase cycle animation;
  - how it is built (SDD Lite, guardrails, CI); quickstart (clone, `uv sync`, `cycle-demo` in replay, `npm run check`); limits; license.
  The current manual moves unchanged in content to `docs/guide.md`, which the README links; per-phase status lines leave the README (the master plan keeps them).
- Diagrams (D3): a command `uv run purchase-cycle diagrams` writes `docs/graphs.md` with one Mermaid diagram per graph, produced by `get_graph().draw_mermaid()` on the compiled graphs with no model call; the README embeds the orchestrator diagram and links the rest.
- Evaluation results (D5): a command `uv run purchase-cycle results-table` prints from `data/eval_summaries/` one Markdown table with, per suite, the case or scenario count, its main metric with the 95% Wilson interval and the gate verdict; with `--check README.md` it exits non-zero when the table between the README markers differs; it runs as the last step of `npm run check`.
  A screenshot of the dashboard, `docs/assets/dashboard.png`, from a replay run.
- Instruction-change comparison: a command `uv run purchase-cycle compare-baselines OLD_REV` prints, per suite whose baseline differs between git revision `OLD_REV` and the working tree, each metric before and after with cases lost, gained and the exact McNemar p from `evaluation/stats.py`; run with the change 018 base `636711e` it reproduces the figures recorded in `docs/changes/018-known-defects.md`, and its Markdown output is the README before/after table.
- Sample traces (D4): screenshots in `docs/assets/` of the local Phoenix: the trace of one replay `cycle-demo` run (the orchestrator tree with its five stages and a module graph under its stage), and one model-call span with its prompt and token counts taken from a live run already stored in Phoenix (16 October), at no cost; captions say what they show and that they come from a local Phoenix.
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
| C5 | The README has at most 250 lines and the sections of "Scope" in that order, in English, with LLM evaluation as the first section after the pitch; it shows the CI badge, the evaluation loop and orchestrator Mermaid diagrams, the evaluation table, the dashboard screenshot, the before/after table and the trace screenshots; the quickstart takes at most four commands from a fresh clone to a replayed `cycle-demo`; every relative link and image path in `README.md`, `docs/guide.md` and `docs/graphs.md` resolves to a versioned file | A pytest that checks the section order and resolves every relative link; the quickstart followed in a clean clone, output saved as evidence |
| C6 | `docs/guide.md` holds every section of the former README; no heading or paragraph is lost in the move | A comparison script over the former README at the base commit and the new pair of files, output saved as evidence |
| C7 | `uv run purchase-cycle results-table` prints the table of "Scope" for the twenty-one suites from the summaries, a missing suite shows as missing, and `--check README.md` exits non-zero when the README table differs; `npm run check` ends with that check, so a stale table fails CI | Pytest tests over sample summaries and a README with a changed value; the CI run of C1 |
| C8 | `docs/assets/dashboard.png` and at least three trace screenshots are versioned and referenced from the README with captions: the orchestrator tree of a replay `cycle-demo` run, a module graph under its stage, and one model-call span showing the prompt and the token counts of a stored live run; they show no key, auth header, email address or local user path | File inspection and a note per image in `plan.md`; the Phoenix spans of that run read back from its API and saved as evidence, as change 013 did |
| C9 | Behaviour of phases 01 to 12 is unchanged: their tests pass, the twenty-one evaluations replay with every gate PASS, `git diff --stat` on recordings, datasets and baselines is empty, and every `*-demo` command (`orders-demo`, `quotes-demo`, `reconcile-demo`, `receipt-demo`, `sales-demo`, `cycle-demo`) prints the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C10 | No secret is versioned: the workflow, `docs/graphs.md` and the new README and guide contain no keys or auth headers | `tests/test_secrets.py` covers the new text files |
| C11 | The phase makes no live model call and spends 0 USD | No `--mode live` or `record` run in the evidence; `plan.md` states the cost |
| C12 | Master plan row 13 shows the phase status | Compare row 13 with the phase status |
| C13 | `uv run purchase-cycle compare-baselines 636711e` prints, for `email_order_extraction`, `clarification_detection`, `document_extraction` and `claim_drafting`, each metric before and after with cases lost, gained and the exact McNemar p, equal to the figures in `docs/changes/018-known-defects.md`; the README before/after table equals its Markdown output, and the README states that the instruction changes of change 018 were caught and checked this way | Pytest tests over two small baseline fixtures with known lost and gained counts; the command output for `636711e` saved as evidence and compared with the change 018 figures; a test comparing the README table with the command output on the versioned baselines |
| C14 | The README "Stack" section names LangChain, LangGraph, LangSmith and Phoenix, and for each one at least one feature and the source file that uses it; every named file exists and contains the named import or call | A pytest that reads the section and checks each file for the named symbol (`ChatAnthropic`, `StateGraph`, `interrupt`, `evaluate`, `configure_phoenix_tracing` in `config.py`) |

## Constraints and risks
- New CI-only dependencies: the GitHub actions `actions/checkout`, `actions/setup-node` and `astral-sh/setup-uv`, pinned by SHA; no new Python or Node package.
- GitHub-hosted standard runners are free for public repositories, so CI adds no cost; a private repository would consume the free minutes quota.
- `npm run check` takes 15 to 25 minutes locally, so each PR waits about that long for its CI result (D2).
- The suite has never run in full on Linux: path separators, line endings, file encodings, fonts or byte-level outputs (the phase 05 XLSX case) may fail there; each fix must leave Windows behaviour unchanged, and C9 guards it.
- `npm ls` in `check` needs the Node dev dependencies, so CI runs `npm ci` first; the hook tests run with Node and must not depend on Windows paths.
- `results-table --check` reads the summaries the same `check` run just wrote, so it must run after `npm run eval`; a README edited by hand to a wrong value fails CI by design.
- Mermaid output of LangGraph may change with a LangGraph upgrade; then the C4 test fails and the file is regenerated, never hand-edited.
- A replay run has no model spans, so the model-call screenshot comes from a live run already stored in the local Phoenix; if that trace is gone, the phase stops for an owner decision instead of running live (C11).
- The change 018 gain in detection recall is partly test-informed (disclosed in change 018); the README before/after section repeats that disclosure.
- The phase 01 LangSmith experiment path (`run_experiment` in `evaluation/harness.py`) runs only in live mode; the README describes it and links no experiment until the catch-up change.
- Phoenix tracing is never set up inside `npm run check`, pytest or CI (change 013 guard).

## Assumptions
- "Graph diagrams" means diagrams generated from the code, so they cannot drift from it.
- "Sample traces" can be local Phoenix screenshots now; public LangSmith links are added later by the catch-up change, which may edit the README traces section.
- "Evaluation results" means the replayed figures of the twenty-one suites that `npm run check` already produces, not a new live measurement.
- The reader is a technical recruiter or hiring engineer who reads English; the LinkedIn series in Spanish links to the repository.
- `get_graph().draw_mermaid()` needs no network and no extra package; this is verified in the first increment, and if false the phase stops for a decision.

## Known limitations
- The traces shown are local Phoenix screenshots; public LangSmith trace and experiment links come with the catch-up change.
- The evaluation table shows the latest replay, not live model measurements; the live figures stay in the baselines and phase plans.
- CI runs on Linux only; Windows stays the owner's local platform.

## Open decisions
None; every decision was resolved by the owner on 2026-10-20.

## Resolved decisions
Owner 2026-10-20 through `.lavish/fase-13-spec.html`: option A for D1 to D6, with the framing note recorded under "Goal".
Each decision keeps its options and trade-offs for the record; the chosen option is marked.
- D1 - README shape:
  - A (recommended, chosen): a short CV-oriented README (about 150 to 250 lines) and the current manual moved to `docs/guide.md`; first screen readable in two minutes, nothing lost; one more file to link.
  - B: a showcase section on top of the current 1,242-line README; no move, but the reader still meets a long manual and the stale status lines must be cleaned anyway.
- D2 - CI scope:
  - A (recommended, chosen): full `npm run check` on every PR and every push to `main`, Linux only; one workflow, CI proves exactly what the method calls "check"; about 15 to 25 minutes per run, free on a public repository.
  - B: ruff and pytest on every PR, full `check` with evaluations only on push to `main` and weekly; faster PR feedback, but a PR can be green with a failing evaluation.
  - C: A on a Linux and Windows matrix; covers the owner's platform too, but doubles the time and Windows runners are slower; adds little since Windows is checked locally.
- D3 - Diagrams:
  - A (recommended, chosen): Mermaid text generated from the compiled graphs into `docs/graphs.md`, rendered natively by GitHub, with a drift test; no dependency, always in sync.
  - B: PNG images through `draw_mermaid_png()`; looks the same everywhere, but needs the mermaid.ink web service or a browser dependency, and binary files cannot be compared in a test.
  - C: one hand-drawn architecture diagram only; nicest look, but it can drift from the code and shows no graph detail.
- D4 - Sample traces:
  - A (recommended, chosen): Phoenix screenshots of a replay `cycle-demo` now, at no cost; the LangSmith catch-up change adds public trace links with model spans after about 2026-11-05.
  - B: Phoenix screenshots of one live `cycle-demo` run that shows model spans with prompts and tokens; more telling, but a new live spend (about 0.15 USD, ceiling 1 USD) that C11 would have to allow.
  - C: wait for public LangSmith links; the native tool for LangGraph, but the phase stays open until the quota resets.
- D5 - Evaluation results and dashboard:
  - A (recommended, chosen): a Markdown table in the README generated from the summaries and checked in CI, plus a dashboard screenshot; readable on GitHub with no click.
  - B: A plus GitHub Pages publishing the dashboard and the two Spanish HTML guides as real pages; live pages, but it needs a repository setting the owner turns on and a deploy workflow with write permissions.
  - C: commit `data/dashboard.html` into `docs/`; no new mechanism, but GitHub shows it as source code, so a visitor must download it.
- D6 - Language and Spanish material:
  - A (recommended, chosen): English README only; the Spanish animations and HTML guides are linked in one "Material in Spanish" line; one text to maintain.
  - B: a full Spanish `README.es.md` too; helps Spanish recruiters, but two texts to keep in sync.
  - C: A with the Spanish animations featured in the README instead of the English ones; consistent with the LinkedIn series, but odd for an English README.
