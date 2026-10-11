"""C2, C4: quote request tables and the shortfall of stored `received` orders against stock."""

import sqlite3

import pytest

from purchase_cycle import db, quotes
from purchase_cycle.catalog import CUSTOMERS, PRODUCTS

CUSTOMER = CUSTOMERS[0].code
# Seeded stock is (i * 37) % 250: 0, 37 and 74 units for the first three products.
EMPTY, LOW, MID = PRODUCTS[0].sku, PRODUCTS[1].sku, PRODUCTS[2].sku


def _seeded(tmp_path):
    conn = db.connect(tmp_path / "b.db")
    db.seed(conn)
    return conn


def _order(conn, lines, status="received", channel="web_form"):
    return db.insert_order(conn, CUSTOMER, channel, status, lines)


def test_seed_creates_the_empty_quote_request_tables(tmp_path):
    conn = _seeded(tmp_path)
    requests = [r["name"] for r in conn.execute("PRAGMA table_info(quote_requests)")]
    assert requests == ["id", "sku", "quantity", "thread_id", "status", "created_at", "reminder_at", "deadline"]
    suppliers = [r["name"] for r in conn.execute("PRAGMA table_info(quote_request_suppliers)")]
    assert suppliers == ["request_id", "supplier_code", "sent_at", "reminded_at", "replied_at", "reply_file"]
    assert conn.execute("SELECT COUNT(*) FROM quote_requests").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM quote_request_suppliers").fetchone()[0] == 0


def test_shortfall_is_the_demand_of_received_orders_minus_on_hand(tmp_path):
    conn = _seeded(tmp_path)
    _order(conn, [(EMPTY, 5), (LOW, 30)])
    _order(conn, [(LOW, 20)], channel="email")
    _order(conn, [(EMPTY, 3)], channel="whatsapp")
    # one row per short SKU, in SKU order
    assert quotes.shortfalls(conn) == sorted(
        [
            {"sku": EMPTY, "demand": 8, "on_hand": 0, "shortfall": 8},
            {"sku": LOW, "demand": 50, "on_hand": 37, "shortfall": 13},
        ],
        key=lambda r: r["sku"],
    )


def test_a_sku_with_enough_stock_gives_no_shortfall(tmp_path):
    conn = _seeded(tmp_path)
    _order(conn, [(MID, 74), (LOW, 37)])
    assert quotes.shortfalls(conn) == []


def test_orders_with_another_status_are_ignored(tmp_path):
    conn = _seeded(tmp_path)
    _order(conn, [(EMPTY, 5)], status="cancelled")
    _order(conn, [(LOW, 50)], status="shipped")
    assert quotes.shortfalls(conn) == []


def test_a_sku_with_a_waiting_request_is_skipped(tmp_path):
    conn = _seeded(tmp_path)
    _order(conn, [(EMPTY, 5), (LOW, 40)])
    conn.execute(
        "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (?, 5, 'quote-QR-0001', 'waiting', '2026-10-12 09:00:00', '2026-10-15 09:00:00', "
        "'2026-10-17 09:00:00')",
        (EMPTY,),
    )
    assert [r["sku"] for r in quotes.shortfalls(conn)] == [LOW]
    # a closed request still covers its units: only new demand beyond them counts
    conn.execute("UPDATE quote_requests SET status = 'no_offers'")
    assert [r["sku"] for r in quotes.shortfalls(conn)] == [LOW]
    _order(conn, [(EMPTY, 2)])
    assert [(r["sku"], r["shortfall"]) for r in quotes.shortfalls(conn)] == sorted([(EMPTY, 2), (LOW, 3)])


def test_a_second_waiting_request_for_the_same_sku_is_refused_by_the_schema(tmp_path):
    conn = _seeded(tmp_path)
    insert = (
        "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (?, 5, ?, 'waiting', '2026-10-12 09:00:00', '2026-10-15 09:00:00', '2026-10-17 09:00:00')"
    )
    conn.execute(insert, (EMPTY, "quote-QR-0001"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, (EMPTY, "quote-QR-0002"))
