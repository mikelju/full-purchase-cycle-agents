"""C12: `quotes-demo` runs the sample timeline, each step in its own process, with the network blocked."""

import os
import sqlite3
import subprocess
import sys

from test_orders_demo import SITECUSTOMIZE


def test_quotes_demo_ends_ready_after_a_reminder_and_no_offers_with_the_network_blocked(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    log = tmp_path / "processes.log"
    env = {**os.environ, "PYTHONPATH": str(site), "ORDERS_DEMO_TEST_LOG": str(log)}
    workdir = tmp_path / "work"
    command = [sys.executable, "-m", "purchase_cycle.cli", "quotes-demo", "--workdir", str(workdir)]
    run = subprocess.run(command, capture_output=True, text=True, env=env)
    out = run.stdout
    assert run.returncode == 0, out + run.stderr
    assert run.stderr == ""
    noted = log.read_text(encoding="utf-8").split()
    assert "network" not in noted
    steps = [line for line in out.splitlines() if line.startswith("== step ")]
    assert len(steps) == 8
    assert noted.count("process") == 1 + len(steps)  # the demo and one process per step
    for label in ("simulated time:", "requests started:", "emails written:", "replies received:", "requests:"):
        assert out.count(label) >= len(steps), label
    assert "QR-0001  sku=GLV-NIT-XS  quantity=12  status=ready_for_decision" in out
    assert "QR-0002  sku=GLV-SURG-NEO-6.5  quantity=4  status=no_offers" in out

    conn = sqlite3.connect(workdir / "business.db")
    try:
        statuses = dict(conn.execute("SELECT id, status FROM quote_requests ORDER BY id").fetchall())
        reminded = conn.execute(
            "SELECT COUNT(*) FROM quote_request_suppliers WHERE request_id = 1 AND reminded_at IS NOT NULL"
        ).fetchone()[0]
        replied_after = conn.execute(  # a reminder goes only to a supplier that has not replied yet
            "SELECT COUNT(*) FROM quote_request_suppliers "
            "WHERE request_id = 1 AND reminded_at IS NOT NULL AND replied_at >= reminded_at"
        ).fetchone()[0]
        parked = conn.execute("SELECT source, step FROM failures WHERE status = 'needs_review'").fetchall()
    finally:
        conn.close()
    assert statuses == {1: "ready_for_decision", 2: "no_offers"}
    assert reminded >= 1 and replied_after == 1  # a reminder went out, and a reply came after it
    assert parked == [("QR-0002", "quote_wait")]
    assert sorted(p.name for p in (workdir / "supplier_outbox").iterdir()) == [
        "QR-0001-reminder-SUP-001.eml",
        "QR-0001-reminder-SUP-003.eml",
        "QR-0001-request-SUP-001.eml",
        "QR-0001-request-SUP-002.eml",
        "QR-0001-request-SUP-003.eml",
        "QR-0002-reminder-SUP-001.eml",
        "QR-0002-reminder-SUP-002.eml",
        "QR-0002-request-SUP-001.eml",
        "QR-0002-request-SUP-002.eml",
    ]
