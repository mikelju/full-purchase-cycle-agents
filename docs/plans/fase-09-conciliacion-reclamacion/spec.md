# Phase 09 - Reconciliation, claim: specification

Status: in review
Approved by the owner: -
Master plan: `../0_plan_maestro.md`

## Goal
For every supplier invoice that phase 08 left with result `discrepancies`, the reconciliation module drafts one claim email to the supplier from the stored differences.
Claude Haiku 4.5 writes the body of the claim, and deterministic code checks that it cites every difference, the right references and no amount that is not in the facts; a draft that fails the check falls back to a fixed template.
A person approves the draft, approves it with an edited text or rejects it from the command line, after a pause with saved state that survives a process restart.
An approved claim is written as an `.eml` to the supplier outbox, as the phase 07 purchase order is; a rejected claim sends nothing.
One command runs the module end to end, from the document scan to the decided claims, in replay with no key, no network and no cost.
It is needed now because phase 08 detects and classifies differences but nothing acts on them, and phase 11 will reuse this claim and approval pattern for customer responses and credit notes on the sales side.
It shows the master plan capabilities "human-in-the-loop", "persistent state" and "structured evaluation" in the reconciliation module.

## Scope
Each decision id points to "Open decisions"; the scope and criteria are written for the recommended option of each.
In:
- Claim candidates: every `reconciliations` row with result `discrepancies` and no claim yet; `matched`, `awaiting_delivery_note` and unlinked documents get no claim, and an invoice that moves from `awaiting_delivery_note` to `discrepancies` on a later scan becomes a candidate then.
- Claim content (D5): one claim per invoice covering all its `discrepancies` rows; for each difference the SKU, the type, the expected and found values and the amount; the amount in dispute, which is the sum of the differences that make us pay more (positive price and quantity amounts and extra lines); differences in the supplier's favour and missing lines are listed for correction and not added to the amount; the requested action is a corrected invoice or a credit note.
- Claim drafting (D1):
  - A new model task `claim_drafting` through the existing client in live, record and replay modes with Claude Haiku 4.5, one call per claim, with the invoice number, purchase order number, supplier name, the discrepancy rows and the amount in dispute as structured facts in the prompt; the answer is the email body only.
  - Recipient, subject and sender are set by code, not by the model: the supplier email from `suppliers`, and a fixed English subject with the invoice and purchase order numbers.
  - Deterministic fact check in code after drafting: the body names the invoice and purchase order numbers, every discrepancy SKU and amount, the amount in dispute and the requested action, contains no money amount outside the facts (compared within half a cent), and is ASCII text under a fixed length.
  - A draft that fails the schema check after the client retries, or fails the fact check, is replaced by a fixed English template filled from the same facts, stored with source `template`, and writes a `failures` row with status `needs_review` and step `claim_drafting`; the other claims go on.
- Storage: a new table `claims`, one row per reconciliation: reconciliation, recipient, subject, draft body, draft source (`model` or `template`), final body, whether it was edited, status (`awaiting_approval`, `sent`, `rejected`), drafted and decided times from the simulated clock, and the outbox file; the phase 08 tables are not altered and `reconciliations.result` keeps its values (D4).
- Claim graph, a new LangGraph subgraph `claim` with the SQLite checkpointer and its own thread per invoice, `claim-INV-NNNN`:
  - `draft`: drafts and stores the claim, or reads back a stored draft so a re-run calls no model.
  - `review`: pauses with `interrupt` until the person answers approve, approve with an edited text, or reject (D2).
  - `send`: writes the final text as an `.eml` to the supplier outbox and sets `sent`; `close`: sets `rejected` and writes nothing (D3).
  - Side-agnostic review (D6): `review` receives a message (recipient, subject, body) and returns the decision and the final body, with no supplier or invoice logic, so phase 11 can reuse it for customer messages; no sales-side table, column or command is added.
- Commands (D7), in the existing `reconcile` group:
  - `reconcile claims` drafts a claim for every candidate, prints each draft with its facts and source, and leaves it `awaiting_approval` with the thread paused; a second run drafts nothing and calls no model.
  - `reconcile approve INV-NNNN` sends the draft as it is; `reconcile approve INV-NNNN --body-file FILE` sends the text of that file instead and keeps the original draft; `reconcile reject INV-NNNN` closes the claim as `rejected`.
  - `reconcile list` and `reconcile show INV-NNNN` add the claim status and, in `show`, the draft and final text, only for invoices that have a claim, so their phase 08 output is unchanged.
