"""Deterministic `claim_approval` evaluation (phase 09, D8, C15).

Each scenario is one versioned JSON file: the invoices of a temporary database already reconciled by phase 08 (with
their discrepancy rows), the hand-written draft the model double returns for each invoice, and a script of `claims`,
`approve`, `reject` and `deliver` steps run through the `claims` functions (a `new_process` step through
`python -m purchase_cycle.cli`). The model is a test double that counts its calls, so no key and no network are needed.
Every expectation is written by hand from the claim rules: one claim per `discrepancies` invoice, an email only
after an approval, the approved or edited text sent as it is, and no second claim or email.

The grader reads the database, the supplier outbox, the `failures` table and, for the process steps, the printed
line. The critical-error counter checks the final state against an oracle computed apart from `claims`. The gate is
100% success and 0 critical errors.

Build the versioned scenario files with `uv run python -m purchase_cycle.evaluation.claim_approval_eval`.
"""

import json
import re
import subprocess
import sys
import tempfile
from email import message_from_bytes, policy
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from purchase_cycle import claims, db, llm, reconciliation
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_eval import _ci, _pct
from purchase_cycle.evaluation.recovery_eval import _rate
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.graph import sqlite_checkpointer

SUITE = "claim_approval"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "claim_approval"
OPS = ("claims", "approve", "reject", "deliver")
TAGS = (
    "discrepancy_claim", "no_claim_matched", "no_claim_awaiting", "no_claim_unlinked", "later_delivery",
    "template_fallback", "approve", "approve_edit", "empty_edit_refused", "reject", "repeat_decision",
    "no_claim_command", "claims_rerun", "restart",
)  # fmt: skip
# C15 minimums: scenarios in all and per coverage tag.
MINIMUMS = {"scenarios": 24, **dict.fromkeys(TAGS, 2)}
TARGET = 1.0  # deterministic: every scenario must end as scripted
UNIT = "scenario"
CRITICAL_KINDS = (
    "email_for_unsent_claim",
    "duplicate_claim_or_email",
    "sent_text_differs",
    "claim_without_discrepancies",
    "status_outbox_mismatch",
    "model_call_on_rerun",
)
INVALID = "<invalid>"  # the double answers with an output that fails the schema
TEMPLATE = "<template>"  # the sent text is the stored template
EMAIL_NAME = re.compile(r"CLAIM-(INV-\d+)-")


# Builders of the rows, the hand-written drafts, the steps and the expectations.


def price(sku, order, invoiced, quantity) -> tuple:
    unit = round(invoiced - order, 4)
    return (sku, "price", order, invoiced, unit, round(unit * quantity, 2), None)


def quant(sku, expected, found, unit_price) -> tuple:
    return (sku, "quantity", expected, found, unit_price, round((found - expected) * unit_price, 2), None)


def extra(sku, amount, text=None) -> tuple:
    return (sku, "extra_line", None, None, None, amount, text)


def missing(sku, amount) -> tuple:
    return (sku, "missing_line", None, None, None, amount, None)


def inv(result, rows=(), supplier="SUP-001", dispute=0.0, draft="good", later=False) -> dict:
    return {"result": result, "rows": [list(r) for r in rows], "supplier": supplier, "dispute": dispute,
            "draft_kind": draft, "later": later}  # fmt: skip


def draft_text(number: str, order: str, rows: list, dispute: float, kind: str) -> str | None:
    """A claim text written from the rows; `kind` breaks one check on purpose."""
    if kind == INVALID:
        return INVALID
    lines = []
    for sku, type_, expected, found, _unit, amount, text in rows:
        item = sku or text
        if type_ == "price":
            line = f"- {item}: price {expected:.2f} EUR on the order, {found:.2f} EUR on the invoice"
        elif type_ == "quantity":
            line = f"- {item}: quantity {expected:g} ordered, {found:g} invoiced"
        elif type_ == "extra_line":
            line = f"- {item}: invoiced but not ordered"
        else:
            line = f"- {item}: ordered but not invoiced"
        lines.append(line + ("" if amount is None else f", difference {abs(amount):.2f} EUR"))
    if kind == "missing_item":
        lines = lines[1:]
    if kind == "invented":
        lines.append("We also paid 99.99 EUR in advance.")
    request = "Please look into it." if kind == "no_request" else "Please send us a corrected invoice or a credit note."
    return (
        f"Dear supplier,\n\nInvoice {number} does not match purchase order {order}:\n" + "\n".join(lines)
        + f"\nThe amount in dispute is {dispute:.2f} EUR.\n{request}\n\nBest regards,\nPurchasing department"
    )  # fmt: skip


