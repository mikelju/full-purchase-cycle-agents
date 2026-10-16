# 013 - Local tracing with Arize Phoenix

Status: in progress

## Contract
Goal and reason:
See the graph traces during development without depending on the LangSmith free trace quota, which is exhausted until about 2026-11-05.
Owner decision of 2026-10-15 (master plan, strategic decisions): Arize Phoenix running locally for development; LangSmith stays for the frozen trace criteria and the showcase traces.
Out of scope:
Replacing LangSmith or editing any frozen trace criterion; Langfuse; hosted Phoenix; evaluations or prompt management in Phoenix; closing the pending LangSmith criteria of phases 02 to 10.
Constraints / decisions that need the owner:
New dependencies approved by the owner on 2026-10-15 (Phoenix and the OpenTelemetry instrumentation it needs).
`npm run check` stays offline and free: it never sends traces anywhere, Phoenix included.
Current authorization and limits:
Owner approval of 2026-10-15 ("go ahead, Phoenix first"): build the change, push the branch and open a PR to `main`; never merge.

| ID | Observable criterion | Evidence at verification |
|---|---|---|
| C1 | One documented command starts Phoenix locally on `localhost` and keeps its data in an ignored folder of the repository | Command output and the UI answering on its port |
| C2 | With the opt-in variable set, a run of an existing demo sends its graph trace to the local Phoenix, where the graph nodes and model calls appear as spans | Spans of that run read back from the Phoenix API |
| C3 | Without the opt-in variable, and always inside `npm run check`, nothing is sent to Phoenix and the instrumentation is not set up | Tests that fail if it is set up |
| C4 | LangSmith tracing behaves as before: the existing tracing tests pass unchanged | `pytest` output of those tests |
| C5 | README explains how to start Phoenix, turn tracing on and off and open a trace; `npm run check` passes | README section followed step by step; `npm run check` exit 0 |

## Plan
- [x] Update merged statuses, record the owner decisions and the status rule (77246c6)
- [x] Phoenix server command and ignored data folder (C1)
- [x] Opt-in instrumentation and its tests (C2, C3, C4)
- [x] README section (C5)
- [ ] Verification, adversarial review and delivery

## Decisions
Only changes of approach or scope with their reason.
- `npm run phoenix` runs `uvx --exclude-newer 2026-10-01 --from arize-phoenix==20.18.0 python src/purchase_cycle/phoenix_server.py`, a Python launcher inside the Phoenix environment, because `phoenix serve` takes no host or folder flags and npm on Windows runs scripts in `cmd`, where `VAR=value cmd` does not work.
  The launcher sets `PHOENIX_HOST=127.0.0.1`, `PHOENIX_WORKING_DIR=data/phoenix` and `PHOENIX_TELEMETRY_ENABLED=false`, then calls Phoenix's own `phoenix serve` entry point; uvx works on Windows, so no dev group is needed for the server.
  It replaces the first inline Node launcher, which could not change the gRPC receiver.
- Phoenix data lives in `data/phoenix/`, already ignored with `data/`, so `.gitignore` is unchanged.
- Owner decision of 2026-10-15: an open port is a security breach, so nothing Phoenix opens may listen beyond loopback, even if the change takes longer.
  Phoenix 20.18.0 binds its OTLP gRPC receiver (port 4317) on `[::]` with only `PHOENIX_GRPC_PORT` to change; no release up to 20.22.0 (2026-10-15) adds a host setting, and `read_only`, the only built-in switch, also makes the app read-only.
  The launcher therefore forces Phoenix's `GrpcServer` to start disabled and refuses to start if that option disappears; traces use the HTTP collector on `127.0.0.1:6006`, the only listening socket (`.evidence/cambio-013/c1.txt`).
  The Windows firewall and other system settings are not touched.
