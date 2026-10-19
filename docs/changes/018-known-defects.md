# 018 - Fix the known defects before phase 12

Status: implemented, adversarial review complete, delivery in progress

## Contract
Goal and reason:
The master plan decision of 2026-10-15 puts the known defects after phase 11 and before phase 12: goods receipt `failures resume` F6, the change 001 email extractor misses, claims CLM-044, CLM-066 and CLM-072, and documents DOC-060 and DOC-112.
This change fixes them in one branch so the orchestrator of phase 12 composes modules whose evaluations have no known product or grader defect.
It absorbs and supersedes `docs/changes/001-email-extractor-dropped-lines.md`: its ten failures and its criteria C1 to C4 are covered by the email group below, and change 001 is closed as superseded by 018 when this change is delivered.
The four defect groups, as diagnosed on 2026-10-19 from the stored recordings (no live call):
- F6: `cmd_failures_resume` in `src/purchase_cycle/cli.py` only refuses the claims channel; a `receipts` row falls through to `_clarify_graph`, which builds the email graph and prints the misleading "no checkpoint to resume found".
  The same fall-through reaches the `quotes` and `sales` rows, and every `reconcile` row is refused as a claim because `claims.CHANNEL` and `reconciliation.CHANNEL` are both `reconcile`, so a failed invoice extraction (step `document_extraction`) is told to use `reconcile approve|reject`.
- Email, phase 03 extractor seen through phase 04 detection (test replay: recall ambiguous 89.1%, unknown 94.3%, quantity 91.3%):
  (a) dropped lines CLD-0041 and CLD-0047 (conflicting extraction instructions in `llm.py`), live only;
  (b) a null quantity rejected by the strict `EmailLine.quantity` schema, CLD-0066, CLD-0123 and CLD-0137, offline;
  (c) a purpose clause copied into the line text, CLD-0083, CLD-0112, CLD-0160 and CLD-0165 (and dev CLD-0045), offline.
  Change 001 described (b) as a non-integer quantity; the recordings show a null quantity.
- Claims (claim_drafting replay 57 of 60, 95.0%, gate passed at the edge):
  CLM-066 and CLM-072 are a grader and fact check defect: `mentions`, `amount_stated` and the invented-amount name removal in `claims.py` match product text case-sensitively, so a capitalised product name is "missing" and its numbers ("2.5 cm") are read as invented amounts; offline.
  CLM-044 is a model miss: the draft omits the amount in dispute when it is 0.00; the product path already falls back to the template; live, by instructions.
- Documents (document_extraction replay, 2 of 90 critical errors):
  DOC-112: the model joins the SKU and the wrapped description in the product field; fixed offline by a shared resolver (deviation below) and live by an instruction to write only the code.
  DOC-060: the model keeps "- 8 pack" in the product with quantity 1; live, by instructions.

Out of scope:
- Email intake: CLD-0149 (the intake model drops a line) stays failing; the intake instructions do not change.
- Phase 03 hallucinated SKU SHARPS-15 on EML-0215 and EML-0237: logged, not fixed.
- The pre-existing dev split recall_unknown of 94.1% (n=17), below 95%: reported, not gated, not fixed.
- Code guards that hide a model miss (a template amount line for CLM-044, a pack-size parser for DOC-060): they lower the bar or overfit and are not added.
- The pending LangSmith traces of phases 02 to 10 (separate change after about 2026-11-05).

Constraints / decisions that need the owner:
- Live calls only in the single re-recording pass below, on Haiku, with a total spend cap of 6 USD including dev tuning.
- Stop and ask the owner if the cap would be exceeded, or if any absolute gate of any evaluation is below its frozen threshold after re-recording.
- Frozen criteria change upward only; nothing in this change lowers a threshold, a gate or a test.
- New dependencies: none.

Current authorization and limits:
Owner decisions of 2026-10-19 in the Lavish page `.lavish/cambio-018-decisiones.html` (git-ignored), the recommended option in all three (see Decisions).
Work on branch `cambio-018-known-defects` from `origin/main`; push it and open a PR to `main`; never merge.

