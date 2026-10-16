"""Phase 11: customer invoicing, signed delivery notes and sales matching.

Deterministic: no model is called here.
"""

import os
import sqlite3
from pathlib import Path

from purchase_cycle import sim_clock
from purchase_cycle.receipts import COMPANY, ascii_text

INVOICE_TABLES = ("customer_invoices", "customer_invoice_lines")


def _atomic_write(path: Path, text: str) -> None:
    """Write an ASCII file whole under its real name or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(text, encoding="ascii", newline="\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _number(prefix: str, row_id: int) -> str:
    return f"{prefix}-{row_id:04d}"


def _invoice_text(conn: sqlite3.Connection, invoice: sqlite3.Row) -> str:
    customer = conn.execute("SELECT * FROM customers WHERE code = ?", (invoice["customer_code"],)).fetchone()
    lines = conn.execute(
        "SELECT l.sku, p.name, l.quantity, p.sale_unit, l.unit_price, l.line_total FROM customer_invoice_lines l "
        "JOIN products p ON p.sku = l.sku WHERE l.invoice_id = ? ORDER BY l.line_no",
        (invoice["id"],),
    ).fetchall()
    out = [
        COMPANY,
        f"Invoice {invoice['number']}",
        f"Date: {invoice['issued_at'][:10]}",
        f"Delivery note: {_number('CDN', invoice['delivery_note_id'])}",
        f"Order: {_number('ORD', invoice['order_id'])}",
        f"Customer: {customer['name']} ({customer['code']}), {customer['city']}",
        "",
        *(
            f"{r['sku']}  {r['name']}  {r['quantity']} x {r['sale_unit']}  {r['unit_price']:.2f} EUR  "
            f"{r['line_total']:.2f} EUR"
            for r in lines
        ),
        "",
        f"Net total: {invoice['net_total']:.2f} EUR",
    ]
    return ascii_text("\n".join(out)) + "\n"


def _issue_one(conn: sqlite3.Connection, note: sqlite3.Row, invoices_dir: Path | str) -> dict:
    lines = conn.execute(
        "SELECT l.sku, l.quantity, p.price_eur FROM customer_delivery_note_lines l JOIN products p ON p.sku = l.sku "
        "WHERE l.note_id = ? ORDER BY l.line_no",
        (note["id"],),
    ).fetchall()
    priced = [(r["sku"], r["quantity"], r["price_eur"], round(r["quantity"] * r["price_eur"], 2)) for r in lines]
    with conn:
        invoice_id = conn.execute(
            "INSERT INTO customer_invoices (number, delivery_note_id, order_id, customer_code, issued_at, net_total, "
            "document_file) VALUES (?, ?, ?, ?, ?, ?, '')",
            (
                f"pending-{note['id']}",
                note["id"],
                note["order_id"],
                note["customer_code"],
                sim_clock.now(conn).strftime(sim_clock.FORMAT),
                round(sum(p[3] for p in priced), 2),
            ),
        ).lastrowid
        number = _number("SINV", invoice_id)
        conn.execute(
            "UPDATE customer_invoices SET number = ?, document_file = ? WHERE id = ?",
            (number, str(Path(invoices_dir) / f"{number}.txt"), invoice_id),
        )
        conn.executemany(
            "INSERT INTO customer_invoice_lines VALUES (?, ?, ?, ?, ?, ?)",
            [(invoice_id, n, *p) for n, p in enumerate(priced, 1)],
        )
    return {"invoice_id": invoice_id, "number": number, "note_id": note["id"]}


def write_invoice_files(conn: sqlite3.Connection) -> list[Path]:
    """Write the file of every invoice whose file is missing; existing files are left alone."""
    written = []
    for invoice in conn.execute("SELECT * FROM customer_invoices ORDER BY id").fetchall():
        path = Path(invoice["document_file"])
        if not path.exists():
            _atomic_write(path, _invoice_text(conn, invoice))
            written.append(path)
    return written


def invoice(conn: sqlite3.Connection, invoices_dir: Path | str) -> list[dict]:
    """Issue one invoice per customer delivery note with none (one transaction each), then write the missing files."""
    notes = conn.execute(
        "SELECT * FROM customer_delivery_notes n WHERE NOT EXISTS "
        "(SELECT 1 FROM customer_invoices i WHERE i.delivery_note_id = n.id) ORDER BY n.id"
    ).fetchall()
    issued = [_issue_one(conn, note, invoices_dir) for note in notes]
    write_invoice_files(conn)
    return issued
