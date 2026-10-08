"""Shared SQLite business database: schema, seed and catalog queries."""

import sqlite3
from pathlib import Path

from purchase_cycle.catalog import CUSTOMERS, PRODUCTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('clinic', 'care_home', 'pharmacy')),
    city TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    name TEXT NOT NULL UNIQUE,
    sale_unit TEXT NOT NULL,
    price_eur REAL NOT NULL,
    latex INTEGER NOT NULL,
    sterile INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS stock (
    sku TEXT PRIMARY KEY REFERENCES products (sku),
    on_hand INTEGER NOT NULL CHECK (on_hand >= 0)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_code TEXT NOT NULL REFERENCES customers (code),
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS order_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders (id),
    sku TEXT NOT NULL REFERENCES products (sku),
    quantity INTEGER NOT NULL CHECK (quantity > 0)
);
CREATE TABLE IF NOT EXISTS clarifications (
    thread_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    customer_code TEXT NOT NULL REFERENCES customers (code),
    question TEXT NOT NULL,
    round INTEGER NOT NULL CHECK (round > 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'answered', 'closed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

TABLES = ("customers", "products", "stock", "orders", "order_lines")


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def seed(conn: sqlite3.Connection) -> dict[str, int]:
    """Create the schema and load the permanent catalog; safe to re-run."""
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT OR REPLACE INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(c.code, c.name, c.kind, c.city, c.contact_name, c.email, c.phone) for c in CUSTOMERS],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO products VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(p.sku, p.family, p.name, p.sale_unit, round(p.price_eur, 2), int(p.latex), int(p.sterile)) for p in PRODUCTS],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO stock VALUES (?, ?)",
        [(p.sku, (i * 37) % 250) for i, p in enumerate(PRODUCTS)],
    )
    conn.commit()
    return row_counts(conn)


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def catalog_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT sku, name, sale_unit FROM products ORDER BY sku").fetchall()


def get_product(conn: sqlite3.Connection, sku: str) -> dict | None:
    row = conn.execute(
        "SELECT p.sku, p.name, p.sale_unit, p.price_eur, s.on_hand FROM products p JOIN stock s USING (sku) WHERE sku = ?",
        (sku,),
    ).fetchone()
    return dict(row) if row else None


def insert_order(
    conn: sqlite3.Connection,
    customer_code: str,
    channel: str,
    status: str,
    lines: list[tuple],
    clarification: tuple[str, str] | None = None,
) -> int:
    """Insert one order and its (sku, quantity) lines in one transaction: all of it, or nothing.

    `clarification` is a (thread id, final status) pair whose row changes status in the same transaction.
    """
    with conn:
        if clarification:
            finish_clarification(conn, *clarification)
        cursor = conn.execute(
            "INSERT INTO orders (customer_code, channel, status) VALUES (?, ?, ?)", (customer_code, channel, status)
        )
        order_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO order_lines (order_id, sku, quantity) VALUES (?, ?, ?)",
            [(order_id, sku, quantity) for sku, quantity in lines],
        )
    return order_id


def order_line_details(conn: sqlite3.Connection, order_id: int) -> list[dict]:
    """Stored lines of one order with the product name, sale unit and price from the database."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT p.name, p.sale_unit, p.price_eur, l.quantity FROM order_lines l "
            "JOIN products p USING (sku) WHERE l.order_id = ? ORDER BY l.id",
            (order_id,),
        )
    ]


def save_clarification(
    conn: sqlite3.Connection, thread_id: str, channel: str, customer_code: str, question: str, round_: int
) -> None:
    """Record the pending question of a thread; a later round replaces the question and the round.

    A later round only updates a row that is still pending, so a thread another process closed or answered
    meanwhile is not reopened; it raises NotPending and nothing changes.
    """
    with conn:
        cursor = conn.execute(
            "INSERT INTO clarifications (thread_id, channel, customer_code, question, round, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending') ON CONFLICT (thread_id) DO UPDATE SET question = excluded.question, "
            "round = excluded.round, updated_at = datetime('now') WHERE clarifications.status = 'pending'",
            (thread_id, channel, customer_code, question, round_),
        )
    if cursor.rowcount == 0:
        raise NotPending(f"thread {thread_id} is not pending; nothing changed")


class NotPending(RuntimeError):
    """The clarification is no longer pending, for example another process finished it first."""


def finish_clarification(conn: sqlite3.Connection, thread_id: str, status: str) -> None:
    """Change the status of a pending clarification row; the caller owns the transaction.

    Raises NotPending when the row is not pending, so the caller's transaction rolls back and stores nothing.
    """
    cursor = conn.execute(
        "UPDATE clarifications SET status = ?, updated_at = datetime('now') WHERE thread_id = ? AND status = 'pending'",
        (status, thread_id),
    )
    if cursor.rowcount == 0:
        raise NotPending(f"thread {thread_id} is not pending; nothing changed")


def get_clarification(conn: sqlite3.Connection, thread_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM clarifications WHERE thread_id = ?", (thread_id,)).fetchone()
    return dict(row) if row else None


def pending_clarifications(conn: sqlite3.Connection) -> list[dict]:
    """Pending threads, oldest first, with their age in whole minutes."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT thread_id, channel, customer_code, round, question, created_at, "
            "CAST((julianday('now') - julianday(created_at)) * 1440 AS INTEGER) AS age_minutes "
            "FROM clarifications WHERE status = 'pending' ORDER BY created_at, thread_id"
        )
    ]
