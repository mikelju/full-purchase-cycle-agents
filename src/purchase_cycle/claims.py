"""Phase 09: claims to the supplier for the invoices that phase 08 left with discrepancies."""

import json
import sqlite3

from purchase_cycle import llm, reconciliation

CLAIM_SUBJECT = "Claim on invoice {invoice} for purchase order {order}"
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


def draft_claim(client: llm.ModelClient, facts: dict) -> dict:
    """One model call; recipient and subject come from the facts, never from the model."""
    answer = client.extract(facts_prompt(facts), case_id=f"claim-{facts['invoice']}")
    return {
        "recipient": facts["recipient"],
        "subject": CLAIM_SUBJECT.format(invoice=facts["invoice_number"] or facts["invoice"], order=facts["order"]),
        "body": answer.body,
        "source": "model",
    }
