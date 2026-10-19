"""C2 (phase 12): the purchase_cycle graph and its stages."""

import json
import shutil
import sqlite3

from conftest import make_email
from purchase_cycle import cli, cycle, db, sales_cli
from purchase_cycle.catalog import CUSTOMERS
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import (
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    MATCHING,
    OFFER_EXTRACTION,
    SUPPLIER_DOCUMENT_EXTRACTION,
    WHATSAPP_EXTRACTION,
    WHATSAPP_INTAKE,
)


def _folders(tmp_path):
    return cycle.Folders(
        tmp_path / "orders",
        tmp_path / "replies",
        tmp_path / "documents",
        tmp_path / "signed",
        tmp_path / "outbox",
        tmp_path / "supplier_outbox",
        tmp_path / "notes",
        tmp_path / "invoices",
    )


def test_graph_has_the_stages_in_order_with_the_sqlite_checkpointer(tmp_path):
    checkpointer = sqlite_checkpointer(tmp_path / "cp.sqlite")
    graph = cycle.build_cycle_graph(tmp_path / "b.db", checkpointer, _folders(tmp_path), lambda task, catalog: None)
    nodes = [n for n in graph.get_graph().nodes if not n.startswith("__")]
    assert nodes == ["orders", "quotes", "reconcile", "receipts", "sales"]
    assert graph.checkpointer is checkpointer
    checkpointer.conn.close()


def test_a_pass_over_empty_folders_counts_nothing_and_the_next_pass_is_a_new_run(tmp_path):
    class NoCalls:
        def extract(self, *args, **kwargs):
            raise AssertionError("no model call expected")

    def client(task, catalog):
        return NoCalls()

    args = (tmp_path / "b.db", tmp_path / "cp.sqlite", _folders(tmp_path), client)
    first, second = cycle.run_cycle(*args), cycle.run_cycle(*args)
    assert (first["thread_id"], second["thread_id"]) == ("cycle-run-0001", "cycle-run-0002")
    assert list(first["counts"]) == list(cycle.STAGES)


# C2: one pass equals the module commands one by one

CUSTOMER = CUSTOMERS[1]  # CLI-002
PHONE = "34600101201"
WHATSAPP_TEXT = "hi, can u send 4 boxes of nitrile gloves S thx"
EMAIL_LINE = "3 boxes of nitrile gloves L"
ANSWERS = {  # hand-written model answers by task, keyed by a text inside the model input
    MATCHING.name: {"nitrile gloves M": {"sku": "GLV-NIT-M"}, "nitrile gloves XS": {"sku": "GLV-NIT-XS"}},
    EMAIL_INTAKE.name: {"": {"is_order": True, "reason": "The email asks for gloves."}},
    EMAIL_EXTRACTION.name: {
        EMAIL_LINE: {"lines": [{"source": "body", "source_text": EMAIL_LINE, "sku": "GLV-NIT-L", "quantity": 3}]}
    },
    WHATSAPP_INTAKE.name: {"": {"is_order": True, "reason": "The message asks for gloves."}},
    WHATSAPP_EXTRACTION.name: {
        "nitrile gloves S": {
            "lines": [
                {"source": "message", "source_text": "4 boxes of nitrile gloves S", "sku": "GLV-NIT-S", "quantity": 4}
            ]
        }
    },
}


class CountingClient:
    """A test double of the model client: hand-written answers, one shared call counter."""

    def __init__(self, task, calls, answers=None):
        self.task, self.calls_list, self.answers = task, calls, answers or ANSWERS
        self.calls, self.usage = 0, []

    def extract(self, sentence, case_id=None, correction=None):
        self.calls_list.append(self.task.name)
        self.calls += 1
        for key, answer in self.answers[self.task.name].items():
            if key in sentence:
                return self.task.answer.model_validate(answer)
        raise AssertionError(f"no hand-written answer for {self.task.name}: {sentence!r}")

    def save_recordings(self):
        return 0