### Acceptance criteria
Replay commands run with no API key and no network.
"Replay eval" means `uv run purchase-cycle eval --suite <suite> --mode replay --split test`.
Every pytest test named below is new or changed in this change and fails on `main` before its fix (reproduced first).

#### F6 and the same fall-through in other channels
| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | `failures resume` on a parked goods receipt thread (`goods-receipt-DOC-0003` from a receipts run) exits 1, its stderr names a goods receipt failure, its source and first error line, says it has no checkpoint and must be fixed by hand, ends with "nothing changed" and does not contain "no checkpoint to resume found"; the `failures` row stays `needs_review` and no checkpoint or table row changes | `uv run pytest tests/test_receipts_cli.py -q` with a test modelled on `tests/test_claims_cli.py` (refused claim resume) |
| C2 | The same fall-through is reproduced first for a parked `quotes` row (offer extraction or quote wait), a parked `sales` row and a parked non-claim `reconcile` row (step `document_extraction`); each confirmed case exits 1 with a message that names its module and the command that retries it, ends with "nothing changed", and leaves the row and the checkpoints unchanged; a case that does not reproduce is recorded in Delivery with its evidence | `uv run pytest tests/test_failures_cli.py -q` (or the module CLI test file), one test per confirmed channel; reproduction output in `.evidence/cambio-018/f6-repro.txt` |
| C3 | A parked claim row (step `claim_drafting`) keeps its current message pointing to `reconcile approve|reject`, and web form, email and WhatsApp threads keep resuming with `failures resume` exactly as before | Existing `tests/test_claims_cli.py`, `tests/test_failures_cli.py` and `tests/test_resume_cli.py` pass unchanged; `failure_recovery` replay eval exits 0 |
| C4 | The README `failures resume` text states which channels it resumes and what it answers for the others | README diff |

#### Email extractor (absorbs change 001)
| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C5 | (b) A null quantity in a recorded extractor answer no longer stops the run: it reaches detection and raises a quantity doubt on that line; the tool JSON schema sent to the model is unchanged | `uv run pytest tests/test_llm.py tests/test_clarification.py -q` with a recorded null-quantity answer (CLD-0123 shape); a test asserts `tool_definition(EMAIL_EXTRACTION)` equals the `main` value |
| C6 | (c) The extracted line text drops a trailing purpose or usage clause ("for the treatment room", "for our diabetic patients", "for after surgery") and keeps every catalog product name that contains "for" unchanged | `uv run pytest tests/test_email_order.py -q` with the four test lines CLD-0083, CLD-0112, CLD-0160, CLD-0165 and a test over every catalog name |
| C7 | (a) After the extraction instructions are tuned and re-recorded, the phase 04 detection replay shows no "no produced line" failure for CLD-0041 and CLD-0047 | Replay eval `clarification_detection`, failures list in `.evidence/cambio-018/replay-clarification_detection.txt` |
| C8 | Phase 04 detection on the test split after re-recording: recall ambiguous, unknown and quantity each at least 95%, or at least the value measured after the fix if lower but never below the current 49/55, 50/53 and 42/46; precision and false question rate gates unchanged (false question rate at most 5%) | Replay eval `clarification_detection` exits 0; `DETECTION_THRESHOLDS` in `clarification_eval.py`, the stored baseline `threshold` and the phase 04 spec gate text show the raised values |
| C9 | Phase 03 email extraction keeps its gates on the test split after re-recording: intake accuracy, line recall and line precision each at least 95%, and no significant drop against the old baseline (exact McNemar, p < 0.05) | Replay eval `email_order_extraction` exits 0; old-versus-new McNemar per email and per expected line in `.evidence/cambio-018/mcnemar-email.txt` |
| C10 | The email intake instructions are unchanged; CLD-0149 may still fail and is listed as known | `git diff origin/main -- src/purchase_cycle/llm.py` shows no change to `EMAIL_INTAKE_INSTRUCTIONS` |

