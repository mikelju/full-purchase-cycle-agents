"""C3: offer extraction, usability and net price, and the `quote_offers` storage."""

import json
from datetime import datetime

import pytest

from purchase_cycle import db, llm, quote_decision

SKU = "GLV-NIT-XS"
SUPPLIERS = ["SUP-001", "SUP-002", "SUP-003"]
GOOD = {
    "offers": True,
    "quantity": 12,
    "unit_price": 4.1,
    "currency": "EUR",
    "vat_included": False,
    "vat_rate": None,
    "lead_time_days": 5,
    "working_days": True,
    "price_text": "4.10 EUR per box",
}


def _reply(tmp_path, supplier, body):
    path = tmp_path / f"QR-0001-{supplier}.eml"
    path.write_text(
        f"From: ventas@{supplier.lower()}.example.com\nTo: purchasing@distributor.example.com\n"
        f"Subject: Re: Quote request QR-0001 - {SKU}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _setup(tmp_path, bodies):
    """A ready_for_decision request QR-0001 whose suppliers replied with the given bodies (None = no reply)."""
    db_path = tmp_path / "decision.db"
    conn = db.connect(db_path)
    db.seed(conn)
    conn.execute(
        "INSERT INTO quote_requests (id, sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (1, ?, 12, 'quote-QR-0001', 'ready_for_decision', 't', 't', 't')",
        (SKU,),
    )
    for supplier, body in zip(SUPPLIERS, bodies, strict=True):
        file = str(_reply(tmp_path, supplier, body)) if body is not None else None
        conn.execute(
            "INSERT INTO quote_request_suppliers (request_id, supplier_code, sent_at, replied_at, reply_file) "
            "VALUES (1, ?, 't', ?, ?)",
            (supplier, "t" if file else None, file),
        )
    conn.commit()
    return db_path, conn


def _client(tmp_path, bodies, answers):
    """A replay client over hand-written recordings: one answer per reply body, keyed on the exact prompt."""
    conn = db.connect(tmp_path / "decision.db")
    request = quote_decision.request_row(conn, 1)
    conn.close()
    path = tmp_path / "offers.jsonl"
    client = llm.ModelClient("replay", [], recordings_path=path, task=llm.OFFER_EXTRACTION)
    rows = []
    for body, answer in zip(bodies, answers, strict=True):
        if body is None or answer is None:
            continue
        text = quote_decision.model_text(request, body)
        key = llm.recording_key(client.system_prompt, text, llm.OFFER_EXTRACTION)
        rows.append({"key": key, "case_id": "t", "sentence": text, "answer": answer})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return llm.ModelClient("replay", [], recordings_path=path, task=llm.OFFER_EXTRACTION)


def _offers(conn):
    return {r["supplier_code"]: dict(r) for r in conn.execute("SELECT * FROM quote_offers")}


def test_one_call_per_replied_supplier_and_the_fields_are_stored(tmp_path):
    bodies = ["We offer 12 boxes at 4.10 EUR, 5 working days.", "Offer: 8 boxes at 4.50 EUR, 3 weeks.", None]
    partial = {**GOOD, "quantity": 8, "unit_price": 4.5, "lead_time_days": 21, "working_days": False}
    db_path, conn = _setup(tmp_path, bodies)
    client = _client(tmp_path, bodies, [GOOD, partial, None])
    stored = quote_decision.extract_offers(db_path, 1, client)
    assert client.calls == 2
    assert [r["supplier_code"] for r in stored] == ["SUP-001", "SUP-002"]
    rows = _offers(conn)
    assert set(rows) == {"SUP-001", "SUP-002"}
    first = rows["SUP-001"]
    assert (first["offers"], first["quantity"], first["unit_price"], first["currency"]) == (1, 12, 4.1, "EUR")
    assert (first["lead_time_days"], first["working_days"], first["price_text"]) == (5, 1, "4.10 EUR per box")
    assert (first["net_unit_price"], first["usable"], first["reason"]) == (4.1, 1, None)
    assert first["reply_file"].endswith("QR-0001-SUP-001.eml")
    assert rows["SUP-002"]["quantity"] == 8 and rows["SUP-002"]["working_days"] == 0


def test_a_rerun_reads_the_stored_rows_and_calls_no_model(tmp_path):
    bodies = ["We offer 12 boxes at 4.10 EUR, 5 working days.", None, None]
    db_path, conn = _setup(tmp_path, bodies)
    quote_decision.extract_offers(db_path, 1, _client(tmp_path, bodies, [GOOD, None, None]))
    again = _client(tmp_path, bodies, [None, None, None])
    stored = quote_decision.extract_offers(db_path, 1, again)
    assert again.calls == 0
    assert [r["supplier_code"] for r in stored] == ["SUP-001"]
    assert conn.execute("SELECT COUNT(*) FROM quote_offers").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("change", "usable", "reason", "net"),
    [
        ({}, 1, None, 4.1),
        ({"offers": False, "quantity": None, "unit_price": None, "currency": None}, 0, "declined", None),
        ({"unit_price": None, "price_text": None}, 0, "no_price", None),
        ({"unit_price": 0.0}, 0, "no_price", None),
        ({"currency": "USD"}, 0, "currency", None),
        ({"lead_time_days": None}, 0, "no_lead_time", None),
        ({"vat_included": True, "vat_rate": None}, 0, "vat_rate_missing", None),
        ({"vat_included": True, "vat_rate": 21.0, "unit_price": 4.84}, 1, None, 4.0),
        ({"vat_included": False, "vat_rate": 21.0}, 1, None, 4.1),
    ],
)
def test_usability_and_net_price_come_from_code(tmp_path, change, usable, reason, net):
    bodies = ["A reply.", None, None]
    db_path, conn = _setup(tmp_path, bodies)
    quote_decision.extract_offers(db_path, 1, _client(tmp_path, bodies, [{**GOOD, **change}, None, None]))
    row = _offers(conn)["SUP-001"]
    assert (row["usable"], row["reason"], row["net_unit_price"]) == (usable, reason, net)


