"""Goods receipt: reconciled supplier delivery notes put their goods into stock.

Deterministic: no model is called here.
"""

import sqlite3

from purchase_cycle import db, sim_clock

CHANNEL = "receipts"
STEP = "goods_receipt"


def candidates(conn: sqlite3.Connection) -> list[int]:
    """Ids of the linked delivery notes that a matched or discrepancies reconciliation references and that have no receipt."""
    rows = conn.execute(
        "SELECT d.id FROM supplier_documents d WHERE d.kind = 'delivery_note' AND d.status = 'linked' "
        "AND EXISTS (SELECT 1 FROM reconciliations r WHERE r.delivery_note_id = d.id "
        "AND r.result IN ('matched', 'discrepancies')) "
        "AND NOT EXISTS (SELECT 1 FROM goods_receipts g WHERE g.delivery_note_id = d.id) ORDER BY d.id"
    ).fetchall()
    return [r["id"] for r in rows]


def _problem(conn: sqlite3.Connection, lines: list[sqlite3.Row]) -> str | None:
    if not lines:
        return "the delivery note has no lines"
    for line in lines:
        sku, quantity = line["sku"], line["quantity"]
        if sku is None or conn.execute("SELECT 1 FROM products WHERE sku = ?", (sku,)).fetchone() is None:
            return f"line {line['line_no']} has no catalog SKU: {sku or line['product_text']}"
        if quantity != int(quantity) or quantity <= 0:
            return f"line {line['line_no']} has quantity {quantity:g}, not a whole positive number"
    return None


def _receive_one(conn: sqlite3.Connection, doc_id: int) -> dict:
    note = conn.execute("SELECT number FROM supplier_documents WHERE id = ?", (doc_id,)).fetchone()
    order_id = conn.execute(
        "SELECT purchase_order_id FROM reconciliations WHERE delivery_note_id = ? "
        "AND result IN ('matched', 'discrepancies') ORDER BY id LIMIT 1",
        (doc_id,),
    ).fetchone()[0]
    lines = conn.execute("SELECT * FROM document_lines WHERE document_id = ? ORDER BY line_no", (doc_id,)).fetchall()
    received_at = sim_clock.now(conn).strftime(sim_clock.FORMAT)
    problem = _problem(conn, lines)
    result = {"document_id": doc_id, "number": note["number"], "purchase_order_id": order_id}
    if problem:
        db.park_failure(
            conn, f"goods-receipt-DOC-{doc_id:04d}", CHANNEL, note["number"] or f"DOC-{doc_id:04d}", STEP, problem
        )
        with conn:
            receipt_id = conn.execute(
                "INSERT INTO goods_receipts (delivery_note_id, purchase_order_id, received_at, status) "
                "VALUES (?, ?, ?, 'failed')",
                (doc_id, order_id, received_at),
            ).lastrowid
        return {**result, "receipt_id": receipt_id, "status": "failed", "reason": problem, "lines": []}
    with conn:
        receipt_id = conn.execute(
            "INSERT INTO goods_receipts (delivery_note_id, purchase_order_id, received_at, status) "
            "VALUES (?, ?, ?, 'received')",
            (doc_id, order_id, received_at),
        ).lastrowid
        for line in lines:
            quantity = int(line["quantity"])
            conn.execute(
                "INSERT INTO goods_receipt_lines VALUES (?, ?, ?, ?)",
                (receipt_id, line["line_no"], line["sku"], quantity),
            )
            conn.execute("UPDATE stock SET on_hand = on_hand + ? WHERE sku = ?", (quantity, line["sku"]))
    return {
        **result,
        "receipt_id": receipt_id,
        "status": "received",
        "reason": None,
        "lines": [(line["sku"], int(line["quantity"])) for line in lines],
    }


def receive(conn: sqlite3.Connection) -> list[dict]:
    """Receive every candidate note, one transaction per note; a note that cannot be received is parked and the rest go on."""
    return [_receive_one(conn, doc_id) for doc_id in candidates(conn)]