def C() -> dict:
    return {"op": "claims", "exit": 0}


def A(n, body=None, exit=0, proc=False) -> dict:
    return {"op": "approve", "n": n, **({} if body is None else {"body": body}), "exit": exit, **({"new_process": True} if proc else {})}  # fmt: skip


def R(n, exit=0, proc=False) -> dict:
    return {"op": "reject", "n": n, "exit": exit, **({"new_process": True} if proc else {})}


def L(n) -> dict:
    return {"op": "deliver", "n": n, "exit": 0}


EDIT = "edit"  # the builder writes an edit text that names its invoice
SENT_D = ("sent", "model", 0, "draft")
SENT_T = ("sent", "template", 0, TEMPLATE)
REJ = ("rejected", "model", 0, None)
REJ_T = ("rejected", "template", 0, None)
WAIT = ("awaiting_approval", "model", 0, None)
WAIT_T = ("awaiting_approval", "template", 0, None)
SENT_E = ("sent", "model", 1, EDIT)
NONE = None

PRICE, QTY, EXTRA, MISS = price("GLV-NIT-XS", 4.0, 4.2, 5), quant("GLV-NIT-XS", 5, 6, 4.0), extra("GLV-NIT-S", 10.0), missing("GLV-NIT-M", 108.0)  # fmt: skip
DISC = "discrepancies"
MATCHED, AWAITING_NOTE, UNLINKED = "matched", "awaiting_delivery_note", "unlinked"

