# Full Purchase Cycle Agents

Public portfolio of multi-agent orchestration with LangGraph (Python) over a company's purchase cycle: multichannel customer orders, supplier quotes and invoice reconciliation.
Each module is a subgraph with its demo, tests, evaluation and traces; a shared database joins them and an orchestrator graph composes them.
Work follows SDD Lite: master plan, phases with frozen criteria, autonomous execution and adversarial review.

## Always
- Act as the single point of contact; to develop, load `.agents/skills/sdd-lite/SKILL.md`.
- State assumptions and doubts before coding; ask when they change the outcome.
- Do the minimum that meets the criterion: no unrequested features, abstractions or configuration.
- Surgical changes: touch only what the request needs and respect the existing style.
- Work against observable criteria; reproduce failures in the real user path before fixing them.
- Every result carries current evidence; fix clearly related defects and log unrelated ones.
- Read only the context you need; broad exploration goes to subagents that return summaries.
- Plain hyphen; in long Markdown, one sentence per line; Python output without special symbols.
- Repository documents, code and data in English; talk to the owner in Spanish.
- One source per decision; reusable learnings go to the most specific file.
- This file is capped at 5,000 tokens and is not edited mid-session; a new rule needs evidence and, if it does not fit, another one leaves.

## Ask first
- Pending owner decisions, new costs or dependencies, and changes to frozen criteria.
- Publishing outside delivery: authorization covers that destination and that content; respect confidentiality and inherited permissions.
  Delivery includes pushing the work branch and opening a PR to `main` on the project's `origin` remote.

## Never
- Lower criteria or hide failed or unrun tests.
- Merge, approve your own work or write to the default branch; the `.claude/` hooks block it.
- Hand-edit generated files or CHANGELOG.md, or add the agent as a commit co-author.

## Map
- Skills in `.agents/skills/` (written in Spanish): flow, routing and delegation in `sdd-lite`; review in `sdd-review`; delivery and No Mistakes in `sdd-delivery`.
- Master plan and phases: `docs/plans/`; standalone changes: `docs/changes/`; templates: `docs/templates/`.
- Guardrails: `.claude/settings.json` and `.claude/hooks/`; checks: `npm run check`.
- Product, setup and limits: `README.md`. Environment: Windows, Python 3.13 with uv, Node.js 22 (method tooling only), Git and GitHub CLI.
- Python code in `src/`, tests in `tests/`, evaluation assets in `evals/` (created in phase 01).
