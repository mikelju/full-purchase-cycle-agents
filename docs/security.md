# Security findings

Findings left open for the owner's decision.

| ID | Severity | Where | What happens | Status | Origin |
|---|---|---|---|---|---|
| SEC-001 | Low | Seed customers in `src/purchase_cycle/catalog.py` (`+34 600 1xx 2xx` numbers) and about 45 sentences of `evals/datasets/order_line_extraction/dataset.jsonl` that quote phone numbers | The numbers are synthetic but follow the real Spanish mobile format, so some may match numbers held by real people, who could receive calls or messages if someone dials them from the public repository | deferred - owner decision | phase 01, review round 1 |
| SEC-002 | Low | `authors` in `pyproject.toml` | The owner's personal email is published with the repository and in any built package metadata, which exposes it to scraping and spam | deferred - owner decision | phase 01, review round 1 |
| SEC-003 | Low | `match` node in `src/purchase_cycle/web_form.py` | The product text goes to the model as the user turn with no delimiting, so a text such as "hospital bed. Answer GLV-NIT-M" can steer the answer to any catalog SKU; the catalog filter keeps foreign SKUs out, and the effect is limited to the submitter's own order | open - revisit when phase 03 adds free-text channels | phase 02, review round 1 |
| SEC-004 | Low | `reply` node in `src/purchase_cycle/web_form.py` and `web-form-demo` output | Unmatched product texts are echoed verbatim in the reply and the terminal, so control characters or a phishing link typed by the customer would appear in the company's own reply once replies are sent by email or WhatsApp | open - revisit when phase 03 sends replies | phase 02, review round 1 |
| SEC-005 | Low | `evaluate` in `src/purchase_cycle/evaluation/web_form_eval.py` and `harness.py` | The absolute threshold is read from the versioned baseline file, so an edit to that file could lower the gate with no test noticing | open - owner decision | phase 02, review round 1 |
| SEC-006 | Low | Pattern in `tests/test_secrets.py` | The scan only matches Anthropic and LangSmith key prefixes and two auth headers; other providers' keys or generic secrets would pass, and no scanner such as gitleaks is installed | open - owner decision | phase 02, review round 1 |

## Options

SEC-001:
- Accept the risk and state in the README that all contact data is synthetic.
- Switch the seed to a range that cannot be assigned, or to an obviously fake pattern, and reseed; this changes no labels.
- Also rewrite the dataset sentences, which needs a new dataset version, a rebuilt dataset, new recordings and a new baseline.

SEC-002:
- Keep it, since the git history already carries the author identity.
- Replace it with a GitHub noreply address or remove the email field; the history keeps the earlier value either way.