# (invoices, steps, expected claim per invoice, expected failures, expected model calls), in a fixed order.
SCENARIOS = [
    # A claim for a discrepancies invoice, approved as drafted.
    ([inv(DISC, [PRICE], dispute=1.0)], [C(), A(1)], [SENT_D], 0, 1),
    ([inv(DISC, [QTY], "SUP-002", 4.0)], [C(), A(1)], [SENT_D], 0, 1),
    ([inv(DISC, [EXTRA], "SUP-003", 10.0)], [C(), A(1)], [SENT_D], 0, 1),
    ([inv(DISC, [PRICE], dispute=1.0), inv(DISC, [EXTRA], "SUP-002", 10.0)], [C(), A(1), A(2)], [SENT_D, SENT_D], 0, 2),
    ([inv(DISC, [PRICE], dispute=1.0), inv(DISC, [EXTRA], "SUP-002", 10.0)], [C(), A(1)], [SENT_D, WAIT], 0, 2),
    # Amounts that are listed but not added, and a line with no price.
    ([inv(DISC, [quant("GLV-NIT-XS", 5, 4, 4.0), MISS], dispute=0.0)], [C(), A(1)], [SENT_D], 0, 1),
    ([inv(DISC, [PRICE, MISS], dispute=1.0)], [C(), R(1)], [REJ], 0, 1),
    ([inv(DISC, [extra(None, 12.5, "Delivery pallet")], dispute=12.5)], [C(), A(1)], [SENT_D], 0, 1),
    ([inv(DISC, [extra(None, None, "Special handling")], dispute=0.0)], [C(), A(1)], [SENT_D], 0, 1),
    # Approve with an edited text, and refused empty texts.
    ([inv(DISC, [PRICE, EXTRA], dispute=11.0)], [C(), A(1, EDIT)], [SENT_E], 0, 1),
    ([inv(DISC, [QTY], dispute=4.0)], [C(), A(1, EDIT)], [SENT_E], 0, 1),
    ([inv(DISC, [PRICE], dispute=1.0)], [C(), A(1, EDIT, proc=True)], [SENT_E], 0, 1),
    ([inv(DISC, [PRICE], dispute=1.0)], [C(), A(1, "", exit=1), A(1, " \n", exit=1), A(1)], [SENT_D], 0, 1),
    ([inv(DISC, [EXTRA], dispute=10.0)], [C(), A(1, "\n", exit=1), R(1)], [REJ], 0, 1),
    # Reject, and a second decision.
    ([inv(DISC, [PRICE], dispute=1.0)], [C(), R(1)], [REJ], 0, 1),
    ([inv(DISC, [QTY], dispute=4.0)], [C(), R(1, proc=True)], [REJ], 0, 1),
    ([inv(DISC, [PRICE], dispute=1.0)], [C(), R(1), A(1, exit=1), R(1, exit=1)], [REJ], 0, 1),
    ([inv(DISC, [EXTRA], dispute=10.0)], [C(), A(1), A(1, exit=1), R(1, exit=1)], [SENT_D], 0, 1),
    ([inv(DISC, [PRICE], dispute=1.0)], [C(), A(1), A(1, exit=1, proc=True)], [SENT_D], 0, 1),
    # Invoices that get no claim.
    ([inv(MATCHED)], [C(), A(1, exit=1), R(1, exit=1)], [NONE], 0, 0),
    ([inv(AWAITING_NOTE, [PRICE], later=True)], [C(), A(1, exit=1)], [NONE], 0, 0),
    ([inv(UNLINKED)], [C(), R(1, exit=1)], [NONE], 0, 0),
    ([inv(MATCHED), inv(AWAITING_NOTE, [QTY], later=True), inv(UNLINKED), inv(DISC, [PRICE], dispute=1.0)],
     [C(), A(4)], [NONE, NONE, NONE, SENT_D], 0, 1),
    ([inv(DISC, [PRICE], dispute=1.0)], [A(1, exit=1), C(), A(1)], [SENT_D], 0, 1),
    # An invoice that becomes discrepancies after a later delivery note.
    ([inv(AWAITING_NOTE, [PRICE], dispute=1.0, later=True)], [C(), L(1), C(), A(1)], [SENT_D], 0, 1),
    ([inv(AWAITING_NOTE, [QTY], dispute=4.0, later=True), inv(DISC, [EXTRA], "SUP-002", 10.0)],
     [C(), A(2), L(1), C(), A(1)], [SENT_D, SENT_D], 0, 2),
    ([inv(AWAITING_NOTE, [PRICE, EXTRA], dispute=11.0, later=True)], [C(), L(1), C(), R(1)], [REJ], 0, 1),
    # A draft that fails the fact check or the schema falls back to the template and parks the invoice.
    ([inv(DISC, [PRICE], dispute=1.0, draft="invented")], [C(), A(1)], [SENT_T], 1, 1),
    ([inv(DISC, [QTY], dispute=4.0, draft=INVALID)], [C(), A(1)], [SENT_T], 1, 1),
    ([inv(DISC, [EXTRA], dispute=10.0, draft="no_request")], [C(), R(1)], [REJ_T], 1, 1),
    ([inv(DISC, [PRICE, EXTRA], dispute=11.0, draft="missing_item"), inv(DISC, [QTY], "SUP-002", 4.0)],
     [C(), A(1), A(2)], [SENT_T, SENT_D], 1, 2),
    # A second `claims` drafts nothing and calls no model.
    ([inv(DISC, [PRICE], dispute=1.0)], [C(), C(), A(1), C()], [SENT_D], 0, 1),
    ([inv(DISC, [QTY], dispute=4.0)], [C(), R(1), C()], [REJ], 0, 1),
    ([inv(DISC, [EXTRA], dispute=10.0)], [C(), C()], [WAIT], 0, 1),
    # A process restart between the draft and the decision.
    ([inv(DISC, [PRICE], dispute=1.0), inv(DISC, [QTY], "SUP-002", 4.0)], [C(), A(1, proc=True), R(2, proc=True)],
     [SENT_D, REJ], 0, 2),
]  # fmt: skip


