"""C5 and C6: pure three-way matching and classification of one invoice."""

import ast
from pathlib import Path

import pytest

from purchase_cycle import reconcile_match as rm

ORDER = {"sku": "GLV-NIT-XS", "quantity": 10, "unit_price": 4.10}
ORDER_100 = {**ORDER, "quantity": 100}


def _line(sku="GLV-NIT-XS", quantity=10, price=4.10, total=None):
    return {"sku": sku, "quantity": quantity, "unit_price": price, "line_total": total}


def _note(sku="GLV-NIT-XS", quantity=10):
    return {"sku": sku, "quantity": quantity, "unit_price": None, "line_total": None}


def _summary(rows):
    return sorted(((r["sku"], r["type"]) for r in rows), key=lambda item: (item[0] or "", item[1]))


def test_a_perfect_match_has_no_rows():
    assert rm.classify(ORDER, [_line()], [_note()]) == (rm.MATCHED, [])


def test_price_over_the_tolerance_stores_unit_and_total_amount():
    result, rows = rm.classify(ORDER, [_line(price=4.50)], [_note()])
    assert result == rm.DISCREPANCIES
    assert rows == [
        {
            "sku": "GLV-NIT-XS",
            "type": "price",
            "expected": 4.1,
            "found": 4.5,
            "unit_amount": 0.4,
            "amount": 4.0,
            "text": None,
        }
    ]


@pytest.mark.parametrize("price", [4.11, 4.09, 4.10, 4.105])
def test_a_price_at_or_within_the_tolerance_is_not_a_difference(price):
    assert rm.classify(ORDER, [_line(price=price)], [_note()]) == (rm.MATCHED, [])


@pytest.mark.parametrize("price", [4.12, 4.08, 4.1101])
def test_a_price_just_over_the_tolerance_is_a_difference(price):
    assert _summary(rm.classify(ORDER, [_line(price=price)], [_note()])[1]) == [("GLV-NIT-XS", "price")]


@pytest.mark.parametrize("quantity, amount", [(12, 8.2), (8, -8.2)])
def test_quantity_above_and_below_the_order(quantity, amount):
    result, rows = rm.classify(ORDER, [_line(quantity=quantity)], [_note(quantity=quantity)])
    assert result == rm.DISCREPANCIES
    assert [(r["type"], r["expected"], r["found"], r["amount"]) for r in rows] == [("quantity", 10.0, quantity, amount)]


def test_quantity_is_checked_against_the_delivery_note():
    result, rows = rm.classify(ORDER, [_line(quantity=10)], [_note(quantity=7)])
    assert result == rm.DISCREPANCIES
    assert [(r["type"], r["expected"], r["found"]) for r in rows] == [("quantity", 7.0, 10.0)]


def test_invoiced_above_delivered_is_a_difference_even_if_it_equals_the_order():
    rows = rm.classify(ORDER, [_line(quantity=10)], [_note(quantity=9)])[1]
    assert [(r["type"], r["expected"]) for r in rows] == [("quantity", 9.0)]


def test_a_note_without_a_line_for_the_sku_counts_as_nothing_delivered():
    sku_b = {"sku": "GLV-NIT-M", "quantity": 5, "unit_price": 5.0}
    result, rows = rm.classify(sku_b, [_line(), _line(sku="GLV-NIT-M", quantity=5, price=5.0)], [_note()])
    assert result == rm.DISCREPANCIES
    assert [(r["sku"], r["type"], r["expected"], r["found"], r["amount"]) for r in rows if r["sku"] == "GLV-NIT-M"] == [
        ("GLV-NIT-M", "quantity", 0.0, 5.0, 25.0)
    ]


def test_a_note_with_only_an_unresolved_line_still_reports_the_missing_sku():
    result, rows = rm.classify(ORDER, [_line()], [_note(sku=None)])
    assert result == rm.DISCREPANCIES
    assert [(r["type"], r["expected"], r["found"]) for r in rows] == [("quantity", 0.0, 10.0)]


def test_extra_line_by_sku_and_by_unresolved_text():
    lines = [_line(), _line(sku="GLV-NIT-M", quantity=2, price=5.0), _line(sku=None, quantity=1, price=9.0, total=9.0)]
    result, rows = rm.classify(ORDER, lines, [_note()])
    assert result == rm.DISCREPANCIES
    assert _summary(rows) == [(None, "extra_line"), ("GLV-NIT-M", "extra_line")]
    assert {r["sku"]: r["amount"] for r in rows} == {"GLV-NIT-M": 10.0, None: 9.0}


