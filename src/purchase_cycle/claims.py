"""Phase 09: claims to the supplier for the invoices that phase 08 left with discrepancies."""

import json
import re
import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from purchase_cycle import config, db, llm, quotes, reconciliation, review, sim_clock

CLAIM_SUBJECT = "Claim on invoice {invoice} for purchase order {order}"
MAX_CLAIM_BODY = 3000
CHANNEL = "reconcile"
STEP = "claim_drafting"
# A money amount: a number with exactly two decimals, or any number next to EUR or euro(s); bare integers are not money.
AMOUNT_PATTERN = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(?:EUR|euros?)\b|\bEUR\s*(\d+(?:[.,]\d+)?)|(?<!\d)(\d+[.,]\d{2})(?!\d)",
    re.IGNORECASE,
)
CLAIM_TEMPLATE = """Dear {supplier},

We have found differences between your invoice {invoice_number} and our purchase order {order}:
{lines}

The amount in dispute is {amount_in_dispute:.2f} EUR.
Please send us a corrected invoice or a credit note.

Best regards,
Purchasing department"""
MONEY_KEYS = ("unit_amount", "amount")

ROW_KEYS = reconciliation.DISCREPANCY_COLUMNS  # sku, type, expected, found, unit_amount, amount, text


def candidates(conn: sqlite3.Connection) -> list[int]:
    """Invoice ids whose reconciliation is `discrepancies` and that have no claim yet, in invoice order."""
    rows = conn.execute(
        "SELECT r.invoice_id FROM reconciliations r LEFT JOIN claims c ON c.reconciliation_id = r.id "
        "WHERE r.result = 'discrepancies' AND c.id IS NULL ORDER BY r.invoice_id"
    )
    return [row[0] for row in rows]


def amount_in_dispute(differences: list[dict]) -> float:
    """Sum of the differences that make us pay more: positive price and quantity amounts and priced extra lines."""
    total = 0.0
    for row in differences:
        amount = row["amount"]
        if amount is None:
            continue
        if row["type"] in ("price", "quantity") and amount > 0 or row["type"] == "extra_line":
            total += amount
    return round(total, 2)


def claim_facts(conn: sqlite3.Connection, invoice_id: int) -> dict | None:
    """The references, supplier, every discrepancy row and the amount in dispute; None without discrepancies."""
    head = conn.execute(
        "SELECT r.id, r.purchase_order_id, d.number, s.name, s.email FROM reconciliations r "
        "JOIN supplier_documents d ON d.id = r.invoice_id "
        "JOIN purchase_orders o ON o.id = r.purchase_order_id JOIN suppliers s ON s.code = o.supplier_code "
        "WHERE r.invoice_id = ? AND r.result = 'discrepancies'",
        (invoice_id,),
    ).fetchone()
    if head is None:
        return None
    rows = conn.execute(
        f"SELECT {', '.join(ROW_KEYS)} FROM discrepancies WHERE reconciliation_id = ? ORDER BY id", (head[0],)
    )
    differences = [dict(row) for row in rows]
    return {
        "invoice": reconciliation.invoice_reference(invoice_id),
        "invoice_number": head[2],
        "order": reconciliation.order_reference(head[1]),
        "supplier": head[3],
        "recipient": head[4],
        "differences": differences,
        "amount_in_dispute": amount_in_dispute(differences),
    }


def facts_prompt(facts: dict) -> str:
    """The facts as one JSON object with sorted keys and two-decimal amounts; no recipient, so the key is stable."""
    differences = []
    for row in facts["differences"]:
        row = dict(row)
        for key in MONEY_KEYS + (("expected", "found") if row["type"] == "price" else ()):
            if row[key] is not None:
                row[key] = f"{row[key]:.2f}"
        differences.append(row)
    shown = {k: v for k, v in facts.items() if k != "recipient"}
    shown |= {"differences": differences, "amount_in_dispute": f"{facts['amount_in_dispute']:.2f}"}
    return json.dumps(shown, sort_keys=True)


def money_amounts(text: str) -> list[float]:
    """Every money amount in the text, read by AMOUNT_PATTERN; a decimal comma counts as a point."""
    return [float(next(g for g in m.groups() if g).replace(",", ".")) for m in AMOUNT_PATTERN.finditer(text)]