- Full module demo (D7): `reconcile-demo` gains the claim steps after the phase 08 steps, each in its own process: draft the claims, then a scripted operator approves one, approves one with an edited text from a versioned file and rejects one.
- Two new evaluations inside `npm run check` next to the fourteen existing ones: `claim_drafting` in replay and the deterministic `claim_approval` (D8).
- README sections for the claim step, the commands, the outbox email, the extended demo, the evaluations and the known limitations.

Out:
- Any sales-side feature: customer reconciliation, customer responses, credit notes to customers and their approval are phase 11.
- Supplier replies to a claim, follow-ups, reminders, negotiation, receiving a credit note or a corrected invoice, and re-reconciling after it.
- Payment holds, payment, accounting entries and a change of the invoice or purchase order status.
- Stock updates from the delivery note and serving customer orders, which are phase 10.
- Real email sending or receiving; editing the recipient or subject.
- Re-drafting a rejected claim or drafting a new claim for an invoice that already has one.
- The orchestrator, which is phase 12.
- Tuning the phase 08 `document_extraction` critical errors on DOC-060 and DOC-112, which is a separate change.
- Changes to the phase 01 to 08 prompts, recordings, datasets, baselines and gates, and to the phase 08 graph and matching.

### Evaluation design
- `claim_drafting` (D8, D9), built with a seeded planning script, automatic validation of every case and a clean-context second-pass review:
  - At least 60 cases, each a set of structured facts (supplier, invoice and order numbers, discrepancy rows and amount in dispute) made from real catalog SKUs and prices and run through the phase 08 `classify` function, so every case is a difference phase 08 can produce.
  - Ten categories of at least 6: price above the order, price below the order, quantity above the order, invoiced above delivered, extra line, unresolved extra line with free product text, missing line, price and quantity on one line, several types over several SKUs, and amounts with many decimals or thousands.
  - Fixed stratified split of about 25% development and 75% test, as in phases 03 to 08; only the development split tunes the instructions, and published figures and gates come from the test split.
- Metrics and grader for `claim_drafting`, all deterministic, scored on the model draft before any template fallback:
  - Reference accuracy (invoice and order numbers), discrepancy coverage (share of discrepancy rows whose SKU and amount appear), amount in dispute accuracy, requested action present, invented amount rate (drafts with a money amount outside the facts), and case pass rate (every check right).
  - Amounts are read from the body by a fixed pattern that accepts a decimal point or comma and an optional EUR sign or word, and compared within half a cent.
  - Critical errors: a missing discrepancy, a wrong or invented amount, a wrong amount in dispute, or a wrong invoice or order number, since a sent claim with them would be wrong.
  - Every metric with a 95% Wilson interval on the test split, globally and per category, with its counting unit, and the Wilson upper bound for a zero-event rate, as phase 05 set.
- Gates for `claim_drafting`:
  - Absolute gate in the test split on case pass rate of at least 95%, if the baseline measured in this phase with the final tuned instructions reaches it; otherwise execution stops and the owner decides, recorded as a deviation.
  - Tuning ends before the test split is recorded; the instructions do not change after the test baseline is measured.
  - Regression gate by exact McNemar test per case against the stored baseline, failing on a significant drop (p < 0.05).
  - Critical errors reported with their rate and Wilson upper bound and a target of 0 fixed before measuring, reported as met or not.
- `claim_approval` (D8): at least 24 hand-built scripted scenarios, versioned with expected final states, run through the CLI functions on a temporary database, with the drafts given by a test double so no model is called.
  - Coverage: at least 2 each for a claim drafted for a `discrepancies` invoice, no claim for `matched`, `awaiting_delivery_note` and unlinked documents, an invoice that becomes `discrepancies` after a later delivery note, a model draft that fails the fact check and falls back to the template, approve, approve with an edited text, an empty edited text refused, reject, a second approve or reject, a command on an invoice with no claim, a second `reconcile claims`, and a process restart between draft and decision.
  - Grader: deterministic, reads the database, the supplier outbox, the `failures` table and the printed output.
  - Critical errors per scenario: an email for a rejected or undecided claim; more than one email or claim per invoice; a sent text that differs from the approved text (the edited text when one was given); a claim for an invoice with no discrepancies; a claim status that does not match the outbox; a model call on a re-run or a resumption.
  - Gates: scenario success 100% and critical errors 0, since the flow is deterministic, with the phase 05 report columns.

## Acceptance criteria
Frozen on approval. Changing them requires a deviation approved by the owner.