def derive_tags(item: dict) -> list[str]:
    """The coverage tags follow from the invoices, the steps and the expectation: the files hold what each does."""
    steps, expected, tags = item["steps"], item["expected"]["claims"], set()
    ref = reconciliation.invoice_reference
    for n, invoice in enumerate(item["invoices"], start=1):
        claim = expected[ref(n)]
        if invoice["result"] == "discrepancies":
            tags.add("discrepancy_claim")
        if claim is None and invoice["result"] == "matched":
            tags.add("no_claim_matched")
        if claim is None and invoice["result"] == "awaiting_delivery_note":
            tags.add("no_claim_awaiting")
        if claim is None and invoice["result"] == "unlinked":
            tags.add("no_claim_unlinked")
        if claim is not None and claim["source"] == "template":
            tags.add("template_fallback")
    if any(s["op"] == "deliver" for s in steps):
        tags.add("later_delivery")
    if any(s["op"] == "approve" and s["exit"] == 0 and "body" not in s for s in steps):
        tags.add("approve")
    if any(s["op"] == "approve" and s["exit"] == 0 and "body" in s for s in steps):
        tags.add("approve_edit")
    if any(s["op"] == "approve" and s["exit"] != 0 and "body" in s and not s["body"].strip() for s in steps):
        tags.add("empty_edit_refused")
    if any(s["op"] == "reject" and s["exit"] == 0 for s in steps):
        tags.add("reject")
    for s in steps:
        if s["op"] in ("approve", "reject") and s["exit"] != 0:
            if expected[s["invoice"]] is None:
                tags.add("no_claim_command")
            elif s["invoice"] in decided_before(steps, s):
                tags.add("repeat_decision")
    if sum(s["op"] == "claims" for s in steps) >= 2:
        tags.add("claims_rerun")
    if any(s.get("new_process") for s in steps):
        tags.add("restart")
    return [t for t in TAGS if t in tags]


def decided_before(steps: list[dict], step: dict) -> set[str]:
    """The invoices decided with exit 0 before `step`; an approve refused before any claim is not a repeat."""
    done = set()
    for s in steps:
        if s is step:
            break
        if s["op"] in ("approve", "reject") and s["exit"] == 0:
            done.add(s["invoice"])
    return done


def _outbox(item: dict) -> list[str]:
    return sorted(
        f"CLAIM-{ref}-{item['invoices'][int(ref[4:]) - 1]['supplier']}.eml"
        for ref, claim in item["expected"]["claims"].items()
        if claim and claim["status"] == "sent"
    )


def build_items() -> list[dict]:
    items, ref = [], reconciliation.invoice_reference
    for n, (invoices, steps, outcomes, failures, calls) in enumerate(SCENARIOS, start=1):
        orders = iter(range(1, len(invoices) + 1))
        built, texts = [], {}
        for k, invoice in enumerate(invoices, start=1):
            invoice = dict(invoice, number=f"F-26{k:02d}")
            if invoice["result"] == UNLINKED:
                invoice["order"] = None
            else:
                invoice["order"] = reconciliation.order_reference(next(orders))
            if invoice["result"] in (DISC, AWAITING_NOTE):
                invoice["draft"] = draft_text(
                    invoice["number"], invoice["order"], invoice["rows"], invoice["dispute"], invoice.pop("draft_kind")
                )
            else:
                invoice["draft"] = invoice.pop("draft_kind") and None
            texts[ref(k)] = invoice["draft"]
            built.append(invoice)
        edits = {}
        built_steps = []
        for step in steps:
            step = dict(step)
            if "n" in step:
                step["invoice"] = ref(step.pop("n"))
            if step.get("body") == EDIT:
                step["body"] = edits.setdefault(step["invoice"], f"Edited claim for {step['invoice']}: please send a corrected invoice.")  # fmt: skip
            built_steps.append(step)
        claims_expected = {}
        for k, outcome in enumerate(outcomes, start=1):
            if outcome is None:
                claims_expected[ref(k)] = None
                continue
            status, source, edited, sent = outcome
            body = edits.get(ref(k)) if sent == EDIT else (texts[ref(k)] if sent == "draft" else sent)
            claims_expected[ref(k)] = {"status": status, "source": source, "edited": edited, "sent_body": body}
        item = {
            "id": f"CA-{n:03d}",
            "dataset_version": DATASET_VERSION,
            "initial_state": INITIAL_STATE,
            "invoices": built,
            "steps": built_steps,
            "expected": {"claims": claims_expected, "failures": failures, "model_calls": calls},
        }
        item["expected"]["outbox"] = _outbox(item)
        item["tags"] = derive_tags(item)
        items.append(item)
    return items


