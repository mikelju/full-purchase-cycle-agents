# Master plan - Full Purchase Cycle Agents

Status: approved (living index: reviewed with the owner as each phase reveals new information)
Approved by the owner: 2026-10-06

## Vision
Public portfolio project that demonstrates multi-agent orchestration with LangGraph (Python) over the full purchase cycle of a fictional company.
Three independent modules (multichannel customer orders, supplier quotes and invoice reconciliation) share one database and are composed by an orchestrator graph.
It must make five capabilities visible, with code and evidence: persistent state, human-in-the-loop, failure recovery, observability and structured evaluation.
It works when every module has a one-command reproducible demo, tests and an evaluation with metrics and thresholds that runs in `npm run check`.
Latency is not critical; code clarity and reproducibility are.

## Phases
Every phase leaves something runnable, with its demo, tests and evaluation cases.
Phases 01 to 05 complete the orders module; quotes, reconciliation and the orchestrator follow.

| Phase | One-sentence goal | Depends on | Status |
|---|---|---|---|
| 01 | Foundations: Python project with uv, schema and seed of the shared database (catalog, customers, stock, orders), configurable LLM client, tracing and an evaluation harness inside `npm run check`, proven by a minimal graph | - | integrated |
| 02 | Web form orders: structured-input subgraph, catalog matching, order stored in the database and reply to the customer | 01 | integrated (merged via PR #4); C13 pending (LangSmith trace and experiment, blocked by the exhausted trace quota) |
| 03 | Email orders: email intake agent and extractor agent for plain text, PDF and Excel, with field-level extraction evaluation | 02 | integrated (merged via PR #5); C13 pending (LangSmith trace and experiment, blocked by the trace quota until about 2026-11-05) |
| 04 | Exceptions: an ambiguous or unknown product or a doubtful quantity leads to a question to the customer, a pause with saved state and resumption when the answer arrives, even after a process restart | 03 | integrated (merged via PR #6); C15 pending (LangSmith trace and experiments, blocked by the trace quota until about 2026-11-05) |
| 05 | Orders module wrap-up: simulated WhatsApp, channel router, failure recovery (retries, invalid model output, crash mid-flow) and full module demo | 04 | executing (adversarial review round 1 fixes) |
| 06 | Quotes, request and wait: out-of-stock detection, quote requests to several suppliers and a multi-day wait with saved state, using a simulated clock and reminders | 05 | pending |
| 07 | Quotes, decision: offer extraction and comparison, human approval and creation of the purchase order | 06 | pending |
| 08 | Reconciliation, detection: match the supplier invoice against the purchase order and delivery note and classify differences (price, quantity, extra or missing line) | 07 | pending |
| 09 | Reconciliation, claim: draft claim to the supplier with human approval, editing or rejection | 08 | pending |
| 10 | Orchestrator: top-level graph composing the three subgraphs over the shared database, with an end-to-end demo and an evaluation metrics dashboard | 09 | pending |
| 11 | Portfolio showcase: CV-oriented README with graph diagrams, sample traces, evaluation results and continuous integration on GitHub Actions | 10 | pending |

Statuses: pending, spec in review, spec approved, in progress, blocked, ready locally, integrated.
Only the owner adds, removes or reorders phases; the agent proposes.

## Strategic decisions
| Date | Decision | Reason | Discarded alternative |
|---|---|---|---|
| 2026-10-06 | LangGraph (Python) as orchestration framework | Project requirement | Hand-made orchestration |
| 2026-10-06 | Claude Haiku 4.5 (Anthropic) to extract orders | Owner decision: low cost and good extraction | OpenAI, local model |
| 2026-10-06 | SQLite for business data and LangGraph checkpoints | Owner decision: zero setup and reproducible | PostgreSQL in Docker |
| 2026-10-06 | LangSmith for tracing | Owner decision: native to LangGraph and new hands-on experience for the CV | Langfuse, local logs only |
| 2026-10-06 | The evaluation in `npm run check` runs with no cost and no network | Owner decision; the mechanism is fixed in the phase 01 spec | Calling the real model on every `check` |
| 2026-10-06 | Product documents and code in English; conversation with the owner in Spanish | Owner decision: public portfolio | Spanish documents |
| 2026-10-06 | Fictional Spanish distributor of medical supplies and parapharmacy products selling to clinics, care homes and pharmacies, with a catalog of common products | Owner decision: close to companies that could hire the owner, with simple vocabulary | Industrial supplies, hospitality food, office supplies, drugs (too hard vocabulary) |
| 2026-10-06 | Public GitHub repository under the MIT license | Owner decision | Private until finished; no license |

## Pending
Out of scope, global criteria, further decisions and milestones are written with the owner phase by phase.
