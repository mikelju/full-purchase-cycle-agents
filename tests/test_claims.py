"""C3 and C4: claim candidates, claim facts and the amount in dispute."""

import json

import pytest

from purchase_cycle import claims, db, llm

SKU = "GLV-NIT-XS"


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "claims.db")
    db.seed(conn)
    return conn


def _invoice(conn, number, result, rows=(), supplier="SUP-001"):
    """A reconciled invoice with its purchase order and discrepancy rows; returns the invoice id."""
    conn.execute(
        "INSERT INTO supplier_documents (kind, number, source_file, content_hash, status, created_at) "
        "VALUES ('invoice', ?, 'f.txt', ?, 'linked', 't')",
        (number, number),
    )
    invoice = conn.execute("SELECT MAX(id) FROM supplier_documents").fetchone()[0]
    conn.execute(
        "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (?, 5, ?, 'ordered', 't', 't', 't')",
        (SKU, f"th-{number}"),
    )
    conn.execute(
        "INSERT INTO purchase_orders (request_id, supplier_code, sku, quantity, unit_price, currency, total, "
        "expected_delivery, issued_at, status) "
        "VALUES ((SELECT MAX(id) FROM quote_requests), ?, ?, 5, 1.0, 'EUR', 5.0, 't', 't', 'issued')",
        (supplier, SKU),
    )
    conn.execute(
        "INSERT INTO reconciliations (invoice_id, purchase_order_id, result, reconciled_at) "
        "VALUES (?, (SELECT MAX(id) FROM purchase_orders), ?, 't')",
        (invoice, result),
    )
    reconciliation_id = conn.execute("SELECT MAX(id) FROM reconciliations").fetchone()[0]
    for sku, kind, expected, found, unit_amount, amount, text in rows:
        conn.execute(
            "INSERT INTO discrepancies (reconciliation_id, sku, type, expected, found, unit_amount, amount, text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (reconciliation_id, sku, kind, expected, found, unit_amount, amount, text),
        )
    conn.commit()
    return invoice


def _claim(conn, invoice, status="awaiting_approval"):
    conn.execute(
        "INSERT INTO claims (reconciliation_id, recipient, subject, draft_body, draft_source, status, drafted_at) "
        "SELECT id, 'a@b.c', 's', 'b', 'model', ?, 't' FROM reconciliations WHERE invoice_id = ?",
        (status, invoice),
    )
    conn.commit()


def _amount(rows) -> float:
    return claims.amount_in_dispute([dict(zip(claims.ROW_KEYS, row, strict=True)) for row in rows])


def test_candidates_are_the_discrepancy_invoices_with_no_claim_in_invoice_order(conn):
    _invoice(conn, "A", "matched")
    first = _invoice(conn, "B", "discrepancies", [(SKU, "price", 4.0, 4.2, 0.2, 1.0, None)])
    _invoice(conn, "C", "awaiting_delivery_note")
    claimed = _invoice(conn, "D", "discrepancies", [(SKU, "price", 4.0, 4.2, 0.2, 1.0, None)])
    second = _invoice(conn, "E", "discrepancies", [(SKU, "quantity", 5, 6, 1.0, 1.0, None)])
    _claim(conn, claimed)
    # a document with no reconciliation (unlinked) is never a candidate
    conn.execute(
        "INSERT INTO supplier_documents (kind, number, source_file, content_hash, status, created_at) "
        "VALUES ('invoice', 'U', 'u.txt', 'U', 'unlinked', 't')"
    )
    assert claims.candidates(conn) == [first, second]


def test_an_invoice_moved_to_discrepancies_by_a_later_delivery_note_becomes_a_candidate(conn):
    invoice = _invoice(conn, "A", "awaiting_delivery_note")
    assert claims.candidates(conn) == []
    conn.execute("UPDATE reconciliations SET result = 'discrepancies' WHERE invoice_id = ?", (invoice,))
    conn.execute(
        "INSERT INTO discrepancies (reconciliation_id, sku, type, expected, found, unit_amount, amount) "
        "SELECT id, ?, 'quantity', 5, 4, 1.0, -1.0 FROM reconciliations WHERE invoice_id = ?",
        (SKU, invoice),
    )
    assert claims.candidates(conn) == [invoice]