INITIAL_STATE = {"seed": "db.seed: catalog, stock, customers, suppliers and the clock at 2026-10-12 09:00"}


def write_items(items: list[dict], folder: Path = DATASET_DIR) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        text = json.dumps(item, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (folder / f"{item['id']}.json").write_text(text, encoding="utf-8", newline="\n")


def load_dataset(folder: Path = DATASET_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("CA-*.json"))]


# Runner, grader and critical errors.


class DraftDouble:
    """The model client of the scenario: returns the stored draft of the invoice and counts its calls."""

    def __init__(self, drafts: dict[str, str]):
        self.drafts, self.calls = drafts, 0

    def extract(self, prompt: str, case_id: str | None = None, correction: str | None = None):
        self.calls += 1
        draft = self.drafts[case_id.removeprefix("claim-")]
        if draft == INVALID:
            try:
                llm.ClaimBody.model_validate({"body": 1})
            except ValidationError as error:
                raise llm.InvalidModelOutput(error, case_id) from error
        return SimpleNamespace(body=draft)


def _setup(item: dict, folder: Path) -> dict:
    paths = {"db": folder / "business.db", "checkpoints": folder / "checkpoints.sqlite", "outbox": folder / "outbox"}
    conn = db.connect(paths["db"])
    try:
        db.seed(conn)
        for n, invoice in enumerate(item["invoices"], start=1):
            status = "unlinked" if invoice["result"] == "unlinked" else "linked"
            cursor = conn.execute(
                "INSERT INTO supplier_documents (kind, number, source_file, content_hash, status, created_at) "
                "VALUES ('invoice', ?, 'f.txt', ?, ?, 't')",
                (invoice["number"], invoice["number"], status),
            )
            assert cursor.lastrowid == n, "the invoice ids follow the scenario order"
            if status == "unlinked":
                continue
            conn.execute(
                "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
                "VALUES ('GLV-NIT-XS', 5, ?, 'ordered', 't', 't', 't')",
                (f"th-{n}",),
            )
            conn.execute(
                "INSERT INTO purchase_orders (request_id, supplier_code, sku, quantity, unit_price, currency, total, "
                "expected_delivery, issued_at, status) "
                "VALUES ((SELECT MAX(id) FROM quote_requests), ?, 'GLV-NIT-XS', 5, 1.0, 'EUR', 5.0, 't', 't', 'issued')",
                (invoice["supplier"],),
            )
            conn.execute(
                "INSERT INTO reconciliations (invoice_id, purchase_order_id, result, reconciled_at) "
                "VALUES (?, (SELECT MAX(id) FROM purchase_orders), ?, 't')",
                (n, "awaiting_delivery_note" if invoice["later"] else invoice["result"]),
            )
            if not invoice["later"]:
                _add_rows(conn, n, invoice["rows"])
        conn.commit()
    finally:
        conn.close()
    return paths


def _add_rows(conn, invoice_id: int, rows: list) -> None:
    for row in rows:
        conn.execute(
            "INSERT INTO discrepancies (reconciliation_id, sku, type, expected, found, unit_amount, amount, text) "
            "VALUES ((SELECT id FROM reconciliations WHERE invoice_id = ?), ?, ?, ?, ?, ?, ?, ?)",
            (invoice_id, *row),
        )


