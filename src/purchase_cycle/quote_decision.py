"""Quotes module, decision step: extract one offer per supplier reply, check it and store it."""

import sqlite3
from pathlib import Path

from purchase_cycle import db, email_order, llm, quotes

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