def _write_world(root):
    """The inputs: web form, email and WhatsApp orders in stock and a web form order short of stock."""
    for name in ("orders", "replies", "documents", "signed"):
        (root / name).mkdir(parents=True)
    form = {
        "submission_id": "WF-1",
        "customer_code": "CLI-001",
        "lines": [{"product": "nitrile gloves M", "quantity": 2}],
    }
    short = {
        "submission_id": "WF-2",
        "customer_code": "CLI-001",
        "lines": [{"product": "nitrile gloves XS", "quantity": 3}],
    }
    (root / "orders" / "a-form.json").write_text(json.dumps(form), encoding="utf-8")
    (root / "orders" / "b-short.json").write_text(json.dumps(short), encoding="utf-8")
    make_email(root / "orders" / "c-email.eml", CUSTOMER.email, body=f"Hello,\nPlease send {EMAIL_LINE}.\nThanks")
    whatsapp = {"message_id": "wamid.CYCLE1", "from": PHONE, "timestamp": "1760000000", "type": "text"}
    whatsapp["text"] = {"body": WHATSAPP_TEXT}
    (root / "orders" / "d-whatsapp.json").write_text(json.dumps(whatsapp), encoding="utf-8")
    conn = db.connect(root / "business.db")
    db.seed(conn)
    conn.execute("UPDATE stock SET on_hand = 0 WHERE sku = 'GLV-NIT-XS'")
    conn.commit()
    conn.close()


WALL_CLOCK_TABLES = ("orders", "clarifications", "failures")  # created_at and updated_at come from the real clock


def _mask(root, text):
    return text.replace(str(root), "<root>").replace(root.as_posix(), "<root>")


def _snapshot(root):
    """Every row of every table and every output file, with the run folder and the wall-clock stamps masked."""
    conn = sqlite3.connect(root / "business.db")
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    rows = {}
    for table in tables:
        names = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
        stamps = {n for n in names if table in WALL_CLOCK_TABLES and n in ("created_at", "updated_at")}
        rows[table] = [
            [
                "<wall-clock>" if n in stamps else _mask(root, v) if isinstance(v, str) else v
                for n, v in zip(names, row, strict=True)
            ]
            for row in conn.execute(f"SELECT * FROM {table}")
        ]
    conn.close()
    files = {}
    for folder in ("outbox", "supplier_outbox", "notes", "invoices"):
        for path in sorted((root / folder).rglob("*")) if (root / folder).is_dir() else []:
            if path.is_file():
                files[f"{folder}/{path.relative_to(root / folder).as_posix()}"] = _mask(
                    root, path.read_text(encoding="utf-8")
                )
    return rows, files


def test_one_pass_gives_the_same_rows_and_files_as_the_module_commands_one_by_one(tmp_path, monkeypatch, capsys):
    base = tmp_path / "base"
    base.mkdir()
    _write_world(base)
    cycle_root, cli_root = tmp_path / "cycle", tmp_path / "cli"
    shutil.copytree(base, cycle_root)
    shutil.copytree(base, cli_root)

    cycle_calls = []
    result = cycle.run_cycle(
        cycle_root / "business.db",
        cycle_root / "cp.sqlite",
        cycle.Folders(
            cycle_root / "orders",
            cycle_root / "replies",
            cycle_root / "documents",
            cycle_root / "signed",
            cycle_root / "outbox",
            cycle_root / "supplier_outbox",
            cycle_root / "notes",
            cycle_root / "invoices",
        ),
        lambda task, catalog: CountingClient(task, cycle_calls),
    )

    cli_calls = []
    factory = lambda mode, catalog, recordings=None, task=None: CountingClient(task, cli_calls)  # noqa: E731
    monkeypatch.setattr(cli, "ModelClient", factory)
    monkeypatch.setattr(sales_cli, "ModelClient", factory)
    r, cp = cli_root, str(cli_root / "cp.sqlite")
    steps = [
        ["route", str(r / "orders"), "--run-id", "c0001", "--checkpoints", cp, "--outbox", str(r / "outbox")],
        ["quotes", "scan", "--checkpoints", cp, "--outbox", str(r / "supplier_outbox")],
        ["quotes", "receive", str(r / "replies"), "--checkpoints", cp],
        ["quotes", "tick", "--checkpoints", cp, "--outbox", str(r / "supplier_outbox")],
        ["quotes", "decide", "--checkpoints", cp],
        ["reconcile", "scan", str(r / "documents"), "--checkpoints", cp],
        ["reconcile", "claims", "--checkpoints", cp],
        ["receipts", "run", "--checkpoints", cp, "--notes-dir", str(r / "notes")],
        ["sales", "scan", str(r / "signed"), "--checkpoints", cp, "--invoices-dir", str(r / "invoices")],
        ["sales", "responses", "--checkpoints", cp],
    ]
    for step in steps:
        assert cli.main(["--db", str(r / "business.db"), *step]) == 0, step
    capsys.readouterr()

    cycle_rows, cycle_files = _snapshot(cycle_root)
    cli_rows, cli_files = _snapshot(cli_root)
    assert len(cycle_rows["orders"]) == 4 and cycle_rows["quote_requests"]
    assert cycle_files
    assert cycle_calls and cycle_calls == cli_calls
    assert cycle_rows == cli_rows
    assert cycle_files == cli_files
    assert result["counts"]["orders"]["stored"] == 4


