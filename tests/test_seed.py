"""C2: schema, seed volume and idempotence."""

import sqlite3
from collections import Counter

import pytest

from purchase_cycle import db
from purchase_cycle.catalog import PRODUCTS


def test_seed_creates_tables_and_catalog(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    counts = db.seed(conn)
    assert set(counts) == {"customers", "products", "stock", "orders", "order_lines"}
    assert counts["products"] >= 300
    assert counts["customers"] >= 50
    assert counts["stock"] == counts["products"]

    families = Counter(r[0] for r in conn.execute("SELECT family FROM products"))
    assert 28 <= len(families) <= 35
    assert all(n >= 4 for n in families.values()), "every family has variants"
    kinds = {r[0] for r in conn.execute("SELECT kind FROM customers")}
    assert kinds == {"clinic", "care_home", "pharmacy"}
    assert conn.execute("SELECT COUNT(*) FROM products WHERE latex = 1").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM products WHERE sterile = 1").fetchone()[0] > 0


def test_seed_creates_empty_clarifications_table(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    counts = db.seed(conn)
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(clarifications)")]
    assert columns == [
        "thread_id",
        "channel",
        "customer_code",
        "question",
        "round",
        "status",
        "created_at",
        "updated_at",
    ]
    assert conn.execute("SELECT COUNT(*) FROM clarifications").fetchone()[0] == 0
    # the existing tables and the seed summary are unchanged
    assert set(counts) == {"customers", "products", "stock", "orders", "order_lines"}
    assert (counts["orders"], counts["order_lines"]) == (0, 0)
    assert counts["stock"] == counts["products"]


def test_seed_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    first = db.seed(conn)
    second = db.seed(conn)
    assert first == second


def test_seed_creates_empty_order_sources_table(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    counts = db.seed(conn)
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(order_sources)")]
    assert columns == ["channel", "message_id", "thread_id", "order_id"]
    assert conn.execute("SELECT COUNT(*) FROM order_sources").fetchone()[0] == 0
    # the seed summary is unchanged
    assert set(counts) == {"customers", "products", "stock", "orders", "order_lines"}
    assert (counts["orders"], counts["order_lines"]) == (0, 0)


def _stored(conn) -> tuple[int, int, int]:
    return tuple(
        conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("orders", "order_sources", "order_lines")
    )


def test_repeated_source_returns_the_stored_order(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    first = db.insert_order(conn, "CLI-002", "email", "received", [("GLV-NIT-M", 40)], source=("m-1", "t-1"))
    again = db.insert_order(conn, "CLI-002", "email", "received", [("GLV-NIT-M", 99)], source=("m-1", "t-2"))
    other = db.insert_order(conn, "CLI-002", "whatsapp", "received", [("GLV-NIT-M", 1)], source=("m-1", "t-3"))
    assert again == first != other
    assert _stored(conn) == (2, 2, 2)
    row = dict(conn.execute("SELECT * FROM order_sources WHERE channel = 'email'").fetchone())
    assert row == {"channel": "email", "message_id": "m-1", "thread_id": "t-1", "order_id": first}


def test_failure_inside_the_store_transaction_leaves_no_partial_row(tmp_path):
    import sqlite3

    import pytest

    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    # the order and its source row are written before the lines, so the refused second line rolls both back
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_order(conn, "CLI-002", "email", "received", [("GLV-NIT-M", 4), ("GLV-NIT-M", 0)], source=("m", "t"))
    assert _stored(conn) == (0, 0, 0)


def _rows(conn, table):
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]


def test_seed_creates_three_suppliers_and_their_families(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    counts = db.seed(conn)
    supplier_columns = [r["name"] for r in conn.execute("PRAGMA table_info(suppliers)")]
    assert supplier_columns == ["code", "name", "email"]
    family_columns = [r["name"] for r in conn.execute("PRAGMA table_info(supplier_families)")]
    assert family_columns == ["supplier_code", "family"]
    suppliers = _rows(conn, "suppliers")
    assert len(suppliers) == 3
    assert len({s[2] for s in suppliers}) == 3, "each supplier has its own email"
    # every catalog product is sold by at least two suppliers
    sellers = conn.execute(
        "SELECT p.sku, COUNT(f.supplier_code) FROM products p "
        "LEFT JOIN supplier_families f ON f.family = p.family GROUP BY p.sku"
    ).fetchall()
    assert len(sellers) == counts["products"]
    assert all(n >= 2 for _, n in sellers)
    # the families overlap but no supplier sells everything
    per_supplier = Counter(r[0] for r in conn.execute("SELECT supplier_code FROM supplier_families"))
    families = conn.execute("SELECT COUNT(DISTINCT family) FROM products").fetchone()[0]
    assert all(n < families for n in per_supplier.values())


def test_seed_twice_gives_the_same_supplier_rows_and_keeps_the_existing_tables(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    first_counts = db.seed(conn)
    tables = ("customers", "products", "stock", "suppliers", "supplier_families")
    first = {t: _rows(conn, t) for t in tables}
    second_counts = db.seed(conn)
    assert {t: _rows(conn, t) for t in tables} == first
    # the seed summary and the existing rows are as before the phase
    assert first_counts == second_counts
    assert set(second_counts) == {"customers", "products", "stock", "orders", "order_lines"}
    stock = dict(conn.execute("SELECT sku, on_hand FROM stock"))
    skus = [r[0] for r in conn.execute("SELECT sku FROM products")]
    assert stock == {p.sku: (i * 37) % 250 for i, p in enumerate(PRODUCTS)}
    assert sorted(skus) == sorted(p.sku for p in PRODUCTS)


def test_seed_creates_empty_quote_offers_and_purchase_orders_tables(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    counts = db.seed(conn)
    offers = [r["name"] for r in conn.execute("PRAGMA table_info(quote_offers)")]
    assert offers == [
        "request_id",
        "supplier_code",
        "offers",
        "quantity",
        "unit_price",
        "currency",
        "vat_included",
        "vat_rate",
        "lead_time_days",
        "working_days",
        "price_text",
        "net_unit_price",
        "usable",
        "reason",
        "reply_file",
        "decided_at",
    ]
    orders = [r["name"] for r in conn.execute("PRAGMA table_info(purchase_orders)")]
    assert orders == [
        "id",
        "request_id",
        "supplier_code",
        "sku",
        "quantity",
        "unit_price",
        "currency",
        "total",
        "expected_delivery",
        "issued_at",
        "status",
    ]
    assert conn.execute("SELECT COUNT(*) FROM quote_offers").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 0
    assert set(counts) == {"customers", "products", "stock", "orders", "order_lines"}


def test_quote_requests_accept_the_decision_statuses_and_refuse_others(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    skus = [r[0] for r in conn.execute("SELECT sku FROM products LIMIT 7")]
    insert = (
        "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline)"
        " VALUES (?, 5, ?, ?, 't', 't', 't')"
    )
    statuses = ["waiting", "ready_for_decision", "no_offers", "awaiting_approval", "ordered", "rejected"]
    for sku, status in zip(skus[:6], statuses, strict=True):
        conn.execute(insert, (sku, f"th-{status}", status))
    assert conn.execute("SELECT COUNT(*) FROM quote_requests").fetchone()[0] == 6
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, (skus[6], "th-closed", "closed"))


def test_seed_twice_keeps_the_decision_tables_empty_and_the_other_rows(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    tables = ("customers", "products", "stock", "suppliers", "supplier_families", "quote_offers", "purchase_orders")
    first = {t: _rows(conn, t) for t in tables}
    db.seed(conn)
    assert {t: _rows(conn, t) for t in tables} == first


RECONCILIATION_TABLES = ("supplier_documents", "document_lines", "reconciliations", "discrepancies")
OLD_TABLES = ("customers", "products", "stock", "suppliers", "supplier_families", "quote_offers", "purchase_orders")


def _document(conn, number="INV-X", content_hash="h1", status="extracted", kind="invoice"):
    conn.execute(
        "INSERT INTO supplier_documents (kind, number, po_reference, supplier_text, purchase_order_id, supplier_code, issue_date, "
        "currency, source_file, content_hash, status, reason, created_at) "
        "VALUES (?, ?, NULL, NULL, NULL, NULL, NULL, 'EUR', 'f.txt', ?, ?, NULL, 't')",
        (kind, number, content_hash, status),
    )


def test_seed_creates_empty_reconciliation_tables(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    counts = db.seed(conn)
    assert [r["name"] for r in conn.execute("PRAGMA table_info(supplier_documents)")] == [
        "id",
        "kind",
        "number",
        "po_reference",
        "supplier_text",
        "purchase_order_id",
        "supplier_code",
        "issue_date",
        "currency",
        "source_file",
        "content_hash",
        "status",
        "reason",
        "created_at",
    ]
    assert [r["name"] for r in conn.execute("PRAGMA table_info(document_lines)")] == [
        "document_id",
        "line_no",
        "sku",
        "product_text",
        "quantity",
        "unit_price",
        "line_total",
    ]
    assert [r["name"] for r in conn.execute("PRAGMA table_info(reconciliations)")] == [
        "id",
        "invoice_id",
        "purchase_order_id",
        "delivery_note_id",
        "result",
        "reconciled_at",
    ]
    assert [r["name"] for r in conn.execute("PRAGMA table_info(discrepancies)")] == [
        "id",
        "reconciliation_id",
        "sku",
        "type",
        "expected",
        "found",
        "unit_amount",
        "amount",
        "text",
    ]
    for table in RECONCILIATION_TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert set(counts) == {"customers", "products", "stock", "orders", "order_lines"}


def test_seed_twice_keeps_the_reconciliation_tables_and_the_other_rows(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    tables = OLD_TABLES + RECONCILIATION_TABLES
    first = {t: _rows(conn, t) for t in tables}
    db.seed(conn)
    assert {t: _rows(conn, t) for t in tables} == first


def test_a_phase_07_database_gains_the_reconciliation_tables_without_losing_rows(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    for table in reversed(RECONCILIATION_TABLES):
        conn.execute(f"DROP TABLE {table}")
    conn.execute(
        "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES ((SELECT sku FROM products LIMIT 1), 5, 'th', 'ordered', 't', 't', 't')"
    )
    conn.commit()
    before = {t: _rows(conn, t) for t in OLD_TABLES + ("quote_requests",)}
    db.seed(conn)
    assert {t: _rows(conn, t) for t in OLD_TABLES + ("quote_requests",)} == before
    for table in RECONCILIATION_TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_document_status_kind_result_and_type_checks_reject_unknown_values(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    for status in ("extracted", "linked", "unlinked", "extraction_failed"):
        _document(conn, number=status, content_hash=status, status=status)
    with pytest.raises(sqlite3.IntegrityError):
        _document(conn, number="bad", content_hash="bad", status="done")
    with pytest.raises(sqlite3.IntegrityError):
        _document(conn, number="bad", content_hash="bad2", kind="receipt")
    invoice = conn.execute("SELECT id FROM supplier_documents LIMIT 1").fetchone()[0]
    sku = conn.execute("SELECT sku FROM products LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (?, 5, 'th', 'ordered', 't', 't', 't')",
        (sku,),
    )
    conn.execute(
        "INSERT INTO purchase_orders (request_id, supplier_code, sku, quantity, unit_price, currency, total, "
        "expected_delivery, issued_at, status) VALUES (1, (SELECT code FROM suppliers LIMIT 1), ?, 5, 1.0, 'EUR', "
        "5.0, 't', 't', 'issued')",
        (sku,),
    )
    insert = "INSERT INTO reconciliations (invoice_id, purchase_order_id, result, reconciled_at) VALUES (?, 1, ?, 't')"
    for i, result in enumerate(("matched", "discrepancies", "awaiting_delivery_note")):
        conn.execute(insert, (invoice + i, result))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, (invoice + 3, "unlinked"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, (invoice, "matched"))  # one reconciliation per invoice
    add = "INSERT INTO discrepancies (reconciliation_id, sku, type, expected, found, unit_amount, amount) VALUES (1, ?, ?, 1, 2, 1, 1)"
    for kind in ("price", "quantity", "extra_line", "missing_line"):
        conn.execute(add, (sku, kind))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(add, (sku, "other"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(add, (sku, "price"))  # unique on reconciliation, sku and type


def test_the_content_hash_is_unique(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    _document(conn, number="A", content_hash="same")
    with pytest.raises(sqlite3.IntegrityError):
        _document(conn, number="B", content_hash="same")


CLAIM_COLUMNS = [
    "id",
    "reconciliation_id",
    "recipient",
    "subject",
    "draft_body",
    "draft_source",
    "final_body",
    "edited",
    "status",
    "drafted_at",
    "decided_at",
    "outbox_file",
]


def _reconciliation(conn, number="INV-X"):
    """One stored reconciliation (with its purchase order) and its id."""
    _document(conn, number=number, content_hash=number)
    invoice = conn.execute("SELECT id FROM supplier_documents WHERE number = ?", (number,)).fetchone()[0]
    sku = conn.execute("SELECT sku FROM products LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (?, 5, ?, 'ordered', 't', 't', 't')",
        (sku, f"th-{number}"),
    )
    request = conn.execute("SELECT MAX(id) FROM quote_requests").fetchone()[0]
    conn.execute(
        "INSERT INTO purchase_orders (request_id, supplier_code, sku, quantity, unit_price, currency, total, "
        "expected_delivery, issued_at, status) VALUES (?, (SELECT code FROM suppliers LIMIT 1), ?, 5, 1.0, 'EUR', "
        "5.0, 't', 't', 'issued')",
        (request, sku),
    )
    order = conn.execute("SELECT MAX(id) FROM purchase_orders").fetchone()[0]
    conn.execute(
        "INSERT INTO reconciliations (invoice_id, purchase_order_id, result, reconciled_at) "
        "VALUES (?, ?, 'discrepancies', 't')",
        (invoice, order),
    )
    return conn.execute("SELECT MAX(id) FROM reconciliations").fetchone()[0]


def _claim(conn, reconciliation_id, status="awaiting_approval", source="model", edited=0):
    conn.execute(
        "INSERT INTO claims (reconciliation_id, recipient, subject, draft_body, draft_source, edited, status, drafted_at) "
        "VALUES (?, 'a@b.c', 's', 'b', ?, ?, ?, 't')",
        (reconciliation_id, source, edited, status),
    )


def test_seed_creates_an_empty_claims_table_and_leaves_the_seed_counts(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    counts = db.seed(conn)
    assert [r["name"] for r in conn.execute("PRAGMA table_info(claims)")] == CLAIM_COLUMNS
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
    assert set(counts) == {"customers", "products", "stock", "orders", "order_lines"}


def test_seed_twice_keeps_the_claims_and_the_other_rows(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    _claim(conn, _reconciliation(conn))
    conn.commit()
    tables = OLD_TABLES + RECONCILIATION_TABLES + ("claims",)
    first = {t: _rows(conn, t) for t in tables}
    db.seed(conn)
    assert {t: _rows(conn, t) for t in tables} == first
    assert len(first["claims"]) == 1


def test_a_phase_08_database_gains_the_claims_table_without_losing_rows(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    _reconciliation(conn)
    conn.execute("DROP TABLE claims")
    conn.commit()
    tables = OLD_TABLES + RECONCILIATION_TABLES + ("quote_requests",)
    before = {t: _rows(conn, t) for t in tables}
    assert before["reconciliations"]
    db.seed(conn)
    assert {t: _rows(conn, t) for t in tables} == before
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0


def test_claim_status_and_source_checks_reject_unknown_values(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    reconciliation = _reconciliation(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _claim(conn, reconciliation, status="done")
    with pytest.raises(sqlite3.IntegrityError):
        _claim(conn, reconciliation, source="human")
    with pytest.raises(sqlite3.IntegrityError):
        _claim(conn, reconciliation, edited=2)
    for i, status in enumerate(("awaiting_approval", "sent", "rejected")):
        _claim(conn, reconciliation if i == 0 else _reconciliation(conn, f"INV-{i}"), status=status)


def test_a_second_claim_for_one_reconciliation_is_rejected(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    reconciliation = _reconciliation(conn)
    _claim(conn, reconciliation)
    with pytest.raises(sqlite3.IntegrityError):
        _claim(conn, reconciliation, status="rejected")


RECEIPT_TABLES = ("goods_receipts", "goods_receipt_lines", "customer_delivery_notes", "customer_delivery_note_lines")


def _delivery_note_and_purchase_order(conn, number="DN-X"):
    """One stored delivery note with its purchase order: (document id, purchase order id)."""
    reconciliation = _reconciliation(conn, number=f"INV-{number}")
    purchase_order = conn.execute(
        "SELECT purchase_order_id FROM reconciliations WHERE id = ?", (reconciliation,)
    ).fetchone()[0]
    _document(conn, number=number, content_hash=number, status="linked", kind="delivery_note")
    return conn.execute("SELECT MAX(id) FROM supplier_documents").fetchone()[0], purchase_order


def _receipt(conn, note, purchase_order, status="received"):
    conn.execute(
        "INSERT INTO goods_receipts (delivery_note_id, purchase_order_id, received_at, status) VALUES (?, ?, 't', ?)",
        (note, purchase_order, status),
    )


def _customer_note(conn, order_id, number_hint="x"):
    conn.execute(
        "INSERT INTO customer_delivery_notes (order_id, customer_code, issued_at, document_file) "
        "VALUES (?, (SELECT code FROM customers LIMIT 1), 't', ?)",
        (order_id, f"{number_hint}.txt"),
    )


def _customer_order(conn):
    conn.execute(
        "INSERT INTO orders (customer_code, channel, status) VALUES ((SELECT code FROM customers LIMIT 1), 'demo', 'received')"
    )
    return conn.execute("SELECT MAX(id) FROM orders").fetchone()[0]


def test_seed_creates_the_four_receipt_tables_empty(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    counts = db.seed(conn)
    for table in RECEIPT_TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert [r["name"] for r in conn.execute("PRAGMA table_info(goods_receipts)")] == [
        "id",
        "delivery_note_id",
        "purchase_order_id",
        "received_at",
        "status",
    ]
    assert set(counts) == {"customers", "products", "stock", "orders", "order_lines"}


def test_seed_twice_gives_the_same_rows_in_every_table(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    names = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    ]
    first = {t: _rows(conn, t) for t in names}
    db.seed(conn)
    assert {t: _rows(conn, t) for t in names} == first


def test_a_phase_09_database_gains_the_receipt_tables_without_losing_rows(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    _claim(conn, _reconciliation(conn))
    for table in reversed(RECEIPT_TABLES):
        conn.execute(f"DROP TABLE {table}")
    conn.commit()
    tables = OLD_TABLES + RECONCILIATION_TABLES + ("claims", "quote_requests")
    before = {t: _rows(conn, t) for t in tables}
    assert before["claims"]
    db.seed(conn)
    assert {t: _rows(conn, t) for t in tables} == before
    for table in RECEIPT_TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_a_changed_stock_survives_a_reseed(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    sku = conn.execute("SELECT sku FROM stock LIMIT 1").fetchone()[0]
    original = conn.execute("SELECT on_hand FROM stock WHERE sku = ?", (sku,)).fetchone()[0]
    conn.execute("UPDATE stock SET on_hand = ? WHERE sku = ?", (original + 17, sku))
    conn.commit()
    db.seed(conn)
    assert conn.execute("SELECT on_hand FROM stock WHERE sku = ?", (sku,)).fetchone()[0] == original + 17


def test_receipt_status_check_rejects_unknown_values(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    note, purchase_order = _delivery_note_and_purchase_order(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _receipt(conn, note, purchase_order, status="done")
    _receipt(conn, note, purchase_order, status="failed")


def test_a_second_receipt_for_one_delivery_note_is_rejected(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    note, purchase_order = _delivery_note_and_purchase_order(conn)
    _receipt(conn, note, purchase_order)
    with pytest.raises(sqlite3.IntegrityError):
        _receipt(conn, note, purchase_order, status="failed")


def test_a_second_customer_delivery_note_for_one_order_is_rejected(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    order = _customer_order(conn)
    _customer_note(conn, order, "a")
    with pytest.raises(sqlite3.IntegrityError):
        _customer_note(conn, order, "b")


def test_receipt_line_quantities_must_be_positive_whole_numbers(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    db.seed(conn)
    note, purchase_order = _delivery_note_and_purchase_order(conn)
    _receipt(conn, note, purchase_order)
    receipt = conn.execute("SELECT MAX(id) FROM goods_receipts").fetchone()[0]
    sku = conn.execute("SELECT sku FROM products LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO goods_receipt_lines VALUES (?, 1, ?, 0)", (receipt, sku))
    conn.execute("INSERT INTO goods_receipt_lines VALUES (?, 1, ?, 3)", (receipt, sku))
    order = _customer_order(conn)
    _customer_note(conn, order)
    cdn = conn.execute("SELECT MAX(id) FROM customer_delivery_notes").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO customer_delivery_note_lines VALUES (?, 1, ?, 0, 'box')", (cdn, sku))
    conn.execute("INSERT INTO customer_delivery_note_lines VALUES (?, 1, ?, 2, 'box')", (cdn, sku))
