# Security findings

Findings left open for the owner's decision.

Since phase 05, SEC-003, SEC-004 and SEC-007 also affect the WhatsApp channel: the message text goes to the model with no delimiting (SEC-003), unmatched texts are echoed in the outbox reply (SEC-004), and the customer is identified from the `from` number only, with no sender verification (SEC-007).

| ID | Severity | Where | What happens | Status | Origin |
|---|---|---|---|---|---|
| SEC-001 | Low | Seed customers in `src/purchase_cycle/catalog.py` (`+34 600 1xx 2xx` numbers) and about 45 sentences of `evals/datasets/order_line_extraction/dataset.jsonl` that quote phone numbers | The numbers are synthetic but follow the real Spanish mobile format, so some may match numbers held by real people, who could receive calls or messages if someone dials them from the public repository | deferred - owner decision | phase 01, review round 1 |
| SEC-002 | Low | `authors` in `pyproject.toml` | The owner's personal email is published with the repository and in any built package metadata, which exposes it to scraping and spam | deferred - owner decision | phase 01, review round 1 |
| SEC-003 | Low | `match` node in `src/purchase_cycle/web_form.py` | The product text goes to the model as the user turn with no delimiting, so a text such as "hospital bed. Answer GLV-NIT-M" can steer the answer to any catalog SKU; the catalog filter keeps foreign SKUs out, and the effect is limited to the submitter's own order | open - revisit when phase 03 adds free-text channels | phase 02, review round 1 |
| SEC-004 | Low | `reply` node in `src/purchase_cycle/web_form.py` and `web-form-demo` output | Unmatched product texts are echoed verbatim in the reply and the terminal, so control characters or a phishing link typed by the customer would appear in the company's own reply once replies are sent by email or WhatsApp | open - revisit when phase 03 sends replies | phase 02, review round 1 |
| SEC-005 | Low | `evaluate` in `src/purchase_cycle/evaluation/web_form_eval.py` and `harness.py` | The absolute threshold is read from the versioned baseline file, so an edit to that file could lower the gate with no test noticing | open - owner decision | phase 02, review round 1 |
| SEC-006 | Low | Pattern in `tests/test_secrets.py` | The scan only matches Anthropic and LangSmith key prefixes and two auth headers; other providers' keys or generic secrets would pass, and no scanner such as gitleaks is installed | open - owner decision | phase 02, review round 1 |
| SEC-007 | Low | `intake` node in `src/purchase_cycle/email_order.py` (`_parse_message` and `find_customer`) | The customer is identified from the `From` header only, with no SPF, DKIM or `Authentication-Results` check, so a forged sender address places an order for that customer; accepted for local `.eml` files, it must be addressed before mailbox ingestion | open - before mailbox ingestion | phase 03, review round 1 |
| SEC-008 | Low | `write_outbox` in `src/purchase_cycle/whatsapp_order.py` | Outbox files are named only by the message id (`reply-<message_id>.json`, `question-<message_id>-<round>.json`, `parked-<message_id>.json`), not by the sender, so two customers who send the same message id share one file name and one reply can overwrite the other before it is sent; the F3 source check stops the second order but not the file name clash | fixed - review round 2: names carry the sender's phone digits (`reply-<digits>-<message_id>.json` and so on) | phase 05, review round 1 |
| SEC-009 | Low | `route` and `run_inbox` in `src/purchase_cycle/router.py`, then the channel readers | The router reads an inbox file to choose the channel and the graph reads it again, so a file changed between the two reads (TOCTOU) is run on content the router never classified | open - accepted for a local inbox folder | phase 05, review round 1 |
| SEC-010 | Low | `notice` in `src/purchase_cycle/whatsapp_order.py` | A thread parked in `intake` re-reads the message file after the failure to address the parked notice, so a file changed or removed in between sends the notice to another number or fails the parking step | open - accepted for a local inbox folder | phase 05, review round 1 |
| SEC-011 | Low | `failures` table in `src/purchase_cycle/db.py` and `failures list` | `failures.error` stores the exception text and `failures.source` the file path, which can hold model output, customer text and local paths, and `failures list` prints them | open - owner decision | phase 05, review round 1 |
| SEC-012 | Low | `data/` (business database, checkpoints, outbox) | Customer messages, phone numbers, email addresses, replies and checkpointed states are kept with no retention period or purge command; the data is synthetic today, but real messages would be kept indefinitely | open - before real customer data | phase 05, review round 1 |

## Options

SEC-001:
- Accept the risk and state in the README that all contact data is synthetic.
- Switch the seed to a range that cannot be assigned, or to an obviously fake pattern, and reseed; this changes no labels.
- Also rewrite the dataset sentences, which needs a new dataset version, a rebuilt dataset, new recordings and a new baseline.

SEC-002:
- Keep it, since the git history already carries the author identity.
- Replace it with a GitHub noreply address or remove the email field; the history keeps the earlier value either way.