#### Claims
| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C11 | The fact check and grader match product text case-insensitively in `mentions`, `amount_stated` and the invented-amount name removal: a draft that writes a capitalised product name passes, and "Zinc oxide tape 2.5 cm" written with other capitals raises no "invented amount 2.50" | `uv run pytest tests/test_claims.py -q` with both cases; SKU matching keeps its token boundaries (existing D13 tests pass) |
| C12 | Regraded from the current recordings before any re-recording, CLM-066 and CLM-072 pass and no other case changes (59 of 60) | Replay eval `claim_drafting` output in `.evidence/cambio-018/claims-regrade.txt` |
| C13 | After the claim instructions are tuned and re-recorded, CLM-044 states the amount in dispute (0.00 EUR) in a sentence with the word dispute, CLM-066 and CLM-072 still pass, the case pass rate is at least 95%, no critical error, and no significant drop against the old baseline (exact McNemar) | Replay eval `claim_drafting` exits 0; McNemar in `.evidence/cambio-018/mcnemar-claims.txt`; replay eval `claim_approval` stays 35 of 35 |

#### Documents
| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C14 | One shared resolver maps a product text that is the match key of a catalog SKU followed by the match key of the same product name (or the reverse) to that SKU; it is used by both `reconciliation.py` and `document_eval.py`, and a text that joins one SKU with another product's name is not resolved | `uv run pytest tests/test_reconciliation.py tests/test_document_eval.py -q`; regraded from the current recordings only the three DOC-112 lines change (1 of 90 critical) |
| C15 | After the document instructions are tuned (the number and unit after a product is the quantity; write only the code in the product field) and re-recorded, DOC-060 and DOC-112 are exact matches, document exact match and unit price accuracy are each at least 95%, critical errors are 0 of 90, and no significant drop against the old baseline (exact McNemar) | Replay eval `document_extraction` exits 0; McNemar in `.evidence/cambio-018/mcnemar-documents.txt`; replay evals `reconciliation_matching` and `sales_reconciliation` exit 0 |

#### Re-recording, baselines and documentation
| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C16 | One combined live pass: dev split tuning first, at most 3 rounds per task (email extraction, documents, claims), then one test-split record per affected suite and the demo recordings; total spend at most 6 USD, with calls, tokens and USD per task logged | `.evidence/cambio-018/live-cost.txt`; Plan lists the rounds used |
| C17 | Baselines of `email_order_extraction`, `clarification_detection`, `document_extraction` and `claim_drafting` are reset from the test-split record, each with the old-versus-new McNemar comparison written in Delivery; no other baseline file changes | `git diff --stat origin/main -- evals/baselines/` |
| C18 | Recordings hold no stale rows for the old prompts, and every recording file whose key includes a changed prompt is re-recorded: `email_order_extraction`, `clarification_detection_email_extraction`, `order_scenarios`, `orders_demo`, `studio`, `document_extraction`, `reconcile_demo`, `claim_drafting`, `claim_demo`; intake recordings are reused where the harness allows it, otherwise their re-record counts against the cap | `npm run eval` exits 0 with no missing recording; a one-off key check (outside `src/`) shows no orphan key, output in `.evidence/cambio-018/recordings-keys.txt` |
| C19 | Demo outputs and every README transcript, figure or defect mention that changes (README sections on email extraction, clarification detection, reconcile demo, document extraction and claims; `docs/security.md`; the DOC-060 and DOC-112 mentions in the phase 09, 10 and 11 specs are history and stay) are updated to the new run; the flow animations are conceptual and are re-recorded only if a figure they show changes, and the change 017 reconcile animation keeps its approved -25.00 EUR story | README and docs diff; `git grep -n "DOC-060\|DOC-112\|CLM-044\|CLM-066\|CLM-072\|89.1\|94.3\|91.3"` reviewed in Delivery; `git diff --stat origin/main -- docs/assets/flow-animation/` empty or justified |
| C20 | `npm run check` exits 0 on the final commit, with every evaluation in replay mode, no key and no `.env` | Check output in `.evidence/cambio-018/npm-check.txt` |

