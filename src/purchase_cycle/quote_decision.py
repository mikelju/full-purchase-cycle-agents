"""Quotes module, decision step: extract one offer per supplier reply, check it and store it."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from purchase_cycle import config, db, email_order, llm, quotes, sim_clock

CURRENCY = "EUR"
CHANNEL = "quotes"
STEP = "offer_extraction"

# Why an offer cannot be recommended; every other offer is usable.
DECLINED = "declined"
NO_PRICE = "no_price"
WRONG_CURRENCY = "currency"
NO_LEAD_TIME = "no_lead_time"
VAT_RATE_MISSING = "vat_rate_missing"
EXTRACTION_FAILED = "extraction_failed"

REQUEST_TEXT = """Request: {reference}
SKU: {sku}
Product: {product}
Sale unit: {sale_unit}
Requested quantity: {quantity}

Supplier reply:
{body}"""


def request_row(conn: sqlite3.Connection, request_id: int) -> dict:
    return quotes._request(conn, request_id)


def model_text(request: dict, body: str) -> str:
    """The only text the model sees: the request and the reply body, with no headers."""
    return REQUEST_TEXT.format(
        reference=quotes.reference(request["id"]),
        sku=request["sku"],
        product=request["product"],
        sale_unit=request["sale_unit"],
        quantity=request["quantity"],
        body=body,
    )


def offer_thread_id(request_id: int, supplier_code: str) -> str:
    return f"offer-{quotes.reference(request_id)}-{supplier_code}"


def assess(fields: dict) -> tuple[bool, str | None, float | None]:
    """Usable or not, the reason when not, and the net unit price before VAT; computed here, never by the model."""
    if not fields["offers"] or not fields["quantity"] or fields["quantity"] <= 0:
        return False, DECLINED, None
    price = fields["unit_price"]
    if price is None or price <= 0:
        return False, NO_PRICE, None
    if fields["currency"] != CURRENCY:
        return False, WRONG_CURRENCY, None
    if fields["lead_time_days"] is None or fields["lead_time_days"] < 0:
        return False, NO_LEAD_TIME, None
    if fields["vat_included"]:
        rate = fields["vat_rate"]
        if rate is None or rate < 0:
            return False, VAT_RATE_MISSING, None
        price = price / (1 + rate / 100)
    return True, None, round(price, 4)


def _store(conn: sqlite3.Connection, request_id: int, supplier: str, reply_file: str, fields: dict | None) -> None:
    if fields is None:  # the answer broke the schema: nothing was extracted
        values = dict.fromkeys(llm.OfferExtraction.model_fields)
        usable, reason, net = False, EXTRACTION_FAILED, None
    else:
        values = fields
        usable, reason, net = assess(fields)

    def flag(value):
        return None if value is None else int(value)

    with conn:
        conn.execute(
            "INSERT INTO quote_offers (request_id, supplier_code, offers, quantity, unit_price, currency, "
            "vat_included, vat_rate, lead_time_days, working_days, price_text, net_unit_price, usable, reason, "
            "reply_file) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                supplier,
                flag(values["offers"]),
                values["quantity"],
                values["unit_price"],
                values["currency"],
                flag(values["vat_included"]),
                values["vat_rate"],
                values["lead_time_days"],
                flag(values["working_days"]),
                values["price_text"],
                net,
                int(usable),
                reason,
                reply_file,
            ),
        )


def stored_offers(conn: sqlite3.Connection, request_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM quote_offers WHERE request_id = ? ORDER BY supplier_code", (request_id,))
    return [dict(r) for r in rows]


def extract_offers(db_path: Path | str, request_id: int, client: llm.ModelClient) -> list[dict]:
    """Store one offer per replied supplier of a request and return every stored offer.

    A reply that already has a row is not read again, so a resumed run calls no model.
    A transient model error propagates before the row is written, for the node retry policy to repeat the call.
    """
    conn = db.connect(db_path)
    try:
        request = request_row(conn, request_id)
        done = {r["supplier_code"] for r in stored_offers(conn, request_id)}
        replies = conn.execute(
            "SELECT supplier_code, reply_file FROM quote_request_suppliers "
            "WHERE request_id = ? AND reply_file IS NOT NULL ORDER BY supplier_code",
            (request_id,),
        ).fetchall()
        for reply in replies:
            supplier, reply_file = reply["supplier_code"], reply["reply_file"]
            if supplier in done:
                continue
            try:
                body = email_order.parse_email(Path(reply_file).read_bytes())["body"]
                text = model_text(request, body)
                fields = client.extract(text, case_id=offer_thread_id(request_id, supplier)).model_dump()
            except (OSError, email_order.EmailRejected, llm.InvalidModelOutput) as error:
                # Park first: a crash between the two writes then repeats the call and loses no failure row.
                db.park_failure(
                    conn,
                    offer_thread_id(request_id, supplier),
                    CHANNEL,
                    reply_file,
                    STEP,
                    f"{type(error).__name__}: {error}",
                )
                _store(conn, request_id, supplier, reply_file, None)
                continue
            _store(conn, request_id, supplier, reply_file, fields)
        return stored_offers(conn, request_id)
    finally:
        conn.close()


def lead_days(offer: dict, start: datetime) -> int:
    """Lead time in calendar days from the start; a null working days flag counts calendar days."""
    reached = sim_clock.add_days(start, offer["lead_time_days"], bool(offer["working_days"]))
    return (reached - start).days


def _entry(offer: dict, requested: int, start: datetime) -> dict:
    full = offer["quantity"] >= requested
    quantity = requested if full else offer["quantity"]
    return {
        "supplier": offer["supplier_code"],
        "full": full,
        "quantity": quantity,
        "total": round(offer["net_unit_price"] * quantity, 2),
        "lead": lead_days(offer, start),
    }


def _describe(entry: dict, requested: int) -> str:
    kind = "full" if entry["full"] else f"partial {entry['quantity']} of {requested}"
    return f"{entry['supplier']} {CURRENCY} {entry['total']:.2f} ({kind}, {entry['lead']} days)"


def compare_offers(offers: list[dict], requested: int, start: datetime) -> dict:
    """Pick the offer to recommend by the fixed rule; no model is called.

    Order: full offers first, then the lowest total, the shortest lead time in calendar days from `start`
    and the lower supplier code.
    The result names the deciding rule: full_quantity, lowest_total, shortest_lead_time, supplier_code,
    only_usable_offer, partial_only (no full offer is usable) or none.
    """
    usable = [o for o in offers if o["usable"]]
    if not usable:
        why = ", ".join(f"{o['supplier_code']} {o['reason']}" for o in offers) or "no supplier replied"
        return {"supplier": None, "rule": "none", "quantity": None, "total": None, "reason": f"No usable offer: {why}."}
    entries = sorted(
        (_entry(o, requested, start) for o in usable),
        key=lambda e: (not e["full"], e["total"], e["lead"], e["supplier"]),
    )
    best = entries[0]
    if len(entries) == 1:
        rule, text = "only_usable_offer", "it is the only usable offer"
    else:
        second = entries[1]
        if best["full"] != second["full"]:
            rule, text = "full_quantity", "it is the only offer that covers the full quantity"
        elif best["total"] != second["total"]:
            rule, text = "lowest_total", "it has the lowest total"
        elif best["lead"] != second["lead"]:
            rule, text = "shortest_lead_time", "totals are equal and it has the shortest lead time"
        else:
            rule, text = "supplier_code", "total and lead time are equal and it has the lower supplier code"
    if not best["full"]:
        rule, text = "partial_only", f"no usable offer covers the full quantity and {text}"
    compared = "; ".join(_describe(e, requested) for e in entries)
    return {
        "supplier": best["supplier"],
        "rule": rule,
        "quantity": best["quantity"],
        "total": best["total"],
        "reason": f"Recommended {best['supplier']}: {text}. Compared: {compared}.",
    }


AWAITING = "awaiting_approval"
ORDERED = "ordered"
REJECTED = "rejected"
APPROVE, CHOOSE, REJECT = "approve", "choose", "reject"

ORDER_SUBJECT = "Purchase order {number} - quote request {reference}"
ORDER_TEXT = """Dear {name},

