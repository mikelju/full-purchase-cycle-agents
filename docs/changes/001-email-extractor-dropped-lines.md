# 001 - Email extractor: dropped lines, non-integer quantities and purpose clauses

Status: proposed

## Contract
Goal and reason:
The phase 03 email extractor loses order lines in two ways that phase 04 detection cannot see.
It drops some email lines, so an unknown or doubtful quantity line has no produced line to raise a doubt on.
It returns a non-integer quantity for some emails, the schema rejects the whole answer and the run stops.
It copies a purpose or usage clause that no product name holds into the line text (for example "for the treatment room" or "for our diabetic patients"), so the phase 04 candidate search finds one product or none and no ambiguous doubt is raised.
Measured in phase 04 deviation 04.1 (detection replay, test split, 2026-10-08): unknown recall 94.3% [84.6, 98.1] n=53 with CLD-0041, CLD-0047 and CLD-0149 dropped; quantity recall 91.3% [79.7, 96.6] n=46 with CLD-0123 and CLD-0137 rejected by the schema; CLD-0066 is also rejected by the schema, which costs one ambiguous line.
Ambiguous recall 89.1% [78.2, 94.9] n=55 on the same replay, with CLD-0083, CLD-0112, CLD-0160 and CLD-0165 missed because of the purpose clause and CLD-0066 because of the schema.
The owner set the phase 04 unknown and quantity thresholds at the measured level and asked for this separate change (decision Q2=A, 2026-10-08), and set the ambiguous threshold at the measured level with the purpose-clause cause added here (second round of deviation 04.1, 2026-10-08).
Out of scope:
The phase 04 detection rules, candidate search and clarification prompts.
Constraints / decisions that need the owner:
It changes the frozen phase 03 extractor prompt, schema or recordings, so the phase 03 evaluation gates and baseline must still pass or be re-baselined with owner approval.
New live recordings have a cost that needs the owner's key and approval.
Current authorization and limits:
Proposed only; not authorized for implementation yet.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | The extractor keeps every order line of the email, including lines with no catalog product and lines with an unstated quantity | Phase 04 detection replay on test: no "no produced line" failure; unknown recall at least 95% |
| C2 | A non-integer quantity in the extractor answer no longer stops the run; it reaches detection as a doubtful quantity | Pytest test with a recorded non-integer answer; quantity recall at least 95% on the test replay |
| C3 | The extracted line text keeps the product words the customer wrote without a purpose or usage clause that no product name holds | Pytest test with the four purpose-clause lines; phase 04 ambiguous detection re-measured on the test replay, recall at least 95% |
| C4 | The phase 03 email extraction evaluation keeps passing its gates, and the phase 04 detection baseline is re-measured and its gates raised to what is met | `npm run eval` output |

## Plan
- [ ] Reproduce the ten failures above from the stored recordings.
- [ ] Choose between a prompt change and a schema change with the owner, then implement and re-record.

## Decisions
None yet.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Delivery
Pending.
