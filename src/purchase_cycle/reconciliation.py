"""Phase 08: supplier documents (invoices and delivery notes), their extraction, storage and linking.

The model only reads a document; everything after the extraction is plain code.
"""

import hashlib
import re
import sqlite3
from pathlib import Path

from purchase_cycle import db, email_order, llm, sim_clock, web_form

CHANNEL = "reconcile"
STEP = "document_extraction"
CURRENCY = "EUR"
MAX_DOCUMENT_TEXT = email_order.MAX_EMAIL_TEXT
SUFFIXES = (".txt", ".pdf", ".xlsx")

EXTRACTED = "extracted"
LINKED = "linked"
UNLINKED = "unlinked"
EXTRACTION_FAILED = "extraction_failed"

NO_REFERENCE = "no_reference"
UNKNOWN_ORDER = "unknown_order"
SUPPLIER_MISMATCH = "supplier_mismatch"
DUPLICATE = "duplicate"

DOCUMENT_TEXT = "Supplier document:\n{text}"
PO_REFERENCE = re.compile(r"\bPO\s*-?\s*0*(\d+)\b", re.IGNORECASE)


class DocumentRejected(ValueError):
    """The document cannot be read or fails a deterministic check; the message names the reason."""


def content_hash(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def thread_id(digest: str) -> str:
    return f"doc-{digest[:12]}"


def document_text(path: Path | str) -> str:
    """The text of a .txt, text .pdf or .xlsx file, cut at the length cap; the file name is never part of it."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUFFIXES:
        raise DocumentRejected(f"the file type '{suffix}' is not supported")
    data = path.read_bytes()
    try:
        if suffix == ".txt":
            text = data.decode("utf-8")
        elif suffix == ".pdf":
            text = email_order.pdf_text(data)
        else:
            text = email_order.xlsx_text(data)
    except Exception as error:  # pypdf and openpyxl raise many types for a broken file
        raise DocumentRejected(f"the file cannot be read: {error}") from error
    return text[:MAX_DOCUMENT_TEXT]


def model_text(text: str) -> str:
    return DOCUMENT_TEXT.format(text=text)


def check_answer(fields: dict) -> None:
    """The deterministic checks after extraction; the kind is already limited by the answer schema."""
    if fields["kind"] not in ("invoice", "delivery_note"):
        raise DocumentRejected(f"unknown document kind '{fields['kind']}'")
    if not fields["lines"]:
        raise DocumentRejected("the document has no lines")
    if any(line["quantity"] <= 0 for line in fields["lines"]):
        raise DocumentRejected("a line has a quantity that is not a positive number")
    currency = fields["currency"]
    if currency is None and fields["kind"] == "invoice":
        raise DocumentRejected("the invoice states no currency")
    if currency is not None and currency.strip().upper() != CURRENCY:
        raise DocumentRejected(f"the currency is {currency}, not {CURRENCY}")


def _now(conn: sqlite3.Connection) -> str:
    return sim_clock.now(conn).strftime(sim_clock.FORMAT)


def stored_document(conn: sqlite3.Connection, digest: str) -> dict | None:
    row = conn.execute("SELECT * FROM supplier_documents WHERE content_hash = ?", (digest,)).fetchone()
    return dict(row) if row else None


def _store_failed(conn: sqlite3.Connection, path: Path, digest: str, reason: str) -> dict:
    # Park first: a crash between the two writes then repeats the extraction and loses no failure row.
    db.park_failure(conn, thread_id(digest), CHANNEL, str(path), STEP, reason)
    with conn:
        conn.execute(
            "INSERT INTO supplier_documents (source_file, content_hash, status, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(path), digest, EXTRACTION_FAILED, reason, _now(conn)),
        )
    return stored_document(conn, digest)


def _store(conn: sqlite3.Connection, path: Path, digest: str, fields: dict) -> dict:
    index = web_form.catalog_index(db.catalog_rows(conn))
    with conn:
        cursor = conn.execute(
            "INSERT INTO supplier_documents (kind, number, po_reference, supplier_text, issue_date, currency, "
            "source_file, content_hash, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fields["kind"],
                fields["number"],
                fields["po_reference"],
                fields["supplier_text"],
                fields["issue_date"],
                fields["currency"],
                str(path),
                digest,
                EXTRACTED,
                _now(conn),
            ),
        )
        for number, line in enumerate(fields["lines"], start=1):
            conn.execute(
                "INSERT INTO document_lines (document_id, line_no, sku, product_text, quantity, unit_price, "
                "line_total) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    cursor.lastrowid,
                    number,
                    index.get(web_form.match_key(line["product"])),
                    line["product"],
                    line["quantity"],
                    line["unit_price"],
                    line["line_total"],
                ),
            )
    return stored_document(conn, digest)


def extract_document(conn: sqlite3.Connection, client: llm.ModelClient, path: Path | str) -> dict:
    """Store one `supplier_documents` row (and its lines) for a file and return it; a stored hash is not read again.

    An unreadable file, an answer that breaks the schema and a failed check store `extraction_failed`.
    A transient model error propagates before any row is written, for the node retry policy to repeat the call.
    """
    path = Path(path)
    digest = content_hash(path)
    stored = stored_document(conn, digest)
    if stored is not None:
        return stored
    try:
        text = document_text(path)
        fields = client.extract(model_text(text), case_id=thread_id(digest)).model_dump()
        check_answer(fields)
    except (DocumentRejected, llm.InvalidModelOutput) as error:
        return _store_failed(conn, path, digest, f"{type(error).__name__}: {error}")
    return _store(conn, path, digest, fields)


def order_id_from_reference(reference: str | None) -> int | None:
    found = PO_REFERENCE.search(reference or "")
    return int(found.group(1)) if found else None


def resolve_supplier(conn: sqlite3.Connection, text: str | None) -> str | None:
    """The supplier code whose name or email the text states, or None when it names none of them."""
    key = web_form.match_key(text or "")
    if not key:
        return None
    for row in conn.execute("SELECT code, name, email FROM suppliers ORDER BY code"):
        name = web_form.match_key(row["name"])
        if row["email"].lower() in (text or "").lower() or key in name or name in key:
            return row["code"]
    return None


def _set_link(conn: sqlite3.Connection, doc_id: int, status: str, reason: str | None, order_id=None, supplier=None):
    with conn:
        conn.execute(
            "UPDATE supplier_documents SET status = ?, reason = ?, purchase_order_id = ?, supplier_code = ? "
            "WHERE id = ?",
            (status, reason, order_id, supplier, doc_id),
        )
    return dict(conn.execute("SELECT * FROM supplier_documents WHERE id = ?", (doc_id,)).fetchone())


def link_document(conn: sqlite3.Connection, doc_id: int) -> dict:
    """Link an extracted document to its purchase order by the written PO-NNNN, or store why it stays unlinked.

    A document with no stated supplier is not a mismatch; one that states a supplier that is not the order's is.
    A second invoice or delivery note for an order that already has a linked one is a duplicate.
    """
    doc = dict(conn.execute("SELECT * FROM supplier_documents WHERE id = ?", (doc_id,)).fetchone())
    if doc["status"] != EXTRACTED:
        return doc
    order_id = order_id_from_reference(doc["po_reference"])
    if order_id is None:
        return _set_link(conn, doc_id, UNLINKED, NO_REFERENCE)
    order = conn.execute("SELECT id, supplier_code FROM purchase_orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        return _set_link(conn, doc_id, UNLINKED, UNKNOWN_ORDER)
    supplier = resolve_supplier(conn, doc["supplier_text"])
    if doc["supplier_text"] and supplier != order["supplier_code"]:
        return _set_link(conn, doc_id, UNLINKED, SUPPLIER_MISMATCH, supplier=supplier)
    twin = conn.execute(
        "SELECT 1 FROM supplier_documents WHERE purchase_order_id = ? AND kind = ? AND status = ? AND id != ?",
        (order["id"], doc["kind"], LINKED, doc_id),
    ).fetchone()
    if twin:
        return _set_link(conn, doc_id, UNLINKED, DUPLICATE, supplier=order["supplier_code"])
    return _set_link(conn, doc_id, LINKED, None, order["id"], order["supplier_code"])
