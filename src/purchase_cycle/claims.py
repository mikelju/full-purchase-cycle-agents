"""Phase 09: claims to the supplier for the invoices that phase 08 left with discrepancies."""

import sqlite3

from purchase_cycle import reconciliation

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