## Plan
Commits, one per defect group, then the re-record commit; each group lists the files it owns so implementation agents can work on disjoint files.
`src/purchase_cycle/llm.py` is shared by three groups (email schema and instructions, document instructions, claim instructions): its edits are made serially by one agent, each in its own group commit.

- [x] 1. Status line of change 001 set to superseded by 018 (first commit of the branch, with the merged-PR status updates of the flow).
- [x] 2. F6 group (C1 to C4), offline: reproduce each channel, then guard in `src/purchase_cycle/cli.py`; tests in `tests/test_receipts_cli.py`, `tests/test_failures_cli.py` (and the quotes, sales and reconcile CLI test files if a case lives there); README `failures resume` paragraph.
- [x] 3. Email offline group (C5, C6): `EmailLine.quantity` before-validator in `src/purchase_cycle/llm.py`; purpose-clause strip in the extract node of `src/purchase_cycle/email_order.py`; tests in `tests/test_llm.py`, `tests/test_email_order.py`, `tests/test_clarification.py`; simulated replay figures recorded here.
- [x] 4. Claims offline group (C11, C12): `src/purchase_cycle/claims.py`; tests in `tests/test_claims.py`; regrade evidence.
- [x] 5. Documents offline group (C14): shared resolver next to `match_key` (one function, no new module unless needed), used by `src/purchase_cycle/reconciliation.py` and `src/purchase_cycle/evaluation/document_eval.py`; tests in `tests/test_reconciliation.py`, `tests/test_document_eval.py`.
- [x] 6. Instruction tuning on dev (C7, C13, C15, C16), at most 3 rounds per task: `EMAIL_EXTRACTION_INSTRUCTIONS`, `SUPPLIER_DOCUMENT_INSTRUCTIONS`, `CLAIM_DRAFTING_INSTRUCTIONS` in `src/purchase_cycle/llm.py`; one commit per task with its dev figures and cost.
  Done live on Haiku, dev split, one round per task, 0.86 USD of the 6 USD cap (log in `.evidence/cambio-018/live-cost.txt`).
  Email (`3f3fb0c`): a product the customer asks to source or to quote stays a line; dev line recall 98.0%, line precision 98.8%, intake accuracy 98.7% (live intake flipped EML-0022), detection recall unknown 100.0% (17), ambiguous 100.0%, quantity 95.8%; replay before: recall 98.8%, precision 98.8%, unknown 94.1%.
  Documents (`7801053`): write only the code, a number with a unit after the product is the quantity; dev exact match 100.0%, critical 0 of 30, unit price 100.0% (the dev split holds no DOC-060 or DOC-112 shape, the test record decides).
  Claims (`87c0b3a`): amount in dispute in a sentence with the word dispute, also when 0.00 EUR; dev case pass rate 100.0% (20 of 20), no critical error.
