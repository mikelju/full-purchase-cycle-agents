"""C14: `reconcile-demo` runs the sample folder from the scan to the stored results, each step in its own process, with the network blocked."""

import json
import os
import sqlite3
import subprocess
import sys

from purchase_cycle import config
from test_orders_demo import SITECUSTOMIZE


def _run_demo(tmp_path, *extra):
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    log = tmp_path / "processes.log"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LANGSMITH_API_KEY")}
    env.update(PYTHONPATH=str(site), ORDERS_DEMO_TEST_LOG=str(log))
    workdir = tmp_path / "work"
    command = [sys.executable, "-m", "purchase_cycle.cli", "reconcile-demo", "--workdir", str(workdir), *extra]
    run = subprocess.run(command, capture_output=True, text=True, env=env)
    return run, log, workdir


def test_reconcile_demo_ends_with_every_outcome_with_the_network_blocked(tmp_path):
    run, log, workdir = _run_demo(tmp_path)
    out = run.stdout
    assert run.returncode == 0, out + run.stderr
    assert run.stderr == ""
    noted = log.read_text(encoding="utf-8").split()
    assert "network" not in noted
    steps = [line for line in out.splitlines() if line.startswith("== step ")]
    assert len(steps) >= 10
    assert noted.count("process") == 1 + len(steps)  # the demo and one process per step
    assert "mode=replay" in out
    assert "extracted documents:" in out and "differences:" in out

    conn = sqlite3.connect(workdir / "business.db")
    try:
        results = [r[0] for r in conn.execute("SELECT result FROM reconciliations ORDER BY invoice_id")]
        kinds = {r[0] for r in conn.execute("SELECT DISTINCT type FROM discrepancies")}
        unlinked = sorted(r[0] for r in conn.execute("SELECT reason FROM supplier_documents WHERE status = 'unlinked'"))
        late = conn.execute(  # the invoice that waited for its note was completed by the second scan
            "SELECT r.result, d.source_file FROM reconciliations r JOIN supplier_documents d ON d.id = r.delivery_note_id "
            "JOIN supplier_documents i ON i.id = r.invoice_id WHERE i.number = 'LV-0306'"
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM reconciliations").fetchone()[0]
        orders = conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0]
    finally:
        conn.close()
    assert orders == 6
    assert count == 6 == len(results)  # one reconciliation per linked invoice, none added by the second scan
    assert "matched" in results
    assert "awaiting_delivery_note" not in results  # the second scan completed it
    assert kinds == {"price", "quantity", "extra_line", "missing_line"}
    assert unlinked == ["supplier_mismatch", "unknown_order"]
    assert late is not None and late[0] == "matched" and late[1].endswith("dn-006-thermometers.pdf")
    assert "result=awaiting_delivery_note" in out  # it was shown awaiting before the second scan
    assert out.count("result=matched") >= 3
    assert "unlinked (unknown_order)" in out and "unlinked (supplier_mismatch)" in out
    for kind in ("price", "quantity", "extra_line", "missing_line"):
        assert f"  {kind}  sku=" in out


def test_the_recordings_hold_one_row_per_sample_document():
    rows = [
        json.loads(line)
        for line in (config.EVALS_DIR / "recordings" / "reconcile_demo.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 14
