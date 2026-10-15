"""C12: `receipt-demo` runs from the sample supplier documents to the customer delivery notes, each step in its own process, with the network blocked."""

import os
import sqlite3
import subprocess
import sys

from conftest import SITECUSTOMIZE


def run_receipt_demo(tmp_path, *extra):
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    log = tmp_path / "processes.log"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LANGSMITH_API_KEY")}
    env.update(PYTHONPATH=str(site), ORDERS_DEMO_TEST_LOG=str(log))
    workdir = tmp_path / "work"
    command = [sys.executable, "-m", "purchase_cycle.cli", "receipt-demo", "--workdir", str(workdir), *extra]
    return subprocess.run(command, capture_output=True, text=True, env=env), log, workdir


def test_receipt_demo_ends_with_every_outcome_with_the_network_blocked(tmp_path):
    run, log, workdir = run_receipt_demo(tmp_path)
    out = run.stdout
    assert run.returncode == 0, out + run.stderr
    assert run.stderr == ""
    noted = log.read_text(encoding="utf-8").split()
    assert "network" not in noted
    steps = [line for line in out.splitlines() if line.startswith("== step ")]
    assert len(steps) >= 8
    assert noted.count("process") == 1 + len(steps)  # the demo and one process per step
    assert "mode=replay" in out
    assert out.count("stock before:") == 2 and out.count("stock after:") == 2
    assert "receipt RCP-" in out and "served ORD-" in out and "skipped ORD-" in out
    assert "Distribuciones Sanitarias Demo S.L." in out  # the notes are shown

    conn = sqlite3.connect(workdir / "business.db")
    try:
        conn.row_factory = sqlite3.Row
        results = {
            r["number"]: r["result"]
            for r in conn.execute(
                "SELECT d.number, r.result FROM goods_receipts g JOIN reconciliations r ON r.delivery_note_id = g.delivery_note_id "
                "JOIN supplier_documents d ON d.id = g.delivery_note_id WHERE g.status = 'received'"
            )
        }
        unlinked = conn.execute(
            "SELECT COUNT(*) FROM goods_receipts g JOIN supplier_documents d ON d.id = g.delivery_note_id "
            "WHERE d.status = 'unlinked'"
        ).fetchone()[0]
        short = conn.execute(
            "SELECT COUNT(*) FROM goods_receipt_lines l JOIN goods_receipts g ON g.id = l.receipt_id "
            "JOIN purchase_orders p ON p.id = g.purchase_order_id AND p.sku = l.sku WHERE l.quantity < p.quantity"
        ).fetchone()[0]
        statuses = [r[0] for r in conn.execute("SELECT status FROM orders ORDER BY id")]
        notes = conn.execute("SELECT COUNT(*) FROM customer_delivery_notes").fetchone()[0]
    finally:
        conn.close()
    assert set(results.values()) == {"matched", "discrepancies"}
    assert unlinked == 0
    assert short >= 1
    assert statuses == ["served", "served", "received", "served", "served"]
    assert notes == 4
    assert "skipped ORD-0003  missing GAUZE-NS-5=" in out
    assert "summary:" in out and "orders skipped for lack of stock at the end: ORD-0003" in out


def test_receipt_demo_exits_non_zero_without_the_later_delivery_note(tmp_path):
    examples = tmp_path / "examples"
    (examples / "later").mkdir(parents=True)
    run, _, _ = run_receipt_demo(tmp_path, "--examples", str(examples))
    assert run.returncode == 1
    assert "did not end with every outcome" in run.stderr or "failed" in run.stderr