def test_a_schema_failing_answer_is_stored_as_extraction_failed_and_the_others_go_on(tmp_path):
    bodies = [
        "We offer 12 boxes at 4.10 EUR, 5 working days.",
        "Sure, 12 for about four euros.",
        "Offer 12 at 4.30 EUR.",
    ]
    broken = {**GOOD, "quantity": "twelve"}
    db_path, conn = _setup(tmp_path, bodies)
    client = _client(tmp_path, bodies, [GOOD, broken, {**GOOD, "unit_price": 4.3}])
    quote_decision.extract_offers(db_path, 1, client)
    assert client.calls == 3
    rows = _offers(conn)
    assert (rows["SUP-002"]["usable"], rows["SUP-002"]["reason"]) == (0, "extraction_failed")
    assert rows["SUP-002"]["unit_price"] is None
    assert rows["SUP-001"]["usable"] == 1 and rows["SUP-003"]["net_unit_price"] == 4.3
    failure = db.get_failure(conn, "offer-QR-0001-SUP-002")
    assert (failure["channel"], failure["step"], failure["status"]) == ("quotes", "offer_extraction", "needs_review")
    assert failure["source"].endswith("QR-0001-SUP-002.eml")
    assert conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0] == 1


def test_the_prompt_holds_the_request_and_the_body_without_headers(tmp_path):
    db_path, conn = _setup(tmp_path, ["We offer 12 boxes.", None, None])
    text = quote_decision.model_text(quote_decision.request_row(conn, 1), "We offer 12 boxes.")
    assert SKU in text and "Sale unit:" in text and "Requested quantity: 12" in text
    assert "We offer 12 boxes." in text
    assert "ventas@" not in text and "Subject:" not in text
    client = llm.ModelClient("replay", [], task=llm.OFFER_EXTRACTION)
    assert "GLV-" not in client.system_prompt  # no catalog in the instructions
    assert client.recordings_path.name == "offer_extraction.jsonl"


# C4: the comparison rule, over fixed offer sets and with no model client.
START = datetime(2026, 10, 12, 9, 0)  # a Monday


