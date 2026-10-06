"""C2: schema, seed volume and idempotence."""

from collections import Counter

from purchase_cycle import db


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


def test_seed_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "seed.db")
    first = db.seed(conn)
    second = db.seed(conn)
    assert first == second