def check_draft(body: str, facts: dict) -> list[str]:
    """Deterministic fact check of a draft; an empty list means it passes."""
    problems = []
    found = money_amounts(body)

    def has(amount: float) -> bool:
        return any(abs(value - amount) < 0.005 for value in found)

    for label, reference in (("invoice number", facts["invoice_number"]), ("purchase order", facts["order"])):
        if reference and reference not in body:
            problems.append(f"missing {label} {reference}")
    allowed = [abs(facts["amount_in_dispute"])]
    for row in facts["differences"]:
        item = row["sku"] or row["text"]
        if item and item not in body:
            problems.append(f"missing difference {item}")
        if row["amount"] is not None and not has(abs(row["amount"])):
            problems.append(f"missing amount {abs(row['amount']):.2f} of {item}")
        allowed += [abs(row[key]) for key in ("expected", "found", "unit_amount", "amount") if row[key] is not None]
    if not has(abs(facts["amount_in_dispute"])):
        problems.append(f"missing amount in dispute {facts['amount_in_dispute']:.2f}")
    if not re.search(r"corrected invoice|credit note", body, re.IGNORECASE):
        problems.append("missing request for a corrected invoice or a credit note")
    for value in found:
        if not any(abs(value - known) < 0.005 for known in allowed):
            problems.append(f"invented amount {value:.2f}")
    if not body.isascii():
        problems.append("body is not ASCII")
    if len(body) > MAX_CLAIM_BODY:
        problems.append(f"body longer than {MAX_CLAIM_BODY} characters")
    return problems


def _template_line(row: dict) -> str:
    item = row["sku"] or row["text"] or "unnamed line"
    amount = "no amount stated" if row["amount"] is None else f"{abs(row['amount']):.2f} EUR"
    if row["type"] == "price":
        detail = "price"
        if row["expected"] is not None and row["found"] is not None:
            detail += f" {row['expected']:.2f} EUR on the order, {row['found']:.2f} EUR on the invoice"
    elif row["type"] == "quantity":
        detail = f"quantity {row['expected']:g} expected, {row['found']:g} invoiced"
    elif row["type"] == "extra_line":
        detail = "invoiced but not on the order"
    else:
        detail = "ordered but not invoiced"
    return f"- {item}: {detail}, difference {amount}"


def fill_template(facts: dict) -> str:
    lines = "\n".join(_template_line(row) for row in facts["differences"])
    return CLAIM_TEMPLATE.format(
        supplier=facts["supplier"],
        invoice_number=facts["invoice_number"] or facts["invoice"],
        order=facts["order"],
        lines=lines,
        amount_in_dispute=facts["amount_in_dispute"],
    )


def draft_claim(conn: sqlite3.Connection, client: llm.ModelClient, facts: dict) -> dict:
    """One model call; recipient and subject come from the facts, never from the model.

    An invalid answer or a failed fact check stores the template instead and parks the invoice as needs_review.
    A transient model error and a missing recording propagate.
    """
    reason = None
    try:
        body = client.extract(facts_prompt(facts), case_id=f"claim-{facts['invoice']}").body
        problems = check_draft(body, facts)
        reason = "; ".join(problems) or None
    except llm.InvalidModelOutput as error:
        reason = str(error)
    source = "model"
    if reason is not None:
        body, source = fill_template(facts), "template"
        db.park_failure(conn, f"claim-{facts['invoice']}", CHANNEL, facts["invoice"], STEP, reason)
    return {
        "recipient": facts["recipient"],
        "subject": CLAIM_SUBJECT.format(invoice=facts["invoice_number"] or facts["invoice"], order=facts["order"]),
        "body": body,
        "source": source,
    }


AWAITING, SENT, REJECTED = "awaiting_approval", "sent", "rejected"


class ClaimState(TypedDict, total=False):
    invoice_id: int
    message: dict
    decision: str
    final_body: str | None


def claim_thread_id(invoice_id: int) -> str:
    return f"claim-{reconciliation.invoice_reference(invoice_id)}"


def _claim_row(conn: sqlite3.Connection, invoice_id: int):
    return conn.execute(
        "SELECT c.* FROM claims c JOIN reconciliations r ON r.id = c.reconciliation_id WHERE r.invoice_id = ?",
        (invoice_id,),
    ).fetchone()


