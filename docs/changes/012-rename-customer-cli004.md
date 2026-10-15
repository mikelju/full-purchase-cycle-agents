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
| C1 | `git grep -F` of each old name in the table above finds it only in the historical notes of `docs/plans/` and in this change | command output |
| C2 | The six customers have their new names in the catalog and the seeded database; their codes, contacts, cities, emails and phones are unchanged | test or `uv run purchase-cycle` query |
| C3 | Every dataset item and audit row that named one of the six customers names it with the new name, and nothing else in those files changes | `git diff --stat` and a diff restricted to the renamed lines |
| C4 | `npm run check` passes with the API keys unset; every evaluation in replay finds its recordings and gives the same figures as `main`, or any difference is recorded and explained | `npm run check` output and the eval figures before and after |

## Plan
- [x] Owner decisions on the new name, the recordings method and the scope.
- [ ] Rename in `catalog.py`, datasets, source texts and audit CSVs through their generators where they exist, by hand only where the file is not generated.
- [ ] Recordings per the owner's decision; eval figures before and after.
- [ ] `npm run check`, adversarial review, delivery.

## Decisions
- Owner, 2026-10-15 via Lavish: CLI-004 becomes "Clinica Demo Gipuzkoa".
- Owner, 2026-10-15 via Lavish: the affected recordings are recorded again with the live model (new API cost, small); any change in the evaluation figures is recorded and explained.
- Owner, 2026-10-15 via Lavish: review every customer name for resemblance to a real centre and propose a list to the owner before renaming; the scope of this change follows that list.
- Owner, 2026-10-15 via Lavish: rename the six customers with a same-name match in the same city (table above); keep the six weaker matches (CLI-002, CLI-010, RES-001, RES-005, RES-008, RES-015) and the 38 customers with no match unchanged.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Delivery