Thank you for your offer for quote request {reference}.
Please find our purchase order {number} below.

  SKU: {sku}
  Product: {product}
  Sale unit: {sale_unit}
  Quantity: {quantity}
  Unit price: {unit_price:.4f} {currency}
  Total: {total:.2f} {currency}
  Expected delivery: {expected_delivery}

Please confirm receipt of this order.

Kind regards,
Purchasing department
"""


class DecisionRefused(Exception):
    """A command that the request cannot take now; nothing was changed."""


class DecisionState(TypedDict, total=False):
    request_id: int
    recommendation: dict
    action: str
    supplier: str


def decision_thread_id(request_id: int) -> str:
    return f"decision-{quotes.reference(request_id)}"


def _decision_config(request_id: int) -> dict:
    return {"configurable": {"thread_id": decision_thread_id(request_id)}}


def po_number(order_id: int) -> str:
    return f"PO-{order_id:04d}"


def recommendation(conn: sqlite3.Connection, request_id: int) -> dict:
    """The recommendation over the stored offers, anchored on the request creation time so it never moves."""
    request = request_row(conn, request_id)
    start = datetime.strptime(request["created_at"], sim_clock.FORMAT)
    return compare_offers(stored_offers(conn, request_id), request["quantity"], start)


def _set_status(conn: sqlite3.Connection, request_id: int, status: str, expected: str) -> None:
    with conn:
        conn.execute("UPDATE quote_requests SET status = ? WHERE id = ? AND status = ?", (status, request_id, expected))


def issue_order(conn: sqlite3.Connection, outbox: Path | str, request_id: int, supplier: str) -> dict:
    """Write the purchase order and its email once; a re-run reads the row back and writes nothing new."""
    request = request_row(conn, request_id)
    order = conn.execute("SELECT * FROM purchase_orders WHERE request_id = ?", (request_id,)).fetchone()
    if order is None:
        offer = next(o for o in stored_offers(conn, request_id) if o["supplier_code"] == supplier)
        now = sim_clock.now(conn)
        entry = _entry(offer, request["quantity"], now)
        delivery = sim_clock.add_days(now, offer["lead_time_days"], bool(offer["working_days"]))
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO purchase_orders (request_id, supplier_code, sku, quantity, unit_price, "
                "currency, total, expected_delivery, issued_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'issued')",
                (
                    request_id,
                    supplier,
                    request["sku"],
                    entry["quantity"],
                    offer["net_unit_price"],
                    CURRENCY,
                    entry["total"],
                    delivery.strftime("%Y-%m-%d"),
                    now.strftime(sim_clock.FORMAT),
                ),
            )
        order = conn.execute("SELECT * FROM purchase_orders WHERE request_id = ?", (request_id,)).fetchone()
    number = po_number(order["id"])
    row = conn.execute("SELECT name, email FROM suppliers WHERE code = ?", (order["supplier_code"],)).fetchone()
    ref = quotes.reference(request_id)
    body = ORDER_TEXT.format(
        name=row["name"],
        reference=ref,
        number=number,
        sku=order["sku"],
        product=request["product"],
        sale_unit=request["sale_unit"],
        quantity=order["quantity"],
        unit_price=order["unit_price"],
        currency=order["currency"],
        total=order["total"],
        expected_delivery=order["expected_delivery"],
    )
    quotes._write_email(
        Path(outbox),
        f"{number}-{order['supplier_code']}",
        row["email"],
        ORDER_SUBJECT.format(number=number, reference=ref),
        body,
        order["issued_at"],
    )
    _set_status(conn, request_id, ORDERED, AWAITING)
    return {**dict(order), "number": number}


def build_quote_decision_graph(
    db_path: Path | str, client: llm.ModelClient | None = None, outbox: Path | str | None = None, checkpointer=None
):
    def extract(state: DecisionState) -> DecisionState:
        extract_offers(db_path, state["request_id"], client)
        return {}

    def compare(state: DecisionState) -> DecisionState:
        conn = db.connect(db_path)
        try:
            found = recommendation(conn, state["request_id"])
            _set_status(conn, state["request_id"], AWAITING, "ready_for_decision")
        finally:
            conn.close()
        return {"recommendation": found}

    def approve(state: DecisionState) -> DecisionState:
        # Resumed with {"action": "approve" | "choose" | "reject", "supplier": code}; the commands check the
        # request and the offer first, so only an accepted answer reaches the thread.
        found = state["recommendation"]
        answer = interrupt(
            {
                "reference": quotes.reference(state["request_id"]),
                "recommended": found["supplier"],
                "reason": found["reason"],
            }
        )
        return {"action": answer["action"], "supplier": answer.get("supplier")}

    def issue(state: DecisionState) -> DecisionState:
        conn = db.connect(db_path)
        try:
            issue_order(conn, outbox or config.SUPPLIER_OUTBOX_DIR, state["request_id"], state["supplier"])
        finally:
            conn.close()
        return {}

    def close(state: DecisionState) -> DecisionState:
        conn = db.connect(db_path)
        try:
            _set_status(conn, state["request_id"], REJECTED, AWAITING)
        finally:
            conn.close()
        return {}

    builder = StateGraph(DecisionState)
    builder.add_node("extract", extract, retry_policy=llm.MODEL_RETRY)
    builder.add_node("compare", compare)
    builder.add_node("approve", approve)
    builder.add_node("issue", issue)
    builder.add_node("close", close)
    builder.add_edge(START, "extract")
    builder.add_edge("extract", "compare")
    builder.add_edge("compare", "approve")
    builder.add_conditional_edges(
        "approve", lambda s: "close" if s["action"] == REJECT else "issue", ["issue", "close"]
    )
    builder.add_edge("issue", END)
    builder.add_edge("close", END)
    return builder.compile(checkpointer=checkpointer, name="quote_decision")


def decide(db_path: Path | str, checkpointer, client: llm.ModelClient, outbox: Path | str | None = None) -> dict:
    """Start the decision of every `ready_for_decision` request; each runs to its pause in `approve`.

    Returns the decisions started and the references of the `no_offers` requests, which wait for a person.
    """
    graph = build_quote_decision_graph(db_path, client, outbox, checkpointer)
    conn = db.connect(db_path)
    decided = []
    try:
        query = "SELECT id FROM quote_requests WHERE status = 'ready_for_decision' ORDER BY id"
        for (request_id,) in conn.execute(query).fetchall():
            run = _decision_config(request_id)
            state = graph.get_state(run)
            if not state.values:
                graph.invoke({"request_id": request_id}, run)
            elif state.next:  # a run that stopped before its pause goes on from its checkpoint
                graph.invoke(None, run)
            request = request_row(conn, request_id)
            decided.append(
                {
                    "reference": quotes.reference(request_id),
                    "sku": request["sku"],
                    "quantity": request["quantity"],
                    "status": request_row(conn, request_id)["status"],
                    "offers": stored_offers(conn, request_id),
                    "recommendation": recommendation(conn, request_id),
                }
            )
        waiting = [
            quotes.reference(r[0])
            for r in conn.execute("SELECT id FROM quote_requests WHERE status = 'no_offers' ORDER BY id")
        ]
    finally:
        conn.close()
    return {"decided": decided, "no_offers": waiting}


def _check_awaiting(conn: sqlite3.Connection, request_id: int) -> None:
    ref = quotes.reference(request_id)
    row = conn.execute("SELECT status FROM quote_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise DecisionRefused(f"unknown quote request {ref}")
    if row["status"] != AWAITING:
        raise DecisionRefused(f"request {ref} is {row['status']}, not {AWAITING}")


def _resume(db_path: Path | str, checkpointer, outbox, request_id: int, answer: dict) -> None:
    graph = build_quote_decision_graph(db_path, None, outbox, checkpointer)
    graph.invoke(Command(resume=answer), _decision_config(request_id))


def approve_request(
    db_path: Path | str, checkpointer, outbox: Path | str, request_id: int, supplier: str | None = None
) -> dict:
    """Approve the recommendation, or the usable offer of `supplier`; refused commands change nothing."""
    ref = quotes.reference(request_id)
    conn = db.connect(db_path)
    try:
        _check_awaiting(conn, request_id)
        if supplier is None:
            chosen = recommendation(conn, request_id)["supplier"]
            if chosen is None:
                raise DecisionRefused(f"request {ref} has no usable offer; only reject is accepted")
        else:
            chosen = supplier
            offer = next((o for o in stored_offers(conn, request_id) if o["supplier_code"] == chosen), None)
            if offer is None:
                raise DecisionRefused(f"supplier {chosen} made no offer for {ref}")
            if not offer["usable"]:
                raise DecisionRefused(f"the offer of {chosen} for {ref} is not usable ({offer['reason']})")
    finally:
        conn.close()
    action = APPROVE if supplier is None else CHOOSE
    _resume(db_path, checkpointer, outbox, request_id, {"action": action, "supplier": chosen})
    conn = db.connect(db_path)
    try:
        order = conn.execute("SELECT * FROM purchase_orders WHERE request_id = ?", (request_id,)).fetchone()
        return {"reference": ref, "supplier": chosen, "order": dict(order), "number": po_number(order["id"])}
    finally:
        conn.close()


def reject_request(db_path: Path | str, checkpointer, request_id: int) -> dict:
    """Close an awaiting request as `rejected`, with no purchase order and no email."""
    conn = db.connect(db_path)
    try:
        _check_awaiting(conn, request_id)
    finally:
        conn.close()
    _resume(db_path, checkpointer, None, request_id, {"action": REJECT})
    return {"reference": quotes.reference(request_id), "status": REJECTED}
