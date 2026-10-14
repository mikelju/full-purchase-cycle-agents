"""C3 and C4: claim candidates, claim facts and the amount in dispute."""

import pytest

from purchase_cycle import claims, db

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
