"""C10: `receipts run`, `receipts list` and `receipts show`; C9: the restart between processes."""

from purchase_cycle import cli, db
from test_receipts import _scenario


def _args(folder, *words):
    return [
        "--db",
        str(folder / "r.db"),
        "receipts",
        *words,
        *(
            ["--checkpoints", str(folder / "checkpoints.sqlite"), "--notes-dir", str(folder / "notes")]
            if words[0] == "run"
            else []
        ),
    ]


def _world(tmp_path):
    folder = tmp_path / "w"
    _scenario(folder)
    return folder


def _run(folder, capsys, *words):
    code = cli.main(_args(folder, *words))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _state(folder):
    conn = db.connect(folder / "r.db")
    try:
        return {
            t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
            for t in ("goods_receipts", "goods_receipt_lines", "customer_delivery_notes", "orders", "stock", "failures")
        }
    finally:
        conn.close()


def test_run_prints_each_receipt_failed_note_served_order_and_skipped_order(tmp_path, capsys):
    tmp_path = _world(tmp_path)
    code, out, _ = _run(tmp_path, capsys, "run")
    assert code == 0
    lines = out.splitlines()
    assert any("RCP-0001" in x and "DN-1" in x and "received" in x and "PO-0001" in x for x in lines)
    assert any("RCP-0002" in x and "DN-2" in x and "failed" in x and "NOPE" in x for x in lines)
    assert any("served ORD-0001" in x and "CDN-0001" in x for x in lines)
    assert any("served ORD-0002" in x and "CDN-0002" in x for x in lines)
    assert any("skipped ORD-0003" in x and "missing" in x and "=490" in x for x in lines)
    assert "nothing changed" not in out


def test_a_second_run_prints_that_nothing_changed_and_changes_nothing(tmp_path, capsys):
    tmp_path = _world(tmp_path)
    _run(tmp_path, capsys, "run")
    before = _state(tmp_path)
    code, out, _ = _run(tmp_path, capsys, "run")
    assert code == 0 and "nothing changed" in out and "served" not in out
    assert _state(tmp_path) == before


def test_list_prints_each_receipt_with_its_order_and_each_note_with_its_order_and_customer(tmp_path, capsys):
    tmp_path = _world(tmp_path)
    _run(tmp_path, capsys, "run")
    code, out, _ = _run(tmp_path, capsys, "list")
    assert code == 0
    lines = out.splitlines()
    assert any("RCP-0001" in x and "PO-0001" in x and "=60" in x for x in lines)
    assert any("RCP-0002" in x and "PO-0002" in x and "failed" in x for x in lines)
    assert any("CDN-0001" in x and "ORD-0001" in x and "customer=" in x and "=50" in x for x in lines)
    assert any("CDN-0002" in x and "ORD-0002" in x for x in lines)
    assert "receipts: 2" in out and "customer delivery notes: 2" in out


def test_show_prints_the_file_text(tmp_path, capsys):
    tmp_path = _world(tmp_path)
    _run(tmp_path, capsys, "run")
    code, out, _ = _run(tmp_path, capsys, "show", "CDN-0001")
    assert code == 0
    assert out == (tmp_path / "notes" / "CDN-0001.txt").read_text(encoding="ascii")
    assert "Delivery note CDN-0001" in out


def test_an_unknown_and_a_malformed_reference_exit_1_and_change_nothing(tmp_path, capsys):
    tmp_path = _world(tmp_path)
    _run(tmp_path, capsys, "run")
    before = _state(tmp_path)
    for reference in ("CDN-0099", "nonsense", "INV-0001"):
        code, out, err = _run(tmp_path, capsys, "show", reference)
        assert code == 1 and out == "" and "Error" in err
    assert _state(tmp_path) == before


def test_reconcile_list_output_is_unchanged_by_the_receipts_tables(tmp_path, capsys):
    tmp_path = _world(tmp_path)
    cli.main(["--db", str(tmp_path / "r.db"), "reconcile", "list"])
    first = capsys.readouterr().out
    _run(tmp_path, capsys, "run")
    cli.main(["--db", str(tmp_path / "r.db"), "reconcile", "list"])
    assert capsys.readouterr().out == first
