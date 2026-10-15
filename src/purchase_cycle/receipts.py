"""Goods receipt: reconciled supplier delivery notes put their goods into stock.

Deterministic: no model is called here.
"""

import math
import os
import sqlite3
import unicodedata
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from purchase_cycle import db, sim_clock

CHANNEL = "receipts"
STEP = "goods_receipt"
COMPANY = "Almacenes Demo S.L."
MAX_QUANTITY = 2**63 - 1  # the largest value of a SQLite INTEGER


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
        if not math.isfinite(quantity) or quantity > MAX_QUANTITY:
            return f"line {line['line_no']} has quantity {quantity:g}, not a number that fits the stock"
        if quantity != int(quantity) or quantity <= 0:
            return f"line {line['line_no']} has quantity {quantity:g}, not a whole positive number"
    added: dict[str, int] = {}
    for line in lines:
        added[line["sku"]] = added.get(line["sku"], 0) + int(line["quantity"])
    for sku, units in added.items():
        on_hand = conn.execute("SELECT on_hand FROM stock WHERE sku = ?", (sku,)).fetchone()
        if (on_hand[0] if on_hand else 0) + units > MAX_QUANTITY:
            return f"receiving {units} units of {sku} would take its stock past {MAX_QUANTITY}"
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


def _needs(conn: sqlite3.Connection, order_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT sku, SUM(quantity) AS quantity FROM order_lines WHERE order_id = ? GROUP BY sku ORDER BY sku",
        (order_id,),
    ).fetchall()
    return {r["sku"]: r["quantity"] for r in rows}


def _insert_note(conn: sqlite3.Connection, order_id: int, notes_dir: Path | str) -> int:
    customer = conn.execute("SELECT customer_code FROM orders WHERE id = ?", (order_id,)).fetchone()[0]
    note_id = conn.execute(
        "INSERT INTO customer_delivery_notes (order_id, customer_code, issued_at, document_file) VALUES (?, ?, ?, '')",
        (order_id, customer, sim_clock.now(conn).strftime(sim_clock.FORMAT)),
    ).lastrowid
    conn.execute(
        "UPDATE customer_delivery_notes SET document_file = ? WHERE id = ?",
        (str(Path(notes_dir) / f"CDN-{note_id:04d}.txt"), note_id),
    )
    conn.execute(
        "INSERT INTO customer_delivery_note_lines SELECT ?, ROW_NUMBER() OVER (ORDER BY l.id), l.sku, l.quantity, "
        "p.sale_unit FROM order_lines l JOIN products p ON p.sku = l.sku WHERE l.order_id = ? ORDER BY l.id",
        (note_id, order_id),
    )
    return note_id


def serve(conn: sqlite3.Connection, notes_dir: Path | str) -> dict:
    """Serve the `received` orders by ascending id when stock covers every line; a skipped order reports its missing units.

    A served order gets its customer delivery note rows in the same transaction; the file is written by `issue`.
    """
    served, skipped = [], []
    for order in conn.execute("SELECT id FROM orders WHERE status = 'received' ORDER BY id").fetchall():
        needs = _needs(conn, order["id"])
        on_hand = {sku: conn.execute("SELECT on_hand FROM stock WHERE sku = ?", (sku,)).fetchone()[0] for sku in needs}
        missing = {sku: need - on_hand[sku] for sku, need in needs.items() if need > on_hand[sku]}
        if missing:
            skipped.append({"order_id": order["id"], "missing": missing})
            continue
        with conn:
            done = conn.execute(
                "UPDATE orders SET status = 'served' WHERE id = ? AND status = 'received'", (order["id"],)
            ).rowcount
            if done:
                for sku, need in needs.items():
                    conn.execute("UPDATE stock SET on_hand = on_hand - ? WHERE sku = ?", (need, sku))
                note_id = _insert_note(conn, order["id"], notes_dir)
        if done:
            served.append({"order_id": order["id"], "note_id": note_id, "lines": list(needs.items())})
    return {"served": served, "skipped": skipped}


