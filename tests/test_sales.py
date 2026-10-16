"""Phase 11 C3 to C5, C7, C10: customer invoices, signed notes, sales matching, corrections and the sales graph."""

import ast
from pathlib import Path

import pytest

from purchase_cycle import db, llm, receipts, sales, sim_clock
from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.graph import sqlite_checkpointer

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


# ---- increment 5: the signed note form, linking and failures (C4) ----


def _form(note_id, body="Received in full", customer=CUSTOMER, signer="Laura Etxeberria", date="2026-10-14"):
    return (
        f"Signed delivery note\nDelivery note: CDN-{note_id:04d}\nCustomer: {customer}\n"
        f"Signed by: {signer}\nSigning date: {date}\n{body}\n"
    )


def _incidents(*rows):
    return "Incidents:\n" + "\n".join(f"{sku} {kind} {units}" for sku, kind, units in rows)


def _inbox(tmp_path, **files):
    folder = tmp_path / "inbox"
    folder.mkdir(exist_ok=True)
    for name, text in files.items():
        (folder / f"{name}.txt").write_text(text, encoding="utf-8")
    return folder


def _signed(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM signed_delivery_notes ORDER BY id")]


def _incident_rows(conn):
    return [tuple(r) for r in conn.execute("SELECT line_no, sku, type, units FROM signed_note_incidents ORDER BY 1, 2")]


def _failures(conn):
    return [(r["thread_id"], r["channel"], r["step"], r["status"]) for r in conn.execute("SELECT * FROM failures")]


def _scan(conn, folder):
    return sales.link_notes(conn, sales.read_notes(conn, folder))


def _setup(tmp_path, lines=((A, 5), (B, 4))):
    conn = _conn(tmp_path)
    note = _served(tmp_path, conn, list(lines))
    sales.invoice(conn, tmp_path / "inv")
    return conn, note


def test_received_in_full_is_stored_linked_with_no_incidents(tmp_path):
    conn, note = _setup(tmp_path)
    _scan(conn, _inbox(tmp_path, one=_form(note)))
    (row,) = _signed(conn)
    assert (row["delivery_note_id"], row["signer"], row["signing_date"], row["status"], row["reason"]) == (
        note,
        "Laura Etxeberria",
        "2026-10-14",
        "linked",
        None,
    )
    assert row["source_file"].endswith("one.txt") and len(row["content_hash"]) == 64
    assert _incident_rows(conn) == [] and _failures(conn) == []


def test_each_incident_type_is_stored_with_its_units(tmp_path):
    conn, note = _setup(tmp_path)
    body = _incidents((A, "missing", 2), (B, "damaged", 1), (A, "extra", 3))
    _scan(conn, _inbox(tmp_path, one=_form(note, body)))
    assert _incident_rows(conn) == [(1, A, "missing", 2), (2, B, "damaged", 1), (3, A, "extra", 3)]
    assert _signed(conn)[0]["status"] == "linked"


def test_an_extra_sku_outside_the_delivery_note_is_accepted(tmp_path):
    conn, note = _setup(tmp_path)
    _scan(conn, _inbox(tmp_path, one=_form(note, _incidents((C, "extra", 2)))))
    assert _incident_rows(conn) == [(1, C, "extra", 2)] and _failures(conn) == []


def test_a_file_is_stored_once_by_content_hash(tmp_path):
    conn, note = _setup(tmp_path)
    folder = _inbox(tmp_path, one=_form(note))
    (folder / "copy.txt").write_text(_form(note), encoding="utf-8")
    _scan(conn, folder)
    _scan(conn, folder)
    assert len(_signed(conn)) == 1


def test_an_unknown_note_is_unlinked_with_its_reason(tmp_path):
    conn, _ = _setup(tmp_path)
    _scan(conn, _inbox(tmp_path, one=_form(99)))
    (row,) = _signed(conn)
    assert row["delivery_note_id"] is None and row["status"] == "unlinked" and "CDN-0099" in row["reason"]
    assert _failures(conn) == []


def test_a_note_already_signed_is_unlinked(tmp_path):
    conn, note = _setup(tmp_path)
    _scan(conn, _inbox(tmp_path, a=_form(note)))
    _scan(conn, _inbox(tmp_path, b=_form(note, signer="Someone Else")))
    first, second = _signed(conn)
    assert first["status"] == "linked"
    assert (second["status"], second["delivery_note_id"]) == ("unlinked", note)
    assert "already signed" in second["reason"]


def test_a_note_of_another_customer_is_unlinked(tmp_path):
    conn, note = _setup(tmp_path)
    _scan(conn, _inbox(tmp_path, one=_form(note, customer=CUSTOMERS[1].code)))
    (row,) = _signed(conn)
    assert row["status"] == "unlinked" and "another customer" in row["reason"]
    assert _incident_rows(conn) == []


def _parked(tmp_path, text, lines=((A, 5), (B, 4))):
    conn, note = _setup(tmp_path, lines)
    _scan(conn, _inbox(tmp_path, bad=text(note) if callable(text) else text, good=_form(note)))
    return conn


def _one_failure(conn):
    (thread, channel, step, status) = _failures(conn)[0]
    assert thread.startswith("signed-note-") and (channel, step, status) == ("sales", "signed_note", "needs_review")
    assert len(_failures(conn)) == 1
    return conn.execute("SELECT error FROM failures").fetchone()[0]


def test_a_file_out_of_form_is_parked_and_the_others_are_still_read(tmp_path):
    conn = _parked(tmp_path, "I got everything, thanks\n")
    assert _one_failure(conn)
    assert [r["status"] for r in _signed(conn)] == ["linked"]  # the good file


def test_a_form_with_neither_statement_nor_incidents_is_out_of_form(tmp_path):
    conn = _parked(tmp_path, lambda n: _form(n, ""))
    assert _one_failure(conn)


def test_a_bad_date_is_out_of_form(tmp_path):
    conn = _parked(tmp_path, lambda n: _form(n, date="14/10/2026"))
    assert "date" in _one_failure(conn)


def test_an_unknown_sku_is_parked(tmp_path):
    conn = _parked(tmp_path, lambda n: _form(n, _incidents(("NOPE-1", "missing", 1))))
    assert "NOPE-1" in _one_failure(conn)


def test_an_unknown_incident_type_is_parked(tmp_path):
    conn = _parked(tmp_path, lambda n: _form(n, _incidents((A, "lost", 1))))
    assert "lost" in _one_failure(conn)


def test_a_non_whole_or_non_positive_quantity_is_parked(tmp_path):
    for units in ("1.5", "0", "-2", "x"):
        conn = _parked(tmp_path / units.replace(".", "_"), lambda n, u=units: _form(n, _incidents((A, "missing", u))))
        assert _one_failure(conn)


def test_more_missing_or_damaged_than_delivered_is_parked_and_stores_nothing(tmp_path):
    conn = _parked(tmp_path, lambda n: _form(n, _incidents((A, "missing", 4), (A, "damaged", 2))))
    assert "more" in _one_failure(conn)
    assert [r["status"] for r in _signed(conn)] == ["linked"]  # only the good file
    assert _incident_rows(conn) == []


def test_missing_units_of_a_sku_not_delivered_are_parked(tmp_path):
    conn = _parked(tmp_path, lambda n: _form(n, _incidents((C, "missing", 1))))
    assert _one_failure(conn)


def test_a_parked_file_is_not_parked_again_and_changes_no_row(tmp_path):
    conn, note = _setup(tmp_path)
    folder = _inbox(tmp_path, bad="nonsense\n")
    _scan(conn, folder)
    before = [tuple(r) for r in conn.execute("SELECT * FROM failures")]
    _scan(conn, folder)
    assert [tuple(r) for r in conn.execute("SELECT * FROM failures")] == before


def test_a_file_that_is_not_text_is_parked(tmp_path):
    conn, note = _setup(tmp_path)
    folder = _inbox(tmp_path, good=_form(note))
    (folder / "binary.txt").write_bytes(b"\xff\xfe\x00bad")
    _scan(conn, folder)
    assert _one_failure(conn) and len(_signed(conn)) == 1


# ---- increment 6: accepted quantities, sales reconciliation, incident per row (C5) ----


def _recon(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM sales_reconciliations ORDER BY id")]


def _diffs(conn):
    return [
        (r["sku"], r["type"], r["expected"], r["found"], r["unit_amount"], r["amount"], r["incident"])
        for r in conn.execute("SELECT * FROM sales_discrepancies ORDER BY id")
    ]


def _signed_and_reconciled(tmp_path, body, lines=((A, 5), (B, 4))):
    conn, note = _setup(tmp_path, lines)
    _scan(conn, _inbox(tmp_path, one=_form(note, body)))
    sales.reconcile(conn)
    return conn


def test_an_invoice_with_no_signed_note_is_awaiting_and_has_no_rows(tmp_path):
    conn, _ = _setup(tmp_path)
    sales.reconcile(conn)
    (row,) = _recon(conn)
    assert (row["invoice_id"], row["signed_note_id"], row["result"]) == (1, None, "awaiting_delivery_note")
    assert _diffs(conn) == []


def test_the_awaiting_row_is_updated_when_the_signed_note_arrives(tmp_path):
    conn, note = _setup(tmp_path)
    sales.reconcile(conn)
    _scan(conn, _inbox(tmp_path, one=_form(note, _incidents((A, "missing", 2)))))
    changed = sales.reconcile(conn)
    (row,) = _recon(conn)
    assert (row["id"], row["signed_note_id"], row["result"]) == (1, 1, "discrepancies")
    assert [c["result"] for c in changed] == ["discrepancies"]


def test_received_in_full_gives_matched(tmp_path):
    conn = _signed_and_reconciled(tmp_path, "Received in full")
    (row,) = _recon(conn)
    assert (row["signed_note_id"], row["result"]) == (1, "matched") and _diffs(conn) == []


def test_missing_units_give_a_quantity_row_with_the_missing_incident(tmp_path):
    conn = _signed_and_reconciled(tmp_path, _incidents((A, "missing", 2)))
    assert _recon(conn)[0]["result"] == "discrepancies"
    assert _diffs(conn) == [(A, "quantity", 3, 5, None, round(2 * _price(conn, A), 4), "missing")]


def test_damaged_units_give_a_quantity_row_with_the_damaged_incident(tmp_path):
    conn = _signed_and_reconciled(tmp_path, _incidents((B, "damaged", 1)))
    assert _diffs(conn) == [(B, "quantity", 3, 4, None, round(1 * _price(conn, B), 4), "damaged")]


def test_extra_units_of_a_delivered_sku_give_a_negative_amount(tmp_path):
    conn = _signed_and_reconciled(tmp_path, _incidents((A, "extra", 3)))
    assert _diffs(conn) == [(A, "quantity", 8, 5, None, round(-3 * _price(conn, A), 4), "extra")]


def test_an_extra_sku_not_on_the_note_is_priced_from_the_catalog(tmp_path):
    conn = _signed_and_reconciled(tmp_path, _incidents((C, "extra", 2)))
    assert _diffs(conn) == [(C, "quantity", 2, 0, None, round(-2 * _price(conn, C), 4), "extra")]


def test_several_incident_types_over_several_skus_give_one_row_each(tmp_path):
    body = _incidents((A, "missing", 1), (B, "damaged", 2), (C, "extra", 1))
    conn = _signed_and_reconciled(tmp_path, body)
    got = _diffs(conn)
    assert [(g[0], g[2], g[3], g[6]) for g in got] == [(A, 4, 5, "missing"), (B, 2, 4, "damaged"), (C, 1, 0, "extra")]
    assert got[1][5] == round(2 * _price(conn, B), 4)


def test_incidents_of_one_sku_net_out_and_the_larger_loss_names_the_row(tmp_path):
    conn = _signed_and_reconciled(tmp_path, _incidents((A, "missing", 1), (A, "damaged", 2), (A, "extra", 1)))
    assert _diffs(conn) == [(A, "quantity", 3, 5, None, round(2 * _price(conn, A), 4), "damaged")]


def test_a_tie_between_missing_and_damaged_is_missing(tmp_path):
    conn = _signed_and_reconciled(tmp_path, _incidents((A, "missing", 1), (A, "damaged", 1)))
    assert _diffs(conn) == [(A, "quantity", 3, 5, None, round(2 * _price(conn, A), 4), "missing")]


def test_incidents_that_cancel_out_give_matched(tmp_path):
    conn = _signed_and_reconciled(tmp_path, _incidents((A, "missing", 1), (A, "extra", 1)))
    assert _recon(conn)[0]["result"] == "matched" and _diffs(conn) == []


def test_all_units_missing_leaves_accepted_zero(tmp_path):
    conn = _signed_and_reconciled(tmp_path, _incidents((B, "missing", 4)))
    assert _diffs(conn) == [(B, "quantity", 0, 4, None, round(4 * _price(conn, B), 4), "missing")]


def test_accepted_quantities_follow_the_rule(tmp_path):
    conn, note = _setup(tmp_path)
    _scan(conn, _inbox(tmp_path, one=_form(note, _incidents((A, "missing", 2), (A, "extra", 1), (C, "extra", 2)))))
    assert sales.accepted(conn, 1) == {A: 4, B: 4, C: 2}


def test_reconcile_twice_changes_nothing_and_unlinked_notes_do_not_reconcile(tmp_path):
    conn, note = _setup(tmp_path)
    _scan(conn, _inbox(tmp_path, one=_form(note + 9, _incidents((A, "missing", 2)))))
    sales.reconcile(conn)
    assert [r["result"] for r in _recon(conn)] == ["awaiting_delivery_note"]
    _scan(conn, _inbox(tmp_path, two=_form(note, _incidents((A, "missing", 2)))))
    sales.reconcile(conn)
    before = (_recon(conn), _diffs(conn))
    assert sales.reconcile(conn) == []
    assert (_recon(conn), _diffs(conn)) == before


def test_each_invoice_gets_its_own_reconciliation(tmp_path):
    conn, note = _setup(tmp_path)
    other = _served(tmp_path, conn, [(C, 2)])
    sales.invoice(conn, tmp_path / "inv")
    _scan(conn, _inbox(tmp_path, one=_form(other, _incidents((C, "damaged", 1)))))
    sales.reconcile(conn)
    assert [(r["invoice_id"], r["result"]) for r in _recon(conn)] == [
        (1, "awaiting_delivery_note"),
        (2, "discrepancies"),
    ]
    assert _diffs(conn) == [(C, "quantity", 1, 2, None, round(_price(conn, C), 4), "damaged")]


# ---- increment 7: correcting documents (C7) ----


def _corrections(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM customer_corrections ORDER BY id")]


def _correction_lines(conn, correction_id):
    return [
        (r["sku"], r["quantity"], r["unit_price"], r["line_total"])
        for r in conn.execute(
            "SELECT * FROM customer_correction_lines WHERE correction_id = ? ORDER BY line_no", (correction_id,)
        )
    ]


def _planned(tmp_path, body, lines=((A, 5), (B, 4))):
    conn = _signed_and_reconciled(tmp_path, body, lines)
    created = sales.plan_corrections(conn)
    return conn, created


def test_missing_and_damaged_units_give_a_credit_note_with_negative_lines(tmp_path):
    conn, created = _planned(tmp_path, _incidents((A, "missing", 2), (B, "damaged", 1)))
    (cn,) = _corrections(conn)
    assert (cn["invoice_id"], cn["number"], cn["kind"], cn["document_file"]) == (1, "CN-0001", "credit_note", None)
    pa, pb = _price(conn, A), _price(conn, B)
    assert _correction_lines(conn, cn["id"]) == [(A, -2, pa, round(-2 * pa, 2)), (B, -1, pb, round(-1 * pb, 2))]
    assert cn["total"] == round(round(-2 * pa, 2) + round(-1 * pb, 2), 2)
    assert [c["number"] for c in created] == ["CN-0001"]


def test_extra_units_give_a_corrective_invoice_with_positive_lines(tmp_path):
    conn, _ = _planned(tmp_path, _incidents((A, "extra", 3), (C, "extra", 2)))
    (ci,) = _corrections(conn)
    assert (ci["number"], ci["kind"]) == ("CI-0001", "corrective_invoice")
    pa, pc = _price(conn, A), _price(conn, C)
    assert _correction_lines(conn, ci["id"]) == [(A, 3, pa, round(3 * pa, 2)), (C, 2, pc, round(2 * pc, 2))]
    assert ci["total"] == round(round(3 * pa, 2) + round(2 * pc, 2), 2)


def test_both_documents_for_an_invoice_with_losses_and_extras(tmp_path):
    conn, _ = _planned(tmp_path, _incidents((A, "damaged", 2), (C, "extra", 1)))
    cn, ci = _corrections(conn)
    assert [(c["number"], c["kind"], c["invoice_id"]) for c in (cn, ci)] == [
        ("CN-0001", "credit_note", 1),
        ("CI-0002", "corrective_invoice", 1),
    ]
    assert _correction_lines(conn, cn["id"]) == [(A, -2, _price(conn, A), round(-2 * _price(conn, A), 2))]
    assert ci["total"] == round(_price(conn, C), 2) and cn["total"] == round(-2 * _price(conn, A), 2)


def test_a_net_zero_or_matched_invoice_gets_no_document(tmp_path):
    conn, created = _planned(tmp_path, _incidents((A, "missing", 1), (A, "extra", 1)))
    assert _corrections(conn) == [] and created == []


def test_an_awaiting_invoice_gets_no_document(tmp_path):
    conn, _ = _setup(tmp_path)
    sales.reconcile(conn)
    assert sales.plan_corrections(conn) == [] and _corrections(conn) == []


def test_planning_twice_changes_nothing_and_writes_no_text_file(tmp_path):
    conn, _ = _planned(tmp_path, _incidents((A, "missing", 2), (C, "extra", 1)))
    before = (_corrections(conn), [tuple(r) for r in conn.execute("SELECT * FROM customer_correction_lines")])
    files = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    assert sales.plan_corrections(conn) == []
    assert (_corrections(conn), [tuple(r) for r in conn.execute("SELECT * FROM customer_correction_lines")]) == before
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == files
    assert not any(n.startswith(("CN-", "CI-")) for n in files)


def test_totals_are_the_sum_of_lines_rounded_to_the_cent(tmp_path):
    conn, note = _setup(tmp_path, ((A, 5), (B, 4)))
    conn.execute("UPDATE customer_invoice_lines SET unit_price = 0.333")
    conn.commit()
    _scan(conn, _inbox(tmp_path, one=_form(note, _incidents((A, "missing", 1), (B, "missing", 1)))))
    sales.reconcile(conn)
    sales.plan_corrections(conn)
    (cn,) = _corrections(conn)
    assert [line[3] for line in _correction_lines(conn, cn["id"])] == [-0.33, -0.33] and cn["total"] == -0.66


def test_numbers_follow_the_row_id_across_invoices(tmp_path):
    conn, note = _setup(tmp_path)
    other = _served(tmp_path, conn, [(C, 2)])
    sales.invoice(conn, tmp_path / "inv")
    _scan(conn, _inbox(tmp_path, one=_form(other, _incidents((C, "damaged", 1)))))
    sales.reconcile(conn)
    sales.plan_corrections(conn)
    _scan(conn, _inbox(tmp_path, two=_form(note, _incidents((A, "extra", 1)))))
    sales.reconcile(conn)
    sales.plan_corrections(conn)
    assert [(c["invoice_id"], c["number"]) for c in _corrections(conn)] == [(2, "CN-0001"), (1, "CI-0002")]


# ---- increment 8: the sales graph, crash and resumption (C10) ----

SALES_TABLES = (
    *sales.INVOICE_TABLES,
    "signed_delivery_notes",
    "signed_note_incidents",
    "sales_reconciliations",
    "sales_discrepancies",
    "customer_corrections",
    "customer_correction_lines",
)


def _sales_dump(conn, folder):
    root = str(Path(folder).parent)  # paths are compared relative to the scenario folder
    rows = {
        t: [
            tuple(v.replace(root, "") if isinstance(v, str) else v for v in r)
            for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")
        ]
        for t in SALES_TABLES
    }
    rows["failures"] = [tuple(r) for r in conn.execute("SELECT thread_id, step, status FROM failures ORDER BY 1")]
    rows["files"] = {p.name: p.read_text(encoding="ascii") for p in sorted(Path(folder).glob("*"))}
    return rows


def _sales_scenario(folder):
    """Three served orders; the first signed with incidents, the second awaiting its note, plus a file out of form."""
    folder.mkdir()
    conn = db.connect(folder / "s.db")
    db.seed(conn)
    first = _served(folder, conn, [(A, 5), (B, 4)])
    _served(folder, conn, [(C, 2)])
    conn.close()
    inbox = folder / "inbox"
    inbox.mkdir()
    (inbox / "a.txt").write_text(_form(first, _incidents((A, "missing", 2), (C, "extra", 1))), encoding="utf-8")
    (inbox / "b.txt").write_text("not a signed note", encoding="utf-8")
    return folder / "s.db", folder / "inv", inbox, sqlite_checkpointer(folder / "checkpoints.sqlite")


def test_one_run_invoices_reads_links_matches_and_plans(tmp_path):
    db_path, inv, inbox, saver = _sales_scenario(tmp_path / "one")
    state = sales.run_sales(db_path, inv, inbox, saver)
    assert [i["number"] for i in state["invoiced"]] == ["SINV-0001", "SINV-0002"]
    assert [r["status"] for r in state["linked"]] == ["linked"]
    assert [(r["invoice_id"], r["result"]) for r in state["reconciled"]] == [
        (1, "discrepancies"),
        (2, "awaiting_delivery_note"),
    ]
    assert [c["number"] for c in state["corrections"]] == ["CN-0001", "CI-0002"]
    conn = db.connect(db_path)
    assert len(_failures(conn)) == 1


def test_a_second_run_changes_no_row_and_writes_no_file(tmp_path):
    db_path, inv, inbox, saver = _sales_scenario(tmp_path / "one")
    sales.run_sales(db_path, inv, inbox, saver)
    conn = db.connect(db_path)
    before = _sales_dump(conn, inv)
    second = sales.run_sales(db_path, inv, inbox, saver)
    assert _sales_dump(conn, inv) == before
    assert second["invoiced"] == second["linked"] == second["reconciled"] == second["corrections"] == []


def test_a_note_arriving_later_is_handled_by_the_next_run(tmp_path):
    db_path, inv, inbox, saver = _sales_scenario(tmp_path / "one")
    sales.run_sales(db_path, inv, inbox, saver)
    (inbox / "c.txt").write_text(_form(2, _incidents((C, "damaged", 1))), encoding="utf-8")
    state = sales.run_sales(db_path, inv, inbox, saver)
    assert [(r["invoice_id"], r["result"]) for r in state["reconciled"]] == [(2, "discrepancies")]
    assert [c["number"] for c in state["corrections"]] == ["CN-0003"]


def test_the_thread_sales_run_nnnn_is_stored_in_the_sqlite_checkpoint(tmp_path):
    db_path, inv, inbox, saver = _sales_scenario(tmp_path / "one")
    sales.run_sales(db_path, inv, inbox, saver)
    sales.run_sales(db_path, inv, inbox, saver)
    assert {c.config["configurable"]["thread_id"] for c in saver.list(None)} == {"sales-run-0001", "sales-run-0002"}


@pytest.mark.parametrize("node", ["invoice", "read", "link", "match"])
def test_a_run_interrupted_after_a_node_and_resumed_ends_like_an_uninterrupted_run(tmp_path, node):
    db_path, inv, inbox, saver = _sales_scenario(tmp_path / "crash")
    sales.run_sales(db_path, inv, inbox, saver, interrupt_after=[node])
    conn = db.connect(db_path)
    assert _sales_dump(conn, inv) != _sales_dump(
        db.connect(_sales_scenario(tmp_path / "ref0")[0]), tmp_path / "ref0" / "inv"
    )
    sales.run_sales(db_path, inv, inbox, saver)
    ref_db, ref_inv, ref_inbox, ref_saver = _sales_scenario(tmp_path / "plain")
    sales.run_sales(ref_db, ref_inv, ref_inbox, ref_saver)
    assert _sales_dump(conn, inv) == _sales_dump(db.connect(ref_db), ref_inv)
    assert {c.config["configurable"]["thread_id"] for c in saver.list(None)} == {"sales-run-0001"}


def test_the_sales_graph_never_reaches_a_model_client(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a model call")

    monkeypatch.setattr(llm.ModelClient, "__init__", boom)
    monkeypatch.setattr(llm.ModelClient, "_call_model", boom)
    db_path, inv, inbox, saver = _sales_scenario(tmp_path / "nomodel")
    assert sales.run_sales(db_path, inv, inbox, saver)["invoiced"]


# ---- increment 23: review findings (idempotent linking, repeated SKUs) ----


def test_linking_the_same_items_again_stores_nothing_new(tmp_path):
    conn, note = _setup(tmp_path)
    items = sales.read_notes(conn, _inbox(tmp_path, one=_form(note, _incidents((A, "missing", 2)))))
    first = sales.link_notes(conn, items)
    before = (_signed(conn), _incident_rows(conn))
    again = sales.link_notes(conn, items)
    assert (_signed(conn), _incident_rows(conn)) == before
    assert again == first


def test_linking_resumes_after_a_crash_between_notes(tmp_path):
    conn, note = _setup(tmp_path)
    other = _served(tmp_path, conn, [(C, 2)])
    sales.invoice(conn, tmp_path / "inv")
    items = sales.read_notes(conn, _inbox(tmp_path, one=_form(note), two=_form(other)))
    assert len(items) == 2
    sales.link_notes(conn, items[:1])  # the process died after the first note committed
    sales.link_notes(conn, items)
    assert [(r["delivery_note_id"], r["status"]) for r in _signed(conn)] == [(note, "linked"), (other, "linked")]


def test_a_sku_on_two_note_lines_is_summed(tmp_path):
    conn, note = _setup(tmp_path, lines=((A, 2), (A, 3)))
    _scan(conn, _inbox(tmp_path, one=_form(note, _incidents((A, "missing", 4)))))
    assert _failures(conn) == [] and len(_signed(conn)) == 1  # 4 of the 5 delivered is not excess
    (signed,) = _signed(conn)
    assert sales.accepted(conn, signed["id"]) == {A: 1}


def test_received_in_full_with_a_repeated_sku_is_matched(tmp_path):
    conn = _signed_and_reconciled(tmp_path, "Received in full", lines=((A, 2), (A, 3)))
    (row,) = _recon(conn)
    assert row["result"] == "matched" and _diffs(conn) == []
