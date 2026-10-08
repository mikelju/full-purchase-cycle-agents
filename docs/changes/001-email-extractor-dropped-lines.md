# 001 - Email extractor: dropped lines and non-integer quantities

Status: proposed

## Contract
Goal and reason:
The phase 03 email extractor loses order lines in two ways that phase 04 detection cannot see.
It drops some email lines, so an unknown or doubtful quantity line has no produced line to raise a doubt on.
It returns a non-integer quantity for some emails, the schema rejects the whole answer and the run stops.
Measured in phase 04 deviation 04.1 (detection replay, test split, 2026-10-08): unknown recall 94.3% [84.6, 98.1] n=53 with CLD-0041, CLD-0047 and CLD-0149 dropped; quantity recall 91.3% [79.7, 96.6] n=46 with CLD-0123 and CLD-0137 rejected by the schema; CLD-0066 is also rejected by the schema.
The owner set the phase 04 unknown and quantity thresholds at the measured level and asked for this separate change (decision Q2=A, 2026-10-08).
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
| C3 | The phase 03 email extraction evaluation keeps passing its gates | `npm run eval` output |

## Plan
- [ ] Reproduce the five failures above from the stored recordings.
- [ ] Choose between a prompt change and a schema change with the owner, then implement and re-record.

## Decisions
None yet.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Delivery
Pending.
