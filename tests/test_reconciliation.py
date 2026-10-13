"""C3 and C4: supplier document extraction, storage and linking to the purchase order."""

import json

import pytest

from conftest import text_pdf, xlsx_bytes
from purchase_cycle import db, llm, reconciliation

SKU = "GLV-NIT-XS"
LINES = [
    "Invoice INV-77",
    "Order reference PO-0001",
    "Nitrile examination gloves, powder-free, size XS\t10\t4.10\t41.00",
]
ANSWER = {
    "kind": "invoice",
    "number": "INV-77",
    "po_reference": "PO-0001",
    "issue_date": "2026-10-20",
    "currency": "EUR",
    "supplier_text": "Suministros Iberia",
    "lines": [
        {
            "product": "Nitrile examination gloves, powder-free, size XS",
            "quantity": 10,
            "unit_price": 4.1,
            "line_total": 41.0,
        }
    ],
}


def _write(tmp_path, name, content):
    path = tmp_path / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _files(tmp_path):
    return [
        _write(tmp_path, "a.txt", "\n".join(LINES)),
        _write(tmp_path, "b.pdf", text_pdf([LINES])),
        _write(tmp_path, "c.xlsx", xlsx_bytes({"Sheet": [line.split("\t") for line in LINES]})),
    ]


def _client(tmp_path, paths, answers, name="docs.jsonl"):
    """A replay client over hand-written recordings keyed on the exact prompt of each file."""
    file = tmp_path / name
    task = llm.SUPPLIER_DOCUMENT_EXTRACTION
    rows = []
    for path, answer in zip(paths, answers, strict=True):
        if answer is None:
            continue
        text = reconciliation.model_text(reconciliation.document_text(path))
        key = llm.recording_key(llm.build_system_prompt([], task), text, task)
        rows.append({"key": key, "case_id": "t", "sentence": text, "answer": answer})
    file.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return llm.ModelClient("replay", [], recordings_path=file, task=task)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "rec.db")
    db.seed(connection)
    yield connection
    connection.close()


def _lines(conn, document_id):
    rows = conn.execute(
        "SELECT line_no, sku, product_text, quantity, unit_price, line_total FROM document_lines "
        "WHERE document_id = ? ORDER BY line_no",
        (document_id,),
    )
    return [tuple(r) for r in rows]


def test_one_call_and_one_row_per_document_in_the_three_formats_with_the_same_lines(tmp_path, conn):
    paths = _files(tmp_path)
    client = _client(tmp_path, paths, [ANSWER, ANSWER, ANSWER])
    stored = [reconciliation.extract_document(conn, client, path) for path in paths]
    assert client.calls == 3
    assert conn.execute("SELECT COUNT(*) FROM supplier_documents").fetchone()[0] == 3
    first = conn.execute("SELECT * FROM supplier_documents WHERE id = ?", (stored[0]["id"],)).fetchone()
    assert (first["kind"], first["number"], first["po_reference"]) == ("invoice", "INV-77", "PO-0001")
    assert (first["currency"], first["issue_date"], first["status"], first["reason"]) == (
        "EUR",
        "2026-10-20",
        "extracted",
        None,
    )
    assert first["source_file"] == str(paths[0]) and len(first["content_hash"]) == 64
    assert first["created_at"] == conn.execute("SELECT now FROM sim_clock").fetchone()[0]
    expected = [(1, SKU, "Nitrile examination gloves, powder-free, size XS", 10.0, 4.1, 41.0)]
    assert [_lines(conn, doc["id"]) for doc in stored] == [expected] * 3
    assert len({doc["content_hash"] for doc in stored}) == 3


def test_the_model_receives_the_labelled_facts_read_from_each_of_the_three_formats(tmp_path):
    facts = ["Invoice INV-77", "Order reference PO-0001", "Nitrile examination gloves, powder-free, size XS", "10", "4.10", "41.00"]
    for path in _files(tmp_path):
        text = reconciliation.model_text(reconciliation.document_text(path))
        for fact in facts:
            assert fact in text, f"{path.suffix}: {fact!r} is missing from the text the model receives"


def test_a_stored_file_is_not_read_again(tmp_path, conn):
    path = _files(tmp_path)[0]
    client = _client(tmp_path, [path], [ANSWER])
    first = reconciliation.extract_document(conn, client, path)
    again = reconciliation.extract_document(conn, client, path)
    assert client.calls == 1 and again["id"] == first["id"]
    assert conn.execute("SELECT COUNT(*) FROM supplier_documents").fetchone()[0] == 1


def test_the_prompt_holds_the_text_and_not_the_file_name(tmp_path):
    path = _write(tmp_path, "secret-name-INV-999.txt", "\n".join(LINES))
    text = reconciliation.model_text(reconciliation.document_text(path))
    assert "Nitrile examination gloves, powder-free, size XS" in text
    assert "secret-name" not in text and path.name not in text