def _deliver(item: dict, paths: dict, ref: str) -> None:
    """The delivery note arrives: the reconciliation of the invoice becomes `discrepancies` with its rows."""
    invoice_id = reconciliation.invoice_id_from_reference(ref)
    conn = db.connect(paths["db"])
    try:
        conn.execute("UPDATE reconciliations SET result = 'discrepancies' WHERE invoice_id = ?", (invoice_id,))
        _add_rows(conn, invoice_id, item["invoices"][invoice_id - 1]["rows"])
        conn.commit()
    finally:
        conn.close()


def _process(step: dict, paths: dict, folder: Path) -> tuple[int, str, str]:
    argv = [
        "--db",
        str(paths["db"]),
        "reconcile",
        step["op"],
        step["invoice"],
        "--checkpoints",
        str(paths["checkpoints"]),
    ]
    if step["op"] == "approve":
        argv += ["--outbox", str(paths["outbox"])]
        if "body" in step:
            path = folder / "body.txt"
            path.write_text(step["body"], encoding="utf-8")
            argv += ["--body-file", str(path)]
    done = subprocess.run(
        [sys.executable, "-m", "purchase_cycle.cli", *argv], capture_output=True, text=True, timeout=120
    )
    return done.returncode, done.stdout, done.stderr


def _run_step(item: dict, step: dict, paths: dict, double: DraftDouble, folder: Path) -> tuple[int, str, str]:
    """The exit code, the printed output and the error output of one step."""
    if step["op"] == "deliver":
        _deliver(item, paths, step["invoice"])
        return 0, "", ""
    if step.get("new_process"):
        return _process(step, paths, folder)
    checkpointer = sqlite_checkpointer(paths["checkpoints"])
    try:
        if step["op"] == "claims":
            claims.draft_claims(paths["db"], checkpointer, double, paths["outbox"])
            return 0, "", ""
        invoice_id = reconciliation.invoice_id_from_reference(step["invoice"])
        try:
            if step["op"] == "approve":
                claims.approve_claim(paths["db"], checkpointer, paths["outbox"], invoice_id, step.get("body"))
            else:
                claims.reject_claim(paths["db"], checkpointer, invoice_id)
        except claims.DecisionRefused as error:
            return 1, "", f"Error: {error}\n"
        return 0, "", ""
    finally:
        checkpointer.conn.close()


def _measure(paths: dict) -> dict:
    """The claims, the reconciliation results, the `failures` count and the emails of the outbox."""
    conn = db.connect(paths["db"])
    try:
        rows = conn.execute(
            "SELECT c.*, r.invoice_id FROM claims c JOIN reconciliations r ON r.id = c.reconciliation_id ORDER BY c.id"
        ).fetchall()
        found = {}
        for row in rows:
            ref = reconciliation.invoice_reference(row["invoice_id"])
            entry = found.setdefault(ref, {**dict(row), "count": 0})
            entry["count"] += 1
            facts = claims.claim_facts(conn, row["invoice_id"])
            entry["template_ok"] = facts is not None and not claims.check_draft(row["draft_body"], facts)
        results = {
            reconciliation.invoice_reference(r["invoice_id"]): r["result"]
            for r in conn.execute("SELECT invoice_id, result FROM reconciliations")
        }
        failures = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
    finally:
        conn.close()
    emails = {}
    for path in sorted(paths["outbox"].glob("*.eml")) if paths["outbox"].is_dir() else []:
        message = message_from_bytes(path.read_bytes(), policy=policy.default)
        emails[path.name] = {"to": message["To"], "subject": message["Subject"], "body": message.get_content().strip()}
    return {"claims": found, "results": results, "failures": failures, "emails": emails}


def _email_ref(name: str) -> str | None:
    found = EMAIL_NAME.match(name)
    return found.group(1) if found else None