def test_facts_hold_every_discrepancy_row_and_the_references(conn):
    rows = [
        (SKU, "price", 4.0, 4.5, 0.5, 5.0, None),
        (None, "extra_line", None, 3.0, 2.0, 6.0, "Surgical masks"),
        ("GLV-NIT-S", "missing_line", 10.0, None, 1.0, 10.0, None),
    ]
    invoice = _invoice(conn, "F-77", "discrepancies", rows, supplier="SUP-002")
    facts = claims.claim_facts(conn, invoice)
    order = conn.execute("SELECT purchase_order_id FROM reconciliations").fetchone()[0]
    assert facts["invoice"] == f"INV-{invoice:04d}"
    assert facts["invoice_number"] == "F-77"
    assert facts["order"] == f"PO-{order:04d}"
    assert facts["supplier"] == conn.execute("SELECT name FROM suppliers WHERE code = 'SUP-002'").fetchone()[0]
    assert facts["recipient"] == conn.execute("SELECT email FROM suppliers WHERE code = 'SUP-002'").fetchone()[0]
    assert [tuple(row[key] for key in claims.ROW_KEYS) for row in facts["differences"]] == rows
    assert facts["amount_in_dispute"] == 11.0


def test_facts_of_an_unknown_invoice_or_one_without_discrepancies_are_none(conn):
    matched = _invoice(conn, "A", "matched")
    assert claims.claim_facts(conn, 999) is None
    assert claims.claim_facts(conn, matched) is None


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([(SKU, "price", 4.0, 4.5, 0.5, 5.0, None)], 5.0),  # positive price: we pay more
        ([(SKU, "price", 4.0, 3.5, -0.5, -5.0, None)], 0.0),  # negative price: in our favour
        ([(SKU, "quantity", 5, 8, 3.0, 3.0, None)], 3.0),  # invoiced above expected
        ([(SKU, "quantity", 5, 3, -2.0, -2.0, None)], 0.0),  # invoiced below expected
        ([(SKU, "extra_line", None, 2.0, 5.0, 10.0, None)], 10.0),  # priced extra line
        ([(None, "extra_line", None, 2.0, None, None, "Free text")], 0.0),  # unpriced extra line
        ([(SKU, "missing_line", 5.0, None, 1.0, 5.0, None)], 0.0),  # never charged
        ([(SKU, "price", None, None, None, None, None)], 0.0),  # price row with no stated price
        (
            [
                (SKU, "price", 4.0, 4.5, 0.5, 5.0, None),
                ("GLV-NIT-S", "quantity", 5, 3, -2.0, -25.0, None),
                (None, "extra_line", None, 1.0, 8.1, 8.1, "Masks"),
                ("GLV-NIT-M", "missing_line", 5.0, None, 1.0, 9.0, None),
                (None, "extra_line", None, 1.0, None, None, "Unpriced"),
            ],
            13.1,
        ),  # mixed invoice
        ([(SKU, "price", 4.0, 4.1, 0.1, 0.1, None), ("GLV-NIT-S", "price", 4.0, 4.2, 0.2, 0.2, None)], 0.3),  # cents
    ],
)
def test_the_amount_in_dispute_follows_the_sign_rule(rows, expected):
    assert _amount(rows) == pytest.approx(expected, abs=1e-9)
    assert _amount(rows) == round(_amount(rows), 2)


ROWS = [(SKU, "price", 4.0, 4.5, 0.5, 5.0, None), (None, "extra_line", None, 3.0, 2.0, 6.0, "Surgical masks")]


def _client(facts, body, tmp_path, mode="replay"):
    path = tmp_path / "claim_recordings.jsonl"
    key = llm.recording_key(
        llm.build_system_prompt([], llm.CLAIM_DRAFTING), claims.facts_prompt(facts), llm.CLAIM_DRAFTING
    )
    path.write_text(json.dumps({"key": key, "case_id": "t", "sentence": "", "answer": {"body": body}}) + "\n")
    return llm.ModelClient(mode, [], path, task=llm.CLAIM_DRAFTING)


def test_the_prompt_holds_the_facts_json_and_no_recipient_or_subject(conn):
    facts = claims.claim_facts(conn, _invoice(conn, "F-77", "discrepancies", ROWS))
    prompt = claims.facts_prompt(facts)
    loaded = json.loads(prompt)
    assert list(loaded) == sorted(loaded)
    assert loaded["invoice_number"] == "F-77"
    assert loaded["amount_in_dispute"] == "11.00"
    assert [row["amount"] for row in loaded["differences"]] == ["5.00", "6.00"]
    assert facts["recipient"] not in prompt
    assert "subject" not in loaded
    assert claims.facts_prompt(facts) == prompt


def test_one_call_per_claim_and_recipient_and_subject_come_from_code(conn, tmp_path):
    invoice = _invoice(conn, "F-77", "discrepancies", ROWS)
    facts = claims.claim_facts(conn, invoice)
    body = claims.fill_template(facts)
    client = _client(facts, body, tmp_path)
    draft = claims.draft_claim(conn, client, facts)
    assert client.calls == 1
    assert draft["body"] == body
    assert draft["source"] == "model"
    assert draft["recipient"] == facts["recipient"]
    assert draft["subject"] == f"Claim on invoice F-77 for purchase order {facts['order']}"


