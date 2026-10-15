# 012 - Rename customer CLI-004

Status: proposed

## Contract
Goal and reason:
Customer CLI-004 "Policlinica Gipuzkoa Norte" resembles a real hospital.
The owner decided on 2026-10-15, during the phase 10 review, to rename it in a separate change, in the catalog, datasets and recordings.
The name appears in 22 tracked files: `src/purchase_cycle/catalog.py`, evaluation datasets and their source texts (email order extraction, order line extraction, clarification detection), audit CSVs in `evals/audit/`, and five recording files in `evals/recordings/`.
Out of scope:
Other customers, suppliers and products; the contact, city, email and phone of CLI-004; historical notes in `docs/plans/` that quote the old name as part of the decision record.
Constraints / decisions that need the owner:
- The new name (open).
- Recordings are keyed by a hash of the model input (`llm.recording_key`), and the name is part of the input text of about 15 recorded calls, so a rename invalidates those entries.
  Either they are recorded again with the live model (new cost, small) or rewritten by a script (no cost, but the stored outputs were produced for the old text) (open).
- Whether to review the rest of the customer names for resemblance to real centres (open; default: only CLI-004).
Current authorization and limits:
Owner request on 2026-10-15 to open this change; delivery as branch `cambio-012-rename-cli004` and a PR to `main`.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `git grep "Gipuzkoa Norte"` finds the old name only in the historical notes of `docs/plans/` and in this change | command output |
| C2 | CLI-004 has the new name in the catalog and the seeded database; its code, contact, city, email and phone are unchanged | test or `uv run purchase-cycle` query |
| C3 | Every dataset item and audit row that named CLI-004 names it with the new name, and nothing else in those files changes | `git diff --stat` and a diff restricted to the renamed lines |
| C4 | `npm run check` passes with the API keys unset; every evaluation in replay finds its recordings and gives the same figures as `main`, or any difference is recorded and explained | `npm run check` output and the eval figures before and after |

## Plan
- [ ] Owner decisions on the new name, the recordings method and the scope.
- [ ] Rename in `catalog.py`, datasets, source texts and audit CSVs through their generators where they exist, by hand only where the file is not generated.
- [ ] Recordings per the owner's decision; eval figures before and after.
- [ ] `npm run check`, adversarial review, delivery.

## Decisions

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|

## Delivery
