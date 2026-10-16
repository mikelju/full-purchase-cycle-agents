"""Phase 11: the response to a customer about the incidents of a signed delivery note.

The facts, the amounts and the documents come from code; the model only writes the body, and a deterministic check
in the style of phase 09 decides whether the draft may be shown or the fixed template takes its place.
"""

import json
import re
import sqlite3

from purchase_cycle import db, llm, sales
from purchase_cycle.claims import _near, _token, mentions, money_amounts

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