def ascii_text(text: str) -> str:
    """Accents are dropped and any other non-ASCII character becomes a question mark."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return plain.encode("ascii", "replace").decode("ascii")


def _note_text(conn: sqlite3.Connection, note: sqlite3.Row) -> str:
    customer = conn.execute("SELECT * FROM customers WHERE code = ?", (note["customer_code"],)).fetchone()
    lines = conn.execute(
        "SELECT l.sku, p.name, l.quantity, l.sale_unit FROM customer_delivery_note_lines l "
        "JOIN products p ON p.sku = l.sku WHERE l.note_id = ? ORDER BY l.line_no",
        (note["id"],),
    ).fetchall()
    out = [
        COMPANY,
        f"Delivery note CDN-{note['id']:04d}",
        f"Date: {note['issued_at'][:10]}",
        f"Order: ORD-{note['order_id']:04d}",
        f"Customer: {customer['name']} ({customer['code']}), {customer['city']}",
        "",
        *(f"{r['sku']}  {r['name']}  {r['quantity']} x {r['sale_unit']}" for r in lines),
        "",
        "Signature of the customer: ______________________",
    ]
    return ascii_text("\n".join(out)) + "\n"


def issue(conn: sqlite3.Connection) -> list[Path]:
    """Write the file of every customer delivery note whose file is missing; existing files are left alone."""
    written = []
    for note in conn.execute("SELECT * FROM customer_delivery_notes ORDER BY id").fetchall():
        path = Path(note["document_file"])
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(
            path.name + ".tmp"
        )  # a crash while writing never leaves a partial file under the real name
        try:
            temp.write_text(_note_text(conn, note), encoding="ascii", newline="\n")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        written.append(path)
    return written


class ReceiptState(TypedDict, total=False):
    receipts: list[dict]
    served: list[dict]
    skipped: list[dict]
    issued: list[str]


def build_receipt_graph(db_path: Path | str, notes_dir: Path | str, checkpointer=None, interrupt_after=None):
    """One thread per run: receive the candidate notes, serve what stock covers, issue the delivery note files.

    Every node reads the database before acting, so a re-run or a resumption repeats no effect.
    """

    def receive_node(state: ReceiptState) -> ReceiptState:
        conn = db.connect(db_path)
        try:
            return {"receipts": receive(conn)}
        finally:
            conn.close()

    def serve_node(state: ReceiptState) -> ReceiptState:
        conn = db.connect(db_path)
        try:
            return serve(conn, notes_dir)
        finally:
            conn.close()

    def issue_node(state: ReceiptState) -> ReceiptState:
        conn = db.connect(db_path)
        try:
            return {"issued": [str(path) for path in issue(conn)]}
        finally:
            conn.close()

    builder = StateGraph(ReceiptState)
    builder.add_node("receive", receive_node)
    builder.add_node("serve", serve_node)
    builder.add_node("issue", issue_node)
    builder.add_edge(START, "receive")
    builder.add_edge("receive", "serve")
    builder.add_edge("serve", "issue")
    builder.add_edge("issue", END)
    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after, name="receipt")


THREAD_PREFIX = "receipt-run-"


def _last_run(checkpointer) -> int:
    numbers = [
        int(c.config["configurable"]["thread_id"].removeprefix(THREAD_PREFIX))
        for c in checkpointer.list(None)
        if c.config["configurable"]["thread_id"].startswith(THREAD_PREFIX)
    ]
    return max(numbers, default=0)


def run_receipts(db_path: Path | str, notes_dir: Path | str, checkpointer, interrupt_after=None) -> ReceiptState:
    """Resume the last run when it stopped before its end, otherwise start the next one; returns what the run did."""
    graph = build_receipt_graph(db_path, notes_dir, checkpointer, interrupt_after)
    last = _last_run(checkpointer)
    config = {"configurable": {"thread_id": f"{THREAD_PREFIX}{last:04d}"}}
    if last and graph.get_state(config).next:
        graph.invoke(None, config)
    else:
        config = {"configurable": {"thread_id": f"{THREAD_PREFIX}{last + 1:04d}"}}
        graph.invoke({}, config)
    return graph.get_state(config).values
