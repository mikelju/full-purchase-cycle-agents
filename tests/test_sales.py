"""Phase 11 C3 to C5, C7, C10: customer invoices, signed notes, sales matching, corrections and the sales graph."""

import ast
from pathlib import Path

from purchase_cycle import db, receipts, sales, sim_clock
from purchase_cycle.catalog import CUSTOMERS, PRODUCTS

A, B, C = PRODUCTS[1].sku, PRODUCTS[2].sku, PRODUCTS[3].sku
CUSTOMER = CUSTOMERS[0].code


def _conn(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.seed(conn)
    return conn


def _price(conn, sku):
    return conn.execute("SELECT price_eur FROM products WHERE sku = ?", (sku,)).fetchone()[0]


def _served(tmp_path, conn, lines, customer=CUSTOMER):
    """A served order with its delivery note row (and file); returns the delivery note id."""
    order = db.insert_order(conn, customer, "web_form", "received", lines)
    for sku, quantity in lines:
        conn.execute("UPDATE stock SET on_hand = on_hand + ? WHERE sku = ?", (quantity, sku))
    conn.commit()
    receipts.serve(conn, tmp_path / "notes")
    receipts.issue(conn)
    return conn.execute("SELECT id FROM customer_delivery_notes WHERE order_id = ?", (order,)).fetchone()[0]


def test_one_invoice_per_delivery_note_with_lines_prices_and_totals(tmp_path):
    conn = _conn(tmp_path)
    note = _served(tmp_path, conn, [(A, 3), (B, 7)])
    other = _served(tmp_path, conn, [(C, 2)])
    sim_clock.advance(conn, days=1)
    issued = sales.invoice(conn, tmp_path / "inv")
    assert [i["note_id"] for i in issued] == [note, other]
    rows = conn.execute("SELECT * FROM customer_invoices ORDER BY id").fetchall()
    assert [r["number"] for r in rows] == ["SINV-0001", "SINV-0002"]
    first = rows[0]
    assert (first["delivery_note_id"], first["customer_code"]) == (note, CUSTOMER)
    assert first["issued_at"] == sim_clock.now(conn).strftime(sim_clock.FORMAT)
    lines = conn.execute("SELECT * FROM customer_invoice_lines WHERE invoice_id = ? ORDER BY line_no", (first["id"],))
    got = [(r["line_no"], r["sku"], r["quantity"], r["unit_price"], r["line_total"]) for r in lines]
    assert got == [
        (1, A, 3, _price(conn, A), round(3 * _price(conn, A), 2)),
        (2, B, 7, _price(conn, B), round(7 * _price(conn, B), 2)),
    ]
    assert first["net_total"] == round(3 * _price(conn, A) + 7 * _price(conn, B), 2)


def test_the_invoice_file_is_one_ascii_text_document(tmp_path):
    conn = _conn(tmp_path)
    note = _served(tmp_path, conn, [(A, 3)])
    sales.invoice(conn, tmp_path / "inv")
    path = Path(conn.execute("SELECT document_file FROM customer_invoices").fetchone()[0])
    assert path == tmp_path / "inv" / "SINV-0001.txt"
    text = path.read_bytes().decode("ascii")
    assert "SINV-0001" in text and f"CDN-{note:04d}" in text and CUSTOMERS[0].name in text
    assert A in text and f"{3 * _price(conn, A):.2f}" in text and "EUR" in text
    assert [p.name for p in path.parent.iterdir()] == ["SINV-0001.txt"]


def test_totals_are_rounded_to_the_cent(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("UPDATE products SET price_eur = 0.333 WHERE sku = ?", (A,))
    conn.execute("UPDATE products SET price_eur = 0.333 WHERE sku = ?", (B,))
    _served(tmp_path, conn, [(A, 1), (B, 1)])
    sales.invoice(conn, tmp_path / "inv")
    invoice = conn.execute("SELECT * FROM customer_invoices").fetchone()
    lines = conn.execute("SELECT line_total FROM customer_invoice_lines").fetchall()
    assert [r[0] for r in lines] == [0.33, 0.33]
    assert invoice["net_total"] == 0.66


def _state(conn, folder):
    rows = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in sales.INVOICE_TABLES}
    files = {p.name: p.read_bytes() for p in Path(folder).glob("*")} if Path(folder).exists() else {}
    return rows, files


def test_a_second_run_issues_nothing_and_changes_no_row_or_file(tmp_path):
    conn = _conn(tmp_path)
    _served(tmp_path, conn, [(A, 3)])
    sales.invoice(conn, tmp_path / "inv")
    before = _state(conn, tmp_path / "inv")
    sim_clock.advance(conn, days=2)
    assert sales.invoice(conn, tmp_path / "inv") == []
    assert _state(conn, tmp_path / "inv") == before


def test_a_delivery_note_added_later_gets_the_next_number(tmp_path):
    conn = _conn(tmp_path)
    _served(tmp_path, conn, [(A, 1)])
    sales.invoice(conn, tmp_path / "inv")
    second = _served(tmp_path, conn, [(B, 1)])
    issued = sales.invoice(conn, tmp_path / "inv")
    assert [(i["note_id"], i["number"]) for i in issued] == [(second, "SINV-0002")]


def test_a_missing_file_is_written_again_without_a_new_invoice(tmp_path):
    conn = _conn(tmp_path)
    _served(tmp_path, conn, [(A, 1)])
    sales.invoice(conn, tmp_path / "inv")
    path = Path(conn.execute("SELECT document_file FROM customer_invoices").fetchone()[0])
    path.unlink()
    assert sales.invoice(conn, tmp_path / "inv") == []
    assert path.exists() and conn.execute("SELECT COUNT(*) FROM customer_invoices").fetchone()[0] == 1


def test_the_module_imports_no_llm_symbol():
    tree = ast.parse(Path(sales.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {node.module or ""} | {a.name for a in node.names}
    assert not any("llm" in n or "anthropic" in n for n in names)
