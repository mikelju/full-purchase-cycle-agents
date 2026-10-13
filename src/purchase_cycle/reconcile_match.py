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
            merged[key]["parts"].append((line["quantity"], line["unit_price"]))
            if merged[key]["unit_price"] is None:
                merged[key]["unit_price"] = line["unit_price"]
        else:
            merged[key] = {
                "sku": line["sku"],
                "text": line.get("product_text"),
                "quantity": line["quantity"],
                "unit_price": line["unit_price"],
                "line_total": line.get("line_total"),
                "parts": [(line["quantity"], line["unit_price"])],
            }
    return merged


def _row(sku, kind, expected, found, unit_amount, amount, text=None) -> dict:
    return {
        "sku": sku,
        "type": kind,
        "expected": _money(expected),
        "found": _money(found),
        "unit_amount": _money(unit_amount),
        "amount": _money(amount),
        "text": text,
    }


def classify(order: dict, invoice_lines: list[dict], note_lines: list[dict] | None) -> tuple[str, list[dict]]:
    """The result and the discrepancy rows of an invoice.

    order: sku, quantity, unit_price. Lines: sku (None when unresolved), quantity, unit_price, line_total.
    note_lines is None while no delivery note exists: the result is then `awaiting_delivery_note`,
    the price, quantity-against-order and line rows are already returned, and the delivery check is not made.
    A note that exists but has no line for the order SKU counts as delivered quantity 0.
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
            rows.append(_row(sku, EXTRA_LINE, None, line["quantity"], line["unit_price"], total, line["text"]))
            continue
        # Every repeated line is checked on its own; the row holds the lines outside the tolerance.
        off = [
            (quantity, price)
            for quantity, price in line["parts"]
            if price is None or round(abs(price - order["unit_price"]), 6) > PRICE_TOLERANCE
        ]
        if off:
            if any(price is None for _, price in off):
                rows.append(_row(sku, PRICE, order["unit_price"], None, None, None))  # no price stated: not a match
            else:
                # Opposite deviations never net out: the row follows the largest deviation and its sign.
                devs = [(price - order["unit_price"], quantity, price) for quantity, price in off]
                unit, _, found = max(devs, key=lambda d: abs(d[0]))
                amount = sum(dev * quantity for dev, quantity, _ in devs if (dev > 0) == (unit > 0))
                rows.append(_row(sku, PRICE, order["unit_price"], found, unit, amount))
        # When the invoice differs from both the order and the note, the larger difference is the one stored
        # (one quantity row per SKU, as the discrepancies key allows).
        references = [order["quantity"]]
        if note_lines is not None:
            # A note without a line for the SKU counts as nothing delivered: the user checks before paying.
            references.append(delivered[sku]["quantity"] if sku in delivered else 0)
        off_by = [ref for ref in references if abs(line["quantity"] - ref) > QUANTITY_TOLERANCE]
        expected = max(off_by, key=lambda ref: abs(line["quantity"] - ref)) if off_by else None
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