def test_text_is_cut_at_the_length_cap(tmp_path):
    path = _write(tmp_path, "long.txt", "x" * (reconciliation.MAX_DOCUMENT_TEXT + 500))
    assert len(reconciliation.document_text(path)) == reconciliation.MAX_DOCUMENT_TEXT


@pytest.mark.parametrize(
    "change",
    [
        {"lines": []},
        {
            "lines": [
                {
                    "product": "Nitrile examination gloves, powder-free, size XS",
                    "quantity": 0,
                    "unit_price": 4.1,
                    "line_total": 0.0,
                }
            ]
        },
        {
            "lines": [
                {
                    "product": "Nitrile examination gloves, powder-free, size XS",
                    "quantity": -3,
                    "unit_price": 4.1,
                    "line_total": -12.3,
                }
            ]
        },
        {"currency": "USD"},
        {"kind": "receipt"},
        {"lines": "ten gloves"},
    ],
    ids=["no_lines", "zero_quantity", "negative_quantity", "currency", "unknown_kind", "schema"],
)
def test_a_failing_check_or_schema_stores_extraction_failed_and_a_failure_row(tmp_path, conn, change):
    path = _write(tmp_path, "bad.txt", "\n".join(LINES))
    client = _client(tmp_path, [path], [{**ANSWER, **change}])
    doc = reconciliation.extract_document(conn, client, path)
    assert doc["status"] == "extraction_failed" and doc["reason"]
    assert conn.execute("SELECT COUNT(*) FROM document_lines").fetchone()[0] == 0
    failure = db.get_failure(conn, f"doc-{doc['content_hash'][:12]}")
    assert (failure["status"], failure["step"], failure["channel"], failure["source"]) == (
        "needs_review",
        "document_extraction",
        "reconcile",
        str(path),
    )


def test_a_failed_document_does_not_stop_the_others(tmp_path, conn):
    bad = _write(tmp_path, "bad.txt", "garbage")
    good = _write(tmp_path, "good.txt", "\n".join(LINES))
    client = _client(tmp_path, [bad, good], [{**ANSWER, "lines": "nope"}, ANSWER])
    results = [reconciliation.extract_document(conn, client, p) for p in (bad, good)]
    assert [r["status"] for r in results] == ["extraction_failed", "extracted"]
    assert len(_lines(conn, results[1]["id"])) == 1


def test_a_delivery_note_has_no_prices_and_may_state_no_currency(tmp_path, conn):
    path = _write(
        tmp_path, "dn.txt", "Delivery note DN-5\nPO-0001\nNitrile examination gloves, powder-free, size XS 10"
    )
    note = {
        "kind": "delivery_note",
        "number": "DN-5",
        "po_reference": "PO-0001",
        "issue_date": None,
        "currency": None,
        "supplier_text": None,
        "lines": [{"product": SKU, "quantity": 10, "unit_price": None, "line_total": None}],
    }
    doc = reconciliation.extract_document(conn, _client(tmp_path, [path], [note]), path)
    assert doc["status"] == "extracted" and doc["kind"] == "delivery_note"
    assert _lines(conn, doc["id"]) == [(1, SKU, SKU, 10.0, None, None)]


def test_replay_without_a_recording_raises(tmp_path, conn):
    path = _write(tmp_path, "a.txt", "\n".join(LINES))
    task = llm.SUPPLIER_DOCUMENT_EXTRACTION
    client = llm.ModelClient("replay", [], recordings_path=tmp_path / "none.jsonl", task=task)
    with pytest.raises(llm.MissingRecording):
        reconciliation.extract_document(conn, client, path)
    assert conn.execute("SELECT COUNT(*) FROM supplier_documents").fetchone()[0] == 0


def test_record_mode_stores_the_answer_and_replay_reads_it(tmp_path, conn, monkeypatch):
    path = _write(tmp_path, "a.txt", "\n".join(LINES))
    recordings = tmp_path / "rec.jsonl"
    task = llm.SUPPLIER_DOCUMENT_EXTRACTION
    recorder = llm.ModelClient("record", [], recordings_path=recordings, task=task)
    monkeypatch.setattr(recorder, "_call_model", lambda sentence: (ANSWER, {}))
    first = reconciliation.extract_document(conn, recorder, path)
    assert recorder.save_recordings() == 1
    other = db.connect(tmp_path / "other.db")
    db.seed(other)
    replay = llm.ModelClient("replay", [], recordings_path=recordings, task=task)
    second = reconciliation.extract_document(other, replay, path)
    assert replay.calls == 1 and second["number"] == first["number"] == "INV-77"
    other.close()


def test_the_task_has_its_own_recordings_file():
    task = llm.SUPPLIER_DOCUMENT_EXTRACTION
    assert task.name == "supplier_document_extraction" and task.tool_name == "record_supplier_document"
    assert task.recordings_path.name == "document_extraction.jsonl"