def test_missing_line():
    result, rows = rm.classify(ORDER, [_line(sku="GLV-NIT-M")], [_note()])
    assert result == rm.DISCREPANCIES
    assert _summary(rows) == [("GLV-NIT-M", "extra_line"), ("GLV-NIT-XS", "missing_line")]
    missing = [r for r in rows if r["type"] == "missing_line"][0]
    assert (missing["expected"], missing["found"], missing["amount"]) == (10.0, None, 41.0)


def test_price_and_quantity_on_one_line_give_two_rows():
    rows = rm.classify(ORDER, [_line(quantity=12, price=4.5)], [_note(quantity=12)])[1]
    assert _summary(rows) == [("GLV-NIT-XS", "price"), ("GLV-NIT-XS", "quantity")]
    assert [r["amount"] for r in rows if r["type"] == "price"] == [4.8]


def test_several_types_in_one_invoice():
    lines = [_line(quantity=9, price=4.5), _line(sku="OTHER", quantity=1, price=1.0)]
    rows = rm.classify(ORDER, lines, [_note(quantity=9)])[1]
    assert _summary(rows) == [("GLV-NIT-XS", "price"), ("GLV-NIT-XS", "quantity"), ("OTHER", "extra_line")]


def test_no_note_yet_is_awaiting_and_makes_no_delivery_check():
    assert rm.classify(ORDER, [_line()], None) == (rm.AWAITING, [])
    # The order quantity, the price and the lines are still compared, so the rows are already there.
    result, rows = rm.classify(ORDER, [_line(price=5.0)], None)
    assert result == rm.AWAITING and _summary(rows) == [("GLV-NIT-XS", "price")]


def test_repeated_lines_of_one_sku_add_up_their_quantities():
    assert rm.classify(ORDER, [_line(quantity=4), _line(quantity=6)], [_note()]) == (rm.MATCHED, [])


def test_repeated_lines_at_different_prices_are_a_price_difference():
    lines = [_line(quantity=50, price=4.10), _line(quantity=50, price=4.60)]
    result, rows = rm.classify(ORDER_100, lines, [_note(quantity=100)])
    assert result == rm.DISCREPANCIES
    assert [(r["type"], r["found"], r["unit_amount"], r["amount"]) for r in rows] == [("price", 4.6, 0.5, 25.0)]


def test_repeated_lines_with_the_same_price_and_one_row_per_sku_and_type():
    lines = [_line(quantity=50, price=5.10), _line(quantity=50, price=5.10), _line(sku=None, price=1.0, quantity=1)]
    lines.append(_line(sku=None, price=2.0, quantity=1))
    rows = rm.classify(ORDER_100, lines, [_note(quantity=100)])[1]
    assert _summary(rows) == [(None, "extra_line"), (None, "extra_line"), ("GLV-NIT-XS", "price")]
    assert len({(r["sku"], r["type"]) for r in rows if r["sku"] is not None}) == 1


def test_an_invoice_line_without_unit_price_is_a_price_difference_not_a_match():
    result, rows = rm.classify(ORDER, [_line(price=None)], [_note()])
    assert result == rm.DISCREPANCIES
    assert [(r["type"], r["expected"], r["found"], r["amount"]) for r in rows] == [("price", 4.1, None, None)]


def test_a_quantity_that_differs_from_the_order_and_the_note_reports_the_larger_difference():
    rows = rm.classify(ORDER, [_line(quantity=12)], [_note(quantity=9)])[1]
    assert [(r["type"], r["expected"], r["found"], r["amount"]) for r in rows] == [("quantity", 9.0, 12.0, 12.3)]
    rows = rm.classify(ORDER, [_line(quantity=12)], [_note(quantity=11)])[1]
    assert [(r["type"], r["expected"]) for r in rows] == [("quantity", 10.0)]


def test_matching_imports_no_model_client():
    tree = ast.parse(Path(rm.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {node.module or ""} | {alias.name for alias in node.names}
    assert not any("llm" in name or "anthropic" in name for name in imported)
    assert not hasattr(rm, "llm")


def test_opposite_price_deviations_on_repeated_lines_never_net_out():
    lines = [_line(quantity=5, price=4.60), _line(quantity=5, price=3.60)]
    result, rows = rm.classify(ORDER, lines, [_note()])
    assert result == rm.DISCREPANCIES
    assert [(r["type"], r["found"], r["unit_amount"], r["amount"]) for r in rows] == [("price", 4.6, 0.5, 2.5)]


def test_a_smaller_opposite_deviation_does_not_flip_the_sign():
    lines = [_line(quantity=5, price=4.60), _line(quantity=5, price=4.00)]
    rows = rm.classify(ORDER, lines, [_note()])[1]
    assert [(r["found"], r["unit_amount"], r["amount"]) for r in rows] == [(4.6, 0.5, 2.5)]
