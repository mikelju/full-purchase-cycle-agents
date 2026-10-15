"""C3, C4, C5: receipt candidates, received quantities and shortfalls after a receipt."""

import ast
from pathlib import Path

from purchase_cycle import db, quotes, receipts
from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.graph import sqlite_checkpointer

A, B, C = PRODUCTS[1].sku, PRODUCTS[2].sku, PRODUCTS[3].sku
EMPTY = PRODUCTS[0].sku  # seeded with no stock
CUSTOMER = CUSTOMERS[0].code


def _conn(tmp_path):
    conn = db.connect(tmp_path / "r.db")
    db.seed(conn)
    return conn


def _stock(conn, sku):
    return conn.execute("SELECT on_hand FROM stock WHERE sku = ?", (sku,)).fetchone()[0]


def _purchase_order(conn, sku=A, quantity=100):
    """A purchase order (with its quote request) and its id."""
    n = conn.execute("SELECT COUNT(*) FROM quote_requests").fetchone()[0]
    request = conn.execute(
        "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (?, ?, ?, 'ordered', 't', 't', 't')",
        (sku, quantity, f"th-{n}"),
    ).lastrowid
    return conn.execute(
        "INSERT INTO purchase_orders (request_id, supplier_code, sku, quantity, unit_price, currency, total, "
        "expected_delivery, issued_at, status) VALUES (?, (SELECT code FROM suppliers LIMIT 1), ?, ?, 1.0, 'EUR', "
        "1.0, 't', 't', 'issued')",
        (request, sku, quantity),
    ).lastrowid


def _document(conn, kind, number, status="linked", lines=(), purchase_order=None):
    doc = conn.execute(
        "INSERT INTO supplier_documents (kind, number, purchase_order_id, source_file, content_hash, status, created_at) "
        "VALUES (?, ?, ?, 'f.txt', ?, ?, 't')",
        (kind, number, purchase_order, f"hash-{number}", status),
    ).lastrowid
    for n, (sku, quantity) in enumerate(lines, 1):
        conn.execute(
            "INSERT INTO document_lines (document_id, line_no, sku, product_text, quantity) VALUES (?, ?, ?, 'x', ?)",
            (doc, n, sku, quantity),
        )
    return doc


def _note(conn, number="DN-1", result="matched", lines=((A, 100),), sku=A, quantity=100, claim=None):
    """A linked delivery note with an invoice reconciliation (result None: no invoice); returns the note id."""
    purchase_order = _purchase_order(conn, sku, quantity)
    note = _document(conn, "delivery_note", number, lines=lines, purchase_order=purchase_order)
    if result:
        invoice = _document(conn, "invoice", f"INV-{number}", purchase_order=purchase_order)
        rec = conn.execute(
            "INSERT INTO reconciliations (invoice_id, purchase_order_id, delivery_note_id, result, reconciled_at) "
            "VALUES (?, ?, ?, ?, 't')",
            (invoice, purchase_order, note, result),
        ).lastrowid
        if claim:
            conn.execute(
                "INSERT INTO claims (reconciliation_id, recipient, subject, draft_body, draft_source, status, drafted_at) "
                "VALUES (?, 'a@b.c', 's', 'b', 'template', ?, 't')",
                (rec, claim),
            )
    conn.commit()
    return note


def _receipt_rows(conn):
    return [tuple(r) for r in conn.execute("SELECT delivery_note_id, status FROM goods_receipts ORDER BY id")]


def test_candidates_are_the_linked_notes_of_matched_and_discrepancies_reconciliations(tmp_path):
    conn = _conn(tmp_path)
    ids = [
        _note(conn, "DN-1", "matched"),
        _note(conn, "DN-2", "discrepancies"),
        _note(conn, "DN-3", "discrepancies", claim="sent"),
        _note(conn, "DN-4", "discrepancies", claim="rejected"),
        _note(conn, "DN-5", "discrepancies", claim="awaiting_approval"),
    ]
    _note(conn, "DN-6", "awaiting_delivery_note")
    _note(conn, "DN-7", None)
    unlinked = _note(conn, "DN-8", "matched")
    conn.execute("UPDATE supplier_documents SET status = 'unlinked' WHERE id = ?", (unlinked,))
    conn.commit()
    assert receipts.candidates(conn) == ids


def test_a_received_note_and_a_failed_note_are_no_longer_candidates(tmp_path):
    conn = _conn(tmp_path)
    _note(conn, "DN-1")
    _note(conn, "DN-2", lines=((A, 1.5),))
    receipts.receive(conn)
    assert receipts.candidates(conn) == []


