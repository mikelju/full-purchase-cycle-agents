"""C3 and C4: claim candidates, claim facts and the amount in dispute."""

import json
from email import message_from_bytes, policy

import pytest
from langgraph.types import Command

from conftest import ROWS, SKU, _invoice, _two_claims_from
from purchase_cycle import claims, db, llm
from purchase_cycle.graph import sqlite_checkpointer


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "claims.db")
    db.seed(conn)
    return conn


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


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("1,050.00 EUR", [1050.0]),
        ("EUR 1,234.56", [1234.56]),
        ("1.234,56 EUR", [1234.56]),
        ("1 234.56", [234.56]),  # a space is no thousands separator: the safe side, flagged as invented
        ("Line 3 350.00 EUR", [350.0]),
        ("a 19.44% rate and 19.44 %", []),
        ("price 4.30-999.00 EUR, Total_999.00 EUR, x999.00", [4.3, 999.0, 999.0, 999.0]),
        ("PO-0001, INV-0002, GAUZE-NS-5-12, F-2026-9939", []),
        ("\u20ac12 and 12 \u20ac", [12.0, 12.0]),
        ("on 12.10.26 and 12.10.2026", []),  # dates are not amounts
        ("GEL-5.5 and N-77002.50", []),  # nothing starts inside a SKU
        ("The total overcharge is 999.5", [999.5]),
    ],
)
def test_amounts_with_separators_signs_and_dates(text, values):
    assert claims.money_amounts(text) == values


CLM = {
    "invoice": "INV-0001",
    "invoice_number": "F-1",
    "order": "PO-0001",
    "supplier": "S",
    "recipient": "a@b.c",
    "differences": [
        {"sku": "GAUZE-NS-5-12", "type": "price", "expected": 3.6, "found": 4.3, "unit_amount": 0.7, "amount": 350.0, "text": None}
    ],
    "amount_in_dispute": 350.0,
}  # fmt: skip
CLM_OK = "F-1 PO-0001 GAUZE-NS-5-12 difference 350.00 EUR. The amount in dispute is 350.00 EUR. Send a credit note."


def _row(sku, kind, amount, **more):
    row = {
        "sku": sku,
        "type": kind,
        "expected": None,
        "found": None,
        "unit_amount": None,
        "amount": amount,
        "text": None,
    }
    return row | more


def test_the_amount_in_dispute_must_be_stated_as_the_dispute():
    assert claims.check_draft(CLM_OK, CLM) == []
    wrong = CLM_OK.replace("is 350.00 EUR", "is 4.30 EUR")
    assert any("amount in dispute" in p for p in claims.check_draft(wrong, CLM))
    never = CLM_OK.replace("The amount in dispute is 350.00 EUR.", "We ask for 350.00 EUR back.")
    assert any("missing amount in dispute" in p for p in claims.check_draft(never, CLM))


def test_a_row_amount_is_not_the_amount_in_dispute():
    facts = CLM | {
        "differences": [_row("A-1", "price", 12.0), _row("B-2", "quantity", -5.0)],
        "amount_in_dispute": 12.0,
    }
    body = "F-1 PO-0001 A-1 12.00 EUR. B-2 5.00 EUR. The amount in dispute is 5.00 EUR. Send a credit note."
    assert any("missing amount in dispute 12.00" in p for p in claims.check_draft(body, facts))
    assert claims.check_draft(body.replace("is 5.00", "is 12.00"), facts) == []


def test_each_row_amount_sits_with_its_sku():
    facts = CLM | {
        "differences": [_row("NDL-20G", "extra_line", 31.6), _row("BND-ELA-7", "missing_line", 40.0)],
        "amount_in_dispute": 31.6,
    }
    good = "F-1 PO-0001\n- NDL-20G: difference 31.60 EUR\n- BND-ELA-7: difference 40.00 EUR\nIn dispute: 31.60 EUR. Credit note."
    assert claims.check_draft(good, facts) == []
    swapped = (
        "F-1 PO-0001\n- NDL-20G: difference 40.00 EUR\n- BND-ELA-7: difference 31.60 EUR\n"
        "In dispute: 31.60 EUR. Credit note."
    )
    assert any("missing amount 31.60 of NDL-20G" in p for p in claims.check_draft(swapped, facts))


