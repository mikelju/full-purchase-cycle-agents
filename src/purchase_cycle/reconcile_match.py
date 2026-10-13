"""Phase 08: three-way matching of one invoice against its purchase order and at most one delivery note.

Plain deterministic code; it imports no model client, so no model call can happen during matching.
"""

PRICE_TOLERANCE = 0.01  # EUR per unit, absolute
QUANTITY_TOLERANCE = 0

MATCHED = "matched"
DISCREPANCIES = "discrepancies"
AWAITING = "awaiting_delivery_note"

PRICE = "price"
QUANTITY = "quantity"
EXTRA_LINE = "extra_line"
MISSING_LINE = "missing_line"


def _money(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _merge(lines: list[dict]) -> dict[str | None, dict]:
    """One entry per SKU (the quantities of repeated lines add up); unresolved lines each stay apart."""
    merged: dict = {}
    for number, line in enumerate(lines):
        key = line["sku"] if line["sku"] is not None else ("unresolved", number)
        if key in merged:
            merged[key]["quantity"] += line["quantity"]
            if merged[key]["unit_price"] is None:
                merged[key]["unit_price"] = line["unit_price"]
        else:
            merged[key] = {
                "sku": line["sku"],
                "quantity": line["quantity"],
                "unit_price": line["unit_price"],
                "line_total": line.get("line_total"),
            }
    return merged


def _row(sku, kind, expected, found, unit_amount, amount) -> dict:
    return {
        "sku": sku,
        "type": kind,
        "expected": _money(expected),
        "found": _money(found),
        "unit_amount": _money(unit_amount),
        "amount": _money(amount),
    }


def classify(order: dict, invoice_lines: list[dict], note_lines: list[dict] | None) -> tuple[str, list[dict]]:
    """The result and the discrepancy rows of an invoice.

    order: sku, quantity, unit_price. Lines: sku (None when unresolved), quantity, unit_price, line_total.
    note_lines is None while no delivery note exists: the result is then `awaiting_delivery_note`,
    the price, quantity-against-order and line rows are already returned, and the delivery check is not made.
    """
    invoice = _merge(invoice_lines)
    delivered = _merge(note_lines) if note_lines is not None else {}
    rows: list[dict] = []
    for line in invoice.values():
        sku = line["sku"]
        if sku != order["sku"]:
            total = line["line_total"]
            if total is None and line["unit_price"] is not None:
                total = line["unit_price"] * line["quantity"]
            rows.append(_row(sku, EXTRA_LINE, None, line["quantity"], line["unit_price"], total))
            continue
        price = line["unit_price"]
        if price is not None and round(abs(price - order["unit_price"]), 6) > PRICE_TOLERANCE:
            unit = price - order["unit_price"]
            rows.append(_row(sku, PRICE, order["unit_price"], price, unit, unit * line["quantity"]))
        expected = None
        if abs(line["quantity"] - order["quantity"]) > QUANTITY_TOLERANCE:
            expected = order["quantity"]
        elif sku in delivered and abs(line["quantity"] - delivered[sku]["quantity"]) > QUANTITY_TOLERANCE:
            expected = delivered[sku]["quantity"]
        if expected is not None:
            rows.append(
                _row(
                    sku, QUANTITY, expected, line["quantity"], None, (line["quantity"] - expected) * order["unit_price"]
                )
            )
    if order["sku"] not in {line["sku"] for line in invoice.values()}:
        rows.append(
            _row(
                order["sku"],
                MISSING_LINE,
                order["quantity"],
                None,
                order["unit_price"],
                order["quantity"] * order["unit_price"],
            )
        )
    if note_lines is None:
        return AWAITING, rows
    return (DISCREPANCIES if rows else MATCHED), rows
