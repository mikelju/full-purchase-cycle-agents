# 006 - Local test page for the orders channels

Status: in progress

## Contract
Goal and reason:
The owner wants to try the orders module by hand from a browser, without preparing files or typing CLI commands.
One local page lets the owner pick a channel (web form, email or WhatsApp) and a seeded customer, browse the catalog, send an order and read what the system answers: stored order, clarification question, not an order, under review or rejected.
When the system asks a question, the owner answers it from the same page and the paused order resumes.
Everything stays simulated: the page writes the same inputs the channels already read and runs them through the existing router and graphs.
The owner decided on 2026-10-13 that this is a standalone change, not a phase, and that a real WhatsApp connection (Meta Cloud API with its free test number) is a later step.
Out of scope:
The graphs, prompts, recordings, datasets, evaluations and baselines.
The quotes module and any later module; a real WhatsApp, email or web connection; authentication, deployment or access from another machine.
Sample buttons, styling beyond a plain usable page, and a mobile layout.
Constraints / decisions that need the owner:
- No new dependency: the server uses the Python standard library (`http.server`) and the page is one HTML file with plain JavaScript, no CDN.
- Model cost: live mode (Claude Haiku 4.5) is the default, because free text only works live; a few cents per order, and it needs the model key in `.env`.
  `--mode replay` costs nothing but only understands the recorded sample texts of `examples/orders/`.