def test_a_note_linked_by_a_later_reconciliation_becomes_a_candidate(tmp_path):
    conn = _conn(tmp_path)
    _note(conn, "DN-1")
    receipts.receive(conn)
    late = _note(conn, "DN-2", None)
    assert receipts.candidates(conn) == []
    invoice = _document(conn, "invoice", "INV-late", purchase_order=1)
    conn.execute(
        "INSERT INTO reconciliations (invoice_id, purchase_order_id, delivery_note_id, result, reconciled_at) "
        "VALUES (?, 1, ?, 'matched', 't')",
        (invoice, late),
    )
    conn.commit()
    assert receipts.candidates(conn) == [late]


def test_receiving_adds_short_exact_and_over_deliveries_and_other_skus(tmp_path):
    conn = _conn(tmp_path)
    before = {sku: _stock(conn, sku) for sku in (A, B, C)}
    _note(conn, "DN-1", lines=((A, 60),), sku=A, quantity=100)  # short
    _note(conn, "DN-2", lines=((B, 50),), sku=B, quantity=50)  # exact
    _note(conn, "DN-3", lines=((C, 70), (B, 5)), sku=C, quantity=40)  # over-delivery and a SKU not on the order
    done = receipts.receive(conn)
    assert [d["status"] for d in done] == ["received"] * 3
    assert _stock(conn, A) == before[A] + 60
    assert _stock(conn, B) == before[B] + 55
    assert _stock(conn, C) == before[C] + 70
    lines = [
        tuple(r)
        for r in conn.execute("SELECT receipt_id, line_no, sku, quantity FROM goods_receipt_lines ORDER BY 1, 2")
    ]
    assert lines == [(1, 1, A, 60), (2, 1, B, 50), (3, 1, C, 70), (3, 2, B, 5)]
    assert conn.execute("SELECT DISTINCT received_at FROM goods_receipts").fetchone()[0] == "2026-10-12 09:00:00"


def test_an_unknown_sku_or_a_fractional_quantity_is_parked_and_the_next_note_is_received(tmp_path):
    conn = _conn(tmp_path)
    before = _stock(conn, A)
    unknown = _note(conn, "DN-1", lines=((A, 10), ("NO-SUCH", 5)))
    missing = _note(conn, "DN-2", lines=((None, 5),))
    fraction = _note(conn, "DN-3", lines=((A, 2.5),))
    empty = _note(conn, "DN-4", lines=())
    ok = _note(conn, "DN-5", lines=((A, 7),))
    done = receipts.receive(conn)
    assert [d["status"] for d in done] == ["failed"] * 4 + ["received"]
    assert _receipt_rows(conn) == [
        (unknown, "failed"),
        (missing, "failed"),
        (fraction, "failed"),
        (empty, "failed"),
        (ok, "received"),
    ]
    assert _stock(conn, A) == before + 7
    assert conn.execute("SELECT COUNT(*) FROM goods_receipt_lines WHERE receipt_id IN (1, 2, 3, 4)").fetchone()[0] == 0
    assert len(db.list_failures(conn)) == 4
    row = db.get_failure(conn, f"goods-receipt-DOC-{unknown:04d}")
    assert (row["channel"], row["step"], row["status"]) == ("receipts", "goods_receipt", "needs_review")
    assert "NO-SUCH" in row["error"]


def test_a_second_call_receives_nothing(tmp_path):
    conn = _conn(tmp_path)
    _note(conn, "DN-1", lines=((A, 10),))
    _note(conn, "DN-2", lines=((A, 1.5),))
    receipts.receive(conn)
    stock = _stock(conn, A)
    rows = _receipt_rows(conn)
    assert receipts.receive(conn) == []
    assert _stock(conn, A) == stock
    assert _receipt_rows(conn) == rows