| ID | Observable criterion | How it is checked |
|---|---|---|
| C1 | On a fresh clone, `uv sync` and `npm run check` pass with no API keys and no `.env` file, and `check` runs the sixteen evaluations in replay mode | Run both commands in a clean clone with the Anthropic and LangSmith variables unset; output saved as evidence |
| C2 | `seed` creates the `claims` table; seeding twice gives the same rows; the other tables, including the phase 08 ones, and their rows are unchanged | Pytest tests on a temporary database |
| C3 | `reconcile claims` drafts one claim per `discrepancies` invoice with no claim, and none for `matched`, `awaiting_delivery_note` or unlinked documents; an invoice that becomes `discrepancies` on a later scan gets its claim on the next run; a second run drafts nothing and calls no model | Pytest tests on a temporary database counting `claims` rows and model client calls |
| C4 | Drafting makes one call per claim through the existing client in live, record and replay modes; recipient and subject are set by code; the stored claim holds every discrepancy of the invoice and the amount in dispute computed by the rule of D5 | Pytest tests with recorded answers comparing the stored claim with expected values |
| C5 | A draft that passes the fact check is stored with source `model`; a draft that fails the schema check or the fact check (missing difference, wrong reference, invented amount) is stored from the template with source `template` and a `failures` row with status `needs_review` and step `claim_drafting`, and the other claims are still drafted | Pytest tests with recorded valid and failing answers reading `claims` and `failures` rows |
| C6 | After drafting, each claim is `awaiting_approval` with its thread `claim-INV-NNNN` paused in an `interrupt` and its state in the SQLite checkpoint | Pytest test reading rows and checkpoint state |
| C7 | `reconcile approve INV-NNNN` writes one `.eml` to the supplier outbox addressed to the supplier, with the fixed subject and the draft body, and sets the claim `sent` with its decision time; with `--body-file FILE` the email carries the file text, the claim keeps the original draft and is marked edited; an empty file is refused and changes nothing | Pytest tests comparing the outbox file and the `claims` row with expected values |
| C8 | `reconcile reject INV-NNNN` sets the claim `rejected` and writes no email; any command on an invoice with no claim or a claim that is not `awaiting_approval`, including a second approve, fails with a message and leaves one email at most; `reconciliations`, `discrepancies`, purchase orders and stock are unchanged by every claim command | Pytest tests per case counting rows and outbox files before and after |
| C9 | A claim survives separate processes: `reconcile claims` and the decision each run in a new process against the same database and checkpoint files, and the final rows and outbox equal the same steps run in one process; resuming calls no model | Pytest test that runs each step as a subprocess and compares with an in-process run, counting recorded calls |
| C10 | `reconcile list` shows the claim status of each invoice that has a claim and `reconcile show INV-NNNN` prints its draft, final text, source and status; for invoices with no claim both print what they printed in phase 08 | Pytest tests on a temporary database with claims in each status, and the phase 08 output tests unchanged |
| C11 | The `review` node takes a message (recipient, subject, body) and returns the decision and final body, with no import of supplier, invoice or reconciliation code | Pytest test that runs `review` on a plain message with each answer; an import check on its module |
| C12 | The versioned `claim_drafting` dataset holds at least 60 cases in the ten categories of "Evaluation design", at least 6 per category, with a fixed stratified split; every case passes the automatic validation and its discrepancy rows equal the output of the phase 08 `classify` function; re-running the planning script with the same seed gives the same plan | Pytest tests over the dataset and plan files; generator output saved as evidence |
| C13 | `npm run eval` in replay mode reports `claim_drafting` with the metrics of "Evaluation design", 95% Wilson intervals on the test split globally and per category, critical errors with their Wilson upper bound, and exits non-zero when an absolute or regression gate fails | Run the command; pytest tests that force each new gate to fail prove the non-zero exit |
| C14 | The baseline of Claude Haiku 4.5 on the `claim_drafting` test split is measured with the final instructions, stored with its per-case results, and the gates are set by the rule in "Evaluation design" | Stored baseline file and the live run report, saved as evidence |
| C15 | `claim_approval` holds at least 24 scenarios with the coverage of "Evaluation design"; `npm run eval` reports scenario success with its Wilson interval and critical errors per scenario, and exits non-zero when success is below 100% or any critical error is counted | Pytest tests over the scenario files and tests that inject each critical error and prove a non-zero exit; replay report saved as evidence |
| C16 | `reconcile-demo` runs the sample folder from the scan to the decided claims with each step in its own process; the phase 08 steps print as before; the claim steps print each draft, the operator's decisions and the written emails; it ends with at least one claim `sent` as drafted, one `sent` with an edited text, one `rejected`, and no claim for the `matched`, `awaiting_delivery_note` and `unlinked` documents; it needs no key and no network | Run with the network blocked through a pytest inside `check`; the claim drafts come from a recording made once in live mode; output saved as evidence |
| C17 | With the LangSmith variables set, a live `reconcile-demo` claim appears in LangSmith as one thread with the pause and the resumption and one span per node, and a live run of `claim_drafting` is logged as an experiment against its uploaded dataset splits | Trace and experiment links, and the owner's screenshot, as evidence; blocked by the trace quota until about 2026-11-05 |
| C18 | Phase 01 to 08 behaviour is unchanged: their tests pass unchanged apart from the phase 08 `reconcile-demo` test, which gains the new end state; the fourteen existing evaluations replay with every gate PASS; `git diff --stat` on their recordings, datasets and baselines is empty; `orders-demo` and `quotes-demo` print the same output in replay as before the phase | Test run, eval run, git diff and a saved output comparison, as evidence |
| C19 | No secret is versioned: the new recordings, edited body file, outbox examples, dataset and scenario files contain no keys or auth headers | `tests/test_secrets.py` covers the new files |
| C20 | The README explains in English the claim step, the claim content and amount in dispute, the fact check and template fallback, the `claims`, `approve` and `reject` commands, the outbox email, the extended `reconcile-demo` in replay and live mode, how both evaluations were built and run, and the known limitations below; master plan row 09 shows the phase status | Follow the new README sections in a clean clone; compare row 09 with the phase status |

