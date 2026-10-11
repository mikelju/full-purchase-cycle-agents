"""C2: schema, seed volume and idempotence."""

from collections import Counter

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