- [x] 7. Re-record commit (C8, C9, C13, C15 to C18): test-split records with `--set-baseline` per suite, demo recordings, stale-row pruning, phase 04 thresholds raised in `src/purchase_cycle/evaluation/clarification_eval.py` and the phase 04 spec gate text; owner check if the cap or a gate fails.
  Done 2026-10-19, 2.142 USD of the 6 USD cap (log in `.evidence/cambio-018/live-cost.txt`), no gate failed.
  Test-split records (baselines reset): email extraction intake 99.1%, line recall 98.0%, line precision 98.6% (live 193, reused 234); detection recall ambiguous 100.0% (55), unknown 98.1% (53), quantity 100.0% (46), false question rate 0 of 48, CLD-0041 and CLD-0047 now pass, failures left CLD-0149, CLD-0107, CLD-0123-L2 (live 74, reused 279); documents exact match 100.0%, unit price 100.0%, critical 0 of 90 (live 90); claims 60 of 60 (live 60); `claim_approval` 35 of 35, `failure_recovery` 34 of 34.
  Demos: orders-demo live 1 reused 9, `order_scenarios` live 10 reused 96 (baseline unchanged), reconcile-demo 19 live (also `claim_demo`), Studio sample email 1 live row.
  Studio needed exactly one row: the plain first email extraction of the sample in `studio.jsonl` (also read by `tests/test_ui.py`); the stale one was pruned.
  Recordings audit (`recordings-keys.txt`): 369 + 169 stale rows pruned (first pass on the catalog prompt, second pass after adding the empty catalog that the document and claim evals send), 4351 rows left, 0 stale, 0 orphan.
  Old-versus-new exact McNemar on the test split, against the baselines of 636711e (`mcnemar-*.txt`): email intake lost 0 gained 1 (p=1.00), line recall lost 2 gained 8 (p=0.11); detection recall lost 0 gained 12 (p<0.01, an improvement); documents exact match lost 0 gained 2 (p=0.50), unit price lost 0 gained 1 (p=1.00); claims case pass rate lost 0 gained 3 (p=0.25); no significant drop anywhere.
  Phase 04 detection thresholds raised to 95% in `DETECTION_THRESHOLDS`, in the stored baseline `threshold` and in the phase 04 spec gate text; `tests/test_report_format.py` pinned the old levels (89.1, 94.3, 91.3) and was updated.
  Offline: full pytest 1625 passed (`pytest-full.txt`), ruff check and format clean.
  Deviation (cost-saving, same precedent as DV-05): the test and demo records ran through one-off scratchpad scripts, none committed: `rec.py` (a reuse wrapper that replays an answer whose recording key already exists in any recording file and calls the model only for new keys), `recstudio.py` (records the one Studio sample row live), `keys.py` (key audit and prune of stale rows) and `keys2.py` (a second pass for the empty catalog that the document and claim evals send); the figures above are therefore the same as a record that keeps the answers of unchanged prompts.
  Disclosure (test-informed wording): the new rule in `EMAIL_EXTRACTION_INSTRUCTIONS` (`llm.py`, `3f3fb0c`) was worded from the diagnosed test misses CLD-0041 and CLD-0047 and then checked on dev (dev recall for unknown products 94.1% in a replay before the tuning, 100.0% live after it); the test detection recall gain for that shape is therefore not a clean held-out estimate.
  Disclosure (test-informed wording, documents): the document instructions (`7801053`) target the test misses DOC-060 and DOC-112 and the dev split holds no such shape, so the test result (0 of 90 critical errors, exact match gain) is not a clean held-out estimate.
  Disclosure (test-informed wording, claims): the claim instructions were changed because of the test miss CLM-044 (amount in dispute stated also when 0.00 EUR); dev was 20 of 20 before and after, so the test result (60 of 60) is not a clean held-out estimate.
  Deviation (LangSmith): the first email record run logged one LangSmith experiment; the clarification record hit a "dataset not found" error and was re-run with `LANGSMITH_API_KEY` empty, as were all later live runs.
- [x] 8. Documentation commit (C19): README, `docs/security.md`, master plan status line; animations only if C19 requires it.
  README: email extraction, detection (thresholds 95%, figures, remaining misses CLD-0149, CLD-0107, CLD-0123-L2), document extraction (0 of 90 critical) and claim drafting (60 of 60) tables and prose, the phase 08 limitation lines of the receipt and sales sections, and the Limits lines; `docs/security.md` SEC-022 no longer names DOC-060 and DOC-112; master plan decision row added.
  README transcripts of the orders, reconcile and claim demos were compared with the replayed demo output; the lines that changed were updated, the rest are unchanged.
  Flow animations (`docs/assets/flow-animation/`): unchanged, they show no figure or output that changed, and the change 017 reconcile animation keeps its -25.00 EUR story.
  Known and not fixed stay logged: CLD-0149, SHARPS-15 on EML-0215 and EML-0237; the dev split is not replayed (recordings not kept), its recall_unknown was 100.0% (17) in the live tuning run.
