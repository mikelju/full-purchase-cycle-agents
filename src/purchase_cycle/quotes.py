"""Quotes module: notice out-of-stock demand and request quotes from suppliers, waiting on a simulated clock."""

import sqlite3
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import TypedDict

from langgraph.graph import START, StateGraph
from langgraph.types import interrupt

from purchase_cycle import db, email_order, sim_clock, web_form, whatsapp_order

# The status every channel gives a stored order; nothing ships or reserves stock yet, so all of them count.
ORDER_STATUSES = sorted({web_form.STATUS, email_order.STATUS, whatsapp_order.STATUS})
WAITING = "waiting"
REMINDER_AFTER = timedelta(days=3)
DEADLINE_AFTER = timedelta(days=5)
BUYER = "Purchasing department <purchasing@distributor.example.com>"

REQUEST_TEXT = """Dear {name},

Please send us your best price and delivery time for the following product:

  SKU: {sku}
  Product: {product}
  Sale unit: {sale_unit}
  Quantity: {quantity}

We need your offer by {deadline}.
Please reply to this email keeping the reference {reference} in the subject.

Kind regards,
Purchasing department
"""


class QuoteRequestState(TypedDict, total=False):
    request_id: int


def reference(request_id: int) -> str:
    return f"QR-{request_id:04d}"


def thread_id(request_id: int) -> str:
    return f"quote-{reference(request_id)}"


def shortfalls(conn: sqlite3.Connection) -> list[dict]:
    """Per SKU, units asked by stored orders minus `on_hand`, when positive and no request for it is waiting."""
    marks = ", ".join("?" for _ in ORDER_STATUSES)
    rows = conn.execute(
        "SELECT l.sku, SUM(l.quantity) AS demand, s.on_hand FROM order_lines l "
        "JOIN orders o ON o.id = l.order_id JOIN stock s ON s.sku = l.sku "
        f"WHERE o.status IN ({marks}) "
        "AND l.sku NOT IN (SELECT sku FROM quote_requests WHERE status = ?) "
        "GROUP BY l.sku HAVING SUM(l.quantity) > s.on_hand ORDER BY l.sku",
        (*ORDER_STATUSES, WAITING),
    ).fetchall()
    return [{**dict(r), "shortfall": r["demand"] - r["on_hand"]} for r in rows]


def _request(conn: sqlite3.Connection, request_id: int) -> dict:
    row = conn.execute(
        "SELECT q.*, p.name AS product, p.sale_unit, p.family FROM quote_requests q "
        "JOIN products p ON p.sku = q.sku WHERE q.id = ?",
        (request_id,),
    ).fetchone()
    return dict(row)


def _family_suppliers(conn: sqlite3.Connection, family: str) -> list[dict]:
    rows = conn.execute(
        "SELECT s.* FROM suppliers s JOIN supplier_families f ON f.supplier_code = s.code "
        "WHERE f.family = ? ORDER BY s.code",
        (family,),
    ).fetchall()
    return [dict(r) for r in rows]


def _write_email(outbox: Path, name: str, to: str, subject: str, body: str, sent: str) -> Path | None:
    """Write one keyed `.eml` file once; an existing file is kept, so a re-run node writes nothing new."""
    path = Path(outbox) / f"{name}.eml"
    if path.exists():
        return None
    message = EmailMessage()
    message["From"] = BUYER
    message["To"] = to
    message["Subject"] = subject
    # The date is the simulated time, never the real one, so the bytes replay the same way.
    message["Date"] = format_datetime(datetime.strptime(sent, sim_clock.FORMAT))
    message["Message-ID"] = f"<{name}@distributor.example.com>"
    message.set_content(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(message))
    return path


def send_request(db_path: Path | str, outbox: Path | str, request_id: int) -> list[Path]:
    """One supplier row and one request email per supplier of the family; a re-run adds no row or file."""
    conn = db.connect(db_path)
    try:
        request = _request(conn, request_id)
        ref = reference(request_id)
        written = []
        for supplier in _family_suppliers(conn, request["family"]):
            body = REQUEST_TEXT.format(
                name=supplier["name"],
                sku=request["sku"],
                product=request["product"],
                sale_unit=request["sale_unit"],
                quantity=request["quantity"],
                deadline=request["deadline"][:16],
                reference=ref,
            )
            subject = f"Quote request {ref} - {request['sku']}"
            path = _write_email(
                outbox, f"{ref}-request-{supplier['code']}", supplier["email"], subject, body, request["created_at"]
            )
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO quote_request_suppliers (request_id, supplier_code, sent_at) VALUES (?, ?, ?)",
                    (request_id, supplier["code"], request["created_at"]),
                )
            if path:
                written.append(path)
        return written
    finally:
        conn.close()


def build_quote_request_graph(db_path: Path | str, outbox: Path | str | None = None, checkpointer=None):
    def send(state: QuoteRequestState) -> QuoteRequestState:
        send_request(db_path, outbox, state["request_id"])
        return {}

    def wait(state: QuoteRequestState) -> QuoteRequestState:
        interrupt({"reference": reference(state["request_id"])})
        return {}

    builder = StateGraph(QuoteRequestState)
    builder.add_node("send", send)
    builder.add_node("wait", wait)
    builder.add_edge(START, "send")
    builder.add_edge("send", "wait")
    return builder.compile(checkpointer=checkpointer, name="quote_request")


def _config(request_id: int) -> dict:
    return {"configurable": {"thread_id": thread_id(request_id)}}


def scan(db_path: Path | str, checkpointer, outbox: Path | str) -> list[dict]:
    """Start one quote request thread per short SKU with no waiting request; no model call."""
    graph = build_quote_request_graph(db_path, outbox, checkpointer)
    conn = db.connect(db_path)
    started = []
    try:
        for short in shortfalls(conn):
            created = sim_clock.now(conn)
            with conn:
                # The thread id derives from the row id, so the row is fixed before the thread starts.
                request_id = conn.execute(
                    "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        short["sku"],
                        short["shortfall"],
                        f"pending-{short['sku']}",
                        WAITING,
                        created.strftime(sim_clock.FORMAT),
                        (created + REMINDER_AFTER).strftime(sim_clock.FORMAT),
                        (created + DEADLINE_AFTER).strftime(sim_clock.FORMAT),
                    ),
                ).lastrowid
                conn.execute(
                    "UPDATE quote_requests SET thread_id = ? WHERE id = ?", (thread_id(request_id), request_id)
                )
            graph.invoke({"request_id": request_id}, _config(request_id))
            started.append({"reference": reference(request_id), "sku": short["sku"], "quantity": short["shortfall"]})
    finally:
        conn.close()
    return started
