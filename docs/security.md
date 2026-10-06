# Security findings

Findings left open for the owner's decision.

| ID | Severity | Where | What happens | Status | Origin |
|---|---|---|---|---|---|
| SEC-001 | Low | Seed customers in `src/purchase_cycle/catalog.py` (`+34 600 1xx 2xx` numbers) and about 45 sentences of `evals/datasets/order_line_extraction/dataset.jsonl` that quote phone numbers | The numbers are synthetic but follow the real Spanish mobile format, so some may match numbers held by real people, who could receive calls or messages if someone dials them from the public repository | deferred - owner decision | phase 01, review round 1 |
| SEC-002 | Low | `authors` in `pyproject.toml` | The owner's personal email is published with the repository and in any built package metadata, which exposes it to scraping and spam | deferred - owner decision | phase 01, review round 1 |

## Options

SEC-001:
- Accept the risk and state in the README that all contact data is synthetic.
- Switch the seed to a range that cannot be assigned, or to an obviously fake pattern, and reseed; this changes no labels.
- Also rewrite the dataset sentences, which needs a new dataset version, a rebuilt dataset, new recordings and a new baseline.

SEC-002:
- Keep it, since the git history already carries the author identity.
- Replace it with a GitHub noreply address or remove the email field; the history keeps the earlier value either way.