## Constraints and risks
- No new dependency is expected: `interrupt`, `Command` and the SQLite checkpointer are in `langgraph`, the model client already serves record and replay, and the email uses the standard library `email` package through the phase 06 writer.
- Live model cost ceiling for the phase (D10), a limit and not a budget; the expected cost is under 1 USD with prompt caching: short answers, about 15 calls per tuning round on the development split for up to three rounds, about 45 calls for the test record and a few for the demo.
- Live runs need the owner's Anthropic and LangSmith keys in `.env`; the agent never reads that file.
- The LangSmith trace quota is exhausted until about 2026-11-05; C17 is pending at delivery, as C13 to C16 of phases 02 to 08, and is delivered with the catch-up change after the reset.
- An invented or wrong amount in a sent claim is the worst error; the fact check stops it before the person sees the draft, the template fallback keeps the flow going, and the evaluation scores the model before the fallback so the check does not hide model errors.
- The fact check reads amounts from free text; a pattern that is too loose or too strict would pass a wrong draft or reject a good one; both directions have scenarios in `claim_approval` and categories in `claim_drafting`.
- LangGraph re-runs an interrupted node from its start on resumption, so `draft`, `send` and `close` must be idempotent: a stored draft is read back instead of calling the model, and a unique claim per reconciliation and a keyed outbox file name prevent duplicates.
- Like earlier phases, the case writer and the evaluated model belong to the same family; here the cases are structured facts made by code, so the risk is lower than in extraction.
- About 45 test cases give a wide interval; per-category figures are indicative only.
- `claims` is a new table; `CREATE TABLE IF NOT EXISTS` adds it to a database made by phase 08 code without touching the old tables.
- The phase 08 extraction critical errors on DOC-060 and DOC-112 can still produce a wrong discrepancy in live use; a claim drafted from it is caught only by the person at review; their tuning is a separate change.

## Assumptions
- A claim is drafted only from a final `discrepancies` result; an `awaiting_delivery_note` invoice waits for its delivery note, since its quantity differences are not known yet.
- The claim is keyed by the invoice reference `INV-NNNN` of phase 08, so commands use that reference and no new claim number is introduced.
- The edited text is the person's text and is sent as written; only an empty text is refused, and the fact check is not applied to it.
- The amount in dispute and each amount are net of VAT in EUR, as phase 08 stores them; a price row with no stated price on the invoice is listed with no amount.
- A rejected claim means the person accepts the differences or will handle them outside the system; the reconciliation result stays `discrepancies` and the claim status says what was decided.
- The demo decides by scripted operator steps in their own processes, standing for the person at the command line, as in phase 07.
- `npm run check` stays fast: both new evaluations replay in seconds.

## Known limitations
- Claims are written to a simulated outbox; nothing is sent or received over a network, and no supplier answer is handled.
- One claim per invoice, decided once; no follow-up, reminder or second claim.
- The tone and wording of a draft are not scored; only its facts are, and the person reviews every claim before it is written.
- Recipient and subject cannot be edited.
- The phase 08 limitations remain: one invoice and one delivery note per order, fixed tolerances, and the DOC-060 and DOC-112 extraction errors until their change lands.

## Open decisions
Questions the owner must resolve before approval; empty on approval.
The recommended option is A in each; the scope and criteria above are written for it.

