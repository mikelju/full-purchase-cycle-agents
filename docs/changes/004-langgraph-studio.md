# 004 - Watch the orders graphs in LangGraph Studio

Status: delivered (PR to `main`, pending owner merge)

## Contract
Goal and reason:
The owner wants to watch the orders graphs run in LangGraph Studio: the nodes of each channel graph, the state after each step, the clarification question (`interrupt()` in the `clarify` subgraph) and the resume with the customer's answer.
The owner decided on 2026-10-09 that this is a standalone change after phase 05, with a contract naming the new `langgraph-cli` dev dependency and the model and LangSmith trace costs.
Out of scope:
The graphs themselves, their prompts, recordings, datasets, evaluations and baselines; one exception, approved by the owner on 2026-10-10: clarify accepts a text resume (see Decisions).
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
- [x] 2b. Clarify accepts a text resume from Studio (owner decision during item 3).
  Evidence: `uv run pytest -q tests/test_studio.py tests/test_clarification*.py tests/test_clarify_cli.py` 120 passed (dict, JSON-text and plain-text resumes reach a stored order in replay); `npm run eval` nine replay evaluations PASS, output identical to item 1 (`.evidence/change-004/eval-resume-text.txt`).
- [x] 2c. Live mode loads the model key from the dotenv file, because the dev server does not load it; LangSmith tracing stays forced off (commit 24a5d53).
  Evidence: `tests/test_studio.py::test_live_mode_loads_the_key_from_dotenv_and_keeps_tracing_off`.
- [x] 3. Owner walkthrough in a fresh session after the context handoff: start `langgraph dev`, open Studio, run the web form sample, then the WhatsApp doubt and its answer; C6.
  Evidence: owner confirmed on 2026-10-11; notes in Delivery.
- [x] 4. Adversarial review, `npm run check`, push and PR to `main`; C7. Evidence: `.evidence/change-004/check-f0319f1.txt` (exit=0, 660 passed; the nine replay evaluation metrics identical to `eval-resume-text.txt`).

## Decisions
Replay by default so watching costs nothing and needs no key; replay recordings come from `evals/recordings/orders_demo.jsonl` plus the Studio-only `evals/recordings/studio.jsonl`.
`orders_demo.jsonl` covers `examples/orders/` including the WhatsApp doubt and its answer, but not the plain first email extraction answer, because the demo deliberately breaks it and records only the re-ask.
The owner chose option A on 2026-10-10: record that missing answer once, live with Claude Haiku 4.5 (a few cents), into the new Studio-only file `studio.jsonl`; existing recordings, datasets, evaluations and baselines stay untouched.
`langgraph dev` needs `--allow-blocking`, because the factories run inside the server event loop and `db.connect` makes blocking calls.
Interrupt resume inside the `clarify` subgraph verified through the dev server API: the WhatsApp doubt resumed to a stored order and intake did not rerun.
A separate Studio database and outbox, so Studio runs never touch `data/purchase_cycle.db` or the real outbox.
Factories compile without a checkpointer because the dev server supplies persistence.
On 2026-10-10, during the walkthrough, Studio's resume box sent the typed value as text, even when it looked like JSON, and `wait` in clarify crashed on `received.get`.
The owner approved letting clarify accept a text resume: JSON text of a dict is used as the dict, any other text becomes `{"answer": <stripped text>}`, and an empty text goes through the existing answer checks; a dict resume behaves as before.
After review round 1, a JSON string resume is read as the answer without quotes, and an empty, odd or answerless resume is rejected as an empty answer without a model call; the same question waits again.
Known risks: a community report says Studio may restart instead of resuming an interrupt inside a subgraph, and the dev server's blocking-call detector may need `--allow-blocking` for our sync SQLite nodes; both are checked first in item 1 and, if they bite, recorded rather than worked around by changing the graphs.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | local, 2026-10-11 | `main..0149d64` | fresh lens | four minor, all fixed (below) | closed |

Round 1 findings, failing tests in b8c3b03:
- Minor: a JSON string resume (Studio's `""` prefill with the answer typed inside) kept its quotes; `resume_value` now uses the decoded string, and `""` becomes an empty answer (191c5d1).
- Minor: a non-text, non-dict resume (`42`, a list) crashed `wait`, and a dict with neither `answer` nor `close` raised `KeyError`; any resume now goes through `resume_value`, an odd one becomes an empty answer, and `interpret` rejects an empty answer without a model call, so the same question waits again (191c5d1).
- Minor: `studio.py` set `LANGGRAPH_CLI_NO_ANALYTICS`, which the CLI reads before any factory runs; the line and its test assertion were removed, and the README keeps setting it in the shell (b8c3b03, c2f4024).
- Minor: first use was decided by the database file existing, so a failed first seed was never repeated; the Studio database is now seeded when its catalog is missing or empty (c2f4024).

Scope note: this branch also carries the master plan row 05 status bookkeeping (phase 05 integrated via PR #9, commit 7ba48ff); accepted, and mentioned in the PR.

## Delivery
Owner walkthrough (item 3, C6), confirmed by the owner on 2026-10-11:
- The owner ran the `web_form` and `whatsapp` graphs in replay in Studio and watched each node and the state step by step.
- The WhatsApp doubt paused on the clarification question and resumed to a stored order, after the fix for Studio's resume box always sending a string (commits 2f7f660 and d086d7e).
- The owner then ran live mode (Claude Haiku 4.5) with their own messages from `data/studio/inbox/` and completed orders on the `whatsapp` graph; live mode loads the key from the dotenv file since commit 24a5d53.
- The built-in Claude desktop browser pane cannot load Studio, because it blocks `smith.langchain.com` from calling `127.0.0.1`; the owner used their own browser.
- Idempotency: a message already processed returns the existing order; to start over, stop the server and delete `data/studio/` and `.langgraph_api/`.
- Replay mode only understands the recorded clarify answer; any other answer needs live mode.
- Known limit, not fixed: an input meant for another graph raises a raw `KeyError` naming the missing input key, observed during the walkthrough.

Results per criterion:
- C1-C5: met (tests in `tests/test_studio.py`, README section, server started on Windows during the walkthrough).
- C6: met, owner walkthrough above.
- C7: met, `npm run check` exit 0 at f0319f1 and the nine replay evaluations unchanged.