def test_the_module_imports_no_llm_symbol():
    tree = ast.parse(Path(receipts.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {node.module or ""} | {a.name for a in node.names}
    assert not any("llm" in n or "anthropic" in n for n in names)


def _old_shortfalls(conn):
    """The phase 06 query, kept here as the oracle for a database with no receipts."""
    marks = ", ".join("?" for _ in quotes.ORDER_STATUSES)
    rows = conn.execute(
        "SELECT l.sku, SUM(l.quantity) AS demand, s.on_hand, "
        "(SELECT COALESCE(SUM(q.quantity), 0) FROM quote_requests q WHERE q.sku = l.sku) AS requested "
        "FROM order_lines l JOIN orders o ON o.id = l.order_id JOIN stock s ON s.sku = l.sku "
        f"WHERE o.status IN ({marks}) "
        "AND l.sku NOT IN (SELECT sku FROM quote_requests WHERE status = ?) "
        "GROUP BY l.sku HAVING SUM(l.quantity) > s.on_hand + requested ORDER BY l.sku",
        (*quotes.ORDER_STATUSES, quotes.WAITING),
    ).fetchall()
    return [
        {
            "sku": r["sku"],
            "demand": r["demand"],
            "on_hand": r["on_hand"],
            "shortfall": r["demand"] - r["on_hand"] - r["requested"],
        }
        for r in rows
    ]


def _order(conn, lines, status="received"):
    return db.insert_order(conn, CUSTOMER, "web_form", status, lines)


def test_shortfalls_without_receipts_equal_the_phase_06_query(tmp_path):
    conn = _conn(tmp_path)
    assert quotes.shortfalls(conn) == _old_shortfalls(conn) == []
    _order(conn, [(EMPTY, 40), (A, _stock(conn, A) + 9)])
    _order(conn, [(B, 5)], status="served")
    assert quotes.shortfalls(conn) == _old_shortfalls(conn) != []
    _purchase_order(conn, EMPTY, 25)  # an ordered request
    assert quotes.shortfalls(conn) == _old_shortfalls(conn)
    conn.execute(
        "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (?, 3, 'th-w', 'waiting', 't', 't', 't')",
        (A,),
    )
    conn.commit()
    assert quotes.shortfalls(conn) == _old_shortfalls(conn)


def _scan(tmp_path, conn):
    return quotes.scan(tmp_path / "r.db", sqlite_checkpointer(tmp_path / "checkpoints.sqlite"), tmp_path / "outbox")


def test_after_a_short_delivery_the_scan_starts_a_request_for_the_unreceived_remainder(tmp_path):
    conn = _conn(tmp_path)
    _order(conn, [(EMPTY, 100)])
    _note(conn, "DN-1", lines=((EMPTY, 60),), sku=EMPTY, quantity=100)
    assert quotes.shortfalls(conn) == []  # the ordered request still covers the demand
    receipts.receive(conn)
    assert [s["shortfall"] for s in quotes.shortfalls(conn)] == [40]
    started = _scan(tmp_path, conn)
    assert [(s["sku"], s["quantity"]) for s in started] == [(EMPTY, 40)]


def test_after_an_exact_delivery_that_covers_the_orders_no_request_starts(tmp_path):
    conn = _conn(tmp_path)
    _order(conn, [(EMPTY, 100)])
    _note(conn, "DN-1", lines=((EMPTY, 100),), sku=EMPTY, quantity=100)
    receipts.receive(conn)
    assert quotes.shortfalls(conn) == []
    assert _scan(tmp_path, conn) == []


def _status(conn, order_id):
    return conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()[0]


def test_serving_takes_orders_by_id_and_a_covered_order_is_served_before_any_receipt(tmp_path):
    conn = _conn(tmp_path)
    before = _stock(conn, A)
    first = _order(conn, [(A, 10)])
    second = _order(conn, [(A, 5), (B, 1)])
    result = receipts.serve(conn)
    assert [o["order_id"] for o in result["served"]] == [first, second]
    assert result["skipped"] == []
    assert _status(conn, first) == _status(conn, second) == "served"
    assert _stock(conn, A) == before - 15


def test_an_order_with_one_short_line_is_skipped_and_reports_the_missing_units(tmp_path):
    conn = _conn(tmp_path)
    before = (_stock(conn, A), _stock(conn, B))
    order = _order(conn, [(A, 1), (B, before[1] + 7), (B, 1)])
    result = receipts.serve(conn)
    assert result["served"] == []
    assert result["skipped"] == [{"order_id": order, "missing": {B: 8}}]
    assert _status(conn, order) == "received"
    assert (_stock(conn, A), _stock(conn, B)) == before


def test_a_large_older_order_is_skipped_while_a_later_one_is_served(tmp_path):
    conn = _conn(tmp_path)
    on_hand = _stock(conn, A)
    large = _order(conn, [(A, on_hand + 1)])
    small = _order(conn, [(A, on_hand)])
    later = _order(conn, [(A, 1)])
    result = receipts.serve(conn)
    assert [o["order_id"] for o in result["served"]] == [small]
    assert [o["order_id"] for o in result["skipped"]] == [large, later]
    assert result["skipped"][0]["missing"] == {A: 1}
    assert [_status(conn, o) for o in (large, small, later)] == ["received", "served", "received"]
    assert _stock(conn, A) == 0


def test_a_receipt_makes_a_waiting_order_servable_and_a_second_pass_serves_nothing(tmp_path):
    conn = _conn(tmp_path)
    order = _order(conn, [(EMPTY, 60)])
    assert [o["order_id"] for o in receipts.serve(conn)["skipped"]] == [order]
    _note(conn, "DN-1", lines=((EMPTY, 100),), sku=EMPTY, quantity=100)
    receipts.receive(conn)
    assert [o["order_id"] for o in receipts.serve(conn)["served"]] == [order]
    assert _stock(conn, EMPTY) == 40
    assert receipts.serve(conn) == {"served": [], "skipped": []}
    assert _stock(conn, EMPTY) == 40


def test_orders_that_are_not_received_are_not_served(tmp_path):
    conn = _conn(tmp_path)
    done = _order(conn, [(A, 1)], status="served")
    assert receipts.serve(conn) == {"served": [], "skipped": []}
    assert _status(conn, done) == "served"
