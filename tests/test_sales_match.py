"""Phase 11 C5 and C6: the generalised `classify` with several order lines."""

import itertools

from purchase_cycle import reconcile_match as rm

A = {"sku": "GLV-NIT-XS", "quantity": 10, "unit_price": 4.10}
B = {"sku": "GLV-NIT-M", "quantity": 5, "unit_price": 5.00}
C = {"sku": "MSK-SRG-50", "quantity": 3, "unit_price": 2.00}


def _line(base, quantity=None):
    quantity = base["quantity"] if quantity is None else quantity
    return {
        "sku": base["sku"],
        "quantity": quantity,
        "unit_price": base["unit_price"],
        "line_total": round(quantity * base["unit_price"], 2),
    }


def _note(sku, quantity):
    return {"sku": sku, "quantity": quantity, "unit_price": None, "line_total": None}


def _by_sku(rows):
    return {r["sku"]: r for r in rows}


def test_several_lines_all_accepted_match():
    order = {"lines": [A, B]}
    result = rm.classify(order, [_line(A), _line(B)], [_note(A["sku"], 10), _note(B["sku"], 5)])
    assert result == (rm.MATCHED, [])


def test_one_short_line_among_several_gives_one_quantity_row():
    order = {"lines": [A, B]}
    result, rows = rm.classify(order, [_line(A), _line(B)], [_note(A["sku"], 10), _note(B["sku"], 3)])
    assert result == rm.DISCREPANCIES
    assert len(rows) == 1
    row = rows[0]
    assert (row["sku"], row["type"], row["expected"], row["found"]) == (B["sku"], rm.QUANTITY, 3.0, 5.0)
    assert row["amount"] == 10.0


def test_each_line_is_checked_against_its_own_unit_price():
    order = {"lines": [A, B]}
    invoice = [_line(A), {**_line(B), "unit_price": 5.50}]
    result, rows = rm.classify(order, invoice, [_note(A["sku"], 10), _note(B["sku"], 5)])
    assert result == rm.DISCREPANCIES
    assert [(r["sku"], r["type"], r["expected"], r["found"]) for r in rows] == [(B["sku"], rm.PRICE, 5.0, 5.5)]


def test_short_and_extra_units_on_two_lines_give_a_row_each():
    order = {"lines": [A, B]}
    result, rows = rm.classify(order, [_line(A), _line(B)], [_note(A["sku"], 8), _note(B["sku"], 7)])
    found = _by_sku(rows)
    assert result == rm.DISCREPANCIES
    assert (found[A["sku"]]["expected"], found[A["sku"]]["found"]) == (8.0, 10.0)
    assert found[A["sku"]]["amount"] == 8.2
    assert (found[B["sku"]]["expected"], found[B["sku"]]["found"]) == (7.0, 5.0)
    assert found[B["sku"]]["amount"] == -10.0


def test_an_accepted_sku_with_no_invoice_line_is_a_quantity_row_with_invoiced_zero():
    order = {"lines": [A]}
    note = [_note(A["sku"], 10), _note(C["sku"], 2)]
    result, rows = rm.classify(order, [_line(A)], note)
    assert result == rm.DISCREPANCIES
    assert len(rows) == 1
    row = rows[0]
    assert (row["sku"], row["type"], row["expected"], row["found"]) == (C["sku"], rm.QUANTITY, 2.0, 0.0)


def test_an_accepted_sku_with_no_invoice_line_and_a_known_price_has_a_signed_amount():
    note = [_note(A["sku"], 10), {**_note(C["sku"], 2), "unit_price": 2.0}]
    rows = rm.classify({"lines": [A]}, [_line(A)], note)[1]
    assert rows[0]["amount"] == -4.0


def test_an_ordered_sku_missing_from_the_invoice_is_a_missing_line():
    order = {"lines": [A, B]}
    result, rows = rm.classify(order, [_line(A)], [_note(A["sku"], 10), _note(B["sku"], 5)])
    assert result == rm.DISCREPANCIES
    assert [(r["sku"], r["type"], r["expected"], r["amount"]) for r in rows] == [(B["sku"], rm.MISSING_LINE, 5.0, 25.0)]


def test_an_invoiced_sku_outside_the_order_is_an_extra_line():
    order = {"lines": [A]}
    result, rows = rm.classify(order, [_line(A), _line(C)], [_note(A["sku"], 10)])
    assert result == rm.DISCREPANCIES
    assert [(r["sku"], r["type"]) for r in rows] == [(C["sku"], rm.EXTRA_LINE)]


def test_repeated_order_lines_for_one_sku_add_up():
    order = {"lines": [{**A, "quantity": 4}, {**A, "quantity": 6}]}
    assert rm.classify(order, [_line(A)], [_note(A["sku"], 10)]) == (rm.MATCHED, [])


def test_no_note_returns_awaiting_with_the_order_checks_only():
    order = {"lines": [A, B]}
    assert rm.classify(order, [_line(A), _line(B)], None) == (rm.AWAITING, [])
    result, rows = rm.classify(order, [_line(A), _line(B, 4)], None)
    assert result == rm.AWAITING
    assert [(r["sku"], r["type"]) for r in rows] == [(B["sku"], rm.QUANTITY)]


def test_the_one_line_form_equals_the_wrapped_form_and_ignores_note_only_skus():
    cases = itertools.product(
        [A, {**A, "unit_price": 4.5}],
        [10, 7, 12],
        [10, 7, 0],
        [None, "same"],
    )
    for invoice_base, invoice_qty, note_qty, mode in cases:
        invoice = [_line(invoice_base, invoice_qty)]
        note = None if mode is None else [_note(A["sku"], note_qty)]
        assert rm.classify({"lines": [A]}, invoice, note) == rm.classify(A, invoice, note)
    # a note SKU outside order and invoice is not a row in the one-line form (phase 08 behaviour)
    assert rm.classify(A, [_line(A)], [_note(A["sku"], 10), _note(C["sku"], 2)]) == (rm.MATCHED, [])


def test_the_input_dicts_are_not_modified():
    order = {"lines": [dict(A), dict(A)]}
    snapshot = [dict(line) for line in order["lines"]]
    rm.classify(order, [_line(A)], [_note(A["sku"], 20)])
    assert order["lines"] == snapshot