def draft_message(db_path: Path | str, client: llm.ModelClient | None, invoice_id: int) -> dict:
    """The stored claim as a message; a claim without a row is drafted and stored first, a stored one calls no model."""
    conn = db.connect(db_path)
    try:
        row = _claim_row(conn, invoice_id)
        if row is None:
            facts = claim_facts(conn, invoice_id)
            draft = draft_claim(conn, client, facts)
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO claims (reconciliation_id, recipient, subject, draft_body, draft_source, "
                    "status, drafted_at) SELECT id, ?, ?, ?, ?, ?, ? FROM reconciliations WHERE invoice_id = ?",
                    (
                        draft["recipient"],
                        draft["subject"],
                        draft["body"],
                        draft["source"],
                        AWAITING,
                        sim_clock.now(conn).strftime(sim_clock.FORMAT),
                        invoice_id,
                    ),
                )
            row = _claim_row(conn, invoice_id)
        return {"to": row["recipient"], "subject": row["subject"], "body": row["draft_body"]}
    finally:
        conn.close()


def send_claim(db_path: Path | str, outbox: Path | str, state: ClaimState) -> None:
    """Write the keyed email once and set the claim `sent`; a claim that is no longer awaiting is left alone."""
    conn = db.connect(db_path)
    try:
        row = _claim_row(conn, state["invoice_id"])
        if row["status"] != AWAITING:
            return
        code = conn.execute(
            "SELECT o.supplier_code FROM reconciliations r JOIN purchase_orders o ON o.id = r.purchase_order_id "
            "WHERE r.id = ?",
            (row["reconciliation_id"],),
        ).fetchone()[0]
        name = f"CLAIM-{reconciliation.invoice_reference(state['invoice_id'])}-{code}"
        quotes._write_email(
            Path(outbox), name, row["recipient"], row["subject"], state["final_body"], row["drafted_at"]
        )
        with conn:
            conn.execute(
                "UPDATE claims SET status = ?, final_body = ?, edited = ?, decided_at = ?, outbox_file = ? "
                "WHERE id = ? AND status = ?",
                (
                    SENT,
                    state["final_body"],
                    int(state["decision"] == review.EDIT),
                    sim_clock.now(conn).strftime(sim_clock.FORMAT),
                    str(Path(outbox) / f"{name}.eml"),
                    row["id"],
                    AWAITING,
                ),
            )
    finally:
        conn.close()


def close_claim(db_path: Path | str, state: ClaimState) -> None:
    """Set the claim `rejected` and write nothing; a claim that is no longer awaiting is left alone."""
    conn = db.connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE claims SET status = ?, decided_at = ? WHERE status = ? AND reconciliation_id = "
                "(SELECT id FROM reconciliations WHERE invoice_id = ?)",
                (REJECTED, sim_clock.now(conn).strftime(sim_clock.FORMAT), AWAITING, state["invoice_id"]),
            )
    finally:
        conn.close()


def build_claim_graph(
    db_path: Path | str, client: llm.ModelClient | None = None, outbox: Path | str | None = None, checkpointer=None
):
    """One thread per invoice: draft, review (the pause), then send or close."""

    def draft(state: ClaimState) -> ClaimState:
        return {"message": draft_message(db_path, client, state["invoice_id"])}

    def send(state: ClaimState) -> ClaimState:
        send_claim(db_path, outbox or config.SUPPLIER_OUTBOX_DIR, state)
        return {}

    def close(state: ClaimState) -> ClaimState:
        close_claim(db_path, state)
        return {}

    builder = StateGraph(ClaimState)
    builder.add_node("draft", draft, retry_policy=llm.MODEL_RETRY)
    builder.add_node("review", review.review)
    builder.add_node("send", send)
    builder.add_node("close", close)
    builder.add_edge(START, "draft")
    builder.add_edge("draft", "review")
    builder.add_conditional_edges(
        "review", lambda s: "close" if s["decision"] == review.REJECT else "send", ["send", "close"]
    )
    builder.add_edge("send", END)
    builder.add_edge("close", END)
    return builder.compile(checkpointer=checkpointer, name="claim")
