# 012 - Rename customer CLI-004 and five more customers

Status: proposed

## Contract
Goal and reason:
Customer CLI-004 "Policlinica Gipuzkoa Norte" resembles a real hospital.
The owner decided on 2026-10-15, during the phase 10 review, to rename it in a separate change, in the catalog, datasets and recordings.
The name appears in 22 tracked files: `src/purchase_cycle/catalog.py`, evaluation datasets and their source texts (email order extraction, order line extraction, clarification detection), audit CSVs in `evals/audit/`, and five recording files in `evals/recordings/`.
A web check of all 50 customers (2026-10-15) found five more names that match a real centre in the same city; the owner added them to this change.

| Code | Old name | New name |
|---|---|---|
| CLI-004 | Policlinica Gipuzkoa Norte | Clinica Demo Gipuzkoa |
| CLI-003 | Clinica Fisioterapia Deusto | Clinica Fisioterapia Demo Deusto |
| CLI-011 | Clinica Dental Ruzafa | Clinica Dental Demo Ruzafa |
| RES-003 | Residencia San Prudencio | Residencia Demo San Prudencio |
| FAR-009 | Farmacia Benimaclet | Farmacia Demo Benimaclet |
| FAR-010 | Farmacia Nervion | Farmacia Demo Nervion |

Out of scope:
The other 44 customers, suppliers and products; the contact, city, email and phone of the renamed customers; historical notes in `docs/plans/` that quote an old name as part of the decision record.
Constraints / decisions that need the owner:
- Recordings are keyed by a hash of the model input (`llm.recording_key`), and the names are part of the input text of at most 116 recorded calls, so the rename invalidates those entries; they are recorded again with the live model (decided).
Current authorization and limits:
Owner request on 2026-10-15 to open this change; delivery as branch `cambio-012-rename-cli004` and a PR to `main`.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `git grep -i -F` of each old name in the table above, and of the fragments "Gipuzkoa Norte" and "Fisioterapia Deusto", finds it only in the historical notes of `docs/plans/` and in this change | command output |
| C2 | The six customers have their new names in the catalog and the seeded database; their codes, contacts, cities, emails and phones are unchanged | test or `uv run purchase-cycle` query |
| C3 | Every dataset item and audit row that named one of the six customers names it with the new name, and nothing else in those files changes | `git diff --stat` and a diff restricted to the renamed lines |
| C4 | `npm run check` passes with the API keys unset; every evaluation in replay finds its recordings and gives the same figures as `main`, or any difference is recorded and explained | `npm run check` output and the eval figures before and after |

## Plan
- [x] Owner decisions on the new name, the recordings method and the scope.
- [x] Rename in `catalog.py`, datasets, source texts and audit CSVs through their generators where they exist, by hand only where the file is not generated.
- [x] Recordings per the owner's decision; eval figures before and after.
- [ ] `npm run check`, adversarial review, delivery.

## Decisions
- Owner, 2026-10-15 via Lavish: CLI-004 becomes "Clinica Demo Gipuzkoa".
- Owner, 2026-10-15 via Lavish: the affected recordings are recorded again with the live model (new API cost, small); any change in the evaluation figures is recorded and explained.
- Owner, 2026-10-15 via Lavish: review every customer name for resemblance to a real centre and propose a list to the owner before renaming; the scope of this change follows that list.
- Owner, 2026-10-15 via Lavish: rename the six customers with a same-name match in the same city (table above); keep the six weaker matches (CLI-002, CLI-010, RES-001, RES-005, RES-008, RES-015) and the 38 customers with no match unchanged.
- Coordinator, 2026-10-15: short forms in email subjects that also abbreviate a new name ("San Prudencio order", "Benimaclet order", "Ruzafa dental", "Deusto physio") stay; the two that keep the part of an old name the new name dropped become "Demo Gipuzkoa order" (CLD-0193) and "Order - Fisioterapia Demo Deusto" (EML-0279).
- Coordinator, 2026-10-15: `record` mode has no skip-existing option, so a scratch script outside the repo called the live model only for missing keys (121 calls, claude-haiku-4-5).
  Net effect on `evals/recordings/`: 118 old-name entries replaced by new-name entries, 1 new entry (the extraction call of EML-0270, reached now that intake classifies it as an order), and 2 intermediate entries for the two renamed subjects recorded and then removed; a replay of every suite confirmed no removed entry is requested.
- Evaluation figures (replay, test split): only `email_order_extraction` moved, because the new recording of EML-0270 now classifies it as an order (every metric of that suite moved with it: intake 98.7% to 99.1%, line recall 96.9% to 97.1%, SKU 97.3% to 97.4%, out-of-catalog detection 96.4% to 96.9%, exact match 94.4% to 94.9%, failures 11 to 10); this is model variation on a re-recorded input, not an effect of the name.
  The baseline check reports one gained case and no drop, so `evals/baselines/` is unchanged.
- Re-recorded answers that differ from the old ones without moving any figure: 23 entries, mostly a different `reason` wording; in the dev cases EML-0063 and EML-0219 `source_text` changed but SKUs and quantities did not, and the dev figures equal `main`.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | Opus subagent | origin/main..HEAD | contract C1-C4, decoded content of emails and attachments, recording keys, seed, evals test and dev | 3 minor, documentation counts and eval detail | fixed in this commit |

## Delivery