def test_a_missing_recording_raises(conn, tmp_path):
    facts = claims.claim_facts(conn, _invoice(conn, "F-77", "discrepancies", ROWS))
    client = llm.ModelClient("replay", [], tmp_path / "none.jsonl", task=llm.CLAIM_DRAFTING)
    with pytest.raises(llm.MissingRecording):
        claims.draft_claim(conn, client, facts)


def test_record_mode_stores_the_answer_and_replay_reads_it(conn, tmp_path, monkeypatch):
    facts = claims.claim_facts(conn, _invoice(conn, "F-77", "discrepancies", ROWS))
    path = tmp_path / "rec.jsonl"
    recorder = llm.ModelClient("record", [], path, task=llm.CLAIM_DRAFTING)
    monkeypatch.setattr(recorder, "_call_model", lambda sentence: ({"body": claims.fill_template(facts)}, {}))
    assert claims.draft_claim(conn, recorder, facts)["body"] == claims.fill_template(facts)
    assert recorder.save_recordings() == 1
    replayer = llm.ModelClient("replay", [], path, task=llm.CLAIM_DRAFTING)
    assert claims.draft_claim(conn, replayer, facts)["body"] == claims.fill_template(facts)


def test_the_task_is_separate_and_existing_keys_are_unchanged():
    assert llm.CLAIM_DRAFTING.name == "claim_drafting"
    assert llm.CLAIM_DRAFTING.recordings_path.name == "claim_drafting.jsonl"
    assert llm.CLAIM_DRAFTING.tool_name == "record_claim_body"
    assert llm.recording_key("s", "x", llm.CLAIM_DRAFTING) != llm.recording_key("s", "x", llm.OFFER_EXTRACTION)
    assert llm.CLAIM_DRAFTING.answer.model_config["extra"] == "forbid"
    assert llm.EXTRACTION.name == "order_line_extraction"


FACTS = {
    "invoice": "INV-0001",
    "invoice_number": "F-77",
    "order": "PO-0007",
    "supplier": "Acme",
    "recipient": "a@b.c",
    "differences": [
        {"sku": SKU, "type": "price", "expected": 4.0, "found": 4.5, "unit_amount": 0.5, "amount": 5.0, "text": None},
        {
            "sku": None,
            "type": "extra_line",
            "expected": None,
            "found": 3.0,
            "unit_amount": 2.0,
            "amount": 6.0,
            "text": "Surgical masks",
        },  # fmt: skip
    ],
    "amount_in_dispute": 11.0,
}
GOOD = (
    f"Invoice F-77 against PO-0007: {SKU} costs 5.00 EUR more, Surgical masks 6.00 EUR extra. "
    "The amount in dispute is 11.00 EUR. Please send a corrected invoice."
)


def _facts(**changes):
    return FACTS | changes


def test_a_complete_draft_passes():
    assert claims.check_draft(GOOD, FACTS) == []


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (GOOD.replace("F-77", "F-78"), "invoice number"),
        (GOOD.replace("PO-0007", "PO-0008"), "purchase order"),
        (GOOD.replace(SKU, "GLV-X"), SKU),
        (GOOD.replace("Surgical masks", "Gauze"), "Surgical masks"),
        (GOOD.replace("5.00 EUR more", "more"), "missing amount 5.00"),
        (GOOD.replace("11.00", "12.00"), "amount in dispute"),
        (GOOD.replace("corrected invoice", "answer"), "corrected invoice or a credit note"),
        (GOOD + " Also 99.00 EUR.", "invented amount 99.00"),
        (GOOD.replace("11.00 EUR", "11.01 EUR"), "invented amount 11.01"),
        (GOOD + " Caf\u00e9.", "ASCII"),
        (GOOD + " x" * 2000, "longer than"),
    ],
)
def test_each_rule_fails_on_its_own(body, problem):
    problems = claims.check_draft(body, FACTS)
    assert any(problem in p for p in problems), problems


@pytest.mark.parametrize(
    "body",
    [
        GOOD.replace("11.00 EUR", "11,00 EUR"),  # decimal comma
        GOOD.replace("11.00 EUR", "EUR 11.00"),  # EUR before
        GOOD.replace("11.00 EUR", "11 euros"),  # euro word, no decimals
        GOOD + " We counted 3 units on line 12 of document 20260101.",  # integers are not money
        GOOD.replace("corrected invoice", "CREDIT NOTE"),  # case ignored
    ],
)
def test_accepted_forms(body):
    assert claims.check_draft(body, FACTS) == []