- [x] 9a. `npm run check` (C20) on the final commit: exit 0, ruff clean, 1626 pytest passed in 717.44 s, every evaluation in replay with all gates PASS, no key and no `.env`; output in `.evidence/cambio-018/npm-check.txt`.
- [ ] 9. Adversarial review, at most two rounds, push and PR to `main`.

## Decisions
Owner decisions of 2026-10-19 in `.lavish/cambio-018-decisiones.html`, the recommended option in all three:
1. Live calls approved for one single re-recording pass covering the email extraction instructions (not intake; CLD-0149 may stay failing), the document instructions (DOC-060, and DOC-112 by instruction) and the claim instructions (CLM-044); budget about 3 to 6 USD on Haiku including the dev tuning rounds, capped here at 6 USD.
2. Frozen criteria may change, upward only: the phase 03 email extraction instructions change with a new measured baseline; the phase 04 detection thresholds are raised to 95% or to the measured value if lower; the DOC-112 catalog matching extension (shared resolver) is recorded as a deviation in this change document.
3. The same `failures resume` fall-through in quotes, sales and non-claim reconcile channels is reproduced and, if confirmed, fixed here without breaking any legitimate resume path.
Also: the phase 03 hallucinated SKU SHARPS-15 (EML-0215, EML-0237) is logged separately, not fixed.

### Deviation DV-018-1: DOC-112 catalog matching extension
Phase 08 spec says documents are linked with "the existing catalog matching of phase 02" (exact match key).
The shared resolver adds one fallback: a product text whose match key is the SKU key followed by the same product's name key (or the reverse) resolves to that SKU.
Reason: the model joins a SKU and its wrapped description on DOC-112; the fallback cannot map a text to a different product, so it does not lower the bar.
Affects phase 08 C12 (document extraction figures) and the reconciliation path; approved by the owner on 2026-10-19 (decision 2).

### Known but not fixed
- CLD-0149: email intake drops a line (intake model error); intake is out of scope.
- SHARPS-15: hallucinated SKU in phase 03 on EML-0215 and EML-0237; to log as its own change.
- Dev split recall_unknown is now 100% live on dev (n=17, after the tuning); it was 94.1% in a replay before the tuning (100.0% is the only live figure) and is reported only.
- The dev replay of `email_order_extraction` and `clarification_detection` is no longer available: the old-prompt dev rows were pruned and the dev tuning ran live without recording.
- `PURPOSE_CLAUSE` in `email_order.py:30` cuts at the first "for the|our|...|<word>ing", so an out-of-catalog text such as "covers for the infusion pump" becomes "covers".
- On the phase 03 email demo path without clarification, a null quantity is now stored as 1 with no doubt; the clarification path raises the quantity doubt.

### Offline fix results (steps 3 to 5, 2026-10-19)
Replay evals on the test split with the three offline fixes and the old recordings, evidence in `.evidence/cambio-018/` (git-ignored); no baseline changed.
- Email, `clarification_detection-test` (`detection-replay.txt`): recall ambiguous 100.0% (55), quantity 100.0% (46), unknown 94.3% (53); precision_quantity 97.9% (one extra quantity doubt on CLD-0107); false question rate 0 of 48; gate PASS.
  Remaining failures: CLD-0041 and CLD-0047 (dropped lines, live fix in step 6) and CLD-0149 (known).
