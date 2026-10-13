"""Phase 08: supplier documents (invoices and delivery notes), their extraction, storage and linking.

The model only reads a document; everything after the extraction is plain code.
"""

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from purchase_cycle import db, email_order, llm, reconcile_match, sim_clock, web_form

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

DISCREPANCY_COLUMNS = ("sku", "type", "expected", "found", "unit_amount", "amount", "text")
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


def invoice_reference(invoice_id: int) -> str:
    return f"INV-{invoice_id:04d}"


def _lines(conn: sqlite3.Connection, doc_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT sku, product_text, quantity, unit_price, line_total FROM document_lines WHERE document_id = ? ORDER BY line_no",
        (doc_id,),
    )
    return [dict(row) for row in rows]


def _stored_result(conn: sqlite3.Connection, invoice_id: int) -> tuple[dict | None, list[tuple]]:
    current = conn.execute("SELECT * FROM reconciliations WHERE invoice_id = ?", (invoice_id,)).fetchone()
    if current is None:
        return None, []
    rows = conn.execute(
        "SELECT sku, type, expected, found, unit_amount, amount, text FROM discrepancies WHERE reconciliation_id = ?",
        (current["id"],),
    )
    return dict(current), sorted((tuple(r) for r in rows), key=repr)


def plan_invoice(conn: sqlite3.Connection, invoice_id: int) -> dict | None:
    """The result and rows the matching gives one linked invoice, or None when nothing would change."""
    invoice = conn.execute("SELECT * FROM supplier_documents WHERE id = ?", (invoice_id,)).fetchone()
    if invoice is None or invoice["kind"] != "invoice" or invoice["status"] != LINKED:
        return None
    order = dict(conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (invoice["purchase_order_id"],)).fetchone())
    note = conn.execute(
        "SELECT id FROM supplier_documents WHERE purchase_order_id = ? AND kind = 'delivery_note' AND status = ? "
        "ORDER BY id LIMIT 1",
        (order["id"], LINKED),
    ).fetchone()
    note_id = note["id"] if note else None
    result, found = reconcile_match.classify(order, _lines(conn, invoice_id), _lines(conn, note_id) if note else None)
    new_rows = sorted((tuple(row[c] for c in DISCREPANCY_COLUMNS) for row in found), key=repr)
    current, old_rows = _stored_result(conn, invoice_id)
    if current and (current["result"], current["delivery_note_id"]) == (result, note_id) and old_rows == new_rows:
        return None
    return {
        "invoice_id": invoice_id,
        "order_id": order["id"],
        "note_id": note_id,
        "result": result,
        "rows": [list(row) for row in new_rows],
    }


def store_plan(conn: sqlite3.Connection, plan: dict) -> int:
    """Write a plan in one transaction: the reconciliation of the invoice is inserted or updated, never doubled."""
    with conn:
        current = conn.execute("SELECT id FROM reconciliations WHERE invoice_id = ?", (plan["invoice_id"],)).fetchone()
        if current is None:
            cursor = conn.execute(
                "INSERT INTO reconciliations (invoice_id, purchase_order_id, delivery_note_id, result, reconciled_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (plan["invoice_id"], plan["order_id"], plan["note_id"], plan["result"], _now(conn)),
            )
            reconciliation_id = cursor.lastrowid
        else:
            reconciliation_id = current["id"]
            conn.execute(
                "UPDATE reconciliations SET delivery_note_id = ?, result = ?, reconciled_at = ? WHERE id = ?",
                (plan["note_id"], plan["result"], _now(conn), reconciliation_id),
            )
            conn.execute("DELETE FROM discrepancies WHERE reconciliation_id = ?", (reconciliation_id,))
        for row in plan["rows"]:
            conn.execute(
                "INSERT INTO discrepancies (reconciliation_id, sku, type, expected, found, unit_amount, amount, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (reconciliation_id, *row),
            )
    return reconciliation_id


def reconcile_invoice(conn: sqlite3.Connection, invoice_id: int) -> bool:
    """Match one linked invoice with its order and delivery note; True when a row was written.

    A delivery note that arrives later updates the same row; unchanged inputs write nothing.
    """
    plan = plan_invoice(conn, invoice_id)
    if plan is None:
        return False
    store_plan(conn, plan)
    return True


def reconcile_pending(conn: sqlite3.Connection) -> list[int]:
    """Run the matching for every linked invoice, in id order; return the invoice ids whose result was written."""
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM supplier_documents WHERE kind = 'invoice' AND status = ? ORDER BY id", (LINKED,)
        )
    ]
    return [invoice_id for invoice_id in ids if reconcile_invoice(conn, invoice_id)]


class ReconcileState(TypedDict, total=False):
    path: str
    digest: str
    doc_id: int
    status: str
    plans: list


def _invoices_to_match(conn: sqlite3.Connection, doc: dict) -> list[int]:
    if doc["status"] != LINKED:
        return []
    if doc["kind"] == "invoice":
        return [doc["id"]]
    rows = conn.execute(
        "SELECT id FROM supplier_documents WHERE purchase_order_id = ? AND kind = 'invoice' AND status = ? ORDER BY id",
        (doc["purchase_order_id"], LINKED),
    )
    return [row["id"] for row in rows]