def test_amount_tolerance_is_half_a_cent():
    facts = _facts(amount_in_dispute=11.004)
    assert claims.check_draft(GOOD, facts) == []  # 11.00 is within half a cent of 11.004
    assert claims.check_draft(GOOD.replace("11.00", "11.01"), _facts(amount_in_dispute=11.0)) != []


def test_integers_with_a_currency_word_are_money():
    assert claims.money_amounts("pay 7 EUR or 8 euros, EUR 9, 1.50, 3, 2026") == [7.0, 8.0, 9.0, 1.5]


def test_a_difference_with_no_amount_needs_no_amount():
    facts = _facts(
        differences=[{**FACTS["differences"][1], "amount": None, "unit_amount": None}], amount_in_dispute=0.0
    )
    body = "F-77 PO-0007 Surgical masks has no price. In dispute 0.00 EUR. Send a credit note."
    assert claims.check_draft(body, facts) == []


SIGN_CASES = [
    [(SKU, "price", 4.0, 4.5, 0.5, 5.0, None)],
    [(SKU, "price", 4.0, 3.5, -0.5, -5.0, None)],
    [(SKU, "quantity", 5.0, 8.0, 3.0, 3.0, None)],
    [(SKU, "quantity", 5.0, 3.0, -2.0, -2.0, None)],
    [(SKU, "extra_line", None, 2.0, 5.0, 10.0, None)],
    [(None, "extra_line", None, 2.0, None, None, "Free text")],
    [(SKU, "missing_line", 5.0, None, 1.0, 5.0, None)],
    [(SKU, "price", None, None, None, None, None)],
    [(SKU, "price", 4.0, 4.5, 0.5, 5.0, None), ("GLV-NIT-S", "quantity", 5.0, 3.0, -2.0, -25.0, None),
     (None, "extra_line", None, 1.0, 8.1, 8.1, "Masks"), ("GLV-NIT-M", "missing_line", 5.0, None, 1.0, 9.0, None)],
]  # fmt: skip


@pytest.mark.parametrize("rows", SIGN_CASES)
def test_the_filled_template_passes_the_check(conn, rows):
    facts = claims.claim_facts(conn, _invoice(conn, "F-77", "discrepancies", rows))
    body = claims.fill_template(facts)
    assert claims.check_draft(body, facts) == []
    assert body.count("\n- ") == len(rows)


def _good_for(facts):
    return f"{facts['invoice_number']} {facts['order']} {SKU} 5.00 EUR Surgical masks 6.00 EUR total 11.00 EUR, credit note"


@pytest.mark.parametrize("failing", ["schema", "fact_check"])
def test_a_failed_draft_stores_the_template_and_the_next_claim_is_still_model(conn, tmp_path, failing):
    ids = [_invoice(conn, n, "discrepancies", ROWS) for n in ("F-1", "F-2")]
    facts = [claims.claim_facts(conn, i) for i in ids]
    bad = {"body": 5} if failing == "schema" else "Please pay us 999.99 EUR."
    facts, client = _two_claims_from(conn, tmp_path, facts, [bad, _good_for(facts[1])])
    first = claims.draft_claim(conn, client, facts[0])
    second = claims.draft_claim(conn, client, facts[1])
    assert (first["source"], second["source"]) == ("template", "model")
    assert claims.check_draft(first["body"], facts[0]) == []
    row = db.get_failure(conn, f"claim-{facts[0]['invoice']}")
    assert (row["status"], row["step"], row["channel"]) == ("needs_review", "claim_drafting", "reconcile")
    assert db.get_failure(conn, f"claim-{facts[1]['invoice']}") is None
    assert len(db.list_failures(conn)) == 1


def _two_claims_from(conn, tmp_path, facts, bodies):
    path = tmp_path / "two.jsonl"
    with path.open("w") as fh:
        for fact, body in zip(facts, bodies, strict=True):
            key = llm.recording_key(
                llm.build_system_prompt([], llm.CLAIM_DRAFTING), claims.facts_prompt(fact), llm.CLAIM_DRAFTING
            )
            answer = body if isinstance(body, dict) else {"body": body}
            fh.write(json.dumps({"key": key, "case_id": "t", "sentence": "", "answer": answer}) + "\n")
    return facts, llm.ModelClient("replay", [], path, task=llm.CLAIM_DRAFTING)


def test_a_good_draft_is_stored_as_model_with_no_failure(conn, tmp_path):
    facts = claims.claim_facts(conn, _invoice(conn, "F-1", "discrepancies", ROWS))
    _, client = _two_claims_from(conn, tmp_path, [facts], [_good_for(facts)])
    assert claims.draft_claim(conn, client, facts)["source"] == "model"
    assert db.list_failures(conn) == []
