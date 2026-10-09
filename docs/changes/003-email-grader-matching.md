# 003 - One-to-one matching in the email grader

Status: ready locally

## Contract
Goal and reason:
An external audit of 2026-10-08 found two ways `email_eval.grade` can give credit the output did not earn.
Out-of-catalog lines were compared only by how many lines had no SKU, so a line with the wrong product, quantity or source still counted as detected and the email could pass `email_exact_match`.
Catalog lines were scored with `hits[key] > 0` on every expected line without consuming the hit, so two identical expected lines and a single produced one scored a line recall of 2 out of 2.
Out of scope:
Grader and dataset versioning, run manifests and any other suite; they are separate items of the audit.
The failing `test_versioned_dataset_is_the_build_of_the_versioned_texts`, which also fails on `main` (XLSX bytes differ across platforms) and is logged below.
Constraints / decisions that need the owner:
None; gated metrics and thresholds are unchanged.
Current authorization and limits:
Owner request of 2026-10-09: a dedicated branch and a PR to `main` for review.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | Two identical expected catalog lines and one produced line give line recall `[True, False]` and one precision hit | `test_one_produced_line_satisfies_one_expected_line` |
| C2 | SKU-only credit goes only to produced lines not used by an exact match | `test_sku_only_credit_goes_to_produced_lines_left_after_exact_matches` |
| C3 | An out-of-catalog line with the wrong quantity, product text or source fails `out_of_catalog_detection` and `email_exact_match` | `test_out_of_catalog_lines_must_keep_product_quantity_and_source` (three cases) |
| C4 | Out-of-catalog lines still match when `source_text` copies the requested text with table columns, case or spacing changes | `test_out_of_catalog_lines_match_text_copied_with_table_columns` |
| C5 | An extra invented out-of-catalog line fails detection | `test_an_invented_out_of_catalog_line_fails_detection` |
| C6 | The replay evaluation report on the test split is unchanged on the recorded answers | `diff` of `npm run eval` output before and after |

## Plan
- [x] Consume matches one to one for catalog lines, exact matches first and SKU-only matches after.
- [x] Pair out-of-catalog lines one to one on source, quantity and the requested text contained in `source_text`.
- [x] Add grader tests for duplicates, wrong fields, copied table text and invented lines.

## Decisions
An out-of-catalog line matches when `source` equals the expected `location`, `quantity` equals `expected_quantity` and the normalised expected `text` is contained in the normalised `source_text`.
Containment and not equality, because the model copies the source line verbatim, often with the quantity and unit columns of a table.
`field_quantity` now equals the one-to-one `line_recall` of each row; before, it was `key in produced`, which was the same value whenever an email had no repeated SKU.
The result gains `unmatched_hits`, the number of expected out-of-catalog lines paired with a produced one.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | local | this change | correctness, metric drift | none open; the current dataset has no repeated SKU in an email, so C6 holds | closed |

## Delivery
C1-C5: `uv run pytest -q tests/test_email_eval.py` passes 18 tests.
C6: the full `npm run eval` output is identical byte for byte with and without the change on 2026-10-09, so the published figures keep their values under the corrected grader.
`uv run ruff check` and `uv run ruff format --check` pass.
`uv run pytest -q`: 329 passed, 1 failed; the failure is `test_versioned_dataset_is_the_build_of_the_versioned_texts` on EML-0003 and fails identically on `main` in this Linux environment (the audit attributes it to the XLSX zip platform marker).
