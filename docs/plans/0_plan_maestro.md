# Master plan - Full Purchase Cycle Agents

Status: approved (living index: reviewed with the owner as each phase reveals new information)
Approved by the owner: 2026-10-06

## Vision
Public portfolio project that demonstrates multi-agent orchestration with LangGraph (Python) over the full purchase cycle of a fictional company.
Three independent modules (multichannel customer orders, supplier quotes and reconciliation of order, delivery note and invoice on both the purchase and the sales side) share one database and are composed by an orchestrator graph.
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
| 05 | Orders module wrap-up: simulated WhatsApp, channel router, failure recovery (retries, invalid model output, crash mid-flow) and full module demo | 04 | integrated (merged via PR #9); C17 pending (LangSmith trace and experiment, blocked by the trace quota until about 2026-11-05) |
| 06 | Quotes, request and wait: out-of-stock detection, quote requests to several suppliers and a multi-day wait with saved state, using a simulated clock and reminders | 05 | integrated (merged via PR #11) |
| 07 | Quotes, decision: offer extraction and comparison, human approval and creation of the purchase order | 06 | integrated (merged via PR #13); C16 pending (LangSmith trace and experiment, blocked by the trace quota until about 2026-11-05) |
| 08 | Reconciliation, detection: match the supplier invoice against the purchase order and delivery note and classify differences (price, quantity, extra or missing line) | 07 | integrated (merged via PR #18); C15 pending (LangSmith trace and experiment, blocked by the trace quota until about 2026-11-05) |
| 09 | Reconciliation, claim: draft claim to the supplier with human approval, editing or rejection | 08 | integrated (merged via PR #20); C17 pending (LangSmith trace and experiment, blocked by the trace quota until about 2026-11-05) |
| 10 | Goods receipt and serving: the reconciled supplier delivery note puts the goods into stock, pending customer orders are served and our delivery note to the customer is issued | 09 | integrated (merged via PR #21); C13 pending (LangSmith trace, blocked by the trace quota until about 2026-11-05) |
| 11 | Customer invoicing and sales reconciliation: invoice to the customer and reconciliation of order, delivery note and invoice on the sales side with the same matching, triggered by the signed delivery note the customer returns with incidents (missing, extra, damaged); response to the customer, credit note or corrective invoice with human approval, reusing the phase 09 pattern; payment out of scope | 10 | integrated (merged via PR #28) |
| 12 | Orchestrator: top-level graph composing the three subgraphs over the shared database, with an end-to-end demo and an evaluation metrics dashboard | 11 | integrated (merged via PR #30) |
| 13 | Portfolio showcase: CV-oriented README with graph diagrams, sample traces, evaluation results and continuous integration on GitHub Actions | 12 | integrated (merged via PR #34) |

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
| 2026-10-14 | Reconciliation works on both sides: purchase (supplier) and sales (customer); two new phases 10 and 11 after the claim phase, before the orchestrator (now 12) | Owner decision of 2026-10-13 and 2026-10-14: any order, delivery note and invoice trio is reconciled whichever side ordered; phase 08 is not reopened | One larger phase; placing them between 08 and 09; customer claim email as trigger; payment reconciliation |
| 2026-10-15 | Arize Phoenix running locally to see traces during development; LangSmith stays for the frozen trace criteria and the showcase traces; Phoenix is set up in change 013 before phase 11 | Owner decision: the LangSmith free trace quota blocks seeing traces for weeks; Phoenix is open source, runs locally with no account, quota or cost | Langfuse (local install needs Docker with several services), LangSmith only |
| 2026-10-15 | Order after phase 10: change 013 (Phoenix), phase 11, then the known defects before phase 12 (goods receipt `failures resume` F6, change 001 email extractor dropped lines, claims CLM-044, CLM-066 and CLM-072, documents DOC-060 and DOC-112); the pending LangSmith traces of phases 02 to 10 go in a separate change once the quota resets about 2026-11-05 | Owner decision via `.lavish/siguientes-pasos.html` | Defects first; orchestrator before phase 11; traces inside phase 13 |
| 2026-10-19 | The known defects are fixed by change 018 before phase 12, with the phase 04 detection thresholds raised to 95%; CLD-0149 and the SHARPS-15 hallucination on EML-0215 and EML-0237 are logged, not fixed | Owner decisions in `.lavish/cambio-018-decisiones.html` | Fixing them inside phase 12 |
| 2026-10-19 | Phase 12 spec approved: one orchestrator graph whose stages call the module functions (D1 A); an end-to-end demo of four customer orders on distinct paths (clean order, clarification, quote approval with a price claim, short delivery with a rejected claim) recorded once live with Claude Haiku 4.5, phase live ceiling 5 USD (D2 A amended by the owner); a static HTML evaluation dashboard (D3 A); a deterministic `purchase_cycle` suite (D4 A); a local Phoenix trace, LangSmith trace left to the catch-up change (D5 A) | Owner decisions via `.lavish/fase-12-spec.html` | Nested module subgraphs in one thread; a demo with no live spend and seeded stages; Streamlit or LangSmith dashboard; pytest only; LangSmith trace pending |

## Pending
Out of scope, global criteria, further decisions and milestones are written with the owner phase by phase.
