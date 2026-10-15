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
- `npm run phoenix` runs `uvx --from arize-phoenix==20.18.0 phoenix serve` through an inline Node launcher, because `phoenix serve` takes no host or folder flags and npm on Windows runs scripts in `cmd`, where `VAR=value cmd` does not work.
  The launcher sets `PHOENIX_HOST=127.0.0.1`, `PHOENIX_WORKING_DIR=data/phoenix` and `PHOENIX_TELEMETRY_ENABLED=false`; uvx works on Windows, so no dev group is needed for the server.
- Phoenix data lives in `data/phoenix/`, already ignored with `data/`, so `.gitignore` is unchanged.
- Phoenix 20.18.0 binds its OTLP gRPC receiver (port 4317) on every interface with no setting to change it; the UI and the HTTP collector used here stay on `127.0.0.1`.
- Exact pins released before 2026-10-01: `arize-phoenix==20.18.0`, `arize-phoenix-otel==0.17.1` and `openinference-instrumentation-langchain==0.1.76`.
  `arize-phoenix-otel==0.17.2` needs OpenTelemetry 1.45, which forced `langgraph-api` (LangGraph Studio) down from 0.16.0 to 0.10.3; 0.17.1 keeps every locked version unchanged and only adds packages.
- The two client packages are runtime dependencies, because `src/purchase_cycle/config.py` imports them during a normal CLI run; the imports are lazy, so nothing loads without the variable.
- Opt-in variable `PHOENIX_TRACING=true`, project `purchase-cycle`, endpoint `http://127.0.0.1:6006/v1/traces`, spans exported one by one so a short CLI run loses none at exit.
- `configure_tracing` calls `configure_phoenix_tracing` last, so every entry point that already sets up LangSmith also honours the opt-in.
- Guard: Phoenix is never set up when `PYTEST_VERSION` is set (pytest sets it for the whole session) or when `npm_lifecycle_event` is `check`, `check:puerta`, `check:python` or `eval`, the scripts `npm run check` runs; replay runs outside those may trace, because Phoenix is local and free.
- C2 real path used a replay `demo`, which shows the graph and node spans but no model-call span, since replay answers from recordings without `ChatAnthropic`; a model-call span needs a live (paid) run, pending an owner decision.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Delivery
