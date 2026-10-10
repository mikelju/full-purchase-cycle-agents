# 004 - Watch the orders graphs in LangGraph Studio

Status: planned

## Contract
Goal and reason:
The owner wants to watch the orders graphs run in LangGraph Studio: the nodes of each channel graph, the state after each step, the clarification question (`interrupt()` in the `clarify` subgraph) and the resume with the customer's answer.
The owner decided on 2026-10-09 that this is a standalone change after phase 05, with a contract naming the new `langgraph-cli` dev dependency and the model and LangSmith trace costs.
Out of scope:
The graphs themselves, their prompts, recordings, datasets, evaluations and baselines.
Deploying to LangSmith or any hosted server; Studio threads do not reach the CLI `clarify`, `resume` or `failures` commands, because the dev server keeps its own persistence.
The router (`route`), which is plain Python and not a LangGraph graph.
Constraints / decisions that need the owner:
- New dev dependency `langgraph-cli[inmem]` (0.4.33, MIT; it brings `langgraph-api`, Elastic-2.0, free for local development), declared in `[dependency-groups] dev`; approved with this contract.
- Model cost: 0 USD by default, because Studio runs in replay mode with the recorded answers; live mode (Claude Haiku 4.5) only when the owner sets `PURCHASE_CYCLE_STUDIO_MODE=live`, a few cents per run.
- LangSmith trace cost: none; the Studio module forces tracing off (`configure_tracing`), and `LANGGRAPH_CLI_NO_ANALYTICS=1` disables the CLI telemetry event.
- Opening Studio in the browser may ask for the owner's free LangSmith login; that step is the owner's.
Current authorization and limits:
Owner request 2026-10-10 to plan this change, then show it after a context handoff; delivery is the work branch `cambio-004-langgraph-studio` and a PR to `main`.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `langgraph.json` at the root exposes three graphs, `web_form`, `email` and `whatsapp`, through factories in `src/purchase_cycle/studio.py` that compile each channel graph without a checkpointer, on a Studio database `data/studio/purchase_cycle.db` (seeded on first use) and outbox `data/studio/outbox`, never the business database | `tests/test_studio.py`; `git diff` |
| C2 | The Studio module forces LangSmith tracing off and defaults to replay mode; `PURCHASE_CYCLE_STUDIO_MODE` (`replay` or `live`) is the only new variable and is listed in `.env.example` | `tests/test_studio.py`, `tests/test_secrets.py` |
| C3 | In replay, with no key and the network blocked, the web form, email and WhatsApp samples of `examples/orders/` run through the Studio graphs: a clear order is stored, and the WhatsApp doubt pauses on the question and resumes with the recorded answer to a stored order | `tests/test_studio.py` |
| C4 | `uv run langgraph dev` starts on Windows and lists the three graphs; `.langgraph_api/` is ignored by Git | Command output; `git status` |
| C5 | README section "Watch the graphs in LangGraph Studio": command, Studio URL, which input to paste for each sample, the exact answer to type for the doubt in replay, live mode and its cost, and the limits (threads separate from the CLI, a new input or answer needs live mode) | README; followed step by step in item 3 |
| C6 | Owner walkthrough: Studio shows the web form graph and a run step by step, and the WhatsApp doubt pauses on the question and resumes with the answer; if Studio cannot resume an interrupt inside a subgraph, that is recorded as a limit with what does work | Owner sees it; notes in Delivery |
| C7 | `npm run check` exits 0 and the nine replay evaluations are unchanged | Command output |

## Plan
- [x] 1. Dependency, `langgraph.json`, `src/purchase_cycle/studio.py` and `tests/test_studio.py` (failing tests first), `.gitignore` and `.env.example`; C1 to C4.
  Evidence: `uv run pytest -q tests/test_studio.py tests/test_secrets.py` 15 passed; `npm run eval` nine replay evaluations PASS (`.evidence/change-004/eval-item1.txt`); C4: langgraph-cli 0.4.33, `langgraph dev --allow-blocking` lists the three graphs.
- [x] 2. README section; C5.
  Evidence: README "Watch the graphs in LangGraph Studio"; the web form input matches `examples/orders/01-web-form.json`.
- [ ] 3. Owner walkthrough in a fresh session after the context handoff: start `langgraph dev`, open Studio, run the web form sample, then the WhatsApp doubt and its answer; C6.
- [ ] 4. Adversarial review, `npm run check`, push and PR to `main`; C7.

## Decisions
Replay by default so watching costs nothing and needs no key; replay recordings come from `evals/recordings/orders_demo.jsonl` plus the Studio-only `evals/recordings/studio.jsonl`.
`orders_demo.jsonl` covers `examples/orders/` including the WhatsApp doubt and its answer, but not the plain first email extraction answer, because the demo deliberately breaks it and records only the re-ask.
The owner chose option A on 2026-10-10: record that missing answer once, live with Claude Haiku 4.5 (a few cents), into the new Studio-only file `studio.jsonl`; existing recordings, datasets, evaluations and baselines stay untouched.
`langgraph dev` needs `--allow-blocking`, because the factories run inside the server event loop and `db.connect` makes blocking calls.
Interrupt resume inside the `clarify` subgraph verified through the dev server API: the WhatsApp doubt resumed to a stored order and intake did not rerun.
A separate Studio database and outbox, so Studio runs never touch `data/purchase_cycle.db` or the real outbox.
Factories compile without a checkpointer because the dev server supplies persistence.
Known risks: a community report says Studio may restart instead of resuming an interrupt inside a subgraph, and the dev server's blocking-call detector may need `--allow-blocking` for our sync SQLite nodes; both are checked first in item 1 and, if they bite, recorded rather than worked around by changing the graphs.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Delivery
