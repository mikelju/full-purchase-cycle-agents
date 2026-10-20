"""Phase 12 (C7): `cycle-demo` replays the versioned recording of the four-order scenario, each step in its own process, network blocked."""

import os
import sqlite3
import subprocess
import sys

import pytest

from conftest import SITECUSTOMIZE
from purchase_cycle import config

RECORDINGS = config.EVALS_DIR / "recordings" / "cycle_demo.jsonl"


@pytest.fixture(scope="module")
def cycle_demo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cycle")
    site = tmp / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    log = tmp / "processes.log"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LANGSMITH_API_KEY")}
    env.update(PYTHONPATH=str(site), ORDERS_DEMO_TEST_LOG=str(log))
    workdir = tmp / "work"
    command = [sys.executable, "-m", "purchase_cycle.cli", "cycle-demo", "--workdir", str(workdir)]
    command += ["--recordings", str(RECORDINGS)]
    run = subprocess.run(command, capture_output=True, text=True, env=env)
    return run, log, workdir


def test_the_demo_runs_with_the_network_blocked_each_step_in_its_own_process(cycle_demo):
    run, log, _ = cycle_demo
    out = run.stdout
    assert run.returncode == 0, out + run.stderr
    assert run.stderr == ""
    noted = log.read_text(encoding="utf-8").split()
    assert "network" not in noted
    steps = [line for line in out.splitlines() if line.startswith("== step ")]
    assert len(steps) == 14
    assert all("exit code 0" in line for line in steps)
    assert noted.count("process") == 1 + len(steps)  # the demo and one process per step
    assert "mode=replay" in out


def test_the_printed_output_shows_stages_pending_decisions_and_operator_decisions(cycle_demo):
    out = cycle_demo[0].stdout
    assert "pending decisions:" in out
    assert out.count("operator decision:") == 6
    for text in ("== step 1:", "== step 14:", "summary:"):
        assert text in out
    summary = out.split("summary:")[1]
    for line in (
        "order 1 (A): served, invoice matched",
        "order 2 (C): served, invoice awaiting_delivery_note",
        "order 3 (D): received, not invoiced",
        "order 4 (B): served, invoice discrepancies, credit note sent",
        "supplier claims: 1 rejected, 1 sent",
        "quote requests waiting: QR-0003 GLV-NIT-XS x1",
    ):
        assert line in summary


def test_the_database_holds_the_four_final_states(cycle_demo):
    run, _, workdir = cycle_demo
    assert run.returncode == 0, run.stdout + run.stderr
    conn = sqlite3.connect(workdir / "business.db")
    try:
        orders = {r[0]: r[1] for r in conn.execute("SELECT id, status FROM orders")}
        results = sorted(r[0] for r in conn.execute("SELECT result FROM sales_reconciliations"))
        corrections = [r[0] for r in conn.execute("SELECT kind FROM customer_corrections")]
        responses = [r[0] for r in conn.execute("SELECT status FROM customer_responses")]
        claims = sorted(r[0] for r in conn.execute("SELECT status FROM claims"))
        waiting = [r[0] for r in conn.execute("SELECT id FROM quote_requests WHERE status = 'waiting'")]
    finally:
        conn.close()
    assert orders == {1: "served", 2: "served", 3: "received", 4: "served"}  # A, C, D, B
    assert results == ["awaiting_delivery_note", "discrepancies", "matched"]
    assert corrections == ["credit_note"] and responses == ["sent"]
    assert claims == ["rejected", "sent"]
    assert waiting == [3]


@pytest.mark.parametrize("target", ["examples/cycle", "examples", "examples/cycle/orders", "src", "."])
def test_the_demo_refuses_a_workdir_that_holds_versioned_files_and_removes_nothing(target, capsys):
    from argparse import Namespace

    from purchase_cycle import cycle_cli

    before = sorted(p for p in (config.ROOT / "examples").rglob("*") if p.is_file())
    args = Namespace(
        mode="replay", examples=config.ROOT / "examples", workdir=config.ROOT / target, recordings=RECORDINGS
    )
    assert cycle_cli.cmd_cycle_demo(args) == 1
    assert "versioned" in capsys.readouterr().err
    assert sorted(p for p in (config.ROOT / "examples").rglob("*") if p.is_file()) == before