# C3: the hand-off through the shared database, one order id followed across passes

SHORT_SKU = "GLV-NIT-XS"
SUPPLIER = "SUP-002"
REPLY_TEXT = "We can supply 3 boxes at 4.10 EUR per box, delivery in 5 working days."
HANDOFF_ANSWERS = {
    MATCHING.name: {"nitrile gloves XS": {"sku": SHORT_SKU}},
    OFFER_EXTRACTION.name: {
        REPLY_TEXT: {
            "offers": True,
            "quantity": 3,
            "unit_price": 4.1,
            "currency": "EUR",
            "vat_included": False,
            "vat_rate": None,
            "lead_time_days": 5,
            "working_days": True,
            "price_text": "4.10 EUR per box",
        }
    },
}


def _document_answer(kind, number, supplier_name, unit_price):
    return {
        "kind": kind,
        "number": number,
        "po_reference": "PO-0001",
        "issue_date": "2026-10-20",
        "currency": "EUR" if kind == "invoice" else None,
        "supplier_text": supplier_name,
        "lines": [
            {
                "product": "Nitrile examination gloves, powder-free, size XS",
                "quantity": 3,
                "unit_price": unit_price,
                "line_total": 12.3 if unit_price else None,
            }
        ],
    }


def _rows(root, sql):
    conn = sqlite3.connect(root / "business.db")
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def test_an_order_short_of_stock_goes_from_quote_request_to_customer_invoice_through_the_tables(tmp_path):
    root = tmp_path
    for name in ("orders", "replies", "documents", "signed"):
        (root / name).mkdir()
    conn = db.connect(root / "business.db")
    db.seed(conn)
    conn.execute("UPDATE stock SET on_hand = 0 WHERE sku = ?", (SHORT_SKU,))
    supplier_name, supplier_email = conn.execute(
        "SELECT name, email FROM suppliers WHERE code = ?", (SUPPLIER,)
    ).fetchone()
    conn.commit()
    conn.close()
    form = {
        "submission_id": "WF-9",
        "customer_code": "CLI-001",
        "lines": [{"product": "nitrile gloves XS", "quantity": 3}],
    }
    (root / "orders" / "short.json").write_text(json.dumps(form), encoding="utf-8")

    calls = []
    answers = {**HANDOFF_ANSWERS, SUPPLIER_DOCUMENT_EXTRACTION.name: {}}
    folders = _folders(root)

    def one_pass():
        return cycle.run_cycle(
            root / "business.db",
            root / "cp.sqlite",
            folders,
            lambda task, catalog: CountingClient(task, calls, answers),
        )

    cp = str(root / "cp.sqlite")
    cli_base = ["--db", str(root / "business.db")]

    # pass 1: the order is stored and, being short of stock, gets a quote request
    one_pass()
    order_id, status = _rows(root, "SELECT id, status FROM orders")[0]
    assert status == "received"
    assert _rows(root, "SELECT sku, quantity, status FROM quote_requests") == [(SHORT_SKU, 3, "waiting")]
    assert _rows(root, "SELECT COUNT(*) FROM purchase_orders") == [(0,)]

    # the supplier replies and time passes: the next pass stores the offer and waits for the approval
    (root / "replies" / "QR-0001-SUP-002.eml").write_text(
        f"From: {supplier_email}\nTo: purchasing@distributor.example.com\n"
        f"Subject: Re: Quote request QR-0001 - {SHORT_SKU}\n\n{REPLY_TEXT}\n",
        encoding="utf-8",
    )
    assert cli.main([*cli_base, "clock", "advance", "--days", "6"]) == 0
    one_pass()
    assert _rows(root, "SELECT supplier_code, usable FROM quote_offers WHERE offers = 1") == [(SUPPLIER, 1)]
    assert _rows(root, "SELECT status FROM quote_requests") == [("awaiting_approval",)]
    assert _rows(root, "SELECT COUNT(*) FROM purchase_orders") == [(0,)]

    # a person approves the offer; the purchase order exists before any further pass
    assert cli.main([*cli_base, "quotes", "approve", "QR-0001", "--checkpoints", cp, "--outbox", str(root / "so")]) == 0
    assert _rows(root, "SELECT request_id, supplier_code, sku, quantity, unit_price FROM purchase_orders") == [
        (1, SUPPLIER, SHORT_SKU, 3, 4.1)
    ]
    assert _rows(root, "SELECT status FROM quote_requests") == [("ordered",)]
    assert _rows(root, f"SELECT on_hand FROM stock WHERE sku = '{SHORT_SKU}'") == [(0,)]

    # the supplier sends the invoice and the delivery note: one pass reconciles, receives, serves and invoices
    (root / "documents" / "a-invoice.txt").write_text("INVOICE F-1\nPO-0001 gloves XS 3 boxes", encoding="utf-8")
    (root / "documents" / "b-note.txt").write_text("DELIVERY NOTE A-1\nPO-0001 gloves XS 3 boxes", encoding="utf-8")
    answers[SUPPLIER_DOCUMENT_EXTRACTION.name] = {
        "INVOICE F-1": _document_answer("invoice", "F-1", supplier_name, 4.1),
        "DELIVERY NOTE A-1": _document_answer("delivery_note", "A-1", supplier_name, None),
    }
    one_pass()
    assert _rows(root, "SELECT kind, number, purchase_order_id, status FROM supplier_documents ORDER BY id") == [
        ("invoice", "F-1", 1, "linked"),
        ("delivery_note", "A-1", 1, "linked"),
    ]
    assert _rows(root, "SELECT result FROM reconciliations") == [("matched",)]
    assert _rows(
        root,
        "SELECT g.purchase_order_id, g.status, l.sku, l.quantity FROM goods_receipts g "
        "JOIN goods_receipt_lines l ON l.receipt_id = g.id",
    ) == [(1, "received", SHORT_SKU, 3)]
    assert _rows(root, f"SELECT status FROM orders WHERE id = {order_id}") == [("served",)]
    assert _rows(root, f"SELECT on_hand FROM stock WHERE sku = '{SHORT_SKU}'") == [(0,)]
    assert _rows(root, "SELECT order_id, customer_code FROM customer_delivery_notes") == [(order_id, "CLI-001")]
    assert _rows(
        root,
        "SELECT i.order_id, i.delivery_note_id, i.net_total, l.sku, l.quantity FROM customer_invoices i "
        "JOIN customer_invoice_lines l ON l.invoice_id = i.id",
    )[0][:2] == (order_id, 1)
    assert _rows(root, "SELECT COUNT(*) FROM customer_invoices") == [(1,)]