- Email, `email_order_extraction-test`: intake accuracy 99.1%, line recall 97.1%, line precision 99.0%, no significant drop against baseline 2026-10-07; gate PASS, 10 failures listed (including SHARPS-15 on EML-0215 and EML-0237, logged).
- Claims, `claims-regrade.txt` (C12): `claim_drafting` case pass rate 98.3% (59 of 60), only CLM-044 fails; `claim_approval` 35 of 35.
- Documents, `document_extraction-test` (C14): exact match 98.9%, unit price accuracy 100.0%, critical errors 1 of 90 (only DOC-060 left); only the DOC-112 lines changed.
- `failure_recovery` replay: 34 of 34, exit 0 (C3).
- Dev split: `clarification_detection-dev` recall_unknown 94.1% (n=17), the pre-existing failure, exit 1; `email_order_extraction-dev` exit 0.
- `tests/test_report_format.py` detection report test updated: recall ambiguous and quantity now meet the 95% target, unknown does not.
- Full pytest 1625 passed, ruff check and ruff format --check clean (`pytest-offline.txt`).

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | local | 17d938a | correctness, tests, docs | 1 important (sales `invoice_file` row given the wrong retry command), 1 important (README document baseline stale), 1 important (test-informed wording not disclosed), 3 minor (change doc pointers and delivery, C18 wording, README misses wording), 2 minor (known limits: purpose clause, null quantity on phase 03 path) | fixed |
| 2 | local | 4976b47..ea1e06e | correctness, docs | 2 important (README documents and claims said dev-only tuning, but the document instructions target test misses and the claim instructions changed after the test miss CLM-044), 2 minor (94.1% came from a replay, only 100.0% is live; Delivery commands used stale local `main`) | fixed, final round |

Round 1 resolutions:
- `invoice_file` row of channel `sales`: `cmd_failures_resume` now advises "fix or remove the file and run purchase-cycle sales invoice again"; case added to the parametrized test in `tests/test_failures_cli.py`.
- README document baseline sentence rewritten: mode record, measured 2026-10-19, 90 answers recorded live in change 018.
- Test-informed wording disclosed in step 7 above and in the README detection paragraph; `llm.py` unchanged.
- Change doc: status, C7 and C20 evidence pointers, Delivery, known-but-not-fixed list and C18 deviation wording corrected.
- README misses wording: CLD-0107 and CLD-0123-L2 are precision errors, CLD-0149 is the only recall miss.
- Purpose-clause cut and null quantity on the phase 03 path added to the known-but-not-fixed list and to README Limits.

Round 2 resolutions (final round, no third round):
- README and step 7 now disclose that the documents test result (0 of 90 critical, exact match gain) and the claims test result (60 of 60) are not clean held-out measures; the claims sentence no longer says the instructions were unchanged after the test run.
- The 94.1% figure is worded as a replay figure before the tuning; 100.0% is the only live figure (README detection paragraph, step 7, known-but-not-fixed list).
- Delivery commands C10, C17 and C19 use `origin/main` as base after `git fetch origin`: `git diff origin/main -- src/purchase_cycle/llm.py` has no change to `EMAIL_INTAKE_INSTRUCTIONS`; `git diff --stat origin/main -- evals/baselines/` lists only the four baselines (claim_drafting, clarification_detection, document_extraction, email_order_extraction: 381 insertions, 279 deletions); `git diff --stat origin/main -- docs/assets/flow-animation/` is empty.

## Delivery
Exact McNemar, old baseline of 636711e versus the re-recorded test split (`mcnemar-*.txt`):
- Email: intake accuracy lost 0 gained 1 (p=1.00), line recall lost 2 gained 8 (p=0.11).
- Detection: recall lost 0 gained 12 (p<0.01, an improvement).
- Documents: exact match lost 0 gained 2 (p=0.50), unit price accuracy lost 0 gained 1 (p=1.00).
- Claims: case pass rate lost 0 gained 3 (p=0.25).
C19 grep (`git grep -n "DOC-060\|DOC-112\|CLM-044\|CLM-066\|CLM-072\|89.1\|94.3\|91.3"`): the README matches are the two defect-history sentences (document and claim baselines) and are correct; the remaining hits are history in earlier plans, specs and change documents, datasets, recordings, baselines (substring matches of other numbers) and animation colour codes, left as they are; the flow animations are unchanged.
Push and PR: pushed to origin, PR https://github.com/mikelju/full-purchase-cycle-agents/pull/29 open to `main`, not merged.
Done means: every criterion met with evidence in `.evidence/cambio-018/` (git-ignored), `npm run check` exits 0, at most two adversarial review rounds, branch pushed and PR to `main` open, not merged.