def _offer(supplier, quantity=12, net=4.0, lead=5, working=False, usable=True, reason=None):
    return {
        "supplier_code": supplier,
        "quantity": quantity,
        "net_unit_price": net,
        "lead_time_days": lead,
        "working_days": None if working is None else int(working),
        "usable": int(usable),
        "reason": reason,
    }


def _pick(offers, quantity=12):
    return quote_decision.compare_offers(offers, quantity, START)


def test_a_full_offer_beats_a_cheaper_partial_one():
    result = _pick([_offer("SUP-001", quantity=6, net=1.0), _offer("SUP-002", net=9.0)])
    assert (result["supplier"], result["rule"], result["quantity"]) == ("SUP-002", "full_quantity", 12)
    assert "SUP-001" in result["reason"] and "SUP-002" in result["reason"]


def test_among_full_offers_the_lowest_total_wins():
    result = _pick([_offer("SUP-001", net=4.2), _offer("SUP-002", net=4.0, lead=30), _offer("SUP-003", net=4.1)])
    assert (result["supplier"], result["rule"]) == ("SUP-002", "lowest_total")
    assert "48.00" in result["reason"] and "49.20" in result["reason"]


def test_a_full_offer_over_the_requested_quantity_is_priced_on_the_requested_quantity():
    result = _pick([_offer("SUP-001", quantity=100, net=4.0), _offer("SUP-002", net=4.1)])
    assert (result["supplier"], result["total"]) == ("SUP-001", 48.0)


def test_equal_totals_go_to_the_shorter_lead_time_in_calendar_days():
    # 3 working days from Monday is Thursday (3 calendar days); 4 calendar days is later.
    result = _pick([_offer("SUP-001", lead=4), _offer("SUP-002", lead=3, working=True)])
    assert (result["supplier"], result["rule"]) == ("SUP-002", "shortest_lead_time")


def test_working_days_over_a_weekend_count_as_more_calendar_days():
    # 5 working days from Monday end the next Monday: 7 calendar days, later than 6 calendar days.
    result = _pick([_offer("SUP-001", lead=5, working=True), _offer("SUP-002", lead=6)])
    assert result["supplier"] == "SUP-002"


def test_a_null_working_days_flag_counts_calendar_days():
    result = _pick([_offer("SUP-001", lead=6, working=None), _offer("SUP-002", lead=5, working=True)])
    assert result["supplier"] == "SUP-001"


def test_equal_total_and_lead_time_go_to_the_lower_supplier_code():
    result = _pick([_offer("SUP-003"), _offer("SUP-001"), _offer("SUP-002")])
    assert (result["supplier"], result["rule"]) == ("SUP-001", "supplier_code")


def test_a_partial_offer_is_recommended_only_when_no_full_offer_is_usable():
    offers = [
        _offer("SUP-001", quantity=6, net=5.0),
        _offer("SUP-002", quantity=8, net=3.0),
        _offer("SUP-003", usable=False),
    ]
    result = _pick(offers)
    assert (result["supplier"], result["rule"], result["quantity"], result["total"]) == (
        "SUP-002",
        "partial_only",
        8,
        24.0,
    )


def test_a_single_usable_offer_is_recommended_by_that_rule():
    result = _pick([_offer("SUP-001", usable=False, reason="declined"), _offer("SUP-002")])
    assert (result["supplier"], result["rule"]) == ("SUP-002", "only_usable_offer")


def test_no_usable_offer_gives_no_recommendation_and_names_the_reasons():
    result = _pick(
        [_offer("SUP-001", usable=False, reason="declined"), _offer("SUP-002", usable=False, reason="currency")]
    )
    assert (result["supplier"], result["rule"]) == (None, "none")
    assert "declined" in result["reason"] and "currency" in result["reason"]
    assert _pick([])["supplier"] is None


def test_comparing_calls_no_model(tmp_path):
    client = _client_without_calls(tmp_path)
    _pick([_offer("SUP-001"), _offer("SUP-002", net=3.0)])
    assert client.calls == 0


def _client_without_calls(tmp_path):
    return llm.ModelClient("replay", [], recordings_path=tmp_path / "none.jsonl", task=llm.OFFER_EXTRACTION)