def test_a_quantity_is_not_a_money_amount():
    facts = CLM | {
        "differences": [_row("A-1", "quantity", 302.0, expected=100, found=302)],
        "amount_in_dispute": 302.0,
    }
    body = "F-1 PO-0001 A-1 invoiced 302 against 100, 302.00 EUR. The amount in dispute is 302.00 EUR. Credit note."
    assert claims.check_draft(body, facts) == []
    facts = facts | {"differences": [_row("A-1", "quantity", 12.0, expected=100, found=302)], "amount_in_dispute": 12.0}
    body = "F-1 PO-0001 A-1 invoiced 302 against 100, 12.00 EUR. The amount in dispute is 12.00 EUR. Credit of 302 EUR."
    assert "invented amount 302.00" in claims.check_draft(body, facts)
    assert "invented amount 999.50" in claims.check_draft(CLM_OK + " The total overcharge is 999.5", CLM)


def test_references_and_skus_match_whole_tokens():
    facts = CLM | {
        "invoice_number": "N-77002",
        "differences": [_row("GEL-500", "price", 5.0)],
        "amount_in_dispute": 5.0,
    }
    body = "N-77002 PO-0001 GEL-500 5.00 EUR. The amount in dispute is 5.00 EUR. Credit note."
    assert claims.check_draft(body, facts) == []
    longer = body.replace("N-77002", "N-770021").replace("GEL-500", "GEL-5000")
    problems = claims.check_draft(longer, facts)
    assert any("missing invoice number N-77002" in p for p in problems)
    assert any("missing difference GEL-500" in p for p in problems)
    assert claims.mentions("X-10 only", "X-1") is False and claims.mentions("pay X-1.", "X-1")


def test_without_an_invoice_number_the_order_is_still_required():
    facts = CLM | {"invoice_number": None}
    assert claims.check_draft(CLM_OK.replace("F-1 ", ""), facts) == []
    assert any("purchase order" in p for p in claims.check_draft(CLM_OK.replace("PO-0001", ""), facts))


def test_a_negative_extra_line_does_not_reduce_the_dispute():
    rows = [(SKU, "price", 4.0, 4.3, 0.3, 30.0, None), (None, "extra_line", None, 1.0, -40.0, -40.0, "Discount")]
    assert _amount(rows) == 30.0


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
    return (
        f"{facts['invoice_number']} {facts['order']} {SKU} 5.00 EUR Surgical masks 6.00 EUR. "
        "The amount in dispute is 11.00 EUR, credit note"
    )


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


def test_a_good_draft_is_stored_as_model_with_no_failure(conn, tmp_path):
    facts = claims.claim_facts(conn, _invoice(conn, "F-1", "discrepancies", ROWS))
    _, client = _two_claims_from(conn, tmp_path, [facts], [_good_for(facts)])
    assert claims.draft_claim(conn, client, facts)["source"] == "model"
    assert db.list_failures(conn) == []


class Counter:
    """A model double that answers with the facts' own template and counts its calls."""

    def __init__(self):
        self.calls = 0

    def extract(self, text, case_id=None):
        self.calls += 1
        facts = json.loads(text)
        return llm.ClaimBody(body=claims.fill_template(CLAIM_FACTS[facts["invoice"]]))


CLAIM_FACTS = {}


class Flow:
    """One claim graph over a temporary database with a call-counting model double."""

    def __init__(self, tmp_path, conn):
        self.db_path = tmp_path / "claims.db"
        self.conn = conn
        self.client = Counter()
        self.outbox = tmp_path / "outbox"
        self.checkpointer = sqlite_checkpointer(tmp_path / "cp.sqlite")
        self.invoice = _invoice(conn, "F-77", "discrepancies", ROWS)
        self.facts = CLAIM_FACTS[claims.reconciliation.invoice_reference(self.invoice)] = claims.claim_facts(
            conn, self.invoice
        )
        self.run = {"configurable": {"thread_id": f"claim-{self.facts['invoice']}"}}

    def graph(self, client=True):
        return claims.build_claim_graph(self.db_path, self.client if client else None, self.outbox, self.checkpointer)

    def row(self):
        return self.conn.execute("SELECT * FROM claims").fetchall()

    def emails(self):
        return sorted(self.outbox.glob("*.eml")) if self.outbox.exists() else []


@pytest.fixture
def flow(tmp_path, conn):
    return Flow(tmp_path, conn)


