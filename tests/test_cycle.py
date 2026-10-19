"""C2 (phase 12): the purchase_cycle graph and its stages."""

import json
import shutil
import sqlite3

from conftest import make_email
from purchase_cycle import cli, cycle, db, sales_cli
from purchase_cycle.catalog import CUSTOMERS
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import EMAIL_EXTRACTION, EMAIL_INTAKE, MATCHING, WHATSAPP_EXTRACTION, WHATSAPP_INTAKE


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

    def __init__(self, task, calls):
        self.task, self.calls_list = task, calls
        self.calls, self.usage = 0, []

    def extract(self, sentence, case_id=None, correction=None):
        self.calls_list.append(self.task.name)
        self.calls += 1
        for key, answer in ANSWERS[self.task.name].items():
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
