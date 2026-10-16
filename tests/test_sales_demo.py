"""Phase 11 C14: `sales-demo` runs from the customer orders to the decided responses, each step in its own process, with the network blocked."""

import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

from conftest import SITECUSTOMIZE
from purchase_cycle import cli


def run_sales_demo(tmp_path, recordings, *extra):
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    log = tmp_path / "processes.log"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LANGSMITH_API_KEY")}
    env.update(PYTHONPATH=str(site), ORDERS_DEMO_TEST_LOG=str(log))
    workdir = tmp_path / "work"
    command = [sys.executable, "-m", "purchase_cycle.cli", "sales-demo", "--workdir", str(workdir)]
    command += ["--recordings", str(recordings), *extra]
    return subprocess.run(command, capture_output=True, text=True, env=env), log, workdir


@pytest.fixture(scope="module")
def sales_demo(tmp_path_factory):
    """One demo run shared by the tests that only read its output, log and database."""
    tmp = tmp_path_factory.mktemp("sales")
    return run_sales_demo(tmp, cli.SALES_DEMO_RECORDINGS)


def test_sales_demo_ends_with_every_outcome_with_the_network_blocked(sales_demo):
    run, log, _ = sales_demo
    out = run.stdout
    assert run.returncode == 0, out + run.stderr
    assert run.stderr == ""
    noted = log.read_text(encoding="utf-8").split()
    assert "network" not in noted
    steps = [line for line in out.splitlines() if line.startswith("== step ")]
    assert len(steps) == 13
    assert noted.count("process") == 1 + len(steps)  # the demo and one process per step
    assert "mode=replay" in out
    for text in (
        "SINV-0001",
        "SINV-0005",
        "CN-0001",
        "CI-0003",
        "source=model",
        "signer=Laura Etxeberria",
    ):
        assert text in out
    assert "response sent (as drafted)" in out and "response sent (edited)" in out and "response rejected" in out
    assert "signed note 05-cdn-0099.txt  unlinked" in out
    assert "summary:" in out and "signed notes unlinked: 1" in out


def test_the_demo_database_holds_the_end_state(sales_demo):
    run, _, workdir = sales_demo
    assert run.returncode == 0, run.stdout + run.stderr
    conn = sqlite3.connect(workdir / "business.db")
    try:
        results = [r[0] for r in conn.execute("SELECT result FROM sales_reconciliations ORDER BY invoice_id")]
        kinds = sorted(r[0] for r in conn.execute("SELECT kind FROM customer_corrections"))
        responses = [tuple(r) for r in conn.execute("SELECT status, edited FROM customer_responses ORDER BY id")]
        edited = conn.execute("SELECT final_body FROM customer_responses WHERE edited = 1").fetchone()[0]
    finally:
        conn.close()
    assert results == ["discrepancies"] * 3 + ["matched", "awaiting_delivery_note"]
    assert kinds == ["corrective_invoice", "credit_note", "credit_note", "credit_note"]
    assert responses == [("sent", 0), ("sent", 1), ("rejected", 0)]
    assert edited.startswith("Dear customer,")
    emails = sorted(p.name for p in (workdir / "customer_outbox").glob("*.eml"))
    assert len(emails) == 2


def test_the_demo_stops_with_a_missing_recording(tmp_path):
    run, _, _ = run_sales_demo(tmp_path, tmp_path / "none.jsonl")
    assert run.returncode == 1
    assert "recordings not found" in run.stderr


def test_the_demo_refuses_the_data_folder_as_workdir():
    assert cli.main(["sales-demo", "--workdir", str(cli.config.DATA_DIR), "--recordings", __file__]) == 1


@pytest.fixture(scope="module")
def demo_db(sales_demo):
    run, _, workdir = sales_demo
    assert run.returncode == 0, run.stdout + run.stderr
    return workdir / "business.db", workdir / "customer_outbox"


def _outcomes(demo_db, tmp_path, *statements):
    path, outbox = demo_db
    copy = tmp_path / "copy.db"
    shutil.copy(path, copy)
    conn = sqlite3.connect(copy)
    try:
        for statement in statements:
            conn.execute(statement)
        return cli.sales_outcomes(conn, outbox)
    finally:
        conn.close()


def test_the_outcome_check_finds_nothing_missing_in_the_demo_database(demo_db, tmp_path):
    assert _outcomes(demo_db, tmp_path)["missing"] == []


@pytest.mark.parametrize(
    ("statement", "missing"),
    [
        ("UPDATE sales_reconciliations SET result = 'discrepancies' WHERE result = 'matched'", "matched invoice"),
        ("UPDATE sales_reconciliations SET result = 'matched' WHERE result = 'awaiting_delivery_note'", "awaiting"),
        ("DELETE FROM customer_corrections WHERE kind = 'corrective_invoice'", "corrective invoice"),
        ("DELETE FROM customer_corrections WHERE kind = 'credit_note'", "credit note"),
        ("UPDATE customer_responses SET edited = 1", "as drafted"),
        ("UPDATE customer_responses SET edited = 0", "sent edited"),
        ("UPDATE customer_responses SET status = 'awaiting_approval' WHERE status = 'rejected'", "rejected"),
        ("UPDATE signed_delivery_notes SET status = 'linked'", "unlinked signed note"),
    ],
)
def test_the_outcome_check_reports_each_missing_outcome(demo_db, tmp_path, statement, missing):
    found = _outcomes(demo_db, tmp_path, statement)["missing"]
    assert len(found) == 1 and missing in found[0]


def test_the_demo_exits_non_zero_when_an_outcome_is_missing(tmp_path, monkeypatch, capsys):
    done = subprocess.CompletedProcess([], 0, stdout="", stderr="")  # every step "succeeds" and changes nothing
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done)
    code = cli.main(["sales-demo", "--workdir", str(tmp_path / "work"), "--recordings", __file__])
    assert code == 1
    assert "did not end with every outcome, missing: matched invoice" in capsys.readouterr().err