def test_drafting_stores_an_awaiting_row_and_the_thread_waits_at_review(flow):
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    [row] = flow.row()
    assert (row["status"], row["draft_source"], row["edited"]) == ("awaiting_approval", "model", 0)
    assert row["recipient"] == flow.facts["recipient"]
    assert row["draft_body"] == claims.fill_template(flow.facts)
    assert flow.graph(client=False).get_state(flow.run).next == ("review",)
    assert flow.client.calls == 1
    assert flow.emails() == []


def _decide(flow, answer):
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    flow.graph(client=False).invoke(Command(resume=answer), flow.run)
    return flow.row()[0]


def test_approve_writes_one_keyed_email_and_sets_sent(flow):
    row = _decide(flow, {"action": "approve"})
    assert (row["status"], row["edited"], row["final_body"]) == ("sent", 0, row["draft_body"])
    [path] = flow.emails()
    assert path.name == f"CLAIM-{flow.facts['invoice']}-SUP-001.eml"
    assert row["outbox_file"] == str(path) and row["decided_at"]
    message = message_from_bytes(path.read_bytes(), policy=policy.default)
    assert message["To"] == flow.facts["recipient"]
    assert message["Subject"] == f"Claim on invoice F-77 for purchase order {flow.facts['order']}"
    assert message.get_content().strip() == row["draft_body"].strip()


def test_edit_sends_the_given_text_keeps_the_draft_and_marks_edited(flow):
    row = _decide(flow, {"action": "edit", "body": "My own words."})
    assert (row["status"], row["edited"], row["final_body"]) == ("sent", 1, "My own words.")
    assert row["draft_body"] == claims.fill_template(flow.facts)
    assert (
        message_from_bytes(flow.emails()[0].read_bytes(), policy=policy.default).get_content().strip()
        == "My own words."
    )


def test_reject_writes_nothing(flow):
    row = _decide(flow, {"action": "reject"})
    assert (row["status"], row["final_body"], row["outbox_file"]) == ("rejected", None, None)
    assert row["decided_at"] and flow.emails() == []


def test_rerunning_a_node_writes_nothing_twice_and_calls_no_model(flow):
    _decide(flow, {"action": "approve"})
    before = ([tuple(r) for r in flow.row()], flow.emails(), flow.client.calls)
    graph = flow.graph(client=False)
    state = {"invoice_id": flow.invoice, "decision": "approve", "final_body": "other text"}
    claims.send_claim(flow.db_path, flow.outbox, state)
    claims.close_claim(flow.db_path, state)
    claims.draft_message(flow.db_path, None, flow.invoice)
    assert ([tuple(r) for r in flow.row()], flow.emails(), flow.client.calls) == before
    assert graph.get_state(flow.run).next == ()


def test_numbers_inside_a_product_text_are_not_amounts():
    facts = CLM | {
        "differences": [_row(None, "extra_line", 20.0, text="zinc oxide tape 2.5 cm x 5 m")],
        "amount_in_dispute": 20.0,
    }
    body = "F-1 PO-0001 zinc oxide tape 2.5 cm x 5 m 20.00 EUR. The amount in dispute is 20.00 EUR. Credit note."
    assert claims.check_draft(body, facts) == []


def test_product_text_matches_ignoring_case():
    facts = CLM | {"differences": [_row(None, "extra_line", 20.0, text="zinc oxide tape 2.5 cm")], "amount_in_dispute": 20.0}
    body = "F-1 PO-0001 Zinc Oxide Tape 2.5 cm 20.00 EUR. The amount in dispute is 20.00 EUR. Credit note."
    assert claims.check_draft(body, facts) == []
    assert claims.mentions("the Surgical Masks", "surgical masks")
    assert claims.mentions("pay x-1.", "X-1") and not claims.mentions("X-10 only", "x-1")


def test_a_sku_header_with_bullets_and_per_unit_figures_pass():
    facts = CLM | {
        "differences": [_row("A-1", "price", 12.0, expected=4.0, found=4.5), _row("B-2", "quantity", 5.0)],
        "amount_in_dispute": 17.0,
    }
    body = (
        "F-1 PO-0001\nA-1:\n- price 4.50 EUR instead of 4.00 EUR, overcharge 12.00 EUR\nB-2:\n- overcharge 5.00 EUR\n"
        "Total amount in dispute: 17.00 EUR. Corrected invoice please."
    )
    assert claims.check_draft(body, facts) == []
    assert claims.check_draft(body.replace("overcharge 12.00", "overcharge 5.00"), facts) != []