def _oracle_calls(item: dict) -> int:
    """The model calls a correct run makes: one per invoice the first time it is a `discrepancies` claim candidate."""
    drafted, now = set(), {}
    for n, invoice in enumerate(item["invoices"], start=1):
        now[reconciliation.invoice_reference(n)] = "awaiting_delivery_note" if invoice["later"] else invoice["result"]
    for step in item["steps"]:
        if step["op"] == "deliver":
            now[step["invoice"]] = "discrepancies"
        elif step["op"] == "claims":
            drafted |= {ref for ref, result in now.items() if result == "discrepancies"}
    return len(drafted)


def _errors(item: dict, final: dict, log: list[dict]) -> dict[str, int]:
    """Critical errors read from the final state and the step log, apart from the `claims` module."""
    counts = dict.fromkeys(CRITICAL_KINDS, 0)
    emails, found = final["emails"], final["claims"]
    per_ref: dict[str, int] = {}
    for name in emails:
        ref = _email_ref(name)
        per_ref[ref] = per_ref.get(ref, 0) + 1
        claim = found.get(ref)
        if claim is None or claim["status"] != "sent":
            counts["email_for_unsent_claim"] += 1
    counts["duplicate_claim_or_email"] = sum(max(0, c["count"] - 1) for c in found.values()) + sum(
        max(0, n - 1) for n in per_ref.values()
    )
    approved = {}
    for step, line in zip(item["steps"], log, strict=True):
        if step["op"] == "approve" and line["code"] == 0:
            approved.setdefault(step["invoice"], step.get("body"))
    for ref, claim in found.items():
        mails = [e for name, e in emails.items() if _email_ref(name) == ref]
        if (claim["status"] == "sent") != bool(mails):
            counts["status_outbox_mismatch"] += 1
        if final["results"].get(ref) != "discrepancies":
            counts["claim_without_discrepancies"] += 1
        wanted = claim["draft_body"] if approved.get(ref) is None else approved[ref]
        if claim["draft_source"] == "model" and approved.get(ref) is None:
            wanted = item["expected"]["claims"][ref]["sent_body"] or claim["draft_body"]
        for mail in mails:
            if mail["body"] != wanted.strip() or (claim["final_body"] or "").strip() != mail["body"]:
                counts["sent_text_differs"] += 1
    counts["model_call_on_rerun"] = max(0, final["calls"] - _oracle_calls(item))
    return counts


def _printed_ok(step: dict, line: dict) -> bool:
    """A process step prints one line for a decision and an error line for a refusal."""
    if not step.get("new_process"):
        return True
    if step["exit"] != 0:
        return line["err"].startswith("Error:")
    if step["op"] == "reject":
        return f"{step['invoice']} claim rejected  no email" in line["out"]
    how = "edited" if "body" in step else "as drafted"
    return f"{step['invoice']} claim sent ({how})" in line["out"]


def run_item(item: dict, tmp: Path) -> dict:
    folder = tmp / item["id"]
    folder.mkdir(parents=True)
    paths = _setup(item, folder)
    ref = reconciliation.invoice_reference
    double = DraftDouble({ref(n): i["draft"] for n, i in enumerate(item["invoices"], start=1)})
    log = []
    for step in item["steps"]:
        code, out, err = _run_step(item, step, paths, double, folder)
        log.append({"code": code, "out": out, "err": err})
    final = _measure(paths)
    final["calls"] = double.calls
    expected = item["expected"]
    measured_claims = {}
    for r in expected["claims"]:
        claim = final["claims"].get(r)
        if claim is None:
            measured_claims[r] = None
            continue
        sent = next((e["body"] for name, e in final["emails"].items() if _email_ref(name) == r), None)
        if sent is not None and claim["draft_source"] == "template" and sent == claim["draft_body"].strip():
            sent = TEMPLATE if claim["template_ok"] else sent
        measured_claims[r] = {
            "status": claim["status"], "source": claim["draft_source"], "edited": claim["edited"], "sent_body": sent,
        }  # fmt: skip
    measured = {
        "claims": measured_claims, "outbox": sorted(final["emails"]), "failures": final["failures"],
        "model_calls": final["calls"], "exits": [s["code"] for s in log],
    }  # fmt: skip
    wanted = {k: expected[k] for k in ("claims", "outbox", "failures", "model_calls")}
    wanted_claims = {
        r: None if c is None else {**c, "sent_body": c["sent_body"] if c["status"] == "sent" else None}
        for r, c in wanted["claims"].items()
    }
    wanted["claims"] = wanted_claims
    correct = (
        {k: measured[k] for k in wanted} == wanted
        and measured["exits"] == [s["exit"] for s in item["steps"]]
        and all(_printed_ok(s, line) for s, line in zip(item["steps"], log, strict=True))
    )
    return {"id": item["id"], "tags": item["tags"], "expected": wanted, **measured, "correct": correct,
            "critical": _errors(item, final, log)}  # fmt: skip