### D1 - Who drafts the claim
- A (recommended): Claude Haiku 4.5 writes the body from structured facts; code sets recipient and subject, checks the facts and falls back to a fixed template on failure. Trade-off: a small live cost and a new evaluation, but a real drafting agent whose worst error is caught by code.
- B: a fixed template only, no model. Trade-off: no cost, simplest and fully deterministic, but no drafting agent to show and no `claim_drafting` evaluation; C4, C5, C12 to C14 change.
- C: the model drafts with no template fallback; a failing draft is left for the person to write. Trade-off: less code, but a failed draft blocks the claim until a person writes it.

### D2 - Human review answers
- A (recommended): one pause with three answers: approve, approve with an edited text from a file (`--body-file`), or reject; both the draft and the final text are kept. Trade-off: one command decides, as in phase 07 `approve --supplier`, but the person cannot save an edit and approve later.
- B: `edit` stores a new text and keeps the claim waiting, then a separate `approve` sends it. Trade-off: a two-step review closer to a mail client, with one more command and more states to test.

### D3 - What sending means
- A (recommended): an `.eml` in the supplier outbox, as the phase 06 requests and phase 07 purchase orders; nothing leaves the machine. Trade-off: consistent and inspectable, no real delivery.
- B: no file; the claim only changes status. Trade-off: less code, but nothing to show of what the supplier would receive.

### D4 - Status after approval or rejection
- A (recommended): a new `claims` table holds `awaiting_approval`, `sent` or `rejected`; `reconciliations.result` stays `discrepancies` and is not altered; a rejected claim is final and no new claim is drafted. Trade-off: the phase 08 tables and checks stay frozen, but closing the reconciliation is left to a later phase.
- B: widen `reconciliations.result` with `claimed` and `accepted`. Trade-off: one status to read, but it changes a phase 08 check constraint, which SQLite cannot alter in place, so local databases must be re-seeded.

### D5 - Claim content
- A (recommended): one claim per invoice listing every difference; the amount in dispute adds only the differences that make us pay more (higher price or quantity, extra line); differences in the supplier's favour and missing lines are listed for correction; the requested action is a corrected invoice or a credit note. Trade-off: honest and complete, with one rule to explain in the README.
- B: claim only the differences that make us pay more; an invoice with only differences in the supplier's favour or missing lines gets no claim. Trade-off: simpler text, but missing lines, which may hide goods not delivered, are never raised.

### D6 - Side-agnostic design for phase 11
- A (recommended): the `review` node handles a plain message (recipient, subject, body) and knows nothing of suppliers or invoices; the supplier logic lives in `draft` and `send`; no sales table, column or command. Trade-off: phase 11 reuses the review step at almost no cost now.
- B: a fully supplier-specific graph; phase 11 refactors it. Trade-off: slightly less code now, more change later.
- C: a generic `claims` table with a `side` column and counterparty fields now. Trade-off: ready for phase 11, but it adds sales-side design before phase 11 is specified.

### D7 - Commands and demo
- A (recommended): `reconcile claims`, `reconcile approve INV-NNNN [--body-file FILE]` and `reconcile reject INV-NNNN` in the existing group, and `reconcile-demo` extended with the claim steps after the phase 08 steps. Trade-off: one demo for the whole module, as phase 07 extended `quotes-demo`, and the phase 08 demo test gains the new end state.
- B: a separate `claims` command group and a `claims-demo`. Trade-off: the phase 08 demo stays untouched, but the module has two demos.

### D8 - Evaluations
- A (recommended): `claim_drafting` in replay over structured cases with a deterministic fact grader, plus the deterministic `claim_approval` scenarios. Trade-off: both run in `check` with no cost; tone is not scored.
- B: A plus an LLM judge of tone and clarity on the test split. Trade-off: a fuller picture of draft quality, but a judge of the same family, more cost and a grader that is not deterministic.

### D9 - Dataset size and owner audit
- A (recommended): at least 60 cases, 25% development and 75% test, no owner audit, as in phases 07 and 08. Trade-off: the facts are made by code and checked against `classify`, so labels are reliable, but the interval is wide.
- B: at least 100 cases and an owner audit of 20 random drafts. Trade-off: a narrower interval and a human look at the text, with more cost and about 10 minutes of owner time.

### D10 - Live model cost ceiling
- A (recommended): 5 USD for the phase, as in phases 06 to 08, a limit and not a budget; the expected cost is under 1 USD.
- B: 2 USD. Trade-off: closer to the expected cost, with less room for an extra tuning round before stopping to ask.
