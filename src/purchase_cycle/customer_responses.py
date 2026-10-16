"""Phase 11: the response to a customer about the incidents of a signed delivery note.

The facts, the amounts and the documents come from code; the model only writes the body, and a deterministic check
in the style of phase 09 decides whether the draft may be shown or the fixed template takes its place.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from purchase_cycle import config, db, llm, quotes, review, sales, sim_clock
from purchase_cycle.claims import _near, _own_file_date, _token, mentions, money_amounts
from purchase_cycle.receipts import COMPANY, ascii_text

RESPONSE_SUBJECT = "Response to your delivery note {delivery_note} on invoice {invoice}"
MAX_RESPONSE_BODY = 3000
CHANNEL = "sales"
STEP = "response_drafting"
KIND_LABEL = {"credit_note": "Credit note", "corrective_invoice": "Corrective invoice"}
TYPE_LABEL = {"missing": "missing", "extra": "delivered in excess", "damaged": "damaged"}
RESPONSE_TEMPLATE = """Dear {customer},

Thank you for signing delivery note {delivery_note} for invoice {invoice}.
You reported these incidents:
{incidents}

The net total of the invoice is {net_total:.2f} EUR.
{documents}

Please contact us if you have any question.

Best regards,
Sales department"""


def response_facts(conn: sqlite3.Connection, invoice_id: int) -> dict | None:
    """The facts of the response of a `discrepancies` invoice; None for any other invoice.

    Plans the correcting documents first (idempotent), so the documents of the facts are the stored ones.
    """
    head = conn.execute(
        "SELECT i.id, i.number, i.delivery_note_id, i.net_total, c.name, c.email, r.signed_note_id "
        "FROM customer_invoices i JOIN customers c ON c.code = i.customer_code "
        "JOIN sales_reconciliations r ON r.invoice_id = i.id WHERE i.id = ? AND r.result = 'discrepancies'",
        (invoice_id,),
    ).fetchone()
    if head is None:
        return None
    sales.plan_corrections(conn)
    incidents = [
        dict(r)
        for r in conn.execute(
            "SELECT n.sku, p.name AS product, n.type, n.units FROM signed_note_incidents n "
            "JOIN products p ON p.sku = n.sku WHERE n.signed_note_id = ? ORDER BY n.line_no",
            (head["signed_note_id"],),
        )
    ]
    documents = []
    for doc in conn.execute("SELECT * FROM customer_corrections WHERE invoice_id = ? ORDER BY id", (invoice_id,)):
        lines = [
            dict(r)
            for r in conn.execute(
                "SELECT sku, quantity, unit_price, line_total FROM customer_correction_lines "
                "WHERE correction_id = ? ORDER BY line_no",
                (doc["id"],),
            )
        ]
        documents.append({"number": doc["number"], "kind": doc["kind"], "total": doc["total"], "lines": lines})
    return {
        "customer": head["name"],
        "recipient": head["email"],
        "invoice": head["number"],
        "delivery_note": sales._number("CDN", head["delivery_note_id"]),
        "net_total": head["net_total"],
        "incidents": incidents,
        "documents": documents,
    }


def facts_prompt(facts: dict) -> str:
    """The facts as one JSON object with sorted keys and two-decimal amounts; no recipient, so the key is stable."""
    documents = []
    for doc in facts["documents"]:
        lines = [
            {**line, "unit_price": f"{line['unit_price']:.2f}", "line_total": f"{line['line_total']:.2f}"}
            for line in doc["lines"]
        ]
        documents.append({**doc, "total": f"{doc['total']:.2f}", "lines": lines})
    shown = {k: v for k, v in facts.items() if k != "recipient"}
    shown |= {"documents": documents, "net_total": f"{facts['net_total']:.2f}"}
    return json.dumps(shown, sort_keys=True)


def _segment(body: str, token: str, stops: set[str]) -> list[str]:
    """The text after each mention of the token up to the next mention of another stop token."""
    cuts = sorted(m.start() for name in stops - {token} for m in re.finditer(_token(name), body))
    parts = []
    for m in re.finditer(_token(token), body):
        end = next((pos for pos in cuts if pos > m.start()), len(body))
        parts.append(body[m.end() : end])
    return parts


def _without(text: str, names: set[str]) -> str:
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name, " ")
    return text


def check_response(body: str, facts: dict) -> list[str]:
    """Deterministic fact check of a response draft; an empty list means it passes."""
    problems = []
    for label, reference in (("invoice number", facts["invoice"]), ("delivery note number", facts["delivery_note"])):
        if not mentions(body, reference):
            problems.append(f"missing {label} {reference}")
    skus = {row["sku"] for row in facts["incidents"]}
    numbers = {doc["number"] for doc in facts["documents"]}
    products = {row["product"] for row in facts["incidents"]}
    for row in facts["incidents"]:
        if not mentions(body, row["sku"]):
            problems.append(f"missing incident {row['sku']}")
            continue
        units = rf"(?<![\d.,]){row['units']}(?!\d|[.,]\d)"
        if not any(re.search(units, _without(part, products)) for part in _segment(body, row["sku"], skus | numbers)):
            problems.append(f"missing units {row['units']} of {row['sku']}")
    for doc in facts["documents"]:
        if not mentions(body, doc["number"]):
            problems.append(f"missing document {doc['number']}")
            continue
        parts = _segment(body, doc["number"], numbers)
        if not any(
            _near(v, abs(doc["total"])) for part in parts for v in money_amounts(_without(part, skus | products))
        ):
            problems.append(f"missing total {abs(doc['total']):.2f} of {doc['number']}")
    known = {abs(facts["net_total"])}
    for doc in facts["documents"]:
        known |= {abs(doc["total"])}
        known |= {abs(line[key]) for line in doc["lines"] for key in ("unit_price", "line_total")}
    for value in money_amounts(_without(body, skus | products)):
        if not any(_near(value, amount) for amount in known):
            problems.append(f"invented amount {value:.2f}")
    if not body.isascii():
        problems.append("body is not ASCII")
    if len(body) > MAX_RESPONSE_BODY:
        problems.append(f"body longer than {MAX_RESPONSE_BODY} characters")
    return problems


def fill_template(facts: dict) -> str:
    incidents = "\n".join(
        f"- {row['sku']} {row['product']}: {TYPE_LABEL[row['type']]}, {row['units']} units"
        for row in facts["incidents"]
    )
    documents = "\n".join(
        f"{KIND_LABEL[doc['kind']]} {doc['number']} has a total of {abs(doc['total']):.2f} EUR."
        for doc in facts["documents"]
    )
    return RESPONSE_TEMPLATE.format(
        customer=facts["customer"],
        delivery_note=facts["delivery_note"],
        invoice=facts["invoice"],
        incidents=incidents,
        net_total=facts["net_total"],
        documents=documents,
    )


def draft_response(conn: sqlite3.Connection, client: llm.ModelClient, facts: dict) -> dict:
    """One model call; recipient and subject come from the facts, never from the model.

    An invalid answer or a failed fact check stores the template instead and parks the invoice as needs_review.
    A transient model error and a missing recording propagate.
    """
    reason = None
    try:
        body = client.extract(facts_prompt(facts), case_id=f"response-{facts['invoice']}").body
        reason = "; ".join(check_response(body, facts)) or None
    except llm.InvalidModelOutput as error:
        reason = str(error)
    source = "model"
    if reason is not None:
        body, source = fill_template(facts), "template"
        db.park_failure(conn, f"response-{facts['invoice']}", CHANNEL, facts["invoice"], STEP, reason)
    return {
        "recipient": facts["recipient"],
        "subject": RESPONSE_SUBJECT.format(delivery_note=facts["delivery_note"], invoice=facts["invoice"]),
        "body": body,
        "source": source,
    }


# ---- the response graph: draft, review (the pause), then send or close ----

AWAITING, SENT, REJECTED = "awaiting_approval", "sent", "rejected"


class ResponseState(TypedDict, total=False):
    invoice_id: int
    message: dict
    decision: str
    final_body: str | None


def _invoice_number(conn: sqlite3.Connection, invoice_id: int) -> str:
    return conn.execute("SELECT number FROM customer_invoices WHERE id = ?", (invoice_id,)).fetchone()[0]


def response_thread_id(conn: sqlite3.Connection, invoice_id: int) -> str:
    return f"response-{_invoice_number(conn, invoice_id)}"


def _response_row(conn: sqlite3.Connection, invoice_id: int):
    return conn.execute(
        "SELECT p.* FROM customer_responses p JOIN sales_reconciliations r ON r.id = p.reconciliation_id "
        "WHERE r.invoice_id = ?",
        (invoice_id,),
    ).fetchone()


def draft_message(db_path: Path | str, client: llm.ModelClient | None, invoice_id: int) -> dict:
    """The stored response as a message; one without a row is drafted and stored first, a stored one calls no model."""
    conn = db.connect(db_path)
    try:
        row = _response_row(conn, invoice_id)
        if row is None:
            facts = response_facts(conn, invoice_id)
            if facts is None:
                raise ValueError(f"invoice {invoice_id} has no discrepancies to respond to")
            draft = draft_response(conn, client, facts)
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO customer_responses (reconciliation_id, recipient, subject, draft_body, "
                    "draft_source, status, drafted_at) SELECT id, ?, ?, ?, ?, ?, ? FROM sales_reconciliations "
                    "WHERE invoice_id = ?",
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
            row = _response_row(conn, invoice_id)
        return {"to": row["recipient"], "subject": row["subject"], "body": row["draft_body"]}
    finally:
        conn.close()


def correction_text(conn: sqlite3.Connection, correction: sqlite3.Row) -> str:
    """The text file of one correcting document, from the stored rows only."""
    invoice = conn.execute("SELECT * FROM customer_invoices WHERE id = ?", (correction["invoice_id"],)).fetchone()
    customer = conn.execute("SELECT * FROM customers WHERE code = ?", (invoice["customer_code"],)).fetchone()
    lines = conn.execute(
        "SELECT l.sku, p.name, l.quantity, p.sale_unit, l.unit_price, l.line_total FROM customer_correction_lines l "
        "JOIN products p ON p.sku = l.sku WHERE l.correction_id = ? ORDER BY l.line_no",
        (correction["id"],),
    ).fetchall()
    out = [
        COMPANY,
        f"{KIND_LABEL[correction['kind']]} {correction['number']}",
        f"Corrects invoice: {invoice['number']}",
        f"Delivery note: {sales._number('CDN', invoice['delivery_note_id'])}",
        f"Customer: {customer['name']} ({customer['code']}), {customer['city']}",
        "",
        *(
            f"{r['sku']}  {r['name']}  {r['quantity']} x {r['sale_unit']}  {r['unit_price']:.2f} EUR  "
            f"{r['line_total']:.2f} EUR"
            for r in lines
        ),
        "",
        f"Total: {correction['total']:.2f} EUR",
    ]
    return ascii_text("\n".join(out)) + "\n"


def _resolve_failure(conn: sqlite3.Connection, invoice_id: int) -> None:
    """The decision settles the parked template-fallback row of the response, if there is one; the caller commits."""
    conn.execute(
        "UPDATE failures SET status = 'resolved', updated_at = datetime('now') "
        "WHERE thread_id = ? AND status = 'needs_review'",
        (response_thread_id(conn, invoice_id),),
    )


def send_response(db_path: Path | str, invoices_dir: Path | str, outbox: Path | str, state: ResponseState) -> None:
    """Write the correction files, then the keyed email, then set the row `sent`; a step already done is kept."""
    conn = db.connect(db_path)
    try:
        row = _response_row(conn, state["invoice_id"])
        if row["status"] != AWAITING:
            return
        path = Path(outbox) / f"RESPONSE-{_invoice_number(conn, state['invoice_id'])}.eml"
        # an own file with another text refuses before any document is written
        decided_at = _own_file_date(path, state["final_body"]) or sim_clock.now(conn).strftime(sim_clock.FORMAT)
        files = []
        documents = conn.execute(
            "SELECT * FROM customer_corrections WHERE invoice_id = ? ORDER BY id", (state["invoice_id"],)
        ).fetchall()
        for doc in documents:
            target = Path(invoices_dir) / f"{doc['number']}.txt"
            if not target.exists():
                sales._atomic_write(target, correction_text(conn, doc))
            files.append((str(target), doc["id"]))
        quotes._write_email(path.parent, path.stem, row["recipient"], row["subject"], state["final_body"], decided_at)
        with conn:
            for target, doc_id in files:
                conn.execute("UPDATE customer_corrections SET document_file = ? WHERE id = ?", (target, doc_id))
            conn.execute(
                "UPDATE customer_responses SET status = ?, final_body = ?, edited = ?, decided_at = ?, outbox_file = ? "
                "WHERE id = ? AND status = ?",
                (
                    SENT,
                    state["final_body"],
                    int(state["decision"] == review.EDIT),
                    decided_at,
                    str(path),
                    row["id"],
                    AWAITING,
                ),
            )
            _resolve_failure(conn, state["invoice_id"])
    finally:
        conn.close()


def close_response(db_path: Path | str, state: ResponseState) -> None:
    """Set the response `rejected` and write nothing; one that is no longer awaiting is left alone."""
    conn = db.connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE customer_responses SET status = ?, decided_at = ? WHERE status = ? AND reconciliation_id = "
                "(SELECT id FROM sales_reconciliations WHERE invoice_id = ?)",
                (REJECTED, sim_clock.now(conn).strftime(sim_clock.FORMAT), AWAITING, state["invoice_id"]),
            )
            _resolve_failure(conn, state["invoice_id"])
    finally:
        conn.close()


def build_response_graph(
    db_path: Path | str,
    client: llm.ModelClient | None = None,
    invoices_dir: Path | str | None = None,
    outbox: Path | str | None = None,
    checkpointer=None,
):
    """One thread per invoice: draft, review (the pause), then send or close."""

    def draft(state: ResponseState) -> ResponseState:
        return {"message": draft_message(db_path, client, state["invoice_id"])}

    def send(state: ResponseState) -> ResponseState:
        send_response(db_path, invoices_dir or config.CUSTOMER_INVOICES_DIR, outbox or config.CUSTOMER_OUTBOX, state)
        return {}

    def close(state: ResponseState) -> ResponseState:
        close_response(db_path, state)
        return {}

    builder = StateGraph(ResponseState)
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
    return builder.compile(checkpointer=checkpointer, name="customer_response")
