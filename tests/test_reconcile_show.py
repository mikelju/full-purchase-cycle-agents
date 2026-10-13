"""C9 and C3: `reconcile list`, `reconcile show INV-NNNN`, the restart between commands and a bad answer in a scan."""

import subprocess
import sys

from purchase_cycle import cli, db
from test_reconcile_scan import INVOICE, NOTE, World
from test_reconciliation import ANSWER

PRODUCT = ANSWER["lines"][0]["product"]
BAD_INVOICE = {
    **INVOICE,
    "lines": [
        {"product": PRODUCT, "quantity": 10, "unit_price": 4.5, "line_total": 45.0},
        {"product": "Mystery widget 3000", "quantity": 2, "unit_price": 3.0, "line_total": 6.0},
    ],
}


def _world(tmp_path, answers=(BAD_INVOICE, NOTE)):
    return World(tmp_path, answers=answers)


def _command(world, *words):
    return ["--db", str(world.db), "reconcile", *words]


def test_show_prints_result_order_and_each_discrepancy_with_type_sku_and_amounts(tmp_path, capsys):
    world = _world(tmp_path)
    assert world.run() == 0
    capsys.readouterr()
    assert cli.main(_command(world, "show", "INV-0001")) == 0
    out = capsys.readouterr().out
    assert "INV-0001" in out and "discrepancies" in out and "PO-0001" in out
    assert (
        "price" in out
        and "GLV-NIT-XS" in out
        and "4.1000" in out
        and "4.5000" in out
        and "0.4000" in out
        and "amount=4.00" in out
    )
    assert "extra_line" in out and "Mystery widget 3000" in out and "amount=6.00" in out


def test_the_original_text_of_an_unresolved_line_is_stored(tmp_path):
    world = _world(tmp_path)
    assert world.run() == 0
    conn = db.connect(world.db)
    try:
        rows = [tuple(r) for r in conn.execute("SELECT sku, type, text FROM discrepancies ORDER BY type")]
    finally:
        conn.close()
    assert rows == [(None, "extra_line", "Mystery widget 3000"), ("GLV-NIT-XS", "price", None)]


def test_list_prints_each_invoice_and_the_counts_by_type(tmp_path, capsys):
    world = _world(tmp_path)
    assert world.run() == 0
    capsys.readouterr()
    assert cli.main(_command(world, "list")) == 0
    out = capsys.readouterr().out
    assert "INV-0001" in out and "PO-0001" in out and "discrepancies" in out
    assert "price=1" in out and "extra_line=1" in out and "quantity=0" in out and "missing_line=0" in out
    assert "discrepancies=1" in out and "matched=0" in out and "awaiting_delivery_note=0" in out


def test_list_of_an_empty_database_prints_zero(tmp_path, capsys):
    world = _world(tmp_path)
    assert cli.main(_command(world, "list")) == 0
    assert "reconciliations: 0" in capsys.readouterr().out


def test_show_of_an_unknown_id_exits_1_with_a_message(tmp_path, capsys):
    world = _world(tmp_path)
    assert world.run() == 0
    capsys.readouterr()
    for reference in ("INV-0099", "nonsense"):
        assert cli.main(_command(world, "show", reference)) == 1
    err = capsys.readouterr().err
    assert "INV-0099" in err and "nonsense" in err


def test_scan_list_and_show_in_separate_processes_print_what_one_process_prints(tmp_path, capsys):
    steps = (("list",), ("show", "INV-0001"))
    single = _world(tmp_path / "single")
    assert single.run() == 0
    scan_out = capsys.readouterr().out
    expected = []
    for step in steps:
        assert cli.main(_command(single, *step)) == 0
        expected.append(capsys.readouterr().out)
    split = _world(tmp_path / "split")
    base = [sys.executable, "-m", "purchase_cycle.cli", "--db", str(split.db)]
    scan = subprocess.run(
        [
            *base,
            "reconcile",
            "scan",
            str(split.folder),
            "--checkpoints",
            str(split.checkpoints),
            "--recordings",
            str(split.recordings),
        ],
        capture_output=True,
        text=True,
    )
    assert scan.returncode == 0, scan.stderr
    assert scan.stdout == scan_out.replace(str(single.folder), str(split.folder))
    for step, want in zip(steps, expected, strict=True):
        run = subprocess.run([*base, "reconcile", *step], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        assert run.stdout == want


def test_a_schema_invalid_answer_in_a_scan_stores_extraction_failed_and_the_scan_goes_on(tmp_path, capsys):
    world = _world(tmp_path, answers=({**INVOICE, "lines": "nope"}, NOTE))
    assert world.run() == 0
    out = capsys.readouterr().out
    assert "extraction_failed" in out
    conn = db.connect(world.db)
    try:
        states = [tuple(r) for r in conn.execute("SELECT kind, status FROM supplier_documents ORDER BY id")]
        failures = conn.execute("SELECT status, step FROM failures").fetchall()
    finally:
        conn.close()
    assert states == [(None, "extraction_failed"), ("delivery_note", "linked")]
    assert [tuple(f) for f in failures] == [("needs_review", "document_extraction")]