- Owner decision of 2026-10-15: Phoenix's AI assistant stays off; the launcher sets `PHOENIX_DISABLE_AGENT_ASSISTANT`, `PHOENIX_AGENTS_DISABLE_BASH`, `PHOENIX_AGENTS_DISABLE_WEB_ACCESS` and `PHOENIX_AGENTS_DISABLE_GITHUB` to `true`, and the banner shows all four disabled.
- Owner decision of 2026-10-15: `PHOENIX_TRACING=` is listed in `.env.example` (added by the owner), so `tests/test_secrets.py` passes again.
- Exact pins released before 2026-10-01: `arize-phoenix==20.18.0`, `arize-phoenix-otel==0.17.1` and `openinference-instrumentation-langchain==0.1.76`.
  `arize-phoenix-otel==0.17.2` needs OpenTelemetry 1.45, which forced `langgraph-api` (LangGraph Studio) down from 0.16.0 to 0.10.3; 0.17.1 keeps every locked version unchanged and only adds packages.
- The two client packages are runtime dependencies, because `src/purchase_cycle/config.py` imports them during a normal CLI run; the imports are lazy, so nothing loads without the variable.
- Opt-in variable `PHOENIX_TRACING=true`, project `purchase-cycle`, endpoint `http://127.0.0.1:6006/v1/traces`.
- Review fix: spans leave through a batch processor with a 1 s export timeout instead of one by one, because with Phoenix down each span blocked about 10 s (OTLP retries).
  The SDK flushes the batch at exit, so a short CLI run still delivers every span (`.evidence/cambio-013/c2-replay-spans.txt`).
  Replay demo with Phoenix down, measured again at `aacc4aa`: 5.8-6.9 s batched with the 1 s timeout, against 3.0-3.2 s without tracing (`.evidence/cambio-013/export-timing.txt`).
  Measured earlier in the review, without an evidence file: 32.5 s one by one and 13-14 s batched with the default 10 s timeout.
- Review fix, owner rule that every unused Phoenix feature is off: the launcher also sets `PHOENIX_ENABLE_MCP_SERVER=false`, because `/mcp` had no login and answered any browser origin with `Access-Control-Allow-Origin: *`, and `PHOENIX_ALLOWED_SANDBOX_PROVIDERS=NONE`.
  The pinned version still sends `Access-Control-Allow-Origin: *` on `/oauth2/register`, `/oauth2/token`, `/oauth2/revoke` and `/.well-known/*`, with no setting to stop it; with authentication off they return only the static UI page or static metadata, while `/v1` and `/graphql` send no CORS header (`.evidence/cambio-013/c1.txt`).
- Review fix: `--exclude-newer 2026-10-01` freezes the launcher's transitive dependencies; the Phoenix version has one source, the `phoenix` script of `package.json`.
- Review fix: a test walks every script `npm run check` reaches in `package.json` and requires each one that runs Python or uv to be covered by the guard.
- `configure_tracing` calls `configure_phoenix_tracing` last, so every entry point that already sets up LangSmith also honours the opt-in.
- Guard: Phoenix is never set up when `PYTEST_VERSION` is set (pytest sets it for the whole session) or when `npm_lifecycle_event` is `check`, `check:puerta`, `check:python` or `eval`, the scripts `npm run check` runs; replay runs outside those may trace, because Phoenix is local and free.
- C2 real path first used a replay `demo`, which shows the graph and node spans but no model-call span, since replay answers from recordings without `ChatAnthropic`.
- Owner decision of 2026-10-15: one live run of the order demo with the real model is authorized, with LangSmith off.
  It ran once with the four LangSmith tracing variables `false` and both LangSmith keys empty in its environment, which `load_dotenv` does not override from `.env`; Phoenix received 3 CHAIN spans (graph, `extract`, `match`) and 1 LLM span (`ChatAnthropic`, `claude-haiku-4-5-20251001`), and no recording changed (`.evidence/cambio-013/c2-spans.txt`).
- Owner decision of 2026-10-15: the launcher sets `PHOENIX_CSRF_TRUSTED_ORIGINS=http://127.0.0.1:6006,http://localhost:6006`, so Phoenix answers 401 to any request whose `Origin` or `Referer` is another host, and the always-on wildcard CORS of the anonymous OAuth and `/.well-known/` paths is accepted as SEC-026 in `docs/security.md` (`.evidence/cambio-013/c1.txt`).

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Delivery