def build_reconcile_graph(client: llm.ModelClient, db_path: Path | str, checkpointer=None):
    """One run per document: read, extract, link, match, store. No pause and no interrupt."""

    def read(state: ReconcileState) -> ReconcileState:
        return {"digest": content_hash(state["path"])}

    def extract(state: ReconcileState) -> ReconcileState:
        conn = db.connect(db_path)
        try:
            doc = extract_document(conn, client, state["path"])
        finally:
            conn.close()
        return {"doc_id": doc["id"], "status": doc["status"]}

    def link(state: ReconcileState) -> ReconcileState:
        conn = db.connect(db_path)
        try:
            doc = link_document(conn, state["doc_id"])
        finally:
            conn.close()
        return {"status": doc["status"]}

    def match(state: ReconcileState) -> ReconcileState:
        conn = db.connect(db_path)
        try:
            doc = dict(conn.execute("SELECT * FROM supplier_documents WHERE id = ?", (state["doc_id"],)).fetchone())
            plans = [plan_invoice(conn, invoice_id) for invoice_id in _invoices_to_match(conn, doc)]
        finally:
            conn.close()
        return {"plans": [plan for plan in plans if plan is not None]}

    def store(state: ReconcileState) -> ReconcileState:
        conn = db.connect(db_path)
        try:
            for plan in state.get("plans", []):
                store_plan(conn, plan)
        finally:
            conn.close()
        return {}

    builder = StateGraph(ReconcileState)
    builder.add_node("read", read)
    builder.add_node("extract", extract, retry_policy=llm.MODEL_RETRY)
    builder.add_node("link", link)
    builder.add_node("match", match)
    builder.add_node("store", store)
    builder.add_edge(START, "read")
    for before, after in (("read", "extract"), ("extract", "link"), ("link", "match"), ("match", "store")):
        builder.add_edge(before, after)
    builder.add_edge("store", END)
    return builder.compile(checkpointer=checkpointer, name="reconcile")


def folder_files(folder: Path | str) -> list[Path]:
    return sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES)


def scan(db_path: Path | str, checkpointer, folder: Path | str, client: llm.ModelClient) -> list[dict]:
    """Process every new document of the folder, in sorted order; a stored content hash is skipped.

    After the folder, the matching runs for every linked invoice, so a document that was stored by an
    interrupted run is still reconciled. Returns one dict per file: its state after this scan.
    """
    graph = build_reconcile_graph(client, db_path, checkpointer)
    results = []
    conn = db.connect(db_path)
    try:
        for path in folder_files(folder):
            digest = content_hash(path)
            new = stored_document(conn, digest) is None
            if new:
                graph.invoke({"path": str(path)}, {"configurable": {"thread_id": f"reconcile-{digest[:12]}"}})
            doc = stored_document(conn, digest)
            results.append({"file": path.name, "new": new, **_summary(doc)})
        reconcile_pending(conn)
        for item in results:
            item["result"] = _result_of(conn, item["id"], item["kind"])
    finally:
        conn.close()
    return results


def _summary(doc: dict) -> dict:
    return {
        "id": doc["id"],
        "kind": doc["kind"],
        "number": doc["number"],
        "status": doc["status"],
        "reason": doc["reason"],
        "order_id": doc["purchase_order_id"],
    }


def _result_of(conn: sqlite3.Connection, doc_id: int, kind: str | None) -> str | None:
    if kind != "invoice":
        return None
    row = conn.execute("SELECT result FROM reconciliations WHERE invoice_id = ?", (doc_id,)).fetchone()
    return row["result"] if row else None


RESULTS = ("matched", "discrepancies", "awaiting_delivery_note")
DISCREPANCY_TYPES = ("price", "quantity", "extra_line", "missing_line")


def order_reference(order_id: int) -> str:
    return f"PO-{order_id:04d}"


def invoice_id_from_reference(text: str) -> int | None:
    found = re.fullmatch(r"INV-(\d+)", text.strip(), re.IGNORECASE)
    return int(found.group(1)) if found else None


def list_results(conn: sqlite3.Connection) -> dict:
    """Every reconciliation in invoice order, the counts by result and the discrepancy counts by type."""
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT r.invoice_id, r.purchase_order_id AS order_id, r.result, o.supplier_code, "
            "(SELECT COUNT(*) FROM discrepancies d WHERE d.reconciliation_id = r.id) AS differences "
            "FROM reconciliations r JOIN purchase_orders o ON o.id = r.purchase_order_id ORDER BY r.invoice_id"
        )
    ]
    by_type = {kind: 0 for kind in DISCREPANCY_TYPES}
    by_type.update(
        {row["type"]: row["n"] for row in conn.execute("SELECT type, COUNT(*) AS n FROM discrepancies GROUP BY type")}
    )
    return {
        "rows": rows,
        "by_result": {result: sum(r["result"] == result for r in rows) for result in RESULTS},
        "by_type": by_type,
    }


def show_result(conn: sqlite3.Connection, invoice_id: int) -> dict | None:
    """The stored reconciliation of one invoice with its order, delivery note and discrepancies, or None."""
    current = conn.execute("SELECT * FROM reconciliations WHERE invoice_id = ?", (invoice_id,)).fetchone()
    if current is None:
        return None
    invoice = conn.execute("SELECT number FROM supplier_documents WHERE id = ?", (invoice_id,)).fetchone()
    note = None
    if current["delivery_note_id"] is not None:
        note = conn.execute(
            "SELECT number FROM supplier_documents WHERE id = ?", (current["delivery_note_id"],)
        ).fetchone()
    order = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (current["purchase_order_id"],)).fetchone()
    rows = conn.execute(
        "SELECT sku, type, expected, found, unit_amount, amount, text FROM discrepancies "
        "WHERE reconciliation_id = ? ORDER BY id",
        (current["id"],),
    )
    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice["number"],
        "result": current["result"],
        "reconciled_at": current["reconciled_at"],
        "order": dict(order),
        "delivery_note": None if note is None else (note["number"] or "-"),
        "discrepancies": [dict(row) for row in rows],
    }
