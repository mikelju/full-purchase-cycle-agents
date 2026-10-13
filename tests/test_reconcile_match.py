"""C5 and C6: pure three-way matching and classification of one invoice."""

import ast
from pathlib import Path

import pytest

from purchase_cycle import reconcile_match as rm

ORDER = {"sku": "GLV-NIT-XS", "quantity": 10, "unit_price": 4.10}


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
        {"sku": "GLV-NIT-XS", "type": "price", "expected": 4.1, "found": 4.5, "unit_amount": 0.4, "amount": 4.0}
    ]


@pytest.mark.parametrize("price", [4.11, 4.09, 4.10, 4.105])
def test_a_price_at_or_within_the_tolerance_is_not_a_difference(price):
    assert rm.classify(ORDER, [_line(price=price)], [_note()]) == (rm.MATCHED, [])


@pytest.mark.parametrize("price", [4.12, 4.08, 4.1101])
def test_a_price_just_over_the_tolerance_is_a_difference(price):
    assert _summary(rm.classify(ORDER, [_line(price=price)], [_note()])[1]) == [("GLV-NIT-XS", "price")]


@pytest.mark.parametrize("quantity, amount", [(12, 8.2), (8, -8.2)])
def test_quantity_above_and_below_the_order(quantity, amount):
    result, rows = rm.classify(ORDER, [_line(quantity=quantity)], [_note(quantity)])
    assert result == rm.DISCREPANCIES
    assert [(r["type"], r["expected"], r["found"], r["amount"]) for r in rows] == [("quantity", 10.0, quantity, amount)]


def test_quantity_is_checked_against_the_delivery_note():
    result, rows = rm.classify(ORDER, [_line(quantity=10)], [_note(quantity=7)])
    assert result == rm.DISCREPANCIES
    assert [(r["type"], r["expected"], r["found"]) for r in rows] == [("quantity", 7.0, 10.0)]


def test_invoiced_above_delivered_is_a_difference_even_if_it_equals_the_order():
    rows = rm.classify(ORDER, [_line(quantity=10)], [_note(quantity=9)])[1]
    assert [(r["type"], r["expected"]) for r in rows] == [("quantity", 9.0)]


def test_a_note_without_the_sku_makes_no_delivery_check():
    assert rm.classify(ORDER, [_line()], [_note(sku=None)]) == (rm.MATCHED, [])


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
    rows = rm.classify(ORDER, [_line(quantity=12, price=4.5)], [_note(12)])[1]
    assert _summary(rows) == [("GLV-NIT-XS", "price"), ("GLV-NIT-XS", "quantity")]
    assert [r["amount"] for r in rows if r["type"] == "price"] == [4.8]


def test_several_types_in_one_invoice():
    lines = [_line(quantity=9, price=4.5), _line(sku="OTHER", quantity=1, price=1.0)]
    rows = rm.classify(ORDER, lines, [_note(9)])[1]
    assert _summary(rows) == [("GLV-NIT-XS", "price"), ("GLV-NIT-XS", "quantity"), ("OTHER", "extra_line")]


def test_no_note_yet_is_awaiting_and_makes_no_delivery_check():
    assert rm.classify(ORDER, [_line()], None) == (rm.AWAITING, [])
    # The order quantity, the price and the lines are still compared, so the rows are already there.
    result, rows = rm.classify(ORDER, [_line(price=5.0)], None)
    assert result == rm.AWAITING and _summary(rows) == [("GLV-NIT-XS", "price")]


def test_repeated_lines_of_one_sku_add_up_their_quantities():
    assert rm.classify(ORDER, [_line(quantity=4), _line(quantity=6)], [_note()]) == (rm.MATCHED, [])


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
