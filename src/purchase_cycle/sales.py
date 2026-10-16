"""Phase 11: customer invoicing, signed delivery notes and sales matching.

Deterministic: no model is called here.
"""

import datetime
import hashlib
import os
import re
import sqlite3
from pathlib import Path

from purchase_cycle import db, reconcile_match, sim_clock
from purchase_cycle.receipts import COMPANY, ascii_text

INVOICE_TABLES = ("customer_invoices", "customer_invoice_lines")


def _atomic_write(path: Path, text: str) -> None:
    """Write an ASCII file whole under its real name or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(text, encoding="ascii", newline="\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _number(prefix: str, row_id: int) -> str:
    return f"{prefix}-{row_id:04d}"


def _invoice_text(conn: sqlite3.Connection, invoice: sqlite3.Row) -> str:
    customer = conn.execute("SELECT * FROM customers WHERE code = ?", (invoice["customer_code"],)).fetchone()
    lines = conn.execute(
        "SELECT l.sku, p.name, l.quantity, p.sale_unit, l.unit_price, l.line_total FROM customer_invoice_lines l "
        "JOIN products p ON p.sku = l.sku WHERE l.invoice_id = ? ORDER BY l.line_no",
        (invoice["id"],),
    ).fetchall()
    out = [
        COMPANY,
        f"Invoice {invoice['number']}",
        f"Date: {invoice['issued_at'][:10]}",
        f"Delivery note: {_number('CDN', invoice['delivery_note_id'])}",
        f"Order: {_number('ORD', invoice['order_id'])}",
        f"Customer: {customer['name']} ({customer['code']}), {customer['city']}",
        "",
        *(
            f"{r['sku']}  {r['name']}  {r['quantity']} x {r['sale_unit']}  {r['unit_price']:.2f} EUR  "
            f"{r['line_total']:.2f} EUR"
            for r in lines
        ),
        "",
        f"Net total: {invoice['net_total']:.2f} EUR",
    ]
    return ascii_text("\n".join(out)) + "\n"


def _issue_one(conn: sqlite3.Connection, note: sqlite3.Row, invoices_dir: Path | str) -> dict:
    lines = conn.execute(
        "SELECT l.sku, l.quantity, p.price_eur FROM customer_delivery_note_lines l JOIN products p ON p.sku = l.sku "
        "WHERE l.note_id = ? ORDER BY l.line_no",
        (note["id"],),
    ).fetchall()
    priced = [(r["sku"], r["quantity"], r["price_eur"], round(r["quantity"] * r["price_eur"], 2)) for r in lines]
    with conn:
        invoice_id = conn.execute(
            "INSERT INTO customer_invoices (number, delivery_note_id, order_id, customer_code, issued_at, net_total, "
            "document_file) VALUES (?, ?, ?, ?, ?, ?, '')",
            (
                f"pending-{note['id']}",
                note["id"],
                note["order_id"],
                note["customer_code"],
                sim_clock.now(conn).strftime(sim_clock.FORMAT),
                round(sum(p[3] for p in priced), 2),
            ),
        ).lastrowid
        number = _number("SINV", invoice_id)
        conn.execute(
            "UPDATE customer_invoices SET number = ?, document_file = ? WHERE id = ?",
            (number, str(Path(invoices_dir) / f"{number}.txt"), invoice_id),
        )
        conn.executemany(
            "INSERT INTO customer_invoice_lines VALUES (?, ?, ?, ?, ?, ?)",
            [(invoice_id, n, *p) for n, p in enumerate(priced, 1)],
        )
    return {"invoice_id": invoice_id, "number": number, "note_id": note["id"]}


def write_invoice_files(conn: sqlite3.Connection) -> list[Path]:
    """Write the file of every invoice whose file is missing; existing files are left alone."""
    written = []
    for invoice in conn.execute("SELECT * FROM customer_invoices ORDER BY id").fetchall():
        path = Path(invoice["document_file"])
        if not path.exists():
            _atomic_write(path, _invoice_text(conn, invoice))
            written.append(path)
    return written


def invoice(conn: sqlite3.Connection, invoices_dir: Path | str) -> list[dict]:
    """Issue one invoice per customer delivery note with none (one transaction each), then write the missing files."""
    notes = conn.execute(
        "SELECT * FROM customer_delivery_notes n WHERE NOT EXISTS "
        "(SELECT 1 FROM customer_invoices i WHERE i.delivery_note_id = n.id) ORDER BY n.id"
    ).fetchall()
    issued = [_issue_one(conn, note, invoices_dir) for note in notes]
    write_invoice_files(conn)
    return issued


# ---- signed delivery notes: the fixed form, reading and linking (C4) ----

FORM_TITLE = "Signed delivery note"
INCIDENT_TYPES = ("missing", "extra", "damaged")
_NOTE_RE = re.compile(r"CDN-(\d{4,})")


class FormError(ValueError):
    """The file does not follow the signed note form."""


def _field(lines: list[str], index: int, label: str) -> str:
    prefix = f"{label}:"
    if index >= len(lines) or not lines[index].startswith(prefix) or not lines[index][len(prefix) :].strip():
        raise FormError(f"expected the line '{label}: ...'")
    return lines[index][len(prefix) :].strip()


def _parse_form(conn: sqlite3.Connection, text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != FORM_TITLE:
        raise FormError(f"the first line must be '{FORM_TITLE}'")
    number = _field(lines, 1, "Delivery note")
    match = _NOTE_RE.fullmatch(number)
    if not match:
        raise FormError(f"delivery note '{number}' is not of the form CDN-NNNN")
    customer = _field(lines, 2, "Customer")
    signer = _field(lines, 3, "Signed by")
    date = _field(lines, 4, "Signing date")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ValueError
        datetime.date.fromisoformat(date)
    except ValueError:
        raise FormError(f"the signing date '{date}' is not a valid YYYY-MM-DD date") from None
    body = lines[5:]
    incidents: list[tuple[str, str, int]] = []
    if body == ["Received in full"]:
        pass
    elif body and body[0] == "Incidents:" and len(body) > 1:
        for line in body[1:]:
            parts = line.split()
            if len(parts) != 3:
                raise FormError(f"incident line '{line}' is not 'SKU type units'")
            sku, kind, units = parts
            if kind not in INCIDENT_TYPES:
                raise FormError(f"incident type '{kind}' is not one of {', '.join(INCIDENT_TYPES)}")
            if not conn.execute("SELECT 1 FROM products WHERE sku = ?", (sku,)).fetchone():
                raise FormError(f"SKU {sku} is not in the catalog")
            if not re.fullmatch(r"\d+", units) or int(units) <= 0:
                raise FormError(f"units '{units}' is not a whole positive number")
            incidents.append((sku, kind, int(units)))
    else:
        raise FormError("the body must be 'Received in full' or 'Incidents:' followed by incident lines")
    return {
        "note_id": int(match.group(1)),
        "number": number,
        "customer": customer,
        "signer": signer,
        "date": date,
        "incidents": incidents,
    }


def _park(conn: sqlite3.Connection, file_hash: str, path: Path, error: str) -> None:
    thread = f"signed-note-{file_hash[:12]}"
    if not db.get_failure(conn, thread):
        db.park_failure(conn, thread, "sales", str(path), "signed_note", error)


def read_notes(conn: sqlite3.Connection, folder: Path | str) -> list[dict]:
    """Read the `.txt` files of the folder in name order; return `{file, hash, form}` for each new file that follows the form.

    A file already stored or already parked (by content hash) is skipped; a file out of form is parked once.
    """
    items: list[dict] = []
    seen: set[str] = set()
    for path in sorted(Path(folder).glob("*.txt")):
        raw = path.read_bytes()
        file_hash = hashlib.sha256(raw).hexdigest()
        if file_hash in seen:
            continue
        seen.add(file_hash)
        if conn.execute("SELECT 1 FROM signed_delivery_notes WHERE content_hash = ?", (file_hash,)).fetchone():
            continue
        if db.get_failure(conn, f"signed-note-{file_hash[:12]}"):
            continue
        try:
            form = _parse_form(conn, raw.decode("utf-8"))
        except UnicodeDecodeError:
            _park(conn, file_hash, path, "the file is not UTF-8 text")
        except FormError as error:
            _park(conn, file_hash, path, str(error))
        else:
            items.append({"file": str(path), "hash": file_hash, "form": form})
    return items


def _unlink_reason(conn: sqlite3.Connection, form: dict) -> str | None:
    note = conn.execute("SELECT * FROM customer_delivery_notes WHERE id = ?", (form["note_id"],)).fetchone()
    if note is None:
        return f"unknown delivery note {form['number']}"
    if note["customer_code"] != form["customer"]:
        return f"{form['number']} belongs to another customer than {form['customer']}"
    if conn.execute(
        "SELECT 1 FROM signed_delivery_notes WHERE delivery_note_id = ? AND status = 'linked'", (note["id"],)
    ).fetchone():
        return f"{form['number']} is already signed"
    return None


def _excess(conn: sqlite3.Connection, form: dict) -> str | None:
    delivered = {
        r["sku"]: r["quantity"]
        for r in conn.execute(
            "SELECT sku, quantity FROM customer_delivery_note_lines WHERE note_id = ?", (form["note_id"],)
        )
    }
    lost: dict[str, int] = {}
    for sku, kind, units in form["incidents"]:
        if kind != "extra":
            lost[sku] = lost.get(sku, 0) + units
    for sku, units in lost.items():
        if units > delivered.get(sku, 0):
            return (
                f"{units} units of {sku} reported missing or damaged, more than the {delivered.get(sku, 0)} delivered"
            )
    return None


def link_notes(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """Store each read note as linked or unlinked (with incidents when linked); park the ones reporting more than delivered."""
    results = []
    for item in items:
        form = item["form"]
        reason = _unlink_reason(conn, form)
        if reason is None:
            excess = _excess(conn, form)
            if excess:
                _park(conn, item["hash"], Path(item["file"]), excess)
                results.append({"file": item["file"], "status": "parked", "reason": excess})
                continue
        status = "unlinked" if reason else "linked"
        known = conn.execute("SELECT 1 FROM customer_delivery_notes WHERE id = ?", (form["note_id"],)).fetchone()
        with conn:
            signed_id = conn.execute(
                "INSERT INTO signed_delivery_notes (delivery_note_id, signer, signing_date, source_file, content_hash, "
                "status, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    form["note_id"] if known else None,
                    form["signer"],
                    form["date"],
                    item["file"],
                    item["hash"],
                    status,
                    reason,
                ),
            ).lastrowid
            if status == "linked":
                conn.executemany(
                    "INSERT INTO signed_note_incidents VALUES (?, ?, ?, ?, ?)",
                    [(signed_id, n, *inc) for n, inc in enumerate(form["incidents"], 1)],
                )
        results.append({"file": item["file"], "status": status, "reason": reason, "signed_note_id": signed_id})
    return results


# ---- sales matching: accepted quantities and one reconciliation per invoice (C5) ----


def accepted(conn: sqlite3.Connection, signed_note_id: int) -> dict[str, int]:
    """Units the customer accepts per SKU: delivered minus missing minus damaged plus extra."""
    note_id = conn.execute(
        "SELECT delivery_note_id FROM signed_delivery_notes WHERE id = ?", (signed_note_id,)
    ).fetchone()[0]
    units = {
        r["sku"]: r["quantity"]
        for r in conn.execute("SELECT sku, quantity FROM customer_delivery_note_lines WHERE note_id = ?", (note_id,))
    }
    for r in conn.execute(
        "SELECT sku, type, units FROM signed_note_incidents WHERE signed_note_id = ?", (signed_note_id,)
    ):
        units[r["sku"]] = units.get(r["sku"], 0) + (r["units"] if r["type"] == "extra" else -r["units"])
    return units


def _incident(rows_incidents: dict[str, dict[str, int]], sku: str | None, row: dict) -> str:
    """The incident type behind a difference row: from the net direction, the larger loss type, a tie to missing."""
    reported = rows_incidents.get(sku, {})
    invoiced_above = row["type"] == reconcile_match.QUANTITY and (row["found"] or 0) > (row["expected"] or 0)
    losses = {k: v for k, v in reported.items() if k != "extra"}
    if (invoiced_above or row["type"] != reconcile_match.QUANTITY) and losses:
        return "damaged" if losses.get("damaged", 0) > losses.get("missing", 0) else "missing"
    if reported.get("extra"):
        return "extra"
    return "missing"


def _reconcile_one(
    conn: sqlite3.Connection, invoice: sqlite3.Row, signed: sqlite3.Row | None
) -> tuple[str, list[dict]]:
    if signed is None:
        return reconcile_match.AWAITING, []
    prices = {
        r["sku"]: r["unit_price"]
        for r in conn.execute(
            "SELECT sku, unit_price FROM customer_invoice_lines WHERE invoice_id = ?", (invoice["id"],)
        )
    }

    def price(sku: str) -> float:
        if sku not in prices:
            prices[sku] = conn.execute("SELECT price_eur FROM products WHERE sku = ?", (sku,)).fetchone()[0]
        return prices[sku]

    order = {"lines": []}
    for r in conn.execute(
        "SELECT sku, quantity FROM order_lines WHERE order_id = ? ORDER BY id", (invoice["order_id"],)
    ):
        order["lines"].append({"sku": r["sku"], "quantity": r["quantity"], "unit_price": price(r["sku"])})
    invoice_lines = [
        {"sku": r["sku"], "quantity": r["quantity"], "unit_price": r["unit_price"], "line_total": r["line_total"]}
        for r in conn.execute(
            "SELECT * FROM customer_invoice_lines WHERE invoice_id = ? ORDER BY line_no", (invoice["id"],)
        )
    ]
    note_lines = [
        {"sku": sku, "quantity": quantity, "unit_price": price(sku), "line_total": None}
        for sku, quantity in accepted(conn, signed["id"]).items()
    ]
    result, rows = reconcile_match.classify(order, invoice_lines, note_lines)
    reported: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        "SELECT sku, type, units FROM signed_note_incidents WHERE signed_note_id = ?", (signed["id"],)
    ):
        reported.setdefault(r["sku"], {}).setdefault(r["type"], 0)
        reported[r["sku"]][r["type"]] += r["units"]
    for row in rows:
        row["incident"] = _incident(reported, row["sku"], row)
    return result, rows


def reconcile(conn: sqlite3.Connection) -> list[dict]:
    """One reconciliation per invoice: `awaiting_delivery_note` first, updated once its signed note is linked.

    Returns the reconciliations created or changed by this call; a second call with nothing new returns nothing.
    """
    changed = []
    for invoice in conn.execute("SELECT * FROM customer_invoices ORDER BY id").fetchall():
        signed = conn.execute(
            "SELECT * FROM signed_delivery_notes WHERE delivery_note_id = ? AND status = 'linked' ORDER BY id LIMIT 1",
            (invoice["delivery_note_id"],),
        ).fetchone()
        existing = conn.execute("SELECT * FROM sales_reconciliations WHERE invoice_id = ?", (invoice["id"],)).fetchone()
        if existing is not None and (signed is None or existing["signed_note_id"] is not None):
            continue
        result, rows = _reconcile_one(conn, invoice, signed)
        now = sim_clock.now(conn).strftime(sim_clock.FORMAT)
        signed_id = signed["id"] if signed else None
        with conn:
            if existing is None:
                rec_id = conn.execute(
                    "INSERT INTO sales_reconciliations (invoice_id, signed_note_id, result, reconciled_at) "
                    "VALUES (?, ?, ?, ?)",
                    (invoice["id"], signed_id, result, now),
                ).lastrowid
            else:
                rec_id = existing["id"]
                conn.execute(
                    "UPDATE sales_reconciliations SET signed_note_id = ?, result = ?, reconciled_at = ? WHERE id = ?",
                    (signed_id, result, now, rec_id),
                )
            conn.executemany(
                "INSERT INTO sales_discrepancies (reconciliation_id, sku, type, incident, expected, found, unit_amount, "
                "amount, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        rec_id,
                        r["sku"],
                        r["type"],
                        r["incident"],
                        r["expected"],
                        r["found"],
                        r["unit_amount"],
                        r["amount"],
                        r["text"],
                    )
                    for r in rows
                ],
            )
        changed.append({"reconciliation_id": rec_id, "invoice_id": invoice["id"], "result": result})
    return changed


# ---- correcting documents: computed from the difference rows, no file until approval (C7) ----

CORRECTION_KINDS = (("credit_note", "CN"), ("corrective_invoice", "CI"))


def _unit_price(conn: sqlite3.Connection, invoice_id: int, sku: str) -> float:
    row = conn.execute(
        "SELECT unit_price FROM customer_invoice_lines WHERE invoice_id = ? AND sku = ?", (invoice_id, sku)
    ).fetchone()
    return row[0] if row else conn.execute("SELECT price_eur FROM products WHERE sku = ?", (sku,)).fetchone()[0]


def correction_lines(
    conn: sqlite3.Connection, reconciliation_id: int
) -> dict[str, list[tuple[str, int, float, float]]]:
    """The lines `(sku, quantity, unit price, line total)` of each correcting document of a reconciliation.

    Units invoiced above accepted give negative credit note lines; units accepted above invoiced give positive
    corrective invoice lines. Only quantity rows count; a kind with no line is absent.
    """
    invoice_id = conn.execute(
        "SELECT invoice_id FROM sales_reconciliations WHERE id = ?", (reconciliation_id,)
    ).fetchone()[0]
    out: dict[str, list[tuple[str, int, float, float]]] = {}
    rows = conn.execute(
        "SELECT sku, expected, found FROM sales_discrepancies WHERE reconciliation_id = ? AND type = 'quantity' ORDER BY id",
        (reconciliation_id,),
    ).fetchall()
    for row in rows:
        units = round(row["found"] - row["expected"])
        if units == 0:
            continue
        price = _unit_price(conn, invoice_id, row["sku"])
        kind = "credit_note" if units > 0 else "corrective_invoice"
        signed_units = -units
        out.setdefault(kind, []).append((row["sku"], signed_units, price, round(signed_units * price, 2)))
    return out


def plan_corrections(conn: sqlite3.Connection) -> list[dict]:
    """Store the credit note and corrective invoice of each `discrepancies` reconciliation that has none yet.

    Writes no text file: that happens on approval. A second call with nothing new returns nothing.
    """
    created = []
    recs = conn.execute(
        "SELECT r.id, r.invoice_id FROM sales_reconciliations r WHERE r.result = 'discrepancies' AND NOT EXISTS "
        "(SELECT 1 FROM customer_corrections c WHERE c.invoice_id = r.invoice_id) ORDER BY r.id"
    ).fetchall()
    for rec in recs:
        documents = correction_lines(conn, rec["id"])
        with conn:
            for kind, prefix in CORRECTION_KINDS:
                lines = documents.get(kind)
                if not lines:
                    continue
                correction_id = conn.execute(
                    "INSERT INTO customer_corrections (invoice_id, number, kind, total) VALUES (?, ?, ?, ?)",
                    (
                        rec["invoice_id"],
                        f"pending-{rec['invoice_id']}-{kind}",
                        kind,
                        round(sum(x[3] for x in lines), 2),
                    ),
                ).lastrowid
                number = _number(prefix, correction_id)
                conn.execute("UPDATE customer_corrections SET number = ? WHERE id = ?", (number, correction_id))
                conn.executemany(
                    "INSERT INTO customer_correction_lines VALUES (?, ?, ?, ?, ?, ?)",
                    [(correction_id, n, *line) for n, line in enumerate(lines, 1)],
                )
                created.append(
                    {"correction_id": correction_id, "number": number, "kind": kind, "invoice_id": rec["invoice_id"]}
                )
    return created