- LangSmith trace cost: none; the command forces tracing off, as the Studio module does, so the page does not spend the trace quota.
Current authorization and limits:
Owner request 2026-10-13 to draft this contract; approved by the owner on 2026-10-13.
Delivery is the work branch `cambio-006-local-test-ui`, created from `main` after phase 07 was merged (PR #13), and a PR to `main`.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `purchase-cycle ui [--mode live\|replay] [--port N]` serves the page on `127.0.0.1` only, on its own database `data/ui/purchase_cycle.db` (seeded on first use), checkpoints and outbox under `data/ui/`, never the business database; LangSmith tracing is forced off | `tests/test_ui.py`; `git diff` |
| C2 | The page lists the catalog (SKU, name, family, sale unit, price and stock) with a text filter, and the seeded customers with the email and phone each channel uses to identify them | `tests/test_ui.py` (JSON endpoints); owner walkthrough |
| C3 | Sending from the page writes one input in the channel's own format (web form JSON with catalog-assisted lines, `.eml` from the customer's email, WhatsApp message from the customer's phone) and runs it through `router.run_inbox`; the answer shows the route, the thread id, the outcome and the reply text the customer would get | `tests/test_ui.py` |
| C4 | A clarification question shows on the page; for WhatsApp the answer is a new message from the same phone, as the router already resumes it, and for web form and email the answer resumes the thread as `clarify answer` does; the resumed order shows its outcome | `tests/test_ui.py` |
| C5 | In replay, with no key and the network blocked, the sample texts of `examples/orders/` sent through the page give the same outcomes as `purchase-cycle orders-demo` (its recordings `orders_demo.jsonl`) on that folder: stored orders, the WhatsApp doubt paused and then stored with the recorded answer, and the photo message answered as text only | `tests/test_ui.py` |
| C6 | A malformed request (unknown channel or customer, empty text, missing answer) returns a JSON error that the page shows, never a traceback; a failed graph run shows its error and keeps the server running | `tests/test_ui.py` |
| C7 | README section "Try the orders by hand": command, URL, live and replay modes with their cost, how to answer a question, how to start over, and the limits | README; followed step by step in item 4 |
| C8 | Owner walkthrough in live mode: one order per channel, one clarification answered from the page, and the stored orders visible in the outcome | Owner sees it; notes in Delivery |
| C9 | `npm run check` exits 0 and the replay evaluations are unchanged | Command output |

## Plan
- [x] 1. `tests/test_ui.py` first (failing), then `src/purchase_cycle/ui.py` (server, JSON endpoints, input writers, router call and resume) and the `ui` CLI command; C1 to C6.
  Evidence: `uv run pytest -q tests/test_ui.py` -> 23 passed (replay, no key, only loopback connections allowed); `ruff check` and `ruff format --check` clean.
- [x] 2. The page `src/purchase_cycle/ui.html`: catalog with filter, channel and customer pickers, one input form per channel, the conversation with outcomes and the answer box; C2 to C4.
  Evidence: `uv run pytest -q tests/test_ui.py` -> 25 passed (page served with its controls, no external URL; wheel built with `uv build --wheel` holds `purchase_cycle/ui.html`); script checked with `node --check`.
- [x] 3. README section and `.gitignore` for `data/ui/`; C7.
  Evidence: README section "Try the orders by hand" added; `data/` is already ignored (`git check-ignore -v data/ui/x` -> .gitignore:13). Real server on port 8766 in replay: the WhatsApp doubt sample asked its question, the answer message resumed it and stored order 1; an unknown channel returned a JSON error. `uv run pytest -q tests/test_ui.py tests/test_router.py tests/test_clarification.py tests/test_secrets.py` -> 138 passed; `npm run --silent eval` -> failures: 0, no file changed.
- [ ] 4. Owner walkthrough in live mode; C8.
- [ ] 5. Adversarial review, `npm run check`, push and PR to `main`; C9.

## Decisions
The page runs every input through `router.run_inbox`, so it exercises the same path as `purchase-cycle route` and gets idempotency, duplicate handling and WhatsApp resumption for free.
Each send gets its own inbox folder under `data/ui/inbox/`, which also leaves a record of what was sent.
Replay is not the default, unlike Studio in change 004: the point of the page is typing new orders, which only live mode understands.
Replay reuses `studio.StudioModelClient` with `orders_demo.jsonl` plus `studio.jsonl`, so it answers the sample texts and the Studio scenes.
Sample 06 (a re-delivered message id) is excluded from the C5 test, because the page gives every send a new message id.
WhatsApp accepts an optional `"type": "image"` and the page a "send a photo" checkbox, so sample 05 can be sent.
Extra endpoints: `/api/info` (mode) and `/api/conversations` (reads the log `data/ui/conversations.jsonl`); every request also passes a Host header check (127.0.0.1 or localhost).
A WhatsApp answer sent to `/api/answer` returns 400, because it is a new message from the same customer.
Request limits: 100 KB per request, 4000 characters per text, 50 lines per form.
An occupied port gives a friendly error, and so does a port outside 1-65535.
Review round 1 decisions: a POST with an `Origin` header other than `http://127.0.0.1:<port>` or `http://localhost:<port>` gets 403, and a POST whose `Content-Type` is not `application/json` gets 415 (CSRF, CWE-352).
The server is a `ThreadingHTTPServer` with a 30 s handler timeout, so an idle pre-connect socket does not block the page; graph runs and log writes stay serialised by `UiApp.lock`, and reads tolerate a half-written log line.
An `answer_rejected` entry counts as pending like `question`, and a WhatsApp thread shows its answer control only when it is the customer's latest pending clarification, as the router decides.
A run parked by recovery shows as `under_review` (status only, no notice text).
C5 wording changed by the owner on 2026-10-13 from `purchase-cycle route` to `purchase-cycle orders-demo`: `route` in replay has no recordings for the sample texts (review round 1).
A 500 returns the exception class and a generic message; the detail goes to the server's stderr.
The `outcome` of a failed run still carries the first line of the exception text (shown on the local page and kept in the log).

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | Claude Opus 5.5 subagents | `origin/main..0f7da4a` | Security; Correctness (two independent runs) | Important: no Origin or Content-Type check on POST (CWE-352); rejected answer hid the answer control (C4); WhatsApp answer control shown when the router would start a new order (C4); single-threaded server with no timeout; `route` on `examples/orders` fails in replay, so C5 and the README name the wrong command. Minor: truncated log line gives 500; parked run shown as failed; raw exception text in 500s (CWE-209); `data/ui/` growth; port out of range; checkpointer never closed | Fixed in 441faf5 (SEC-012 widened for `data/ui/` growth); C5 wording pending the owner |
| 2 | Claude Opus 5.5 subagent | `441faf5` | Security; Correctness | Medium: thread parked after a transient error shown as failed. Low: race in `conversations()` could give 500; app closed under an in-flight run and leaked on bind failure; rejected-answer test forced an impossible state. No Critical or High | Fixed in 2c521d2 |

## Delivery
