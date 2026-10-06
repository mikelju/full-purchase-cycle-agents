# Security findings

Findings left open for the owner's decision.
Each one states where it was found, the risk and the options.

## SEC-001 (Low) - Realistic phone number format in synthetic data

- Status: deferred - owner decision.
- Found in: the seed customers in `src/purchase_cycle/catalog.py` (`+34 600 1xx 2xx` numbers) and about 45 sentences of `evals/datasets/order_line_extraction/dataset.jsonl` that quote phone numbers.
- Risk: the numbers are synthetic but follow the real Spanish mobile format, so some may match numbers held by real people, who could receive calls or messages if someone dials them from the public repository.
- Options:
  - Accept the risk and state in the README that all contact data is synthetic.
  - Switch the seed to a range that cannot be assigned, or to an obviously fake pattern, and reseed; this changes no labels.
  - Also rewrite the dataset sentences, which needs a new dataset version, a rebuilt dataset, new recordings and a new baseline.

## SEC-002 (Low) - Personal email in package metadata

- Status: deferred - owner decision.
- Found in: `authors` in `pyproject.toml`.
- Risk: the owner's personal email is published with the repository and in any built package metadata, which exposes it to scraping and spam.
- Options:
  - Keep it, since the git history already carries the author identity.
  - Replace it with a GitHub noreply address or remove the email field; the history keeps the earlier value either way.