def critical_totals(results: list[dict]) -> dict[str, int]:
    return {kind: sum(r["critical"][kind] for r in results) for kind in CRITICAL_KINDS}


def print_critical(errors: dict[str, int], n: int) -> None:
    """The critical-error row: a count gated at 0 with no averaging, then the count per kind."""
    total = sum(errors.values())
    target, met = target_cells(total, 0, "<=")
    print(
        f"{'critical_errors':<26}{total:<8}{'-':<16}{n:<6}{UNIT:<16}{'=0':<9}{met:<12}{'0':<11}"
        f"{'PASS' if total == 0 else 'FAIL'}"
    )
    print("  by kind: " + "  ".join(f"{kind}={errors[kind]}" for kind in CRITICAL_KINDS))


def critical_gate(errors: dict[str, int], results: list[dict]) -> int:
    """Exit 1 when any scenario holds a critical error, naming each one."""
    if not sum(errors.values()):
        return 0
    for r in results:
        found = {kind: c for kind, c in r["critical"].items() if c}
        if found:
            print(f"  {r['id']}  critical errors {found}")
    print(f"GATE FAILED: critical_errors {sum(errors.values())} above threshold 0")
    return 1


def evaluate(
    mode: str, split: str, *, dataset_dir: Path = DATASET_DIR, set_baseline: bool = False, workers: int = 1
) -> int:
    """Run every scenario and print the report; exit 1 below 100% success or with a critical error."""
    items = load_dataset(dataset_dir)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = [run_item(item, Path(tmp)) for item in items]
    s = _rate(results)
    failed = zero_event_note(_rate([{"correct": not r["correct"]} for r in results]))
    target, met = target_cells(s["value"], TARGET)
    gate = "PASS" if s["value"] >= TARGET else "FAIL"
    errors = critical_totals(results)
    print(f"{SUITE}  mode={mode}  scenarios={len(results)}  (deterministic: model double, no model call, no split, no baseline)")  # fmt: skip
    print(
        f"{'metric':<26}{'value':<8}{'95% CI':<16}{'n':<6}{'unit':<16}{'target':<9}{'target met':<12}{'threshold':<11}gate"
    )
    print(
        f"{'scenario_success':<26}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{UNIT:<16}{target:<9}{met:<12}"
        f"{_pct(TARGET):<11}{gate}" + zero_event_note(s) + (f"  failed scenarios{failed}" if failed else "")
    )
    print_critical(errors, len(results))
    print("per tag:")
    for tag in TAGS:
        rows = [r for r in results if tag in r["tags"]]
        if rows:
            g = _rate(rows)
            print(f"  {tag:<20} {_pct(g['value'])} {_ci(g)} n={g['n']}")
    failures = [r for r in results if not r["correct"]]
    print(f"failures: {len(failures)}")
    for r in failures:
        got = {k: r[k] for k in ("claims", "outbox", "failures", "model_calls", "exits")}
        print(f"  {r['id']}  expected {r['expected']}  got {got}")
    code = 0
    if failures:
        print(f"GATE FAILED: scenario_success {_pct(s['value'])} below threshold {_pct(TARGET)}")
        code = 1
    return max(code, critical_gate(errors, results))


def main() -> None:
    items = build_items()
    write_items(items)
    print(f"Built {len(items)} claim approval scenarios -> {DATASET_DIR}")


if __name__ == "__main__":
    main()