def _past_draft(flow, node):
    """Move the claim thread to `node` (review or send) without a decision from a person."""
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    if node == "send":
        flow.graph(client=False).update_state(flow.run, {"decision": "approve", "final_body": "x"}, as_node="review")
    assert flow.graph(client=False).get_state(flow.run).next == (node,)


@pytest.mark.parametrize("node", ["review", "send"])
def test_a_thread_past_draft_with_no_claim_row_is_discarded_and_drafted_again(flow, node):
    _past_draft(flow, node)
    flow.conn.execute("DELETE FROM claims")  # the business database was reset; the checkpoints file was kept
    flow.conn.commit()
    [item] = claims.draft_claims(flow.db_path, flow.checkpointer, flow.client, flow.outbox)
    assert item["claim"]["status"] == "awaiting_approval" and item["claim"]["recipient"]
    assert flow.graph(client=False).get_state(flow.run).next == ("review",)
    assert flow.client.calls == 2 and flow.emails() == []
    decided = claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice)
    assert decided["status"] == "sent" and len(flow.emails()) == 1


def test_a_thread_still_at_draft_is_resumed(flow):
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    flow.graph(client=False).update_state(flow.run, {"invoice_id": flow.invoice}, as_node="__start__")
    flow.conn.execute("DELETE FROM claims")
    flow.conn.commit()
    assert flow.graph(client=False).get_state(flow.run).next == ("draft",)
    [item] = claims.draft_claims(flow.db_path, flow.checkpointer, flow.client, flow.outbox)
    assert item["claim"]["status"] == "awaiting_approval"


@pytest.mark.parametrize("decide", ["approve", "reject"])
def test_a_claim_whose_thread_is_missing_can_still_be_decided_with_no_model_call(flow, tmp_path, decide):
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    other = sqlite_checkpointer(tmp_path / "other.sqlite")  # a different checkpoints file: no thread
    if decide == "approve":
        done = claims.approve_claim(flow.db_path, other, flow.outbox, flow.invoice)
        assert done["status"] == "sent" and len(flow.emails()) == 1
    else:
        done = claims.reject_claim(flow.db_path, other, flow.invoice)
        assert done["status"] == "rejected" and flow.emails() == []
    assert flow.client.calls == 1


def _occupy_outbox(flow, body):
    name = f"CLAIM-{flow.facts['invoice']}-SUP-001"
    claims.quotes._write_email(flow.outbox, name, "x@y.z", "other", body, "2026-10-12 09:00:00")


def test_an_existing_different_file_refuses_the_send_and_leaves_the_claim_awaiting(flow):
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    _occupy_outbox(flow, "Some other text.")
    with pytest.raises(claims.DecisionRefused, match="already exists"):
        claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice)
    assert flow.row()[0]["status"] == "awaiting_approval"


def test_an_existing_identical_file_is_kept_and_the_claim_is_sent(flow):
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    _occupy_outbox(flow, flow.row()[0]["draft_body"])
    done = claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice)
    assert done["status"] == "sent" and len(flow.emails()) == 1


def test_the_email_date_is_the_decision_time(flow):
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    claims.sim_clock.advance(flow.conn, days=2)
    done = claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice)
    message = message_from_bytes(flow.emails()[0].read_bytes(), policy=policy.default)
    assert message["Date"].datetime.strftime(claims.sim_clock.FORMAT) == done["decided_at"] != done["drafted_at"]


def test_approve_with_a_different_body_while_the_send_is_pending_is_refused(flow):
    _past_draft(flow, "send")
    with pytest.raises(claims.DecisionRefused, match="earlier decision"):
        claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice, "Other words.")
    assert flow.row()[0]["status"] == "awaiting_approval" and flow.emails() == []
    done = claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice)
    assert (done["status"], done["final_body"]) == ("sent", "x")