SUPPLIER_NAME = "Suministros Medicos Iberia S.L."


def _order(conn, order_id=1, supplier="SUP-001"):
    conn.execute(
        "INSERT INTO quote_requests (id, sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (?, ?, 10, ?, 'ordered', 't', 't', 't')",
        (order_id, SKU, f"quote-{order_id}"),
    )
    conn.execute(
        "INSERT INTO purchase_orders (id, request_id, supplier_code, sku, quantity, unit_price, currency, total, "
        "expected_delivery, issued_at, status) VALUES (?, ?, ?, ?, 10, 4.1, 'EUR', 41.0, '2026-10-20', 't', 'issued')",
        (order_id, order_id, supplier, SKU),
    )
    conn.commit()


def _extract(tmp_path, conn, name, **change):
    path = _write(tmp_path, name, f"document {name}")
    answer = {**ANSWER, "supplier_text": SUPPLIER_NAME, **change}
    doc = reconciliation.extract_document(conn, _client(tmp_path, [path], [answer], name + ".jsonl"), path)
    return reconciliation.link_document(conn, doc["id"])


def _assert_unlinked(conn, doc, reason):
    assert (doc["status"], doc["reason"], doc["purchase_order_id"]) == ("unlinked", reason, None)
    assert conn.execute("SELECT COUNT(*) FROM reconciliations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM discrepancies").fetchone()[0] == 0


def test_a_known_order_and_the_same_supplier_link_the_document(tmp_path, conn):
    _order(conn)
    doc = _extract(tmp_path, conn, "a.txt")
    assert (doc["status"], doc["purchase_order_id"], doc["supplier_code"], doc["reason"]) == (
        "linked",
        1,
        "SUP-001",
        None,
    )
    row = conn.execute("SELECT status, purchase_order_id FROM supplier_documents WHERE id = ?", (doc["id"],)).fetchone()
    assert tuple(row) == ("linked", 1)


@pytest.mark.parametrize("written", ["PO-0001", "po 0001", "PO0001", "Purchase order PO-0001 of 12 October"])
def test_the_reference_is_found_however_it_is_written(tmp_path, conn, written):
    _order(conn)
    assert _extract(tmp_path, conn, "a.txt", po_reference=written)["purchase_order_id"] == 1


def test_the_supplier_may_be_written_as_name_or_email(tmp_path, conn):
    for order_id, text in enumerate(["Suministros Medicos Iberia", "ofertas@suministros-iberia.example.com", None], 1):
        _order(conn, order_id)
        doc = _extract(tmp_path, conn, f"{order_id}.txt", supplier_text=text, po_reference=f"PO-000{order_id}")
        assert doc["status"] == "linked", text


def test_no_reference_is_unlinked(tmp_path, conn):
    _order(conn)
    for i, reference in enumerate([None, "see our last email", "PO-"]):
        doc = _extract(tmp_path, conn, f"{i}.txt", po_reference=reference)
        _assert_unlinked(conn, doc, "no_reference")


def test_an_unknown_order_number_is_unlinked(tmp_path, conn):
    _order(conn)
    _assert_unlinked(conn, _extract(tmp_path, conn, "a.txt", po_reference="PO-0099"), "unknown_order")


def test_a_different_supplier_is_unlinked(tmp_path, conn):
    _order(conn)
    other = _extract(tmp_path, conn, "a.txt", supplier_text="Material Clinico Levante")
    _assert_unlinked(conn, other, "supplier_mismatch")
    stranger = _extract(tmp_path, conn, "b.txt", supplier_text="Acme Trading Ltd", number="INV-2")
    _assert_unlinked(conn, stranger, "supplier_mismatch")


def test_a_second_invoice_or_note_for_the_same_order_is_a_duplicate(tmp_path, conn):
    _order(conn)
    assert _extract(tmp_path, conn, "a.txt")["status"] == "linked"
    _assert_unlinked(conn, _extract(tmp_path, conn, "b.txt", number="INV-78"), "duplicate")
    note = {"kind": "delivery_note", "currency": None, "number": "DN-1"}
    assert _extract(tmp_path, conn, "c.txt", **note)["status"] == "linked"
    _assert_unlinked(conn, _extract(tmp_path, conn, "d.txt", **{**note, "number": "DN-2"}), "duplicate")
    assert conn.execute("SELECT COUNT(*) FROM supplier_documents WHERE status = 'linked'").fetchone()[0] == 2


def test_a_failed_extraction_is_not_linked(tmp_path, conn):
    _order(conn)
    path = _write(tmp_path, "bad.txt", "garbage")
    doc = reconciliation.extract_document(conn, _client(tmp_path, [path], [{**ANSWER, "lines": []}]), path)
    again = reconciliation.link_document(conn, doc["id"])
    assert (again["status"], again["purchase_order_id"]) == ("extraction_failed", None)
