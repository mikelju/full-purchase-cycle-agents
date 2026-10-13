"""C7: result per invoice, delivery note and the completion of the same reconciliation."""

import pytest

from purchase_cycle import db, reconciliation

SKU = "GLV-NIT-XS"


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "res.db")
    db.seed(connection)
    connection.execute(
        "INSERT INTO quote_requests (id, sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (1, ?, 10, 'quote-1', 'ordered', 't', 't', 't')",
        (SKU,),
    )
    connection.execute(
        "INSERT INTO purchase_orders (id, request_id, supplier_code, sku, quantity, unit_price, currency, total, "
        "expected_delivery, issued_at, status) VALUES (1, 1, 'SUP-001', ?, 10, 4.1, 'EUR', 41.0, '2026-10-20', 't', "
        "'issued')",
        (SKU,),
    )
    connection.commit()
    yield connection
    connection.close()


def add(conn, kind, quantity=10, price=4.1, sku=SKU, order=1, status="linked", digest=None):
    """A stored supplier document with one line, as the extraction and linking leave it."""
    cursor = conn.execute(
        "INSERT INTO supplier_documents (kind, number, po_reference, purchase_order_id, currency, source_file, "
        "content_hash, status, created_at) VALUES (?, 'N', 'PO-0001', ?, 'EUR', 'f', ?, ?, 't')",
        (
            kind,
            order if status == "linked" else None,
            digest or f"h{conn.execute('SELECT COUNT(*) FROM supplier_documents').fetchone()[0]}",
            status,
        ),
    )
    conn.execute(
        "INSERT INTO document_lines (document_id, line_no, sku, product_text, quantity, unit_price, line_total) "
        "VALUES (?, 1, ?, 'p', ?, ?, NULL)",
        (cursor.lastrowid, sku, quantity, None if kind == "delivery_note" else price),
    )
    conn.commit()
    return cursor.lastrowid


def rows(conn):
    recs = [tuple(r) for r in conn.execute("SELECT id, invoice_id, delivery_note_id, result FROM reconciliations")]
    diffs = [tuple(r) for r in conn.execute("SELECT reconciliation_id, sku, type FROM discrepancies ORDER BY id")]
    return recs, diffs


def test_an_invoice_with_its_note_and_no_difference_is_matched(conn):
    invoice = add(conn, "invoice")
    note = add(conn, "delivery_note")
    reconciliation.reconcile_pending(conn)
    assert rows(conn) == ([(1, invoice, note, "matched")], [])


def test_no_note_yet_is_awaiting_with_the_detected_rows_already_stored(conn):
    invoice = add(conn, "invoice", price=5.0)
    reconciliation.reconcile_pending(conn)
    assert rows(conn) == ([(1, invoice, None, "awaiting_delivery_note")], [(1, SKU, "price")])


def test_no_quantity_check_against_delivery_while_awaiting(conn):
    add(conn, "invoice", quantity=10)
    reconciliation.reconcile_pending(conn)
    assert rows(conn)[1] == []


def test_a_later_note_completes_the_same_reconciliation_to_matched(conn):
    add(conn, "invoice")
    reconciliation.reconcile_pending(conn)
    first = rows(conn)[0][0][0]
    note = add(conn, "delivery_note")
    reconciliation.reconcile_pending(conn)
    recs, diffs = rows(conn)
    assert len(recs) == 1 and recs[0][0] == first and recs[0][2:] == (note, "matched") and diffs == []


def test_a_later_note_completes_to_discrepancies_without_duplicate_rows(conn):
    add(conn, "invoice", price=5.0)
    reconciliation.reconcile_pending(conn)
    add(conn, "delivery_note", quantity=8)
    reconciliation.reconcile_pending(conn)
    reconciliation.reconcile_pending(conn)
    recs, diffs = rows(conn)
    assert len(recs) == 1 and recs[0][3] == "discrepancies"
    assert sorted(d[1:] for d in diffs) == [(SKU, "price"), (SKU, "quantity")]


def test_a_note_stored_before_its_invoice_is_used_when_the_invoice_arrives(conn):
    note = add(conn, "delivery_note", quantity=7)
    reconciliation.reconcile_pending(conn)
    assert rows(conn) == ([], [])
    invoice = add(conn, "invoice")
    reconciliation.reconcile_pending(conn)
    assert rows(conn) == ([(1, invoice, note, "discrepancies")], [(1, SKU, "quantity")])


def test_an_unlinked_document_creates_no_reconciliation(conn):
    add(conn, "invoice", status="unlinked")
    add(conn, "delivery_note", status="unlinked")
    assert reconciliation.reconcile_pending(conn) == []
    assert rows(conn) == ([], [])


def test_the_time_comes_from_the_simulated_clock_and_a_rerun_changes_nothing(conn):
    add(conn, "invoice")
    reconciliation.reconcile_pending(conn)
    stamp = conn.execute("SELECT reconciled_at FROM reconciliations").fetchone()[0]
    assert stamp == conn.execute("SELECT now FROM sim_clock").fetchone()[0]
    conn.execute("UPDATE sim_clock SET now = '2030-01-01 00:00:00'")
    conn.commit()
    assert reconciliation.reconcile_pending(conn) == []
    assert conn.execute("SELECT reconciled_at FROM reconciliations").fetchone()[0] == stamp
    add(conn, "delivery_note")
    reconciliation.reconcile_pending(conn)
    assert conn.execute("SELECT reconciled_at FROM reconciliations").fetchone()[0] == "2030-01-01 00:00:00"