@pytest.mark.parametrize("decide", ["approve", "reject"])
def test_a_decision_resolves_the_parked_failure(flow, decide):
    claims.db.park_failure(flow.conn, f"claim-{flow.facts['invoice']}", "reconcile", flow.facts["invoice"], "s", "e")
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    if decide == "approve":
        claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice)
    else:
        claims.reject_claim(flow.db_path, flow.checkpointer, flow.invoice)
    assert flow.conn.execute("SELECT status FROM failures").fetchall()[0][0] == "resolved"


def test_an_amount_after_a_word_or_a_hyphen_is_read_as_invented():
    for text in ("price 4.30-999.00 EUR", "Total_999.00 EUR", "x999.00 EUR"):
        problems = claims.check_draft(CLM_OK + " " + text, CLM)
        assert "invented amount 999.00" in problems, text


def test_a_small_integer_before_an_amount_and_a_percentage_are_handled():
    assert claims.check_draft(CLM_OK + " Line 3 350.00 EUR.", CLM) == []
    assert "invented amount 19.44" in claims.check_draft(CLM_OK + " A 19.44 EUR fee.", CLM)
    assert claims.check_draft(CLM_OK + " That is 19.44% more.", CLM) == []


def _probe_row_thread(flow, values, as_node):
    """Store the claim row with the model double, then leave the thread as a stop would."""
    claims.draft_message(flow.db_path, flow.client, flow.invoice)
    flow.graph(client=False).update_state(flow.run, values, as_node=as_node)


@pytest.mark.parametrize("decide", ["approve", "reject"])
def test_an_awaiting_claim_whose_thread_stopped_at_the_draft_can_be_decided(flow, decide):
    _probe_row_thread(flow, {"invoice_id": flow.invoice}, "__start__")
    assert flow.graph(client=False).get_state(flow.run).next == ("draft",)
    if decide == "approve":
        done = claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice)
        assert done["status"] == "sent" and len(flow.emails()) == 1
    else:
        done = claims.reject_claim(flow.db_path, flow.checkpointer, flow.invoice)
        assert done["status"] == "rejected" and flow.emails() == []
    assert flow.client.calls == 1


@pytest.mark.parametrize("finished", [False, True])
def test_a_foreign_thread_is_replaced_by_the_stored_claim(flow, tmp_path, finished):
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    foreign = {"to": "x@y.z", "subject": "old", "body": "OLD BODY FROM ANOTHER DB"}
    flow.graph(client=False).update_state(flow.run, {"message": foreign}, as_node="draft")
    if finished:
        flow.graph(client=False).update_state(flow.run, {"decision": "reject", "final_body": None}, as_node="close")
        assert flow.graph(client=False).get_state(flow.run).next == ()
    done = claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice)
    assert (done["status"], done["edited"]) == ("sent", 0)
    assert done["final_body"] == done["draft_body"] != foreign["body"]
    assert "OLD BODY" not in flow.emails()[0].read_text()


def test_a_crlf_body_is_not_refused_on_a_retry_and_keeps_the_written_date(flow):
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    body = "Dear S,\r\nline\r\nBest regards"
    name = f"CLAIM-{flow.facts['invoice']}-SUP-001"
    claims.quotes._write_email(flow.outbox, name, "a@b.c", "s", body, "2026-10-12 09:00:00")
    claims.sim_clock.advance(flow.conn, days=2)
    done = claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice, body)
    assert done["status"] == "sent" and done["decided_at"] == "2026-10-12 09:00:00"


@pytest.mark.parametrize("decide", ["approve", "reject"])
def test_the_failure_is_resolved_in_the_same_transaction_as_the_decision(flow, monkeypatch, decide):
    claims.db.park_failure(flow.conn, f"claim-{flow.facts['invoice']}", "reconcile", flow.facts["invoice"], "s", "e")
    flow.graph().invoke({"invoice_id": flow.invoice}, flow.run)
    real = claims._resolve_failure

    def stop(conn, invoice_id):
        real(conn, invoice_id)
        raise KeyboardInterrupt

    monkeypatch.setattr(claims, "_resolve_failure", stop)
    with pytest.raises(KeyboardInterrupt):
        if decide == "approve":
            claims.approve_claim(flow.db_path, flow.checkpointer, flow.outbox, flow.invoice)
        else:
            claims.reject_claim(flow.db_path, flow.checkpointer, flow.invoice)
    assert flow.row()[0]["status"] == "awaiting_approval"
    assert flow.conn.execute("SELECT status FROM failures").fetchall()[0][0] == "needs_review"
