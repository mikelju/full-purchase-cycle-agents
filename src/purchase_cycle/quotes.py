"""Quotes module: notice out-of-stock demand and request quotes from suppliers, waiting on a simulated clock."""

import re
import sqlite3
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from purchase_cycle import db, email_order, sim_clock, web_form, whatsapp_order

# The status every channel gives a stored order; nothing ships or reserves stock yet, so all of them count.
ORDER_STATUSES = sorted({web_form.STATUS, email_order.STATUS, whatsapp_order.STATUS})
WAITING = "waiting"
REMINDER_AFTER = timedelta(days=3)
DEADLINE_AFTER = timedelta(days=5)
REFERENCE = re.compile(r"\bQR-(\d{4,})\b")
BUYER = "Purchasing department <purchasing@distributor.example.com>"

REQUEST_OPENING = "Please send us your best price and delivery time for the following product:"
REMINDER_OPENING = (
    "We have not received your offer for our quote request {reference} yet.\n"
    "Please send us your best price and delivery time for the following product:"
)
EMAIL_TEXT = """Dear {name},

{opening}

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
    reminders: list[str]  # supplier codes reminded by the last event
    done: bool  # the last event closes the request


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


def _email_body(opening: str, supplier: dict, request: dict) -> str:
    ref = reference(request["id"])
    return EMAIL_TEXT.format(
        name=supplier["name"],
        opening=opening.format(reference=ref),
        sku=request["sku"],
        product=request["product"],
        sale_unit=request["sale_unit"],
        quantity=request["quantity"],
        deadline=request["deadline"][:16],
        reference=ref,
    )


def send_request(db_path: Path | str, outbox: Path | str, request_id: int) -> list[Path]:
    """One supplier row and one request email per supplier of the family; a re-run adds no row or file."""
    conn = db.connect(db_path)
    try:
        request = _request(conn, request_id)
        ref = reference(request_id)
        written = []
        for supplier in _family_suppliers(conn, request["family"]):
            body = _email_body(REQUEST_OPENING, supplier, request)
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


def send_reminders(conn: sqlite3.Connection, outbox: Path | str, request_id: int, now: str) -> list[str]:
    """One reminder to each supplier that has neither replied nor been reminded; a re-run writes nothing new."""
    request = _request(conn, request_id)
    ref = reference(request_id)
    pending = conn.execute(
        "SELECT s.* FROM quote_request_suppliers r JOIN suppliers s ON s.code = r.supplier_code "
        "WHERE r.request_id = ? AND r.replied_at IS NULL AND r.reminded_at IS NULL ORDER BY s.code",
        (request_id,),
    ).fetchall()
    reminded = []
    for supplier in map(dict, pending):
        body = _email_body(REMINDER_OPENING, supplier, request)
        subject = f"Reminder: quote request {ref} - {request['sku']}"
        _write_email(outbox, f"{ref}-reminder-{supplier['code']}", supplier["email"], subject, body, now)
        with conn:
            conn.execute(
                "UPDATE quote_request_suppliers SET reminded_at = ? "
                "WHERE request_id = ? AND supplier_code = ? AND reminded_at IS NULL",
                (now, request_id, supplier["code"]),
            )
        reminded.append(supplier["code"])
    return reminded


def _pending(conn: sqlite3.Connection, request_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM quote_request_suppliers WHERE request_id = ? AND replied_at IS NULL", (request_id,)
    ).fetchone()[0]


def close_request(conn: sqlite3.Connection, request_id: int) -> str:
    """Close a waiting request as `ready_for_decision` with a reply, or `no_offers` with a parked failure."""
    request = _request(conn, request_id)
    replied = conn.execute(
        "SELECT COUNT(*) FROM quote_request_suppliers WHERE request_id = ? AND replied_at IS NOT NULL", (request_id,)
    ).fetchone()[0]
    status = "ready_for_decision" if replied else "no_offers"
    if request["status"] != WAITING:
        return request["status"]
    if not replied:
        # Parked before the status changes, so a re-run after a crash between both writes still parks it once.
        ref = reference(request_id)
        error = f"no supplier replied to quote request {ref} for {request['sku']} by {request['deadline'][:16]}"
        db.park_failure(conn, thread_id(request_id), "quotes", ref, "quote_wait", error)
    with conn:
        conn.execute("UPDATE quote_requests SET status = ? WHERE id = ? AND status = ?", (status, request_id, WAITING))
    return status


def build_quote_request_graph(db_path: Path | str, outbox: Path | str | None = None, checkpointer=None):
    def send(state: QuoteRequestState) -> QuoteRequestState:
        send_request(db_path, outbox, state["request_id"])
        return {}

    def wait(state: QuoteRequestState) -> QuoteRequestState:
        # Resumed with {"event": "reply", "supplier", "file", "now"} or {"event": "tick", "now"}; the CLI checks
        # a reply against the database first, so only an accepted one reaches the thread.
        event = interrupt({"reference": reference(state["request_id"])})
        conn = db.connect(db_path)
        reminders, done = [], False
        try:
            if event["event"] == "tick":
                request = _request(conn, state["request_id"])
                # The deadline closes the request at its first tick, with no reminder, since no email follows a close.
                done = event["now"] >= request["deadline"]
                if not done and event["now"] >= request["reminder_at"]:
                    reminders = send_reminders(conn, outbox, state["request_id"], event["now"])
            elif event["event"] == "reply":
                with conn:
                    conn.execute(
                        "UPDATE quote_request_suppliers SET replied_at = ?, reply_file = ? "
                        "WHERE request_id = ? AND supplier_code = ? AND replied_at IS NULL",
                        (event["now"], event["file"], state["request_id"], event["supplier"]),
                    )
                done = _pending(conn, state["request_id"]) == 0
        finally:
            conn.close()
        return {"reminders": reminders, "done": done}

    def close(state: QuoteRequestState) -> QuoteRequestState:
        conn = db.connect(db_path)
        try:
            close_request(conn, state["request_id"])
        finally:
            conn.close()
        return {}

    builder = StateGraph(QuoteRequestState)
    builder.add_node("send", send)
    builder.add_node("wait", wait)
    builder.add_edge(START, "send")
    builder.add_edge("send", "wait")
    builder.add_node("close", close)
    builder.add_conditional_edges("wait", lambda s: "close" if s["done"] else "wait", ["wait", "close"])
    builder.add_edge("close", END)
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


def check_reply(conn: sqlite3.Connection, path: Path) -> dict:
    """Match a reply file to a waiting request and one of its suppliers that has not replied; no body is read."""
    result = {"file": path.name, "reference": None, "supplier": None, "accepted": False, "reason": None}
    try:
        message, sender = email_order._parse_message(path.read_bytes())
    except (OSError, email_order.EmailRejected) as error:
        return {**result, "reason": f"the reply cannot be read: {error}"}
    found = REFERENCE.search(email_order._subject(message))
    if not found:
        return {**result, "reason": "no quote request reference in the subject"}
    request_id = int(found.group(1))
    ref = reference(request_id)
    result["reference"] = ref
    row = conn.execute("SELECT status FROM quote_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        return {**result, "reason": f"unknown request reference {ref}"}
    if row["status"] != WAITING:
        return {**result, "reason": f"request {ref} is closed ({row['status']})"}
    supplier = conn.execute(
        "SELECT r.supplier_code, r.replied_at FROM quote_request_suppliers r "
        "JOIN suppliers s ON s.code = r.supplier_code WHERE r.request_id = ? AND lower(s.email) = ?",
        (request_id, sender.lower()),
    ).fetchone()
    if supplier is None:
        return {**result, "reason": f"the sender {sender} is not a supplier of {ref}"}
    result["supplier"] = supplier["supplier_code"]
    if supplier["replied_at"] is not None:
        return {**result, "reason": f"supplier {supplier['supplier_code']} already replied to {ref}"}
    return {**result, "accepted": True, "request_id": request_id}


def receive(db_path: Path | str, checkpointer, folder: Path | str) -> list[dict]:
    """Read the reply `.eml` files of a folder in name order; each accepted reply resumes its request thread."""
    graph = build_quote_request_graph(db_path, None, checkpointer)
    conn = db.connect(db_path)
    results = []
    try:
        for path in sorted(Path(folder).glob("*.eml")):
            result = check_reply(conn, path)
            if result["accepted"]:
                now = sim_clock.now(conn).strftime(sim_clock.FORMAT)
                event = {"event": "reply", "supplier": result["supplier"], "file": str(path), "now": now}
                graph.invoke(Command(resume=event), _config(result.pop("request_id")))
            results.append(result)
    finally:
        conn.close()
    return results


def tick(db_path: Path | str, checkpointer, outbox: Path | str) -> list[dict]:
    """Resume every waiting request thread with the current simulated time, in request order."""
    graph = build_quote_request_graph(db_path, outbox, checkpointer)
    conn = db.connect(db_path)
    results = []
    try:
        now = sim_clock.now(conn).strftime(sim_clock.FORMAT)
        waiting = [
            r["id"] for r in conn.execute("SELECT id FROM quote_requests WHERE status = ? ORDER BY id", (WAITING,))
        ]
        for request_id in waiting:
            state = graph.invoke(Command(resume={"event": "tick", "now": now}), _config(request_id))
            status = conn.execute("SELECT status FROM quote_requests WHERE id = ?", (request_id,)).fetchone()[0]
            results.append(
                {"reference": reference(request_id), "reminders": len(state.get("reminders", [])), "status": status}
            )
    finally:
        conn.close()
    return results


def list_requests(conn: sqlite3.Connection) -> list[dict]:
    """Every request in id order with its replied and pending suppliers and its next reminder, if one is still due."""
    rows = []
    for request in conn.execute("SELECT * FROM quote_requests ORDER BY id").fetchall():
        suppliers = conn.execute(
            "SELECT supplier_code, reminded_at, replied_at FROM quote_request_suppliers "
            "WHERE request_id = ? ORDER BY supplier_code",
            (request["id"],),
        ).fetchall()
        reminded = any(s["reminded_at"] for s in suppliers)
        rows.append(
            {
                "reference": reference(request["id"]),
                "sku": request["sku"],
                "quantity": request["quantity"],
                "status": request["status"],
                "replied": [s["supplier_code"] for s in suppliers if s["replied_at"]],
                "pending": [s["supplier_code"] for s in suppliers if not s["replied_at"]],
                # One reminder per request (D5): none is due once it was sent or the request closed.
                "next_reminder": request["reminder_at"] if request["status"] == WAITING and not reminded else None,
                "deadline": request["deadline"],
            }
        )
    return rows
