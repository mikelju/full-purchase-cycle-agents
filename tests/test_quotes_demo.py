"""C12 and C15: `quotes-demo` runs the sample timeline to the purchase order, each step in its own process, with the network blocked."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from test_orders_demo import SITECUSTOMIZE

PHASE_06_OUTPUT = Path(__file__).parent / "golden" / "quotes_demo_phase06.txt"  # the output before phase 07, header left out


def test_quotes_demo_ends_with_an_order_and_no_offers_listed_with_the_network_blocked(tmp_path):
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
    assert len(steps) == 11
    assert noted.count("process") == 1 + len(steps)  # the demo and one process per step
    for label in ("simulated time:", "requests started:", "emails written:", "replies received:", "requests:"):
        assert out.count(label) >= len(steps), label
    # The phase 06 steps and their summary print as before the phase, apart from the header line.
    before = PHASE_06_OUTPUT.read_text(encoding="utf-8").splitlines()
    assert out.splitlines()[1 : 1 + len(before)] == before
    assert "QR-0001  sku=GLV-NIT-XS  quantity=12  status=ordered" in out
    assert "QR-0002  sku=GLV-SURG-NEO-6.5  quantity=4  status=no_offers" in out
    assert "recommended: SUP-002" in out and "SUP-003  usable  quantity=8" in out
    assert "waiting for a person (no offers, no order): QR-0002" in out
    assert "QR-0001 ordered  PO-0001  supplier=SUP-002" in out
    assert "recommended=SUP-002  order=PO-0001" in out

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
    assert statuses == {1: "ordered", 2: "no_offers"}
    assert reminded >= 1 and replied_after == 1  # a reminder went out, and a reply came after it
    assert parked == [("QR-0002", "quote_wait")]
    conn = sqlite3.connect(workdir / "business.db")
    try:
        orders = conn.execute("SELECT request_id, supplier_code, quantity, status FROM purchase_orders").fetchall()
    finally:
        conn.close()
    assert orders == [(1, "SUP-002", 12, "issued")]
    assert sorted(p.name for p in (workdir / "supplier_outbox").iterdir()) == [
        "PO-0001-SUP-002.eml",
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
