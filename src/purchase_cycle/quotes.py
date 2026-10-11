"""Quotes module: notice out-of-stock demand and request quotes from suppliers, waiting on a simulated clock."""

import sqlite3

from purchase_cycle import email_order, web_form, whatsapp_order

# The status every channel gives a stored order; nothing ships or reserves stock yet, so all of them count.
ORDER_STATUSES = sorted({web_form.STATUS, email_order.STATUS, whatsapp_order.STATUS})
WAITING = "waiting"


def shortfalls(conn: sqlite3.Connection) -> list[dict]:
    """Per SKU, units asked by stored orders minus `on_hand`, when positive and no request for it is waiting."""
    marks = ", ".join("?" for _ in ORDER_STATUSES)
    rows = conn.execute(
        "SELECT l.sku, SUM(l.quantity) AS demand, s.on_hand FROM order_lines l "
        "JOIN orders o ON o.id = l.order_id JOIN stock s ON s.sku = l.sku "
        f"WHERE o.status IN ({marks}) "
        "AND l.sku NOT IN (SELECT sku FROM quote_requests WHERE status = ?) "
        "GROUP BY l.sku HAVING SUM(l.quantity) > s.on_hand ORDER BY l.sku",
        (*ORDER_STATUSES, WAITING),
    ).fetchall()
    return [{**dict(r), "shortfall": r["demand"] - r["on_hand"]} for r in rows]
