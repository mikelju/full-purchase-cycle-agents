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
- [ ] Phoenix server command and ignored data folder (C1)
- [ ] Opt-in instrumentation and its tests (C2, C3, C4)
- [ ] README section (C5)
- [ ] Verification, adversarial review and delivery

## Decisions
Only changes of approach or scope with their reason.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Delivery
